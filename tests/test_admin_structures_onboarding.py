import pytest
from contextlib import contextmanager

from sqlalchemy import event

pytestmark = pytest.mark.shared_platform


@contextmanager
def _count_select_queries(app):
    from backend.extensions import db

    counts = {"select": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if str(statement or "").lstrip().lower().startswith("select"):
            counts["select"] += 1

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


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


def test_structure_create_accepts_valid_organization_type(client, session):
    admin = _make_admin(
        session, username="creator_valid_type", email="creator_valid_type@test.local", role="superadmin"
    )
    _login_admin(client, admin)
    resp = client.post(
        "/admin/structures/new",
        data={
            "name": "CCAS Valid Type",
            "slug": "ccas-valid-type",
            "organization_type": "ccas",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from backend.models import Structure

    created = Structure.query.filter_by(slug="ccas-valid-type").first()
    assert created is not None
    assert created.organization_type == "ccas"


def test_structure_create_rejects_invalid_organization_type(client, session):
    admin = _make_admin(
        session,
        username="creator_invalid_type",
        email="creator_invalid_type@test.local",
        role="superadmin",
    )
    _login_admin(client, admin)
    resp = client.post(
        "/admin/structures/new",
        data={
            "name": "Invalid Type",
            "slug": "invalid-type",
            "organization_type": "invented_agency",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400

    from backend.models import Structure

    assert Structure.query.filter_by(slug="invalid-type").first() is None


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


def test_structure_operational_intelligence_endpoint(client, session):
    st = _make_structure(session, name="Operational Detail", slug="operational-detail")
    admin = _make_admin(
        session,
        username="ops_intel_admin",
        email="ops_intel_admin@test.local",
        role="superadmin",
    )
    _login_admin(client, admin)

    resp = client.get(
        f"/admin/structures/{st.id}/operational-intelligence",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["profile"]["name"] == "Operational Detail"
    assert payload["capacity"]["active_cases"]["source_tables"] == ["requests"]
    assert payload["health"]["display"] == "Not enough operational data"


def test_structure_operational_intelligence_blocks_foreign_structure(client, session):
    own = _make_structure(session, name="Own Tenant", slug="own-tenant")
    foreign = _make_structure(session, name="Foreign Tenant", slug="foreign-tenant")
    admin = _make_admin(
        session,
        username="tenant_ops_intel_admin",
        email="tenant_ops_intel_admin@test.local",
        role="superadmin",
        structure_id=own.id,
    )
    _login_admin(client, admin)

    resp = client.get(
        f"/admin/structures/{foreign.id}/operational-intelligence",
        follow_redirects=False,
    )

    assert resp.status_code == 403


def test_empty_intervenant_availability_does_not_count_as_available(client, session):
    from backend.models import Intervenant

    st = _make_structure(session, name="Availability Truth", slug="availability-truth")
    admin = _make_admin(
        session,
        username="availability_admin",
        email="availability_admin@test.local",
        role="superadmin",
    )
    session.add(
        Intervenant(
            structure_id=st.id,
            name="Unknown Availability",
            actor_type="social_worker",
            availability="",
            is_active=True,
        )
    )
    session.commit()
    _login_admin(client, admin)

    resp = client.get(
        f"/admin/structures/{st.id}/operational-intelligence",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["capacity"]["professionals"]["value"] == 1
    assert payload["capacity"]["available_professionals"]["value"] == 0


def test_structures_index_query_count_is_not_per_structure(client, session, app):
    admin = _make_admin(
        session,
        username="query_count_admin",
        email="query_count_admin@test.local",
        role="superadmin",
    )
    for index in range(8):
        st = _make_structure(session, name=f"Query Org {index}", slug=f"query-org-{index}")
        user = _make_user(session, username=f"query-user-{index}", email=f"query-user-{index}@test.local")
        _make_request(session, title=f"Query Request {index}", user_id=user.id, structure_id=st.id)
    _login_admin(client, admin)

    with _count_select_queries(app) as counts:
        resp = client.get("/admin/structures", follow_redirects=False)

    assert resp.status_code == 200
    assert counts["select"] <= 12


def test_structure_detail_query_count_is_materially_reduced(client, session, app):
    st = _make_structure(session, name="Detail Query Count", slug="detail-query-count")
    user = _make_user(session, username="detail-query-user", email="detail-query-user@test.local")
    _make_request(session, title="Detail Query Request", user_id=user.id, structure_id=st.id)
    admin = _make_admin(
        session,
        username="detail_query_admin",
        email="detail_query_admin@test.local",
        role="superadmin",
    )
    _login_admin(client, admin)

    with _count_select_queries(app) as counts:
        resp = client.get(f"/admin/structures/{st.id}", follow_redirects=False)

    assert resp.status_code == 200
    assert counts["select"] <= 30


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
