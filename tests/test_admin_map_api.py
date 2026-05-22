from datetime import datetime, timezone

from backend.extensions import db
from backend.helpchain_backend.src.models import AdminUser, Case, CaseCollaborator, Request, Structure, User


def _login_admin_session(client, admin_id: int, *, role: str = "admin"):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["_fresh"] = True
        sess["user_id"] = admin_id
        sess["admin_id"] = admin_id
        sess["admin_logged_in"] = True
        sess["admin_user_id"] = admin_id
        sess["role"] = role
        sess["is_authenticated"] = True
        sess["is_admin"] = True


def _make_structure(*, name: str, slug: str) -> Structure:
    row = Structure(name=name, slug=slug)
    db.session.add(row)
    db.session.flush()
    return row


def _make_admin(*, username: str, email: str, role: str, structure_id: int | None) -> AdminUser:
    row = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        is_active=True,
        structure_id=structure_id,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _make_user(*, username: str, email: str, structure_id: int) -> User:
    row = User(
        username=username,
        email=email,
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=structure_id,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _make_request(
    *,
    title: str,
    user_id: int,
    structure_id: int,
    status: str = "open",
    latitude: float | None = 48.8566,
    longitude: float | None = 2.3522,
) -> Request:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    row = Request(
        title=title,
        category="general",
        status=status,
        user_id=user_id,
        structure_id=structure_id,
        latitude=latitude,
        longitude=longitude,
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _make_case(
    *,
    request_id: int,
    structure_id: int,
    status: str = "open",
    latitude: float | None = 48.8566,
    longitude: float | None = 2.3522,
    risk_score: int = 0,
) -> Case:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    row = Case(
        request_id=request_id,
        structure_id=structure_id,
        status=status,
        latitude=latitude,
        longitude=longitude,
        risk_score=risk_score,
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _seed_map_scope(prefix: str = "map_scope"):
    structure_a = _make_structure(name=f"{prefix} Alpha", slug=f"{prefix}-alpha")
    structure_b = _make_structure(name=f"{prefix} Beta", slug=f"{prefix}-beta")
    structure_c = _make_structure(name=f"{prefix} Gamma", slug=f"{prefix}-gamma")

    admin_a = _make_admin(
        username=f"{prefix}_admin_a",
        email=f"{prefix}_admin_a@test.local",
        role="admin",
        structure_id=structure_a.id,
    )
    ops_a = _make_admin(
        username=f"{prefix}_ops_a",
        email=f"{prefix}_ops_a@test.local",
        role="ops",
        structure_id=structure_a.id,
    )
    admin_b = _make_admin(
        username=f"{prefix}_admin_b",
        email=f"{prefix}_admin_b@test.local",
        role="admin",
        structure_id=structure_b.id,
    )
    global_admin = _make_admin(
        username=f"{prefix}_global",
        email=f"{prefix}_global@test.local",
        role="superadmin",
        structure_id=None,
    )

    user_a = _make_user(
        username=f"{prefix}_user_a",
        email=f"{prefix}_user_a@test.local",
        structure_id=structure_a.id,
    )
    user_b = _make_user(
        username=f"{prefix}_user_b",
        email=f"{prefix}_user_b@test.local",
        structure_id=structure_b.id,
    )
    user_c = _make_user(
        username=f"{prefix}_user_c",
        email=f"{prefix}_user_c@test.local",
        structure_id=structure_c.id,
    )

    request_visible = _make_request(
        title=f"{prefix} visible request",
        user_id=user_a.id,
        structure_id=structure_a.id,
    )
    request_hidden = _make_request(
        title=f"{prefix} hidden request",
        user_id=user_b.id,
        structure_id=structure_b.id,
    )
    request_request_only = _make_request(
        title=f"{prefix} request only",
        user_id=user_a.id,
        structure_id=structure_a.id,
    )
    request_foreign_only = _make_request(
        title=f"{prefix} foreign request only",
        user_id=user_c.id,
        structure_id=structure_c.id,
    )

    case_visible = _make_case(
        request_id=request_visible.id,
        structure_id=structure_a.id,
        risk_score=90,
    )
    case_hidden = _make_case(
        request_id=request_hidden.id,
        structure_id=structure_b.id,
        risk_score=85,
    )
    db.session.add(
        CaseCollaborator(
            case_id=case_visible.id,
            structure_id=structure_b.id,
            role="viewer",
        )
    )
    db.session.commit()
    return {
        "structure_a": structure_a,
        "structure_b": structure_b,
        "admin_a": admin_a,
        "ops_a": ops_a,
        "admin_b": admin_b,
        "global_admin": global_admin,
        "request_visible": request_visible,
        "request_hidden": request_hidden,
        "request_request_only": request_request_only,
        "request_foreign_only": request_foreign_only,
        "case_visible": case_visible,
        "case_hidden": case_hidden,
    }


def test_admin_cases_map_api_returns_json(client, app):
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    with app.app_context():
        structure = Structure.query.filter_by(slug="default").first()
        if not structure:
            structure = Structure(name="Default", slug="default")
            db.session.add(structure)
            db.session.flush()

        admin = AdminUser(
            username="map_admin",
            email="map_admin@test.local",
            password_hash="x",
            role="admin",
            is_active=True,
        )
        requester = User(
            username="map_req_user",
            email="map_req_user@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
        )
        db.session.add_all([admin, requester])
        db.session.flush()

        req = Request(
            title="map req",
            category="general",
            status="open",
            user_id=requester.id,
            structure_id=structure.id,
            created_at=now,
        )
        db.session.add(req)
        db.session.flush()

        case = Case(
            request_id=req.id,
            structure_id=structure.id,
            status="open",
            created_at=now,
        )
        db.session.add(case)
        db.session.commit()
        admin_id = admin.id

    _login_admin_session(client, admin_id)
    resp = client.get("/admin/api/cases/map")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert "cases" in data
    assert isinstance(data["cases"], list)
    if data["cases"]:
        row = data["cases"][0]
        assert "id" in row
        assert "lat" in row
        assert "lng" in row
        assert "status" in row
        assert "risk_level" in row
        assert "created_at" in row


def test_scoped_admin_cases_map_hides_foreign_cases(app, session):
    seeded = _seed_map_scope("map_cases_scope")
    visible_case_id = seeded["case_visible"].id
    hidden_case_id = seeded["case_hidden"].id
    admin_a_id = seeded["admin_a"].id

    owner_client = app.test_client()
    _login_admin_session(owner_client, admin_a_id, role="admin")
    owner_response = owner_client.get("/admin/api/cases/map")
    assert owner_response.status_code == 200
    owner_case_ids = {int(row["id"]) for row in owner_response.get_json()["cases"]}
    assert visible_case_id in owner_case_ids
    assert hidden_case_id not in owner_case_ids


def test_global_superadmin_cases_map_preserves_cross_tenant_visibility(app, session):
    seeded = _seed_map_scope("map_cases_global")
    visible_case_id = seeded["case_visible"].id
    hidden_case_id = seeded["case_hidden"].id
    global_admin_id = seeded["global_admin"].id

    client = app.test_client()
    _login_admin_session(client, global_admin_id, role="superadmin")
    response = client.get("/admin/api/cases/map")

    assert response.status_code == 200
    case_ids = {int(row["id"]) for row in response.get_json()["cases"]}
    assert visible_case_id in case_ids
    assert hidden_case_id in case_ids


def test_risk_map_items_remain_tenant_scoped_for_ops(app, session):
    seeded = _seed_map_scope("map_risk_scope")
    ops_a_id = seeded["ops_a"].id

    client = app.test_client()
    _login_admin_session(client, ops_a_id, role="ops")
    response = client.get("/admin/api/risk-map")

    assert response.status_code == 200
    items = response.get_json()["items"]
    case_items = [item for item in items if item.get("source_type") == "case"]
    case_titles = {str(item["title"]) for item in case_items}
    request_titles = {str(item["title"]) for item in items if item.get("source_type") == "request"}
    assert "map_risk_scope visible request" in case_titles
    assert "map_risk_scope hidden request" not in case_titles
    assert len(case_items) == 1
    assert "map_risk_scope foreign request only" not in request_titles


def test_global_superadmin_risk_map_preserves_full_visibility(app, session):
    seeded = _seed_map_scope("map_risk_global")
    global_admin_id = seeded["global_admin"].id

    client = app.test_client()
    _login_admin_session(client, global_admin_id, role="superadmin")
    response = client.get("/admin/api/risk-map")

    assert response.status_code == 200
    case_items = [item for item in response.get_json()["items"] if item.get("source_type") == "case"]
    case_titles = {str(item["title"]) for item in case_items}
    assert "map_risk_global visible request" in case_titles
    assert "map_risk_global hidden request" in case_titles
    assert len(case_items) == 2
