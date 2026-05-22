from __future__ import annotations

from types import SimpleNamespace

from backend.helpchain_backend.src.admin_actor import AdminActor
from backend.helpchain_backend.src.admin_policies import (
    can_access_professional_leads,
    can_export_operational_data,
    can_mutate_request,
    can_view_global_analytics,
    scope_case_query,
    scope_notification_query,
    scope_request_query,
)
from backend.helpchain_backend.src.models import Case
from backend.models import NotificationJob, Request, Structure, User


def _actor(
    *,
    role: str | None,
    structure_id: int | None,
    is_authenticated: bool = True,
    is_platform_global: bool = False,
) -> AdminActor:
    return AdminActor(
        admin_id=1 if is_authenticated else None,
        role=role,
        structure_id=structure_id,
        is_authenticated=is_authenticated,
        is_platform_global=is_platform_global,
        auth_source="test",
        raw_admin=None,
    )


def test_professional_lead_and_global_analytics_helpers_match_founder_access():
    structure_admin = _actor(role="admin", structure_id=12)
    founder_global = _actor(role="superadmin", structure_id=None, is_platform_global=True)
    founder_scoped = _actor(role="superadmin", structure_id=12, is_platform_global=False)

    assert can_access_professional_leads(structure_admin) is False
    assert can_view_global_analytics(structure_admin) is False

    assert can_access_professional_leads(founder_global) is True
    assert can_view_global_analytics(founder_global) is True

    assert can_access_professional_leads(founder_scoped) is True
    assert can_view_global_analytics(founder_scoped) is True


def test_operational_export_helper_respects_admin_scope():
    anonymous = _actor(role=None, structure_id=None, is_authenticated=False)
    structure_admin = _actor(role="admin", structure_id=12)
    founder_global = _actor(role="superadmin", structure_id=None, is_platform_global=True)

    assert can_export_operational_data(anonymous) is False
    assert can_export_operational_data(structure_admin) is True
    assert can_export_operational_data(structure_admin, structure_id=12) is True
    assert can_export_operational_data(structure_admin, structure_id=99) is False
    assert can_export_operational_data(founder_global) is True
    assert can_export_operational_data(founder_global, structure_id=99) is True


def test_request_mutation_helper_respects_request_tenant_scope():
    structure_admin = _actor(role="admin", structure_id=12)
    founder_global = _actor(role="superadmin", structure_id=None, is_platform_global=True)

    visible_request = SimpleNamespace(id=101, structure_id=12)
    hidden_request = SimpleNamespace(id=202, structure_id=99)
    unscoped_request = SimpleNamespace(id=303, structure_id=None)

    assert can_mutate_request(structure_admin, visible_request) is True
    assert can_mutate_request(structure_admin, hidden_request) is False
    assert can_mutate_request(structure_admin, unscoped_request) is False
    assert can_mutate_request(founder_global, visible_request) is True
    assert can_mutate_request(founder_global, hidden_request) is True


def test_query_scope_helpers_enforce_actor_scope_consistently(session):
    structure_a = Structure(name="Policy Scope A", slug="policy-scope-a")
    structure_b = Structure(name="Policy Scope B", slug="policy-scope-b")
    session.add_all([structure_a, structure_b])
    session.flush()

    user_a = User(
        username="policy_scope_user_a",
        email="policy_scope_user_a@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=structure_a.id,
    )
    user_b = User(
        username="policy_scope_user_b",
        email="policy_scope_user_b@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=structure_b.id,
    )
    session.add_all([user_a, user_b])
    session.flush()

    request_a = Request(
        title="policy-scope-request-a",
        description="A",
        category="general",
        user_id=user_a.id,
        structure_id=structure_a.id,
        status="pending",
    )
    request_b = Request(
        title="policy-scope-request-b",
        description="B",
        category="general",
        user_id=user_b.id,
        structure_id=structure_b.id,
        status="pending",
    )
    session.add_all([request_a, request_b])
    session.flush()

    case_a = Case(request_id=request_a.id, structure_id=structure_a.id, status="new")
    case_b = Case(request_id=request_b.id, structure_id=structure_b.id, status="new")
    job_a = NotificationJob(
        channel="email",
        event_type="policy_scope_a",
        recipient="policy-a@test.local",
        status="failed",
        structure_id=structure_a.id,
    )
    job_b = NotificationJob(
        channel="email",
        event_type="policy_scope_b",
        recipient="policy-b@test.local",
        status="failed",
        structure_id=structure_b.id,
    )
    session.add_all([case_a, case_b, job_a, job_b])
    session.commit()

    scoped_actor = _actor(role="admin", structure_id=structure_a.id)
    global_actor = _actor(role="superadmin", structure_id=None, is_platform_global=True)

    scoped_request_ids = {
        row.id
        for row in scope_request_query(
            scoped_actor,
            Request.query.filter(Request.structure_id == structure_b.id),
        ).all()
    }
    scoped_notification_ids = {
        row.id
        for row in scope_notification_query(scoped_actor, NotificationJob.query).all()
    }
    scoped_case_ids = {
        row.id for row in scope_case_query(scoped_actor, Case.query).all()
    }
    global_request_ids = {
        row.id for row in scope_request_query(global_actor, Request.query).all()
    }
    global_notification_ids = {
        row.id for row in scope_notification_query(global_actor, NotificationJob.query).all()
    }
    global_case_ids = {
        row.id for row in scope_case_query(global_actor, Case.query).all()
    }

    assert scoped_request_ids == set()
    assert scoped_notification_ids == {job_a.id}
    assert scoped_case_ids == {case_a.id}
    assert global_request_ids == {request_a.id, request_b.id}
    assert global_notification_ids == {job_a.id, job_b.id}
    assert global_case_ids == {case_a.id, case_b.id}
