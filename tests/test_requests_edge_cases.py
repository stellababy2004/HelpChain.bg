import re

import pytest

from backend.models import AdminUser, Request, SocialRequest

pytestmark = pytest.mark.mixed_transition


def _admin_id_from_client(client) -> int:
    with client.session_transaction() as sess:
        val = (
            sess.get("admin_user_id")
            or sess.get("admin_id")
            or sess.get("user_id")
            or sess.get("_user_id")
        )
    return int(val)


def _set_admin_role(session, client, role: str) -> None:
    admin_id = _admin_id_from_client(client)
    admin = session.get(AdminUser, admin_id)
    admin.role = role
    session.commit()


@pytest.fixture
def admin_login(authenticated_admin_client, db_session):
    _set_admin_role(db_session, authenticated_admin_client, "superadmin")
    admin_id = _admin_id_from_client(authenticated_admin_client)
    with authenticated_admin_client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["user_id"] = admin_id
        sess["admin_id"] = admin_id
        sess["admin_user_id"] = admin_id
        sess["role"] = "superadmin"
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["is_authenticated"] = True
    return authenticated_admin_client


def test_create_request_missing_fields_redirects(client, db_session, init_test_data):
    structure = init_test_data["structure"]
    before = db_session.query(SocialRequest).count()

    resp = client.post(
        "/requests/new",
        data={
            "structure_id": str(structure.id),
            "need_type": "",
            "urgency": "medium",
            "description": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    assert "/requests/new" in location
    after = db_session.query(SocialRequest).count()
    assert after == before


def test_create_request_valid_fields_redirects_to_details_and_list(
    client, admin_login, init_test_data, db_session
):
    structure = init_test_data["structure"]

    resp = client.post(
        "/requests/new",
        data={
            "structure_id": str(structure.id),
            "need_type": "urgence_sociale",
            "urgency": "high",
            "person_ref": "Dossier #EDGE-1",
            "description": "Signalement urgent.",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    assert "/requests/" in location
    match = re.search(r"/requests/(\d+)", location)
    assert match is not None
    req_id = int(match.group(1))

    list_resp = admin_login.get("/requests")
    assert list_resp.status_code == 200
    html = list_resp.get_data(as_text=True)
    assert "urgence_sociale" in html
    assert f"#{req_id}" in html


def test_admin_listing_requests_shows_new_request(admin_login, init_test_data, db_session):
    structure = init_test_data["structure"]
    before = db_session.query(Request).count()

    resp = admin_login.post(
        "/admin/requests/new",
        data={
            "title": "Edge Admin Request",
            "description": "Besoin de suivi administratif.",
            "person_name": "M. Martin",
            "email": "martin@example.com",
            "phone": "+33111222333",
            "city": "Lyon",
            "category": "admin_help",
            "priority": "standard",
            "structure_id": str(structure.id),
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert db_session.query(Request).count() == before + 1

    list_resp = admin_login.get("/admin/requests")
    assert list_resp.status_code == 200
    html = list_resp.get_data(as_text=True)
    assert "Edge Admin Request" in html


def test_invalid_status_change_redirects(client, admin_login, init_test_data, db_session):
    structure = init_test_data["structure"]
    create_resp = client.post(
        "/requests/new",
        data={
            "structure_id": str(structure.id),
            "need_type": "aide_alimentaire",
            "urgency": "medium",
            "description": "Demande d'aide.",
        },
        follow_redirects=False,
    )
    assert create_resp.status_code in (302, 303)
    location = create_resp.headers.get("Location", "")
    match = re.search(r"/requests/(\d+)", location)
    assert match is not None
    req_id = int(match.group(1))

    sr = db_session.get(SocialRequest, req_id)
    assert sr is not None
    old_status = sr.status

    status_resp = admin_login.post(
        f"/requests/{req_id}/status",
        data={"status": "not_a_status"},
        follow_redirects=False,
    )
    assert status_resp.status_code in (302, 303)
    assert f"/requests/{req_id}" in (status_resp.headers.get("Location", "") or "")

    db_session.refresh(sr)
    assert sr.status == old_status
