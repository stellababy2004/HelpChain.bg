from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models import Request

pytestmark = pytest.mark.spine


def test_admin_request_new_get_smoke(authenticated_admin_client):
    resp = authenticated_admin_client.get("/admin/requests/new")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Nouvelle demande" in html
    assert "Créer la demande" in html


def test_admin_request_new_post_creates_request(authenticated_admin_client, session):
    title = f"Demande interne smoke {int(datetime.now(UTC).timestamp())}"
    payload = {
        "title": title,
        "description": "Situation créée par un opérateur pour test smoke.",
        "person_name": "Personne Test",
        "email": "",
        "phone": "",
        "city": "Paris",
        "category": "general",
        "priority": "attention",
        "structure_id": "",
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
