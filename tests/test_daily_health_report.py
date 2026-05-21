from __future__ import annotations

import importlib
from uuid import uuid4

import pytest

from backend.models import AdminUser, db
from backend.helpchain_backend.src.services import daily_health_report

pytestmark = pytest.mark.shared_platform


@pytest.fixture
def health_report_app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("HC_DB_PATH", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)

    import backend.helpchain_backend.src.config as config_module
    import backend.helpchain_backend.src.app as app_module

    importlib.reload(config_module)
    app_module = importlib.reload(app_module)

    app = app_module.create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "PUBLIC_BASE_URL": "https://helpchain.test",
        }
    )

    with app.app_context():
        import backend.models  # noqa: F401
        import backend.models_with_analytics  # noqa: F401

        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def health_report_session(health_report_app):
    with health_report_app.app_context():
        yield db.session


def _make_admin(
    session,
    *,
    role: str,
    structure_id: int | None,
    is_active: bool = True,
    email: str | None = None,
):
    suffix = uuid4().hex[:8]
    admin = AdminUser(
        username=f"report_admin_{suffix}",
        email=email if email is not None else f"report_admin_{suffix}@test.local",
        role=role,
        is_active=is_active,
        structure_id=structure_id,
    )
    admin.set_password("TestPassword1")
    session.add(admin)
    session.commit()
    return admin


def test_get_daily_health_report_recipients_returns_only_active_global_superadmins(
    health_report_session,
):
    included = _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=None,
        email="global-superadmin@test.local",
    )
    _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=1,
        email="structure-superadmin@test.local",
    )
    _make_admin(
        health_report_session,
        role="admin",
        structure_id=None,
        email="admin@test.local",
    )
    _make_admin(
        health_report_session,
        role="ops",
        structure_id=None,
        email="ops@test.local",
    )
    _make_admin(
        health_report_session,
        role="readonly",
        structure_id=None,
        email="readonly@test.local",
    )
    _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=None,
        is_active=False,
        email="inactive-superadmin@test.local",
    )
    _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=None,
        email="",
    )

    recipients = daily_health_report.get_daily_health_report_recipients()

    assert recipients == [included.email]


def test_enqueue_daily_health_report_with_no_recipients_returns_clean_zero_result(
    monkeypatch,
    health_report_session,
):
    monkeypatch.setattr(
        daily_health_report,
        "enqueue_email_notification",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue")),
    )

    result = daily_health_report.enqueue_daily_health_report()

    assert result["recipient_count"] == 0
    assert result["enqueued"] == 0
    assert result["errors"] == 0


def test_render_daily_health_report_text_renders_stable_sections():
    report = {
        "generated_at": "2026-04-08T10:00:00",
        "queue": {
            "pending": 4,
            "processing": 1,
            "done": 9,
            "dead_letter": 2,
            "dead_letter_15m": 1,
            "oldest_pending_min": 6,
        },
        "sla": {
            "unowned_due": 3,
            "inactive_due": 5,
            "escalation_due": 2,
            "owner_reminders_24h": 7,
            "inactivity_reminders_24h": 4,
            "inactivity_escalations_24h": 1,
        },
        "security": {
            "admin_logins_success_24h": 11,
            "admin_logins_failed_24h": 3,
            "admin_logins_failed_1h": 1,
            "distinct_failed_ips_24h": 2,
            "distinct_failed_usernames_24h": 2,
            "lockout_buckets_24h": 1,
            "denied_actions_24h": 4,
            "denied_actions_1h": 0,
            "risky_actions_24h": 6,
        },
        "system": {
            "db_latency_ms": 42,
        },
    }

    text = daily_health_report.render_daily_health_report_text(report)

    assert "Queue" in text
    assert "SLA" in text
    assert "Security" in text
    assert "System" in text
    assert "- pending: 4" in text
    assert "- escalation_due: 2" in text
    assert "- risky_actions_24h: 6" in text
    assert "- db_latency_ms: 42" in text


def test_enqueue_daily_health_report_success_path(monkeypatch, health_report_session):
    _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=None,
        email="superadmin1@test.local",
    )
    _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=None,
        email="superadmin2@test.local",
    )
    enqueue_calls: list[dict] = []

    def _fake_enqueue_email_notification(**kwargs):
        enqueue_calls.append(kwargs)
        return object(), False

    monkeypatch.setattr(
        daily_health_report,
        "enqueue_email_notification",
        _fake_enqueue_email_notification,
    )

    result = daily_health_report.enqueue_daily_health_report()

    assert result["recipient_count"] == 2
    assert result["enqueued"] == 2
    assert result["errors"] == 0
    assert len(enqueue_calls) == 2
    assert result["subject"].startswith("[HelpChain] Daily Health Report - ")


def test_enqueue_daily_health_report_partial_error_path(monkeypatch, health_report_session):
    _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=None,
        email="partial1@test.local",
    )
    _make_admin(
        health_report_session,
        role="superadmin",
        structure_id=None,
        email="partial2@test.local",
    )
    calls = {"count": 0}

    def _fake_enqueue_email_notification(**kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("boom")
        return object(), False

    monkeypatch.setattr(
        daily_health_report,
        "enqueue_email_notification",
        _fake_enqueue_email_notification,
    )

    result = daily_health_report.enqueue_daily_health_report()

    assert result["recipient_count"] == 2
    assert result["enqueued"] == 1
    assert result["errors"] == 1


def test_collect_daily_health_report_returns_expected_top_level_shape(
    monkeypatch,
    health_report_session,
):
    monkeypatch.setattr(daily_health_report, "_collect_db_latency_ms", lambda: 7)

    report = daily_health_report.collect_daily_health_report()

    assert "generated_at" in report
    assert "queue" in report
    assert "sla" in report
    assert "security" in report
    assert "system" in report
    assert isinstance(report["queue"], dict)
    assert isinstance(report["sla"], dict)
    assert isinstance(report["security"], dict)
    assert isinstance(report["system"], dict)
