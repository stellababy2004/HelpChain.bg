from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.helpchain_backend.src.models import Case, ProfessionalLead
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
        totp_secret="professional-lead-access-test",
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
) -> Request:
    row = Request(
        title=title,
        description=f"Description for {title}",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status="open",
        priority="normal",
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(row)
    session.flush()
    return row


def _seed_professional_lead_access_data(session):
    structure_a = _make_structure(
        session,
        name="Lead Scope Alpha",
        slug="lead-scope-alpha",
    )
    structure_b = _make_structure(
        session,
        name="Lead Scope Beta",
        slug="lead-scope-beta",
    )
    structure_admin = _make_admin(
        session,
        username="lead_scope_structure_admin",
        email="lead-scope-structure-admin@test.local",
        role="admin",
        structure_id=structure_a.id,
    )
    superadmin = _make_admin(
        session,
        username="lead_scope_superadmin",
        email="lead-scope-superadmin@test.local",
        role="superadmin",
        structure_id=None,
    )
    user_a = _make_user(
        session,
        username="lead_scope_requester_a",
        email="lead-scope-requester-a@test.local",
        structure_id=structure_a.id,
    )
    request_a = _make_request(
        session,
        title="Lead-linked case request",
        user_id=user_a.id,
        structure_id=structure_a.id,
    )
    lead = ProfessionalLead(
        email="global-lead@example.org",
        full_name="Global Lead",
        profession="Coordinatrice",
        city="Paris",
        source="demo_page",
        status="new",
    )
    session.add(lead)
    session.flush()
    case_row = Case(
        request_id=request_a.id,
        structure_id=structure_a.id,
        assigned_professional_lead_id=lead.id,
        status="assigned",
        priority="high",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(case_row)
    session.commit()
    return structure_admin, superadmin, lead, request_a, case_row


def test_structure_admin_cannot_access_professional_lead_surfaces_and_nav_is_hidden(app, session):
    structure_admin, _superadmin, lead, _request_a, _case_row = _seed_professional_lead_access_data(
        session
    )
    client = app.test_client()
    _login_admin(client, app, structure_admin)

    requests_page = client.get("/admin/requests")
    assert requests_page.status_code == 200
    assert 'href="/admin/professional-leads"' not in requests_page.get_data(as_text=True)

    listing = client.get("/admin/professional-leads", follow_redirects=False)
    assert listing.status_code == 403

    demo_listing = client.get("/admin/professional-leads/demo", follow_redirects=False)
    assert demo_listing.status_code == 403

    detail = client.get(f"/admin/professional-leads/{lead.id}", follow_redirects=False)
    assert detail.status_code == 403


def test_structure_admin_cannot_edit_unrelated_professional_lead_detail(app, session):
    structure_admin, _superadmin, lead, _request_a, _case_row = _seed_professional_lead_access_data(
        session
    )
    client = app.test_client()
    _login_admin(client, app, structure_admin)

    response = client.post(
        f"/admin/professional-leads/{lead.id}",
        data={"status": "contacted", "notes": "Should not be allowed"},
        follow_redirects=False,
    )
    assert response.status_code == 403

    session.refresh(lead)
    assert lead.status == "new"
    assert lead.contacted_at is None
    assert (lead.notes or "") == ""


def test_superadmin_keeps_global_professional_lead_access_and_nav(app, session):
    _structure_admin, superadmin, lead, _request_a, _case_row = _seed_professional_lead_access_data(
        session
    )
    client = app.test_client()
    _login_admin(client, app, superadmin)

    requests_page = client.get("/admin/requests")
    assert requests_page.status_code == 200
    assert 'href="/admin/professional-leads"' in requests_page.get_data(as_text=True)

    listing = client.get("/admin/professional-leads")
    assert listing.status_code == 200
    assert "global-lead@example.org" in listing.get_data(as_text=True)

    detail = client.get(f"/admin/professional-leads/{lead.id}")
    assert detail.status_code == 200
    assert "global-lead@example.org" in detail.get_data(as_text=True)

    update = client.post(
        f"/admin/professional-leads/{lead.id}",
        data={"status": "contacted", "notes": "Founder review"},
        follow_redirects=False,
    )
    assert update.status_code in (302, 303)

    session.refresh(lead)
    assert lead.status == "contacted"
    assert lead.contacted_at is not None
    assert "Founder review" in (lead.notes or "")


def test_structure_admin_case_detail_still_renders_linked_professional_lead_reference(app, session):
    structure_admin, _superadmin, _lead, _request_a, case_row = _seed_professional_lead_access_data(
        session
    )
    client = app.test_client()
    _login_admin(client, app, structure_admin)

    response = client.get(f"/admin/cases/{case_row.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Global Lead" in html
    assert "Lead-linked case request" in html
