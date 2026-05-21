import pytest

pytestmark = pytest.mark.shared_platform


def _login_admin(client, admin_user):
    with client.session_transaction() as s:
        s["_user_id"] = str(admin_user.id)
        s["user_id"] = admin_user.id
        s["role"] = admin_user.role
        s["is_authenticated"] = True
        s["is_admin"] = True
        s["admin_logged_in"] = True
        s["admin_id"] = admin_user.id


def _make_admin(session, *, username, email, role="admin", structure_id=None):
    from backend.models import AdminUser

    admin = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        is_active=True,
        structure_id=structure_id,
    )
    session.add(admin)
    session.commit()
    return admin


def _make_structure(session, *, name, slug):
    from backend.models import Structure

    row = Structure(name=name, slug=slug)
    session.add(row)
    session.commit()
    return row


def _make_user(session, *, username, email):
    from backend.models import User

    user = User(username=username, email=email, password_hash="x", role="requester")
    session.add(user)
    session.commit()
    return user


def _make_request(session, *, title, user_id, structure_id):
    from backend.models import Request

    req = Request(
        title=title,
        description="Test",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
    )
    session.add(req)
    session.commit()
    return req


def test_structures_list_requires_global_admin(client):
    resp = client.get("/admin/structures", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/admin/login" in (resp.headers.get("Location") or "")


def test_structures_list_accessible_for_global_admin(client, session):
    admin = _make_admin(
        session, username="global_admin", email="global_admin@test.local", role="superadmin"
    )
    _login_admin(client, admin)
    resp = client.get("/admin/structures", follow_redirects=False)
    assert resp.status_code == 200


def test_structure_create_success(client, session):
    admin = _make_admin(
        session, username="creator_admin", email="creator_admin@test.local", role="superadmin"
    )
    _login_admin(client, admin)
    resp = client.post(
        "/admin/structures/new",
        data={"name": "Structure A", "slug": "structure-a"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from backend.models import Structure

    created = Structure.query.filter_by(slug="structure-a").first()
    assert created is not None
    assert created.name == "Structure A"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "slug": "missing-name"},
        {"name": "Missing Slug", "slug": ""},
        {"name": "", "slug": ""},
    ],
)
def test_structure_create_missing_fields(client, session, payload):
    admin = _make_admin(
        session, username="creator_missing", email="creator_missing@test.local", role="superadmin"
    )
    _login_admin(client, admin)
    resp = client.post("/admin/structures/new", data=payload, follow_redirects=False)
    assert resp.status_code == 400


def test_structure_create_duplicate_slug(client, session):
    _make_structure(session, name="Existing", slug="dup-slug")
    admin = _make_admin(
        session, username="creator_dup", email="creator_dup@test.local", role="superadmin"
    )
    _login_admin(client, admin)
    resp = client.post(
        "/admin/structures/new",
        data={"name": "Other", "slug": "dup-slug"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_structure_detail_loads(client, session):
    st = _make_structure(session, name="Detail", slug="detail")
    admin = _make_admin(
        session, username="detail_admin", email="detail_admin@test.local", role="superadmin"
    )
    _login_admin(client, admin)
    resp = client.get(f"/admin/structures/{st.id}", follow_redirects=False)
    assert resp.status_code == 200


def test_assign_admin_success(client, session):
    st = _make_structure(session, name="Assign", slug="assign")
    global_admin = _make_admin(
        session, username="assign_global", email="assign_global@test.local", role="superadmin"
    )
    target_admin = _make_admin(
        session, username="assign_target", email="assign_target@test.local", role="admin"
    )
    _login_admin(client, global_admin)
    resp = client.post(
        f"/admin/structures/{st.id}/assign-admin",
        data={"admin_id": str(target_admin.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    session.refresh(target_admin)
    assert target_admin.structure_id == st.id


@pytest.mark.parametrize("admin_id", ["", "not-an-int", "999999"])
def test_assign_admin_invalid_id(client, session, admin_id):
    st = _make_structure(session, name="AssignBad", slug="assign-bad")
    global_admin = _make_admin(
        session, username="assign_bad_global", email="assign_bad_global@test.local", role="superadmin"
    )
    target_admin = _make_admin(
        session, username="assign_bad_target", email="assign_bad_target@test.local", role="admin"
    )
    _login_admin(client, global_admin)
    resp = client.post(
        f"/admin/structures/{st.id}/assign-admin",
        data={"admin_id": admin_id},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    session.refresh(target_admin)
    assert target_admin.structure_id is None


def test_tenant_scoping_global_admin_unfiltered(client, session):
    st1 = _make_structure(session, name="Tenant 1", slug="tenant-1")
    st2 = _make_structure(session, name="Tenant 2", slug="tenant-2")
    u1 = _make_user(session, username="u1", email="u1@test.local")
    u2 = _make_user(session, username="u2", email="u2@test.local")
    _make_request(session, title="Req 1", user_id=u1.id, structure_id=st1.id)
    _make_request(session, title="Req 2", user_id=u2.id, structure_id=st2.id)

    global_admin = _make_admin(
        session, username="global_ops", email="global_ops@test.local", role="superadmin"
    )
    _login_admin(client, global_admin)
    resp = client.get("/admin/api/ops-kpis", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert int(payload.get("new_requests") or 0) == 2


def test_tenant_scoping_structure_bound_admin_filtered(client, session):
    st1 = _make_structure(session, name="Tenant 1b", slug="tenant-1b")
    st2 = _make_structure(session, name="Tenant 2b", slug="tenant-2b")
    u1 = _make_user(session, username="u1b", email="u1b@test.local")
    u2 = _make_user(session, username="u2b", email="u2b@test.local")
    _make_request(session, title="Req 1b", user_id=u1.id, structure_id=st1.id)
    _make_request(session, title="Req 2b", user_id=u2.id, structure_id=st2.id)

    scoped_admin = _make_admin(
        session,
        username="scoped_admin",
        email="scoped_admin@test.local",
        role="admin",
        structure_id=st1.id,
    )
    _login_admin(client, scoped_admin)
    resp = client.get("/admin/api/ops-kpis", follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert int(payload.get("new_requests") or 0) == 1


@pytest.mark.parametrize("role", ["ops", "readonly"])
def test_ops_readonly_cannot_access_structure_routes(client, session, role):
    admin = _make_admin(
        session,
        username=f"{role}_admin",
        email=f"{role}_admin@test.local",
        role=role,
    )
    _login_admin(client, admin)
    resp = client.get("/admin/structures", follow_redirects=False)
    assert resp.status_code == 403
