from __future__ import annotations

import importlib
from datetime import timedelta
from uuid import uuid4

import pytest

from backend.models import AdminUser, Request, RequestActivity, Structure, User, db, utc_now
from backend.helpchain_backend.src.services import request_sla


@pytest.fixture
def sla_engine_app(monkeypatch):
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
def sla_engine_session(sla_engine_app):
    with sla_engine_app.app_context():
        yield db.session


@pytest.fixture
def enqueue_calls(monkeypatch):
    calls: list[dict] = []

    def _fake_enqueue_email_notification(**kwargs):
        calls.append(kwargs)
        return object(), False

    monkeypatch.setattr(
        request_sla,
        "enqueue_email_notification",
        _fake_enqueue_email_notification,
    )
    return calls


def _make_structure(session, *, name: str = "SLA Structure", slug: str | None = None) -> Structure:
    structure = Structure(
        name=name,
        slug=slug or f"sla-{uuid4().hex[:8]}",
        status="active",
    )
    session.add(structure)
    session.commit()
    return structure


def _make_user(session, *, structure_id: int | None = None) -> User:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"user_{suffix}",
        email=f"user_{suffix}@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=structure_id,
    )
    session.add(user)
    session.commit()
    return user


def _make_admin(
    session,
    *,
    role: str,
    structure_id: int | None,
    email: str | None = None,
) -> AdminUser:
    suffix = uuid4().hex[:8]
    admin = AdminUser(
        username=f"admin_{suffix}",
        email=email or f"admin_{suffix}@test.local",
        role=role,
        is_active=True,
        structure_id=structure_id,
    )
    admin.set_password("TestPassword1")
    session.add(admin)
    session.commit()
    return admin


def _make_request(
    session,
    *,
    structure_id: int | None,
    user_id: int,
    owner_id: int | None = None,
    created_at=None,
    updated_at=None,
    status: str | None = "open",
    is_archived: bool = False,
    deleted_at=None,
) -> Request:
    req = Request(
        title=f"Request {uuid4().hex[:8]}",
        description="SLA test request",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status=status,
        owner_id=owner_id,
        owned_at=created_at if owner_id else None,
        created_at=created_at or utc_now(),
        updated_at=updated_at,
        is_archived=is_archived,
        deleted_at=deleted_at,
    )
    session.add(req)
    session.commit()
    return req


def _add_activity(
    session,
    *,
    request_id: int,
    action: str,
    created_at,
    actor_admin_id: int | None = None,
) -> RequestActivity:
    activity = RequestActivity(
        request_id=request_id,
        actor_admin_id=actor_admin_id,
        action=action,
        created_at=created_at,
    )
    session.add(activity)
    session.commit()
    return activity


def _marker_count(session, *, request_id: int, action: str) -> int:
    return (
        session.query(RequestActivity)
        .filter_by(request_id=request_id, action=action)
        .count()
    )


def test_unowned_request_over_threshold_enqueues_owner_reminder_once(
    sla_engine_session, enqueue_calls
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    _make_admin(
        sla_engine_session,
        role="admin",
        structure_id=structure.id,
        email="owner-reminder-admin@test.local",
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)
    req = _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        created_at=now - timedelta(hours=60),
        updated_at=now - timedelta(hours=60),
    )

    stats = request_sla.process_request_sla(now=now)

    assert stats["owner_reminders_enqueued"] == 1
    assert len(enqueue_calls) == 1
    assert _marker_count(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_OWNER_REMINDER_SENT,
    ) == 1


def test_rerun_inside_cooldown_suppresses_duplicate_owner_reminder(
    sla_engine_session, enqueue_calls
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    _make_admin(
        sla_engine_session,
        role="admin",
        structure_id=structure.id,
        email="cooldown-admin@test.local",
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)
    req = _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        created_at=now - timedelta(hours=60),
        updated_at=now - timedelta(hours=60),
    )

    first = request_sla.process_request_sla(now=now)
    second = request_sla.process_request_sla(now=now + timedelta(hours=1))

    assert first["owner_reminders_enqueued"] == 1
    assert second["suppressed"] >= 1
    assert len(enqueue_calls) == 1
    assert _marker_count(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_OWNER_REMINDER_SENT,
    ) == 1


def test_owned_inactive_request_enqueues_inactivity_reminder(
    sla_engine_session, enqueue_calls
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    owner = _make_admin(
        sla_engine_session,
        role="ops",
        structure_id=structure.id,
        email="owner-inactive@test.local",
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)
    req = _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=owner.id,
        created_at=now - timedelta(hours=48),
        updated_at=now - timedelta(hours=48),
    )

    stats = request_sla.process_request_sla(now=now)

    assert stats["inactive_due"] == 1
    assert stats["inactivity_reminders_enqueued"] == 1
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["recipient"] == "owner-inactive@test.local"
    assert _marker_count(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_INACTIVITY_REMINDER_SENT,
    ) == 1


def test_escalation_requires_prior_reminder_and_threshold(
    sla_engine_session, enqueue_calls
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    _make_admin(
        sla_engine_session,
        role="admin",
        structure_id=structure.id,
        email="structure-admin@test.local",
    )
    _make_admin(
        sla_engine_session,
        role="superadmin",
        structure_id=None,
        email="global-superadmin@test.local",
    )
    owner = _make_admin(
        sla_engine_session,
        role="ops",
        structure_id=structure.id,
        email="escalation-owner@test.local",
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)
    req = _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=owner.id,
        created_at=now - timedelta(hours=96),
        updated_at=now - timedelta(hours=96),
    )
    _add_activity(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_INACTIVITY_REMINDER_SENT,
        created_at=now - timedelta(hours=30),
    )

    stats = request_sla.process_request_sla(now=now)

    assert stats["escalation_due"] == 1
    assert stats["inactivity_escalations_enqueued"] == 1
    assert _marker_count(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_INACTIVITY_ESCALATION_SENT,
    ) == 1


def test_closed_completed_archived_and_deleted_requests_are_ignored(
    sla_engine_session, enqueue_calls
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    _make_admin(
        sla_engine_session,
        role="admin",
        structure_id=structure.id,
        email="ignored-admin@test.local",
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)

    _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        status="done",
        created_at=now - timedelta(hours=96),
        updated_at=now - timedelta(hours=96),
    )
    _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        status="closed",
        created_at=now - timedelta(hours=96),
        updated_at=now - timedelta(hours=96),
    )
    _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        status="open",
        is_archived=True,
        created_at=now - timedelta(hours=96),
        updated_at=now - timedelta(hours=96),
    )
    _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        status="open",
        deleted_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=96),
        updated_at=now - timedelta(hours=96),
    )

    stats = request_sla.process_request_sla(now=now)

    assert stats["owner_reminders_enqueued"] == 0
    assert stats["inactivity_reminders_enqueued"] == 0
    assert stats["inactivity_escalations_enqueued"] == 0
    assert enqueue_calls == []


def test_sla_read_normalization_treats_active_as_open_and_resolved_as_terminal(
    sla_engine_session, enqueue_calls
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    _make_admin(
        sla_engine_session,
        role="admin",
        structure_id=structure.id,
        email="compat-admin@test.local",
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)

    active_req = _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        status="active",
        created_at=now - timedelta(hours=60),
        updated_at=now - timedelta(hours=60),
    )
    _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=None,
        status="resolved",
        created_at=now - timedelta(hours=60),
        updated_at=now - timedelta(hours=60),
    )

    stats = request_sla.process_request_sla(now=now)

    assert stats["owner_reminders_enqueued"] == 1
    assert len(enqueue_calls) == 1
    assert _marker_count(
        sla_engine_session,
        request_id=active_req.id,
        action=request_sla.SLA_OWNER_REMINDER_SENT,
    ) == 1


def test_recent_request_activity_prevents_stale_detection_even_when_created_is_old(
    sla_engine_session,
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    owner = _make_admin(
        sla_engine_session,
        role="ops",
        structure_id=structure.id,
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)
    req = _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=owner.id,
        created_at=now - timedelta(hours=96),
        updated_at=now - timedelta(hours=96),
    )
    recent_activity = now - timedelta(hours=2)
    _add_activity(
        sla_engine_session,
        request_id=req.id,
        action="note",
        created_at=recent_activity,
        actor_admin_id=owner.id,
    )

    last_activity = request_sla.get_request_last_meaningful_activity(req)
    due_ids = {
        row.id
        for row in request_sla.find_inactive_owned_requests_due(now=now)
    }

    assert last_activity.replace(tzinfo=None) == recent_activity.replace(tzinfo=None)
    assert req.id not in due_ids


def test_same_run_does_not_send_both_inactivity_reminder_and_escalation_for_same_request(
    sla_engine_session, enqueue_calls
):
    now = utc_now()
    structure = _make_structure(sla_engine_session)
    _make_admin(
        sla_engine_session,
        role="admin",
        structure_id=structure.id,
        email="same-run-admin@test.local",
    )
    _make_admin(
        sla_engine_session,
        role="superadmin",
        structure_id=None,
        email="same-run-superadmin@test.local",
    )
    owner = _make_admin(
        sla_engine_session,
        role="ops",
        structure_id=structure.id,
        email="same-run-owner@test.local",
    )
    requester = _make_user(sla_engine_session, structure_id=structure.id)
    req = _make_request(
        sla_engine_session,
        structure_id=structure.id,
        user_id=requester.id,
        owner_id=owner.id,
        created_at=now - timedelta(hours=96),
        updated_at=now - timedelta(hours=96),
    )
    _add_activity(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_INACTIVITY_REMINDER_SENT,
        created_at=now - timedelta(hours=30),
    )

    stats = request_sla.process_request_sla(now=now)

    assert stats["inactive_due"] == 1
    assert stats["escalation_due"] == 1
    assert stats["inactivity_reminders_enqueued"] == 0
    assert stats["inactivity_escalations_enqueued"] == 1
    assert len(enqueue_calls) >= 1
    assert {
        call["purpose"] for call in enqueue_calls
    } == {"request_sla_inactivity_escalation"}
    assert _marker_count(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_INACTIVITY_REMINDER_SENT,
    ) == 1
    assert _marker_count(
        sla_engine_session,
        request_id=req.id,
        action=request_sla.SLA_INACTIVITY_ESCALATION_SENT,
    ) == 1
