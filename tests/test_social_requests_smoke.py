import pytest

pytestmark = pytest.mark.compat_social_request


def test_social_requests_pages(client, authenticated_admin_client, init_test_data, db_session):
    from backend.models import SocialRequest, SocialRequestEvent, User

    structure = init_test_data["structure"]

    r = client.get("/requests/new")
    assert r.status_code == 200

    resp = client.post(
        "/requests/new",
        data={
            "structure_id": str(structure.id),
            "need_type": "aide_alimentaire",
            "urgency": "medium",
            "person_ref": "Dossier #A-1",
            "description": "Besoin alimentaire urgent.",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    assert "/requests/" in location

    r2 = authenticated_admin_client.get("/requests")
    assert r2.status_code == 200

    detail_path = location
    if detail_path.startswith("http://") or detail_path.startswith("https://"):
        detail_path = "/" + detail_path.split("/", 3)[-1]
    r3 = client.get(detail_path)
    assert r3.status_code == 200

    req_id = int(detail_path.rstrip("/").split("/")[-1])
    created_events = (
        SocialRequestEvent.query.filter_by(request_id=req_id, event_type="created").all()
    )
    assert len(created_events) == 1

    user = User.query.filter_by(email="social.assign@test.local").first()
    if not user:
        user = User(
            username="social_assign_test_user",
            email="social.assign@test.local",
            password_hash="x",
            role="admin",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

    assign_resp = client.post(
        f"/requests/{req_id}/assign",
        data={"assigned_to_user_id": str(user.id)},
        follow_redirects=False,
    )
    assert assign_resp.status_code in (302, 303)

    sr = SocialRequest.query.get(req_id)
    assert sr is not None
    assert sr.assigned_to_user_id == user.id
    assert sr.assigned_at is not None
    assert sr.status == "in_progress"
    assigned_events = (
        SocialRequestEvent.query.filter_by(request_id=req_id, event_type="assigned").all()
    )
    assert len(assigned_events) == 1

    unassign_resp = client.post(
        f"/requests/{req_id}/unassign",
        follow_redirects=False,
    )
    assert unassign_resp.status_code in (302, 303)

    sr2 = SocialRequest.query.get(req_id)
    assert sr2 is not None
    assert sr2.assigned_to_user_id is None
    assert sr2.assigned_at is None
    unassigned_events = (
        SocialRequestEvent.query.filter_by(request_id=req_id, event_type="unassigned").all()
    )
    assert len(unassigned_events) == 1

    status_resp = client.post(
        f"/requests/{req_id}/status",
        data={"status": "in_progress"},
        follow_redirects=False,
    )
    assert status_resp.status_code in (302, 303)

    sr3 = SocialRequest.query.get(req_id)
    assert sr3 is not None
    assert sr3.status == "in_progress"
    status_events = (
        SocialRequestEvent.query.filter_by(
            request_id=req_id, event_type="status_changed"
        ).all()
    )
    assert len(status_events) >= 1


def test_dashboard(authenticated_admin_client):
    r = authenticated_admin_client.get("/requests/dashboard")
    assert r.status_code == 200


def test_operations(authenticated_admin_client):
    r = authenticated_admin_client.get("/requests/operations")
    assert r.status_code == 200


def test_multi_structure_filtering(authenticated_admin_client, db_session, init_test_data):
    from backend.models import SocialRequest, Structure

    structure_a = init_test_data["structure"]
    structure_b = Structure.query.filter_by(slug="second").first()
    if not structure_b:
        structure_b = Structure(name="Second", slug="second")
        db_session.add(structure_b)
        db_session.commit()

    req_a = SocialRequest(
        structure_id=structure_a.id,
        need_type="aide_alimentaire",
        urgency="medium",
        person_ref=None,
        description="Request A",
        status="new",
    )
    req_b = SocialRequest(
        structure_id=structure_b.id,
        need_type="urgence_sociale",
        urgency="high",
        person_ref=None,
        description="Request B",
        status="new",
    )
    db_session.add(req_a)
    db_session.add(req_b)
    db_session.commit()

    r_all = authenticated_admin_client.get("/requests")
    assert r_all.status_code == 200
    body_all = r_all.get_data(as_text=True)
    assert "aide_alimentaire" in body_all
    assert "urgence_sociale" in body_all

    r_scoped = authenticated_admin_client.get(f"/requests?structure_id={structure_a.id}")
    assert r_scoped.status_code == 200
    body_scoped = r_scoped.get_data(as_text=True)
    assert "aide_alimentaire" in body_scoped
    assert "urgence_sociale" not in body_scoped

    r_dash_scoped = authenticated_admin_client.get(f"/requests/dashboard?structure_id={structure_b.id}")
    assert r_dash_scoped.status_code == 200

    r_ops_scoped = authenticated_admin_client.get(f"/requests/operations?structure_id={structure_b.id}")
    assert r_ops_scoped.status_code == 200
