from __future__ import annotations

import logging
import shutil
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask

from backend.helpchain_backend.src.app import maybe_self_heal_local_sqlite
from backend.local_db_guard import select_local_runtime_db


def _create_runtime_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE admin_users (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE structures (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        con.execute(
            """
            CREATE TABLE cases (
                id INTEGER PRIMARY KEY,
                latitude REAL,
                longitude REAL,
                status TEXT,
                priority TEXT
            )
            """
        )
        con.commit()
    finally:
        con.close()


def _workspace_tmp_dir() -> Path:
    path = Path(".tmp") / f"local_db_guard_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _repo_local_tmp_dir(prefix: str) -> Path:
    base = Path("instance") / "_tmp_test_runs"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_select_local_runtime_db_prefers_healthy_primary():
    tmp_path = _workspace_tmp_dir()
    try:
        primary = tmp_path / "hc_local_dev.db"
        fallback = tmp_path / "app_clean.db"
        _create_runtime_db(primary)
        _create_runtime_db(fallback)

        selection = select_local_runtime_db(
            env={},
            root=tmp_path,
            primary_path=primary,
            fallback_path=fallback,
        )

        assert selection.apply_contract is True
        assert selection.selected_label == "primary"
        assert selection.selected_path == primary.resolve()
        assert selection.selected_health is not None
        assert selection.selected_health.healthy is True
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_select_local_runtime_db_uses_fallback_when_primary_is_unhealthy():
    tmp_path = _workspace_tmp_dir()
    try:
        primary = tmp_path / "hc_local_dev.db"
        fallback = tmp_path / "app_clean.db"
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.touch()
        _create_runtime_db(fallback)

        selection = select_local_runtime_db(
            env={},
            root=tmp_path,
            primary_path=primary,
            fallback_path=fallback,
        )

        assert selection.apply_contract is True
        assert selection.selected_label == "fallback"
        assert selection.selected_path == fallback.resolve()
        assert selection.selected_health is not None
        assert selection.selected_health.healthy is True
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_select_local_runtime_db_does_not_override_testing_context():
    tmp_path = _workspace_tmp_dir()
    try:
        primary = tmp_path / "hc_local_dev.db"
        fallback = tmp_path / "app_clean.db"
        _create_runtime_db(fallback)

        selection = select_local_runtime_db(
            env={
                "HELPCHAIN_TESTING": "1",
                "HC_DB_PATH": str(primary.resolve()),
            },
            root=tmp_path,
            primary_path=primary,
            fallback_path=fallback,
        )

        assert selection.apply_contract is False
        assert selection.selected_label == "configured"
        assert selection.selected_path == primary.resolve()
        assert "test environment" in selection.reason
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def _make_selfheal_app(db_path: Path) -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    return app


def _make_fake_db() -> SimpleNamespace:
    return SimpleNamespace(
        engine=SimpleNamespace(dispose=Mock()),
        session=SimpleNamespace(remove=Mock()),
    )


def test_self_heal_missing_structures_does_not_delete_db_by_default(monkeypatch, caplog):
    tmp_path = _repo_local_tmp_dir("selfheal_blocked_")
    try:
        db_path = tmp_path / "hc_local_dev.db"
        db_path.write_bytes(b"demo-data")

        app = _make_selfheal_app(db_path)
        fake_db = _make_fake_db()

        class _Inspector:
            @staticmethod
            def get_table_names():
                return ["admin_users"]

        subprocess_run = Mock(side_effect=AssertionError("destructive repair should be blocked"))

        monkeypatch.setattr("sqlalchemy.inspect", lambda engine: _Inspector())
        monkeypatch.setattr("subprocess.run", subprocess_run)
        monkeypatch.delenv("HC_SELFHEAL_ALLOW_DESTRUCTIVE", raising=False)
        monkeypatch.delenv("HC_SKIP_SELFHEAL", raising=False)
        monkeypatch.delenv("DISABLE_SELF_HEAL", raising=False)
        monkeypatch.delenv("HELPCHAIN_TESTING", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        caplog.set_level(logging.WARNING)

        maybe_self_heal_local_sqlite(app, fake_db)

        backups = list(db_path.parent.glob("hc_local_dev.db.selfheal_backup_*"))
        assert db_path.exists()
        assert db_path.read_bytes() == b"demo-data"
        assert len(backups) == 1
        assert backups[0].read_bytes() == b"demo-data"
        assert "[SELFHEAL] destructive repair blocked: set HC_SELFHEAL_ALLOW_DESTRUCTIVE=true to allow" in caplog.text
        assert "[SELFHEAL] backup created:" in caplog.text
        assert "[SELFHEAL] original DB preserved" in caplog.text
        fake_db.session.remove.assert_not_called()
        fake_db.engine.dispose.assert_not_called()
        subprocess_run.assert_not_called()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_self_heal_destructive_repair_only_runs_when_explicitly_allowed(monkeypatch, caplog):
    tmp_path = _repo_local_tmp_dir("selfheal_allowed_")
    try:
        db_path = tmp_path / "hc_local_dev.db"
        db_path.write_bytes(b"demo-data")

        app = _make_selfheal_app(db_path)
        fake_db = _make_fake_db()

        class _Inspector:
            @staticmethod
            def get_table_names():
                return ["admin_users"]

        def _fake_run(*args, **kwargs):
            db_path.write_bytes(b"recreated")
            return SimpleNamespace(returncode=0, stderr="")

        subprocess_run = Mock(side_effect=_fake_run)

        monkeypatch.setattr("sqlalchemy.inspect", lambda engine: _Inspector())
        monkeypatch.setattr("subprocess.run", subprocess_run)
        monkeypatch.setattr(Path, "unlink", lambda self: None)
        monkeypatch.setenv("HC_SELFHEAL_ALLOW_DESTRUCTIVE", "true")
        monkeypatch.delenv("HC_SKIP_SELFHEAL", raising=False)
        monkeypatch.delenv("DISABLE_SELF_HEAL", raising=False)
        monkeypatch.delenv("HELPCHAIN_TESTING", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        caplog.set_level(logging.WARNING)

        maybe_self_heal_local_sqlite(app, fake_db)

        backups = list(db_path.parent.glob("hc_local_dev.db.selfheal_backup_*"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == b"demo-data"
        assert db_path.exists()
        assert db_path.read_bytes() == b"recreated"
        fake_db.session.remove.assert_called_once()
        fake_db.engine.dispose.assert_called_once()
        subprocess_run.assert_called_once()
        assert "[SELFHEAL] destructive repair blocked: set HC_SELFHEAL_ALLOW_DESTRUCTIVE=true to allow" not in caplog.text
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

