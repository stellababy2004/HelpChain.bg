import pytest
from werkzeug.security import generate_password_hash

pytestmark = pytest.mark.shared_platform


def _ensure_structure(session, structure_id: int, name: str, slug: str):
    from backend.models import Structure

    structure = session.get(Structure, structure_id)
    if structure:
        return structure
    structure = Structure(id=structure_id, name=name, slug=slug)
    session.add(structure)
    session.commit()
    return structure


def _ensure_admin_user(session, username: str, role: str, structure_id: int | None):
    from backend.models import AdminUser

    admin = session.query(AdminUser).filter_by(username=username).first()
    if not admin:
        admin = AdminUser(
            username=username,
            email=f"{username}@test.local",
            password_hash=generate_password_hash("TestPass123!"),
            role=role,
            is_active=True,
        )
        session.add(admin)
        session.commit()
    admin.role = role
    admin.structure_id = structure_id
    session.commit()
    return admin


def _login_admin_session(client, admin_user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["admin_user_id"] = admin_user.id


@pytest.fixture
def global_admin(client, session):
    _ensure_structure(session, 2, "Structure 2", "structure-2")
    admin = _ensure_admin_user(session, "global_admin", "superadmin", None)
    _login_admin_session(client, admin)
    return client


@pytest.fixture
def structure_admin(client, session):
    _ensure_structure(session, 2, "Structure 2", "structure-2")
    admin = _ensure_admin_user(session, "structure_admin", "admin", 2)
    _login_admin_session(client, admin)
    return client


@pytest.fixture
def bound_superadmin(client, session):
    _ensure_structure(session, 2, "Structure 2", "structure-2")
    admin = _ensure_admin_user(session, "bound_superadmin", "superadmin", 2)
    _login_admin_session(client, admin)
    return client


@pytest.fixture
def operator_user(client, session):
    _ensure_structure(session, 2, "Structure 2", "structure-2")
    admin = _ensure_admin_user(session, "operator_user", "ops", 2)
    _login_admin_session(client, admin)
    return client


PUBLIC_ROUTES = [
    "/",
    "/comment-ca-marche",
    "/professionnels",
    "/pour-les-structures",
]

PLATFORM_ADMIN_ROUTES = [
    "/admin/structures",
    "/admin/roles",
    "/admin/security",
    "/admin/sla",
    "/admin/audit",
]

STRUCTURE_ADMIN_ROUTES = [
    "/admin/requests",
]

STRUCTURE_SUPERADMIN_ONLY_ROUTES = [
    "/admin/requests/new",
    "/admin/structures/2",
]

OPS_ROUTES = [
    "/ops/workspace",
    "/ops/cases",
    "/ops/notifications",
    "/requests/operations",
]


@pytest.mark.parametrize("path", PUBLIC_ROUTES)
def test_public_routes_accessible_anonymous(client, path):
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", PLATFORM_ADMIN_ROUTES)
def test_platform_admin_routes_block_anonymous(client, path):
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert "/admin/login" in (resp.headers.get("Location") or "")


@pytest.mark.parametrize("path", PLATFORM_ADMIN_ROUTES)
def test_platform_admin_routes_block_operator(operator_user, path):
    resp = operator_user.get(path, follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.parametrize("path", PLATFORM_ADMIN_ROUTES)
def test_platform_admin_routes_block_structure_admin(structure_admin, path):
    resp = structure_admin.get(path, follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.parametrize("path", PLATFORM_ADMIN_ROUTES)
def test_platform_admin_routes_allow_global_admin(global_admin, path):
    resp = global_admin.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", STRUCTURE_ADMIN_ROUTES)
def test_structure_admin_routes_allow_structure_admin(structure_admin, path):
    resp = structure_admin.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", STRUCTURE_ADMIN_ROUTES + STRUCTURE_SUPERADMIN_ONLY_ROUTES)
def test_structure_admin_routes_allow_global_admin(global_admin, path):
    resp = global_admin.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", STRUCTURE_SUPERADMIN_ONLY_ROUTES)
def test_structure_admin_routes_block_structure_admin_on_superadmin_only_pages(
    structure_admin, path
):
    resp = structure_admin.get(path, follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.parametrize("path", STRUCTURE_ADMIN_ROUTES)
def test_structure_admin_routes_block_operator(operator_user, path):
    resp = operator_user.get(path, follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.parametrize("path", OPS_ROUTES)
def test_ops_routes_allow_operator(operator_user, path):
    resp = operator_user.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", OPS_ROUTES)
def test_ops_routes_allow_structure_admin(structure_admin, path):
    resp = structure_admin.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", OPS_ROUTES)
def test_ops_routes_allow_global_admin(global_admin, path):
    resp = global_admin.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", OPS_ROUTES)
def test_ops_routes_block_anonymous(client, path):
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert "/admin/ops/login" in (resp.headers.get("Location") or "")


def test_structure_admin_structure_scoping(bound_superadmin, session):
    _ensure_structure(session, 2, "Structure 2", "structure-2")
    _ensure_structure(session, 3, "Structure 3", "structure-3")

    resp_ok = bound_superadmin.get("/admin/structures/2", follow_redirects=False)
    assert resp_ok.status_code == 200

    resp_forbidden = bound_superadmin.get("/admin/structures/3", follow_redirects=False)
    assert resp_forbidden.status_code == 403
