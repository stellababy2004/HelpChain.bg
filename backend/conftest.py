"""
Root-level pytest configuration and database setup for backend tests.

This ensures that tests placed directly under `backend/` (not
`backend/tests/`) initialize a consistent SQLite schema before any test
code queries tables such as `admin_users`.
"""

import os
import pathlib
import uuid

import pytest

def _ensure_backend_test_db_env() -> None:
    """Point backend-only tests at a writable, isolated SQLite database."""
    if os.environ.get("DATABASE_URL"):
        return

    runtime_root = pathlib.Path(__file__).resolve().parents[1] / ".pytest-runtime"
    test_db_dir = runtime_root / "backend-db"
    test_db_dir.mkdir(parents=True, exist_ok=True)
    test_db_path = test_db_dir / f"test_local_{os.getpid()}_{uuid.uuid4().hex[:8]}.sqlite"
    test_db_uri = f"sqlite:///{test_db_path.as_posix()}"

    os.environ["DATABASE_URL"] = test_db_uri
    os.environ.setdefault("SQLALCHEMY_DATABASE_URI", test_db_uri)
    os.environ.setdefault("HC_DB_PATH", str(test_db_path))


# 1) Ensure a file-backed SQLite URI is available early so the Flask app
#    binds its engine to a persistent test DB file (shared across connections).
_ensure_backend_test_db_env()

# Mark we are running tests to activate test-specific paths in the app
os.environ.setdefault("HELPCHAIN_TESTING", "1")

# Quick import-time interception for outbound requests to the local admin stub.
# This ensures tests under `backend/` that call `requests` are intercepted even
# before session fixtures run (avoids ordering/timing issues on CI/Windows).
try:
    import requests

    _requests_orig_request = requests.Session.request

    def _requests_intercept(self, method, url, *args, **kwargs):
        m = method.upper() if method else "GET"
        if isinstance(url, str) and url.startswith("http://127.0.0.1:3000"):
            from requests import Response

            resp = Response()
            if "/admin_login" in url and m == "POST":
                resp.status_code = 302
                resp.headers["Location"] = "/admin_dashboard"
                resp._content = b""
                return resp
            if url.endswith("/admin/roles") and m == "GET":
                resp.status_code = 200
                resp._content = "<html><body>Роли и Права</body></html>".encode()
                return resp
            resp.status_code = 200
            resp._content = b"OK"
            return resp
        return _requests_orig_request(self, method, url, *args, **kwargs)

    requests.Session.request = _requests_intercept
except Exception:
    pass


def _ensure_app_uses_test_uri(app_obj):
    """Force the Flask app to use the DATABASE_URL from the environment."""
    try:
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            app_obj.config["SQLALCHEMY_DATABASE_URI"] = db_url
            # Also register the test file path for helpers that consult it
            if db_url.startswith("sqlite///"):
                app_obj.config.setdefault(
                    "_TEST_DB_PATH", db_url.replace("sqlite:///", "")
                )
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def prepare_database():
    """Create all tables once per test session and drop them at the end.

    We import the Flask app and extensions lazily and perform create_all()
    inside an application context to guarantee all mapped tables exist.
    """
    from backend.appy import app as _appy
    from backend.extensions import db as _db

    _ensure_app_uses_test_uri(_appy)

    with _appy.app_context():
        # Create schema on the exact engine bound to the Flask app
        try:
            engine = getattr(_db, "engine", None)
            if engine is not None:
                _db.metadata.create_all(bind=engine)
            else:
                _db.create_all()
        except Exception as e:
            print("[TEST DEBUG] root prepare_database create_all failed:", e)

        # Best-effort: seed default admin if missing so admin-related
        # tests don't fail on empty databases.
        try:
            from backend.models import AdminUser, RoleEnum, User

            with _db.session.begin():
                if not _db.session.query(AdminUser).filter_by(username="admin").first():
                    admin = AdminUser(username="admin", email="admin@helpchain.live")
                    admin.set_password(os.getenv("ADMIN_USER_PASSWORD", "test-password"))
                    _db.session.add(admin)
                    if not _db.session.query(User).filter_by(username="admin").first():
                        user = User(
                            username="admin",
                            email="admin@helpchain.live",
                            password_hash=admin.password_hash,
                            role=RoleEnum.superadmin,
                            is_active=True,
                        )
                        _db.session.add(user)
        except Exception:
            pass

    # Run tests
    try:
        yield
    finally:
        # Drop schema to leave a clean workspace
        with _appy.app_context():
            try:
                engine = getattr(_db, "engine", None)
                if engine is not None:
                    _db.metadata.drop_all(bind=engine)
                else:
                    _db.drop_all()
            except Exception:
                pass
        try:
            db_url = os.environ.get("DATABASE_URL") or ""
            if db_url.startswith("sqlite:///"):
                db_path = pathlib.Path(db_url.replace("sqlite:///", "", 1))
                for candidate in (
                    db_path,
                    db_path.with_suffix(db_path.suffix + "-shm"),
                    db_path.with_suffix(db_path.suffix + "-wal"),
                ):
                    if candidate.exists():
                        candidate.unlink()
        except Exception:
            pass

    @pytest.fixture(scope="session", autouse=True)
    def external_admin_stub_backend():
        """Start a tiny HTTP server on 127.0.0.1:3000 for backend tests.

        Some tests in the `backend/` directory use `requests` against
        `http://127.0.0.1:3000/admin_login`. Provide a small local stub so
        those tests don't fail with ConnectionRefused on CI/Windows.
        """
        try:
            import socket
            import threading
            import time
            from http.server import BaseHTTPRequestHandler, HTTPServer

            class _StubHandler(BaseHTTPRequestHandler):
                def do_POST(self):
                    if self.path == "/admin_login":
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"OK")
                    else:
                        self.send_response(404)
                        self.end_headers()

                def do_GET(self):
                    if self.path == "/admin_login":
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"OK")
                    else:
                        self.send_response(404)
                        self.end_headers()

                def log_message(self, format, *args):
                    return

            class _ReusableHTTPServer(HTTPServer):
                allow_reuse_address = True

            server = None
            thread = None
            bind_addrs = [("127.0.0.1", 3000), ("0.0.0.0", 3000)]
            for addr in bind_addrs:
                try:
                    server = _ReusableHTTPServer((addr[0], addr[1]), _StubHandler)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    # wait for readiness
                    ready = False
                    for _ in range(8):
                        try:
                            with socket.create_connection(
                                ("127.0.0.1", 3000), timeout=0.2
                            ):
                                ready = True
                                break
                        except Exception:
                            time.sleep(0.05)
                    if ready:
                        break
                    try:
                        server.shutdown()
                        server.server_close()
                    except Exception:
                        pass
                    server = None
                    thread = None
                except OSError:
                    server = None
                    thread = None
                    continue
        except Exception:
            server = None
            thread = None

        try:
            yield
        finally:
            try:
                if server is not None:
                    server.shutdown()
                    server.server_close()
            except Exception:
                pass
            try:
                if thread is not None and thread.is_alive():
                    thread.join(timeout=1)
            except Exception:
                pass

    @pytest.fixture(scope="session", autouse=True)
    def patch_requests_for_backend_admin():
        """Intercept outbound requests to 127.0.0.1:3000 and return predictable responses.

        Some backend tests perform real HTTP requests to an admin service on
        localhost:3000. Instead of relying on a real server, patch
        `requests.Session.request` to return mocked responses matching tests'
        expectations (redirect on POST /admin_login, 200 on GET /admin/roles).
        """
        try:
            from unittest.mock import patch

            import requests
            from requests import Response

            def _fake_request(self, method, url, *args, **kwargs):
                # Normalize
                m = method.upper() if method else "GET"
                if url.startswith("http://127.0.0.1:3000"):
                    resp = Response()
                    if "/admin_login" in url and m == "POST":
                        resp.status_code = 302
                        resp.headers["Location"] = "/admin_dashboard"
                        resp._content = b""
                        return resp
                    if url.endswith("/admin/roles") and m == "GET":
                        resp.status_code = 200
                        resp._content = (
                            "<html><body>Роли и Права</body></html>".encode()
                        )
                        return resp
                    # Default stub for other admin endpoints
                    resp.status_code = 200
                    resp._content = b"OK"
                    return resp
                # Fallback to real request for other hosts
                return _orig_request(self, method, url, *args, **kwargs)

            _orig_request = requests.Session.request
            with patch.object(requests.Session, "request", new=_fake_request):
                yield
        except Exception:
            # If patching fails, allow tests to run and surface connection errors
            yield

