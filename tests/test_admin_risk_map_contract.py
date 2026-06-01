from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.extensions import db
from backend.helpchain_backend.src.models import Case, Intervenant
from backend.models import AdminUser, Request, Structure, User, utc_now


def _login_admin(client, app, admin_user: AdminUser) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["admin_id"] = admin_user.id
        sess["admin_user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["mfa_required"] = True
        sess[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        sess["admin_mfa_last_verified"] = 4102444800
        sess["admin_mfa_user_id"] = admin_user.id


def _admin_stub(admin_id: int, role: str = "ops"):
    return type("AdminStub", (), {"id": admin_id, "role": role})()


def _make_structure(*, name: str, slug: str) -> Structure:
    row = Structure(name=name, slug=slug)
    db.session.add(row)
    db.session.flush()
    return row


def _make_ops_admin(*, username: str, email: str, structure_id: int) -> AdminUser:
    row = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role="ops",
        structure_id=structure_id,
        is_active=True,
        mfa_enabled=True,
        totp_secret=f"risk-map-{username}",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _make_request(
    *,
    title: str,
    user_id: int,
    structure_id: int,
    city: str,
    latitude: float,
    longitude: float,
    status: str = "open",
    risk_score: int = 0,
    updated_at: datetime | None = None,
) -> Request:
    now = updated_at or datetime.now(UTC).replace(tzinfo=None)
    row = Request(
        title=title,
        description=title,
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status=status,
        city=city,
        latitude=latitude,
        longitude=longitude,
        risk_score=risk_score,
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_admin_risk_map_returns_canonical_territorial_contract(client, app):
    with app.app_context():
        structure = _make_structure(name="Risk Contract Alpha", slug="risk-contract-alpha")
        ops_admin = _make_ops_admin(
            username="risk_contract_ops",
            email="risk_contract_ops@test.local",
            structure_id=structure.id,
        )
        user = User(
            username="risk_contract_user",
            email="risk_contract_user@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
            structure_id=structure.id,
        )
        db.session.add(user)
        db.session.flush()

        active_request = _make_request(
            title="critical visible request",
            user_id=user.id,
            structure_id=structure.id,
            city="Paris",
            latitude=48.8566,
            longitude=2.3522,
            risk_score=92,
        )
        db.session.add(
            Case(
                request_id=active_request.id,
                structure_id=structure.id,
                status="new",
                priority="high",
                risk_score=92,
                latitude=48.8566,
                longitude=2.3522,
                created_at=datetime.now(UTC) - timedelta(hours=96),
                updated_at=datetime.now(UTC) - timedelta(hours=96),
            )
        )
        db.session.add_all(
            [
                Intervenant(
                    structure_id=structure.id,
                    name="Visible Worker",
                    actor_type="social_worker",
                    email="visible-worker@test.local",
                    location="Paris || 10 Rue A",
                    latitude=48.8567,
                    longitude=2.3524,
                    availability="available",
                    is_active=True,
                ),
                Intervenant(
                    structure_id=structure.id,
                    name="Partner Busy",
                    actor_type="partenaire",
                    email="partner-busy@test.local",
                    location="Paris || 12 Rue B",
                    latitude=48.8568,
                    longitude=2.3525,
                    availability="busy",
                    is_active=True,
                ),
            ]
        )
        db.session.commit()
        admin_id = ops_admin.id

    _login_admin(client, app, _admin_stub(admin_id))
    response = client.get("/admin/api/risk-map?city=Paris")
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["status"] == "ok"
    assert isinstance(payload["items"], list)
    assert payload["default_center"] == {"lat": 46.603354, "lng": 1.888334, "zoom": 6}
    assert payload["generated_at"]
    assert payload["territorial_contract"]["version"] == "pilotage-v1"
    assert len(payload["territories"]) == 1

    territory = payload["territories"][0]
    assert territory["city"] == "Paris"
    assert territory["active_requests"] == 1
    assert territory["critical_requests"] == 1
    assert territory["unassigned_requests"] == 1
    assert territory["stale_requests"] == 1
    assert territory["available_intervenants"] == 2
    assert territory["overloaded_intervenants"] == 1
    assert territory["partner_coverage"] == 1
    assert territory["risk_level"] == "high"
    assert territory["status_label"]
    assert territory["recommended_action"]


def test_admin_risk_map_territorial_contract_respects_tenant_scope(client, app):
    with app.app_context():
        structure_a = _make_structure(name="Risk Scope Alpha", slug="risk-scope-alpha")
        structure_b = _make_structure(name="Risk Scope Beta", slug="risk-scope-beta")
        ops_admin = _make_ops_admin(
            username="risk_scope_ops",
            email="risk_scope_ops@test.local",
            structure_id=structure_a.id,
        )
        user_a = User(
            username="risk_scope_user_a",
            email="risk_scope_user_a@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
            structure_id=structure_a.id,
        )
        user_b = User(
            username="risk_scope_user_b",
            email="risk_scope_user_b@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
            structure_id=structure_b.id,
        )
        db.session.add_all([user_a, user_b])
        db.session.flush()

        req_a = _make_request(
            title="scope alpha request",
            user_id=user_a.id,
            structure_id=structure_a.id,
            city="Paris",
            latitude=48.8566,
            longitude=2.3522,
            risk_score=80,
        )
        req_b = _make_request(
            title="scope beta request",
            user_id=user_b.id,
            structure_id=structure_b.id,
            city="Lyon",
            latitude=45.7640,
            longitude=4.8357,
            risk_score=90,
        )
        db.session.add_all(
            [
                Case(
                    request_id=req_a.id,
                    structure_id=structure_a.id,
                    status="new",
                    risk_score=80,
                    latitude=48.8566,
                    longitude=2.3522,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                ),
                Case(
                    request_id=req_b.id,
                    structure_id=structure_b.id,
                    status="new",
                    risk_score=90,
                    latitude=45.7640,
                    longitude=4.8357,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                ),
                Intervenant(
                    structure_id=structure_a.id,
                    name="Alpha Worker",
                    actor_type="social_worker",
                    email="alpha-worker@test.local",
                    location="Paris || 10 Rue A",
                    latitude=48.8567,
                    longitude=2.3523,
                    availability="available",
                    is_active=True,
                ),
                Intervenant(
                    structure_id=structure_b.id,
                    name="Beta Worker",
                    actor_type="social_worker",
                    email="beta-worker@test.local",
                    location="Lyon || 10 Rue B",
                    latitude=45.7641,
                    longitude=4.8358,
                    availability="available",
                    is_active=True,
                ),
            ]
        )
        db.session.commit()
        admin_id = ops_admin.id

    _login_admin(client, app, _admin_stub(admin_id))
    response = client.get("/admin/api/risk-map")
    assert response.status_code == 200
    payload = response.get_json()

    visible_cities = {row["city"] for row in payload["territories"]}
    assert "Paris" in visible_cities
    assert "Lyon" not in visible_cities


def test_admin_risk_map_city_variants_keep_professional_coverage(client, app):
    with app.app_context():
        structure = _make_structure(name="Risk Variant Alpha", slug="risk-variant-alpha")
        ops_admin = _make_ops_admin(
            username="risk_variant_ops",
            email="risk_variant_ops@test.local",
            structure_id=structure.id,
        )
        user = User(
            username="risk_variant_user",
            email="risk_variant_user@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
            structure_id=structure.id,
        )
        db.session.add(user)
        db.session.flush()

        req = _make_request(
            title="variant city request",
            user_id=user.id,
            structure_id=structure.id,
            city="Saint-Denis",
            latitude=48.9362,
            longitude=2.3574,
            risk_score=60,
        )
        db.session.add(
            Case(
                request_id=req.id,
                structure_id=structure.id,
                status="in_progress",
                risk_score=60,
                latitude=48.9362,
                longitude=2.3574,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        db.session.add(
            Intervenant(
                structure_id=structure.id,
                name="Variant Worker",
                actor_type="social_worker",
                email="variant-worker@test.local",
                location="Saint Denis || 10 Rue A",
                latitude=48.9363,
                longitude=2.3575,
                availability="available",
                is_active=True,
            )
        )
        db.session.commit()
        admin_id = ops_admin.id

    _login_admin(client, app, _admin_stub(admin_id))
    response = client.get("/admin/api/risk-map?city=Saint-Denis")
    assert response.status_code == 200
    payload = response.get_json()

    assert len(payload["territories"]) == 1
    territory = payload["territories"][0]
    assert territory["city"] == "Saint-Denis"
    assert territory["available_intervenants"] == 1


def test_admin_risk_map_returns_empty_territories_when_no_visible_data(client, app):
    with app.app_context():
        structure = _make_structure(name="Risk Empty Alpha", slug="risk-empty-alpha")
        ops_admin = _make_ops_admin(
            username="risk_empty_ops",
            email="risk_empty_ops@test.local",
            structure_id=structure.id,
        )
        db.session.commit()
        admin_id = ops_admin.id

    _login_admin(client, app, _admin_stub(admin_id))
    response = client.get("/admin/api/risk-map")
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["items"] == []
    assert payload["territories"] == []
