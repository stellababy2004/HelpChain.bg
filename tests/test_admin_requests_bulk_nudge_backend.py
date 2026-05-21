from uuid import uuid4

import pytest

from backend.models import AdminUser, Notification, Request, RequestActivity, Structure, User, Volunteer

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
def make_request_with_volunteer(session, admin_login):
    admin_id = _admin_id_from_client(admin_login)

    def _make() -> tuple[Request, Volunteer]:
        structure = session.query(Structure).filter_by(slug="default").first()
        suffix = uuid4().hex[:8]

        user = User(
            username=f"nudge_req_user_{suffix}",
            email=f"nudge_req_{suffix}@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
        )
        session.add(user)
        session.flush()

        volunteer = Volunteer(
            name=f"Nudge Volunteer {suffix}",
            email=f"nudge_vol_{suffix}@test.local",
            is_active=True,
        )
        session.add(volunteer)
        session.flush()

        req = Request(
            title=f"Nudge request {suffix}",
            user_id=user.id,
            status="in_progress",
            category="general",
            structure_id=getattr(structure, "id", None),
            owner_id=admin_id,
            assigned_volunteer_id=volunteer.id,
        )
        session.add(req)
        session.commit()
        return req, volunteer

    return _make


def test_bulk_nudge_selected_volunteers_creates_event(
    admin_login, db_session, make_request_with_volunteer
):
    req, volunteer = make_request_with_volunteer()

    before_notifs = (
        db_session.query(Notification)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, type="admin_nudge")
        .count()
    )
    before_acts = (
        db_session.query(RequestActivity)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, action="admin_nudge_sent")
        .count()
    )

    r = admin_login.post(
        f"/admin/requests/{req.id}/nudge",
        data={},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    after_notifs = (
        db_session.query(Notification)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, type="admin_nudge")
        .count()
    )
    after_acts = (
        db_session.query(RequestActivity)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, action="admin_nudge_sent")
        .count()
    )

    assert after_notifs == before_notifs + 1
    assert after_acts == before_acts + 1


def test_bulk_nudge_selected_volunteers_respects_cooldown(
    admin_login, db_session, make_request_with_volunteer
):
    req, volunteer = make_request_with_volunteer()

    r1 = admin_login.post(f"/admin/requests/{req.id}/nudge", data={}, follow_redirects=False)
    assert r1.status_code in (302, 303)

    notif_count_after_first = (
        db_session.query(Notification)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, type="admin_nudge")
        .count()
    )
    acts_after_first = (
        db_session.query(RequestActivity)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, action="admin_nudge_sent")
        .count()
    )
    assert notif_count_after_first == 1
    assert acts_after_first == 1

    r2 = admin_login.post(f"/admin/requests/{req.id}/nudge", data={}, follow_redirects=False)
    assert r2.status_code in (302, 303)

    notif_count_after_second = (
        db_session.query(Notification)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, type="admin_nudge")
        .count()
    )
    acts_after_second = (
        db_session.query(RequestActivity)
        .filter_by(request_id=req.id, volunteer_id=volunteer.id, action="admin_nudge_sent")
        .count()
    )

    # cooldown should suppress additional nudge side-effects
    assert notif_count_after_second == notif_count_after_first
    assert acts_after_second == acts_after_first
