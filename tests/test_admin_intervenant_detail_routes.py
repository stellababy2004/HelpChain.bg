from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

pytestmark = pytest.mark.spine


def _login_admin(client, app, admin_user):
    from backend.models import utc_now

    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["admin_id"] = admin_user.id
        sess[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        sess["admin_mfa_last_verified"] = 4102444800
        sess["admin_mfa_user_id"] = admin_user.id


def test_intervenant_detail_routes_registered(app):
    routes = {
        rule.rule: rule.endpoint
        for rule in app.url_map.iter_rules()
        if "intervenant" in rule.rule
    }

    assert routes["/admin/intervenants/<int:intervenant_id>"] == "admin.admin_intervenant_detail"
    assert (
        routes["/admin/structures/<int:structure_id>/intervenants/<int:intervenant_id>"]
        == "admin.admin_structure_intervenant_detail"
    )


def _seed_intervenant_route_data(session):
    from backend.models import AdminUser, Intervenant, Structure

    structure_a = Structure(name="Structure A", slug="structure-a")
    structure_b = Structure(name="Structure B", slug="structure-b")
    session.add_all([structure_a, structure_b])
    session.flush()

    intervenant_a = Intervenant(
        structure_id=structure_a.id,
        name="Claire Martin",
        actor_type="social_worker",
        email="claire@example.test",
        availability="available",
        is_active=True,
    )
    intervenant_b = Intervenant(
        structure_id=structure_b.id,
        name="Marc Dubois",
        actor_type="coordinator",
        email="marc@example.test",
        availability="busy",
        is_active=True,
    )
    superadmin = AdminUser(
        username="intervenant_route_superadmin",
        email="intervenant-route-superadmin@test.local",
        password_hash=generate_password_hash("TestPass123!"),
        role="superadmin",
        is_active=True,
    )
    structure_admin = AdminUser(
        username="intervenant_route_structure_admin",
        email="intervenant-route-structure-admin@test.local",
        password_hash=generate_password_hash("TestPass123!"),
        role="admin",
        structure_id=structure_a.id,
        is_active=True,
    )
    session.add_all([intervenant_a, intervenant_b, superadmin, structure_admin])
    session.commit()

    return structure_a, structure_b, intervenant_a, intervenant_b, superadmin, structure_admin


def _seed_request(session, structure, title="Coordination active"):
    from backend.models import Request, User

    user = User(
        username=f"requester_{structure.id}_{title.lower().replace(' ', '_')}",
        email=f"requester-{structure.id}-{abs(hash(title))}@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=structure.id,
    )
    session.add(user)
    session.flush()

    req = Request(
        title=title,
        category="general",
        status="new",
        priority="high",
        structure_id=structure.id,
        user_id=user.id,
    )
    session.add(req)
    session.commit()
    return req


def test_intervenant_detail_routes_render_for_superadmin(app, session):
    structure_a, _structure_b, intervenant_a, _intervenant_b, superadmin, _structure_admin = (
        _seed_intervenant_route_data(session)
    )

    global_client = app.test_client()
    _login_admin(global_client, app, superadmin)

    global_detail = global_client.get(f"/admin/intervenants/{intervenant_a.id}")
    assert global_detail.status_code == 200

    scoped_detail = global_client.get(
        f"/admin/structures/{structure_a.id}/intervenants/{intervenant_a.id}"
    )
    assert scoped_detail.status_code == 200


def test_intervenant_detail_routes_scope_structure_admin(app, session):
    structure_a, structure_b, intervenant_a, intervenant_b, _superadmin, structure_admin = (
        _seed_intervenant_route_data(session)
    )

    structure_client = app.test_client()
    _login_admin(structure_client, app, structure_admin)

    matching_detail = structure_client.get(
        f"/admin/structures/{structure_a.id}/intervenants/{intervenant_a.id}"
    )
    assert matching_detail.status_code == 200

    cross_tenant_detail = structure_client.get(
        f"/admin/structures/{structure_b.id}/intervenants/{intervenant_b.id}"
    )
    assert cross_tenant_detail.status_code == 403


def test_intervenant_profile_update_logs_activity_and_availability(app, session):
    (
        structure_a,
        _structure_b,
        intervenant_a,
        _intervenant_b,
        superadmin,
        _structure_admin,
    ) = _seed_intervenant_route_data(session)

    client = app.test_client()
    _login_admin(client, app, superadmin)

    resp = client.post(
        f"/admin/structures/{structure_a.id}/intervenants/{intervenant_a.id}",
        data={
            "name": "Claire Martin",
            "actor_type": "social_worker",
            "email": "claire@example.test",
            "phone": "0102030405",
            "location": "Boulogne || Centre social",
            "availability": "in_intervention",
            "competencies": ["coordination", "urgence_terrain"],
            "coverage_zones": "Nord\nCentre",
            "coverage_communes": "Boulogne, Issy",
            "radius_km": "12",
            "internal_notes": "Mobilisee terrain.",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 302

    from backend.models import IntervenantActivity

    session.refresh(intervenant_a)
    assert intervenant_a.availability == "in_intervention"
    assert "coordination" in (intervenant_a.competencies_json or "")

    event_types = {
        row.event_type
        for row in session.query(IntervenantActivity)
        .filter_by(intervenant_id=intervenant_a.id)
        .all()
    }
    assert "availability_changed" in event_types
    assert "notes_updated" in event_types
    assert "profile_updated" in event_types


def test_intervenant_assignment_flow_and_active_cases_render(app, session):
    (
        structure_a,
        _structure_b,
        intervenant_a,
        _intervenant_b,
        superadmin,
        _structure_admin,
    ) = _seed_intervenant_route_data(session)
    req = _seed_request(session, structure_a, title="Aide administrative urgente")

    client = app.test_client()
    _login_admin(client, app, superadmin)

    assign_resp = client.post(
        f"/admin/structures/{structure_a.id}/intervenants/{intervenant_a.id}",
        data={"intervenant_action": "assign_request", "request_id": str(req.id)},
        follow_redirects=False,
    )
    assert assign_resp.status_code == 302

    from backend.models import Assignment, IntervenantActivity

    assignment = session.query(Assignment).filter_by(
        intervenant_id=intervenant_a.id,
        request_id=req.id,
        structure_id=structure_a.id,
    ).first()
    assert assignment is not None
    assert assignment.status == "active"

    page = client.get(f"/admin/structures/{structure_a.id}/intervenants/{intervenant_a.id}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Dossiers actifs" in html
    assert "Aide administrative urgente" in html

    activity = session.query(IntervenantActivity).filter_by(
        intervenant_id=intervenant_a.id,
        event_type="affectation_created",
        request_id=req.id,
    ).first()
    assert activity is not None


def test_intervenant_assignment_rejects_cross_tenant_request(app, session):
    (
        structure_a,
        structure_b,
        intervenant_a,
        _intervenant_b,
        superadmin,
        _structure_admin,
    ) = _seed_intervenant_route_data(session)
    other_request = _seed_request(session, structure_b, title="Cross tenant")

    client = app.test_client()
    _login_admin(client, app, superadmin)

    resp = client.post(
        f"/admin/structures/{structure_a.id}/intervenants/{intervenant_a.id}",
        data={"intervenant_action": "assign_request", "request_id": str(other_request.id)},
        follow_redirects=False,
    )

    assert resp.status_code == 403
