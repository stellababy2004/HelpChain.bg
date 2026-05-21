from uuid import uuid4

import pytest

from backend.models import AdminUser, Request, RequestActivity, Structure, User

pytestmark = pytest.mark.spine


def _login_admin(client, admin_user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["admin_id"] = admin_user.id
        sess["admin_user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True


def _make_structure(session, *, structure_id: int, name: str, slug: str):
    structure = session.get(Structure, structure_id)
    if structure is None:
        structure = Structure(id=structure_id, name=name, slug=slug)
        session.add(structure)
        session.commit()
    return structure


def _make_admin(session, *, role: str, structure_id=None):
    suffix = uuid4().hex[:8]
    admin = AdminUser(
        username=f"rbac_{role}_{suffix}",
        email=f"rbac_{role}_{suffix}@test.local",
        password_hash="x",
        role=role,
        is_active=True,
        structure_id=structure_id,
    )
    session.add(admin)
    session.commit()
    return admin


def _make_request(session, *, title: str, structure_id: int, owner_id=None):
    suffix = uuid4().hex[:8]
    user = User(
        username=f"rbac_requester_{suffix}",
        email=f"rbac_requester_{suffix}@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
    )
    session.add(user)
    session.flush()
    req = Request(
        title=title,
        description=f"Description for {title}",
        category="general",
        user_id=user.id,
        structure_id=structure_id,
        status="pending",
        owner_id=owner_id,
    )
    session.add(req)
    session.commit()
    return req


def test_unauthenticated_admin_api_is_blocked(client):
    resp = client.get("/admin/api/action-queue", follow_redirects=False)

    assert resp.status_code in (303, 404)


def test_ops_cannot_access_or_mutate_professional_access(client, session):
    operator = _make_admin(session, role="ops")
    _login_admin(client, operator)

    listing = client.get("/admin/pro-access", follow_redirects=False)
    detail = client.get("/admin/pro-access/999999", follow_redirects=False)
    approve = client.post("/admin/pro-access/999999/approve", follow_redirects=False)

    assert listing.status_code == 403
    assert detail.status_code == 403
    assert approve.status_code == 403


def test_readonly_owner_cannot_add_request_note(client, session):
    structure = _make_structure(session, structure_id=9101, name="RBAC One", slug="rbac-one")
    readonly = _make_admin(session, role="readonly", structure_id=structure.id)
    _login_admin(client, readonly)
    req = _make_request(
        session,
        title="readonly-owner-note-denied",
        structure_id=structure.id,
        owner_id=readonly.id,
    )

    before = session.query(RequestActivity).filter_by(request_id=req.id).count()
    resp = client.post(
        f"/admin/requests/{req.id}/note",
        data={"note": "readonly should not mutate"},
        follow_redirects=False,
    )
    after = session.query(RequestActivity).filter_by(request_id=req.id).count()

    assert resp.status_code == 403
    assert after == before


def test_action_queue_is_structure_scoped_for_structure_admin(client, session):
    own_structure = _make_structure(
        session, structure_id=9102, name="RBAC Scoped", slug="rbac-scoped"
    )
    other_structure = _make_structure(
        session, structure_id=9103, name="RBAC Other", slug="rbac-other"
    )
    admin = _make_admin(session, role="admin", structure_id=own_structure.id)
    _login_admin(client, admin)
    own_request = _make_request(
        session,
        title="rbac-action-queue-visible",
        structure_id=own_structure.id,
    )
    other_request = _make_request(
        session,
        title="rbac-action-queue-hidden",
        structure_id=other_structure.id,
    )

    resp = client.get("/admin/api/action-queue", follow_redirects=False)

    assert resp.status_code == 200
    ids = {item["id"] for item in resp.get_json()["items"]}
    assert own_request.id in ids
    assert other_request.id not in ids
