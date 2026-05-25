import pytest
from datetime import timedelta
from pathlib import Path
from sqlalchemy import inspect as sa_inspect

from backend.models import utc_now


def _sqlite_db_path(app) -> Path | None:
    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    if not uri.startswith("sqlite:///"):
        return None
    return Path(uri.replace("sqlite:///", "", 1))


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch, tmp_path_factory):
    tmp_root = tmp_path_factory.mktemp("hc-pytest-db")
    test_db_path = tmp_root / "test.sqlite"
    test_db_uri = f"sqlite:///{test_db_path.as_posix()}"

    monkeypatch.setenv("HC_ENV", "test")
    monkeypatch.setenv("HELPCHAIN_TESTING", "1")
    monkeypatch.setenv("HC_DEFAULT_STRUCTURE_SLUG", "default")
    # Ensure admin test passwords satisfy policy (upper/lower/digit).
    monkeypatch.setenv("TEST_ADMIN_PASSWORD", "TestPassword1")
    monkeypatch.setenv("ADMIN_PASSWORD", "TestPassword1")
    monkeypatch.setenv("ADMIN_USER_PASSWORD", "TestPassword1")
    monkeypatch.setenv("HC_DB_PATH", str(test_db_path))
    monkeypatch.setenv("DATABASE_URL", test_db_uri)
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", test_db_uri)
    monkeypatch.setenv("TMPDIR", str(tmp_root))
    monkeypatch.setenv("TEMP", str(tmp_root))
    monkeypatch.setenv("TMP", str(tmp_root))


@pytest.fixture
def app():
    import importlib
    import os

    from backend.helpchain_backend.src import config as app_config
    from backend.models import db

    app_config = importlib.reload(app_config)
    from backend.helpchain_backend.src import app as app_module

    app_module = importlib.reload(app_module)
    test_db_uri = f"sqlite:///{os.environ['HC_DB_PATH'].replace('\\', '/')}"
    app_config.Config.SQLALCHEMY_DATABASE_URI = test_db_uri
    if hasattr(app_config, "DevConfig"):
        app_config.DevConfig.SQLALCHEMY_DATABASE_URI = test_db_uri
    if hasattr(app_config, "ProdConfig"):
        app_config.ProdConfig.SQLALCHEMY_DATABASE_URI = test_db_uri

    app = app_module.create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": test_db_uri,
        }
    )
    # create_app loads config objects after dict update; force test-only overrides here.
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ALLOW_DEFAULT_TENANT_FALLBACK"] = True
    app.config["VOLUNTEER_DEV_BYPASS_ENABLED"] = True
    app.config["VOLUNTEER_DEV_BYPASS_EMAIL"] = "volunteer@test.local"
    yield app
    with app.app_context():
        db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass
    db_path = Path(str(app.config.get("SQLALCHEMY_DATABASE_URI") or "").replace("sqlite:///", "", 1))
    for candidate in (db_path, db_path.with_suffix(db_path.suffix + "-shm"), db_path.with_suffix(db_path.suffix + "-wal")):
        try:
            if candidate.exists():
                candidate.unlink()
        except Exception:
            pass


@pytest.fixture
def db_schema(app):
    with app.app_context():
        from backend.models import db
        import backend.models  # noqa: F401
        import backend.models_with_analytics  # noqa: F401

        db_path = _sqlite_db_path(app)
        db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass
        existing_tables = set(sa_inspect(db.engine).get_table_names())
        if not existing_tables:
            db.create_all()

        from backend.models import Structure

        if not Structure.query.filter_by(slug="default").first():
            db.session.add(Structure(name="Default", slug="default"))
            db.session.commit()

        yield

        db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass
        if db_path and db_path.exists():
            try:
                db_path.unlink()
            except Exception:
                pass


@pytest.fixture
def client(app, db_schema):
    return app.test_client()


@pytest.fixture
def session(app, db_schema):
    """Legacy fixture: SQLAlchemy session handle."""
    from backend.models import db

    return db.session


@pytest.fixture
def db_session(session):
    """Compatibility alias used by parts of the test suite."""
    return session


@pytest.fixture
def real_app(app, db_schema):
    """Legacy alias fixture."""
    return app


@pytest.fixture
def init_test_data(app, session, db_schema):
    """
    Legacy + integration fixture.
    Returns common seeded entities under stable keys.
    """
    data = {}
    from backend.models import Structure

    structure = session.query(Structure).filter_by(slug="default").first()
    if not structure:
        structure = Structure(name="Default", slug="default")
        session.add(structure)
        session.commit()
    data["structure"] = structure

    volunteer = None
    try:
        from backend.models import Volunteer

        volunteer = session.query(Volunteer).filter_by(email="volunteer@test.local").first()
        if not volunteer:
            volunteer = Volunteer(email="volunteer@test.local", name="Test Volunteer")
            if hasattr(volunteer, "structure_id"):
                volunteer.structure_id = getattr(structure, "id", None)
            session.add(volunteer)
            session.commit()
    except Exception:
        volunteer = None

    if volunteer is None:
        try:
            from backend.models import User

            volunteer = session.query(User).filter_by(email="volunteer@test.local").first()
            if not volunteer:
                volunteer = User(
                    username="volunteer_test_user",
                    email="volunteer@test.local",
                    password_hash="x",
                    role="volunteer",
                    is_active=True,
                )
                if hasattr(volunteer, "structure_id"):
                    volunteer.structure_id = getattr(structure, "id", None)
                session.add(volunteer)
                session.commit()
        except Exception:
            volunteer = {"id": 1, "email": "volunteer@test.local", "role": "volunteer"}

    data["volunteer"] = volunteer
    admin_with_2fa = None
    try:
        from werkzeug.security import generate_password_hash

        from backend.models import AdminUser

        admin_with_2fa = (
            session.query(AdminUser).filter_by(username="admin_2fa_test").first()
        )
        if not admin_with_2fa:
            admin_with_2fa = AdminUser(
                username="admin_2fa_test",
                email="admin_2fa_test@helpchain.local",
                password_hash=generate_password_hash("TestPass123"),
                role="admin",
                is_active=True,
            )
            session.add(admin_with_2fa)
            session.commit()
    except Exception:
        admin_with_2fa = None

    data["admin_with_2fa"] = admin_with_2fa
    return data


@pytest.fixture
def authenticated_volunteer_client(app, session, init_test_data):
    """Legacy fixture: test client with volunteer-like authenticated session."""
    client = app.test_client()
    volunteer = init_test_data.get("volunteer")
    vid = int(getattr(volunteer, "id", 1) or 1)

    with client.session_transaction() as s:
        s["volunteer_id"] = vid
        s["volunteer_logged_in"] = True
        s["_user_id"] = str(vid)
        s["user_id"] = vid
        s["role"] = "volunteer"
        s["is_authenticated"] = True

    return client


@pytest.fixture
def authenticated_admin_client(app, session, init_test_data, db_schema):
    """
    Legacy fixture: returns a client authenticated as an admin.
    """
    client = app.test_client()
    admin_id = None
    try:
        from backend.models import AdminUser

        admin = session.query(AdminUser).filter_by(email="admin@test.local").first()
        if not admin:
            admin = AdminUser(
                username="admin_test_user",
                email="admin@test.local",
                password_hash="x",
                role="superadmin",
                is_active=True,
                mfa_enabled=True,
                totp_secret="test-mfa-secret",
            )
            session.add(admin)
            session.commit()
        else:
            admin.role = "superadmin"
            admin.is_active = True
            admin.mfa_enabled = True
            admin.totp_secret = "test-mfa-secret"
            session.commit()
        admin_id = getattr(admin, "id", None)
    except Exception:
        admin_id = 1

    with client.session_transaction() as s:
        s["_user_id"] = str(admin_id)
        s["user_id"] = admin_id
        s["role"] = "superadmin"
        s["is_authenticated"] = True
        s["is_admin"] = True
        s["admin_logged_in"] = True
        s["admin_id"] = admin_id
        s["mfa_required"] = True
        s[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        s["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        s["admin_mfa_last_verified"] = 4102444800
        s["admin_mfa_user_id"] = admin_id

    return client


@pytest.fixture
def mock_ai_service(monkeypatch):
    async def _generate_response(message, context=None):
        return {
            "response": "Тестов отговор от AI",
            "confidence": 0.95,
            "provider": "mock-provider",
        }

    monkeypatch.setattr(
        "backend.helpchain_backend.src.routes.api.ai_service.generate_response",
        _generate_response,
    )
    return True


@pytest.fixture
def mock_smtp(monkeypatch):
    """Legacy fixture expected by route tests."""
    from unittest.mock import MagicMock

    try:
        mock_send = MagicMock()
        monkeypatch.setattr("backend.appy.mail.send", mock_send)
        return mock_send
    except Exception:
        return MagicMock()


@pytest.fixture
def test_volunteer(session):
    from backend.models import Volunteer

    volunteer = session.query(Volunteer).filter_by(email="dupe@test.local").first()
    if not volunteer:
        volunteer = Volunteer(
            name="Existing Volunteer",
            email="dupe@test.local",
            phone="+359888000000",
            location="Sofia",
        )
        session.add(volunteer)
        session.commit()
    return volunteer


@pytest.fixture
def test_admin_user(session):
    from backend.models import AdminUser
    from werkzeug.security import generate_password_hash

    admin = session.query(AdminUser).filter_by(username="security_admin").first()
    if not admin:
        admin = AdminUser(
            username="security_admin",
            email="security_admin@test.local",
            password_hash=generate_password_hash("SecurePass123"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.commit()
    return admin
