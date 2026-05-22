from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.extensions import db
from backend.helpchain_backend.src.models import Case, CaseCollaborator, CaseParticipant
from backend.models import AdminUser, Request, Structure, User, utc_now

pytestmark = pytest.mark.spine


def _login_admin(client, app, admin_user: AdminUser) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["admin_id"] = admin_user.id
        sess["admin_user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["mfa_required"] = True
        sess[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        sess["admin_mfa_last_verified"] = 4102444800
        sess["admin_mfa_user_id"] = admin_user.id


def _make_structure(session, *, name: str, slug: str) -> Structure:
    row = Structure(name=name, slug=slug)
    session.add(row)
    session.flush()
    return row


def _make_admin(
    session,
    *,
    username: str,
    email: str,
    role: str,
    structure_id: int | None,
) -> AdminUser:
    row = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        structure_id=structure_id,
        is_active=True,
        mfa_enabled=True,
        totp_secret="case-tenant-consistency-test",
    )
    session.add(row)
    session.flush()
    return row


def _make_user(session, *, username: str, email: str, structure_id: int) -> User:
    row = User(
        username=username,
        email=email,
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=structure_id,
    )
    session.add(row)
    session.flush()
    return row


def _make_request(
    session,
    *,
    title: str,
    user_id: int,
    structure_id: int,
    city: str = "Paris",
) -> Request:
    now = datetime.now(UTC).replace(tzinfo=None)
    row = Request(
        title=title,
        description=f"Description for {title}",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status="open",
        city=city,
        latitude=48.8566,
        longitude=2.3522,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _make_case(
    session,
    *,
    request_id: int,
    structure_id: int,
    title_hint: str,
    latitude: float = 48.8566,
    longitude: float = 2.3522,
) -> Case:
    now = datetime.now(UTC)
    row = Case(
        request_id=request_id,
        structure_id=structure_id,
        status="new",
        priority="high",
        risk_score=75,
        latitude=latitude,
        longitude=longitude,
        opened_at=now,
        last_activity_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _seed_case_scope(session):
    structure_a = _make_structure(
        session,
        name="Case Tenant Alpha",
        slug="case-tenant-alpha",
    )
    structure_b = _make_structure(
        session,
        name="Case Tenant Beta",
        slug="case-tenant-beta",
    )
    structure_c = _make_structure(
        session,
        name="Case Tenant Gamma",
        slug="case-tenant-gamma",
    )
    admin_a = _make_admin(
        session,
        username="case_tenant_admin_a",
        email="case-tenant-admin-a@test.local",
        role="admin",
        structure_id=structure_a.id,
    )
    admin_b = _make_admin(
        session,
        username="case_tenant_admin_b",
        email="case-tenant-admin-b@test.local",
        role="admin",
        structure_id=structure_b.id,
    )
    ops_a = _make_admin(
        session,
        username="case_tenant_ops_a",
        email="case-tenant-ops-a@test.local",
        role="ops",
        structure_id=structure_a.id,
    )
    user_a = _make_user(
        session,
        username="case_tenant_user_a",
        email="case-tenant-user-a@test.local",
        structure_id=structure_a.id,
    )
    user_b = _make_user(
        session,
        username="case_tenant_user_b",
        email="case-tenant-user-b@test.local",
        structure_id=structure_b.id,
    )
    request_a = _make_request(
        session,
        title="tenant-a-case-visible",
        user_id=user_a.id,
        structure_id=structure_a.id,
        city="Boulogne",
    )
    request_b = _make_request(
        session,
        title="tenant-b-case-hidden",
        user_id=user_b.id,
        structure_id=structure_b.id,
        city="Nanterre",
    )
    request_b_mismatch = _make_request(
        session,
        title="tenant-b-case-mismatch",
        user_id=user_b.id,
        structure_id=structure_b.id,
        city="Courbevoie",
    )
    visible_case = _make_case(
        session,
        request_id=request_a.id,
        structure_id=structure_a.id,
        title_hint="visible",
    )
    hidden_case = _make_case(
        session,
        request_id=request_b.id,
        structure_id=structure_b.id,
        title_hint="hidden",
    )
    inconsistent_case = _make_case(
        session,
        request_id=request_b_mismatch.id,
        structure_id=structure_a.id,
        title_hint="inconsistent",
        latitude=48.9,
        longitude=2.4,
    )
    session.commit()
    return {
        "structure_a": structure_a,
        "structure_b": structure_b,
        "structure_c": structure_c,
        "admin_a": admin_a,
        "admin_b": admin_b,
        "ops_a": ops_a,
        "user_a": user_a,
        "user_b": user_b,
        "request_a": request_a,
        "request_b": request_b,
        "request_b_mismatch": request_b_mismatch,
        "visible_case": visible_case,
        "hidden_case": hidden_case,
        "inconsistent_case": inconsistent_case,
    }


def test_open_case_from_request_keeps_case_and_request_structure_in_sync(app, session):
    structure = _make_structure(session, name="Open Case Scope", slug="open-case-scope")
    admin = _make_admin(
        session,
        username="open_case_ops",
        email="open-case-ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    user = _make_user(
        session,
        username="open_case_user",
        email="open-case-user@test.local",
        structure_id=structure.id,
    )
    req = _make_request(
        session,
        title="open-case-request",
        user_id=user.id,
        structure_id=structure.id,
    )
    session.commit()

    client = app.test_client()
    _login_admin(client, app, admin)

    response = client.post(f"/admin/requests/{req.id}/open-case", follow_redirects=False)
    case_row = Case.query.filter_by(request_id=req.id).one()

    assert response.status_code == 303
    assert case_row.structure_id == req.structure_id


def test_inconsistent_case_is_hidden_from_case_views_and_kpis(app, session):
    seeded = _seed_case_scope(session)
    client = app.test_client()
    _login_admin(client, app, seeded["admin_a"])

    listing = client.get("/admin/cases")
    assert listing.status_code == 200
    html = listing.get_data(as_text=True)
    assert "tenant-a-case-visible" in html
    assert "tenant-b-case-hidden" not in html

    inconsistent_detail = client.get(
        f"/admin/cases/{seeded['inconsistent_case'].id}",
        follow_redirects=False,
    )
    assert inconsistent_detail.status_code == 404

    territorial = client.get("/admin/api/territorial-kpis")
    assert territorial.status_code == 200
    payload = territorial.get_json()
    assert payload["active_cases"] == 1
    assert payload["new_cases_week"] == 1


def test_cross_tenant_case_participant_user_attachment_is_rejected(app, session):
    seeded = _seed_case_scope(session)
    client = app.test_client()
    _login_admin(client, app, seeded["admin_a"])

    response = client.post(
        f"/admin/cases/{seeded['visible_case'].id}/participants",
        data={
            "participant_type": "admin_user",
            "role": "contributor",
            "status": "active",
            "user_id": str(seeded["user_b"].id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    participant = (
        CaseParticipant.query.filter(CaseParticipant.case_id == seeded["visible_case"].id)
        .filter(CaseParticipant.user_id == seeded["user_b"].id)
        .first()
    )
    assert participant is None


def test_case_map_and_risk_api_are_tenant_scoped(app, session):
    seeded = _seed_case_scope(session)
    client = app.test_client()
    _login_admin(client, app, seeded["admin_a"])

    map_response = client.get("/admin/api/cases/map")
    assert map_response.status_code == 200
    case_ids = {int(row["id"]) for row in map_response.get_json()["cases"]}
    assert seeded["visible_case"].id in case_ids
    assert seeded["hidden_case"].id not in case_ids
    assert seeded["inconsistent_case"].id not in case_ids

    own_risk = client.get(f"/admin/api/cases/{seeded['visible_case'].id}/risk")
    assert own_risk.status_code == 200

    hidden_risk = client.get(f"/admin/api/cases/{seeded['hidden_case'].id}/risk")
    assert hidden_risk.status_code == 404

    inconsistent_risk = client.get(
        f"/admin/api/cases/{seeded['inconsistent_case'].id}/risk"
    )
    assert inconsistent_risk.status_code == 404


def test_case_collaborator_invite_is_controlled_by_case_owner_scope(app, session):
    seeded = _seed_case_scope(session)

    owner_client = app.test_client()
    _login_admin(owner_client, app, seeded["ops_a"])

    invited = owner_client.post(
        f"/api/cases/{seeded['visible_case'].id}/invite-structure",
        json={"structure_id": seeded["structure_b"].id, "role": "viewer"},
    )
    assert invited.status_code == 200
    collaborator = (
        CaseCollaborator.query.filter(CaseCollaborator.case_id == seeded["visible_case"].id)
        .filter(CaseCollaborator.structure_id == seeded["structure_b"].id)
        .first()
    )
    assert collaborator is not None

    foreign_client = app.test_client()
    _login_admin(foreign_client, app, seeded["admin_b"])
    denied = foreign_client.post(
        f"/api/cases/{seeded['visible_case'].id}/invite-structure",
        json={"structure_id": seeded["structure_c"].id, "role": "viewer"},
    )
    assert denied.status_code == 403
