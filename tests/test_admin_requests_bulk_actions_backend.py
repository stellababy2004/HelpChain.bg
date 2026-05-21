from uuid import uuid4

import pytest

from backend.models import AdminUser, Request, Structure, User

pytestmark = pytest.mark.spine


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
def admin_login(authenticated_admin_client, session):
    _set_admin_role(session, authenticated_admin_client, "ops")
    return authenticated_admin_client


@pytest.fixture
def make_request(session, admin_login):
    admin_id = _admin_id_from_client(admin_login)

    def _make_request(status: str = "pending") -> Request:
        structure = session.query(Structure).filter_by(slug="default").first()
        suffix = uuid4().hex[:8]
        user = User(
            username=f"bulk_req_user_{suffix}",
            email=f"bulk_req_{suffix}@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
        )
        session.add(user)
        session.flush()

        req = Request(
            title=f"Bulk request {suffix}",
            user_id=user.id,
            status=status,
            category="general",
            structure_id=getattr(structure, "id", None),
            owner_id=admin_id,
        )
        session.add(req)
        session.commit()
        return req

    return _make_request


def test_bulk_set_status_pending(admin_login, db_session, make_request):
    req1 = make_request(status="in_progress")
    req2 = make_request(status="in_progress")

    for req in (req1, req2):
        r = admin_login.post(
            f"/admin/requests/{req.id}/status",
            data={"status": "pending"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)

    db_session.refresh(req1)
    db_session.refresh(req2)
    assert req1.status == "open"
    assert req2.status == "open"


def test_bulk_set_status_done(admin_login, db_session, make_request):
    req = make_request(status="pending")

    r = admin_login.post(
        f"/admin/requests/{req.id}/status",
        data={"status": "done"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    db_session.refresh(req)
    assert req.status == "done"
    assert req.completed_at is not None
