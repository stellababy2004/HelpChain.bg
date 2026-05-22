from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.models import AdminUser, Request, Structure, utc_now

pytestmark = pytest.mark.spine


def _login_admin(client, app, admin_user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["admin_id"] = admin_user.id
        sess[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        sess["admin_mfa_last_verified"] = 4102444800
        sess["admin_mfa_user_id"] = admin_user.id


def _make_admin(session, *, username, email, structure_id=None):
    admin = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role="superadmin",
        is_active=True,
        structure_id=structure_id,
        mfa_enabled=True,
        totp_secret="test-mfa-secret",
    )
    session.add(admin)
    session.commit()
    return admin


def _make_structure(session, *, name, slug):
    structure = Structure(name=name, slug=slug)
    session.add(structure)
    session.commit()
    return structure


def test_admin_request_new_get_smoke(authenticated_admin_client):
    resp = authenticated_admin_client.get("/admin/requests/new")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Nouvelle demande" in html
    assert "Créer la demande" in html


def test_admin_request_new_post_creates_request(authenticated_admin_client, session):
    title = f"Demande interne smoke {int(datetime.now(UTC).timestamp())}"
    default_structure = session.query(Structure).filter_by(slug="default").first()
    assert default_structure is not None
    payload = {
        "title": title,
        "description": "Situation créée par un opérateur pour test smoke.",
        "person_name": "Personne Test",
        "email": "",
        "phone": "",
        "city": "Paris",
        "category": "general",
        "priority": "attention",
        "structure_id": str(default_structure.id),
        "owner_id": "",
        "internal_notes": "",
    }

    resp = authenticated_admin_client.post("/admin/requests/new", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location") or ""
    assert "/admin/requests/" in location

    created = session.query(Request).filter_by(title=title).order_by(Request.id.desc()).first()
    assert created is not None
    assert created.name == "Personne Test"
    assert created.city == "Paris"
    assert created.structure_id == default_structure.id


def test_admin_request_new_global_admin_requires_explicit_structure(app, client, session):
    admin = _make_admin(
        session,
        username="request_global_admin",
        email="request-global-admin@test.local",
    )
    _login_admin(client, app, admin)

    title = f"Demande sans structure {int(datetime.now(UTC).timestamp())}"
    payload = {
        "title": title,
        "description": "Situation créée sans structure explicite.",
        "person_name": "Personne Sans Structure",
        "email": "",
        "phone": "",
        "city": "Paris",
        "category": "general",
        "priority": "attention",
        "structure_id": "",
        "owner_id": "",
        "internal_notes": "",
    }

    resp = client.post("/admin/requests/new", data=payload, follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Veuillez" in html
    assert "structure" in html.lower()

    created = session.query(Request).filter_by(title=title).order_by(Request.id.desc()).first()
    assert created is None


def test_admin_request_new_global_admin_creates_request_with_selected_structure(
    app, client, session
):
    structure = _make_structure(
        session,
        name="Structure Choisie",
        slug=f"structure-choisie-{int(datetime.now(UTC).timestamp())}",
    )
    admin = _make_admin(
        session,
        username="request_global_admin_structured",
        email="request-global-admin-structured@test.local",
    )
    _login_admin(client, app, admin)

    title = f"Demande structure explicite {int(datetime.now(UTC).timestamp())}"
    payload = {
        "title": title,
        "description": "Situation avec structure explicite.",
        "person_name": "Personne Ciblée",
        "email": "",
        "phone": "",
        "city": "Lyon",
        "category": "general",
        "priority": "attention",
        "structure_id": str(structure.id),
        "owner_id": "",
        "internal_notes": "",
    }

    resp = client.post("/admin/requests/new", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)

    created = session.query(Request).filter_by(title=title).order_by(Request.id.desc()).first()
    assert created is not None
    assert created.structure_id == structure.id


def test_admin_request_new_structure_bound_admin_uses_bound_structure(app, client, session):
    structure = _make_structure(
        session,
        name="Structure Portée",
        slug=f"structure-portee-{int(datetime.now(UTC).timestamp())}",
    )
    admin = _make_admin(
        session,
        username="request_scoped_superadmin",
        email="request-scoped-superadmin@test.local",
        structure_id=structure.id,
    )
    _login_admin(client, app, admin)

    title = f"Demande structure portée {int(datetime.now(UTC).timestamp())}"
    payload = {
        "title": title,
        "description": "Situation avec structure imposée par le compte.",
        "person_name": "Personne Portée",
        "email": "",
        "phone": "",
        "city": "Marseille",
        "category": "general",
        "priority": "attention",
        "structure_id": "",
        "owner_id": "",
        "internal_notes": "",
    }

    resp = client.post("/admin/requests/new", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)

    created = session.query(Request).filter_by(title=title).order_by(Request.id.desc()).first()
    assert created is not None
    assert created.structure_id == structure.id
