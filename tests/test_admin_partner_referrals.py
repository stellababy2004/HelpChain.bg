from __future__ import annotations

from datetime import timedelta

from backend.models import (
    AdminUser,
    CaseReferral,
    OrganizationConnection,
    ReferralActivity,
    Request,
    Structure,
    User,
    db,
    default_referral_shared_scope,
    utc_now,
)


def _admin(username: str, structure_id: int | None, role: str = "admin") -> AdminUser:
    admin = AdminUser(
        username=username,
        email=f"{username}@helpchain.local",
        password_hash="x",
        role=role,
        is_active=True,
        structure_id=structure_id,
        mfa_enabled=True,
        totp_secret="test",
    )
    db.session.add(admin)
    db.session.flush()
    return admin


def _login(client, app, admin: AdminUser) -> None:
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(admin.id)
        session["user_id"] = admin.id
        session["role"] = admin.role
        session["is_authenticated"] = True
        session["is_admin"] = True
        session["admin_logged_in"] = True
        session["admin_id"] = admin.id
        session["admin_user_id"] = admin.id
        session[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        session["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        session["admin_mfa_last_verified"] = 4102444800
        session["admin_mfa_user_id"] = admin.id


def _seed_referral_context(active_connection: bool = True):
    a = Structure(name="Structure A", slug="structure-a", status="active")
    b = Structure(name="Structure B", slug="structure-b", status="active")
    c = Structure(name="Structure C", slug="structure-c", status="active")
    db.session.add_all([a, b, c])
    db.session.flush()

    admin_a = _admin("admin_a_referrals", a.id)
    admin_b = _admin("admin_b_referrals", b.id)
    admin_c = _admin("admin_c_referrals", c.id)
    superadmin = _admin("super_referrals", None, role="superadmin")

    requester = User(
        username="requester_referrals",
        email="requester_referrals@helpchain.local",
        password_hash="x",
        role="requester",
        is_active=True,
        structure_id=a.id,
    )
    db.session.add(requester)
    db.session.flush()

    source_request = Request(
        title="Aide alimentaire",
        description="Besoin d'un relais social.",
        name="Identité privée",
        email="private@example.test",
        phone="0102030405",
        status="pending",
        priority="high",
        category="food",
        structure_id=a.id,
        user_id=requester.id,
        risk_level="attention",
        risk_score=62,
    )
    db.session.add(source_request)
    if active_connection:
        db.session.add(
            OrganizationConnection(
                source_structure_id=a.id,
                target_structure_id=b.id,
                status="active",
                connection_type="referral",
                created_by_admin_id=superadmin.id,
                created_at=utc_now(),
                accepted_at=utc_now(),
            )
        )
    db.session.commit()
    return {
        "a": a,
        "b": b,
        "c": c,
        "admin_a": admin_a,
        "admin_b": admin_b,
        "admin_c": admin_c,
        "superadmin": superadmin,
        "request": source_request,
    }


def _send_referral(client, app, ctx):
    _login(client, app, ctx["admin_a"])
    response = client.post(
        f"/admin/requests/{ctx['request'].id}/refer",
        data={
            "to_structure_id": str(ctx["b"].id),
            "reason": "Besoin d'un partenaire spécialisé",
            "message": "Résumé strictement partagé",
            "share_category": "1",
            "share_priority": "1",
            "share_summary": "1",
            "share_risk_level": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return CaseReferral.query.order_by(CaseReferral.id.desc()).first()


def _create_partner_connection(client, app, ctx):
    _login(client, app, ctx["admin_a"])
    response = client.post(
        "/admin/referrals/partners/new",
        data={"target_structure_id": str(ctx["b"].id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return OrganizationConnection.query.order_by(OrganizationConnection.id.desc()).first()


def test_partners_page_loads(client, app):
    ctx = _seed_referral_context()
    _login(client, app, ctx["admin_a"])

    response = client.get("/admin/referrals/partners")

    assert response.status_code == 200
    assert "Partenaires".encode("utf-8") in response.data


def test_structure_a_can_create_pending_partner_connection_to_b(client, app):
    ctx = _seed_referral_context(active_connection=False)

    connection = _create_partner_connection(client, app, ctx)

    assert connection.source_structure_id == ctx["a"].id
    assert connection.target_structure_id == ctx["b"].id
    assert connection.status == "pending"
    assert connection.permissions_json["can_send_referrals"] is True
    assert connection.permissions_json["can_share_documents"] is False


def test_duplicate_active_or_pending_connection_is_blocked(client, app):
    ctx = _seed_referral_context(active_connection=False)
    first = _create_partner_connection(client, app, ctx)

    _login(client, app, ctx["admin_a"])
    response = client.post(
        "/admin/referrals/partners/new",
        data={"target_structure_id": str(ctx["b"].id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert OrganizationConnection.query.count() == 1
    assert OrganizationConnection.query.first().id == first.id


def test_structure_b_can_accept_pending_partner_connection(client, app):
    ctx = _seed_referral_context(active_connection=False)
    connection = _create_partner_connection(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])

    response = b_client.post(
        f"/admin/referrals/partners/{connection.id}/accept",
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(connection)
    assert connection.status == "active"
    assert connection.accepted_at is not None


def test_structure_b_can_refuse_pending_partner_connection(client, app):
    ctx = _seed_referral_context(active_connection=False)
    connection = _create_partner_connection(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])

    response = b_client.post(
        f"/admin/referrals/partners/{connection.id}/refuse",
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(connection)
    assert connection.status == "revoked"
    assert connection.revoked_at is not None


def test_structure_a_or_b_can_suspend_and_revoke_active_connection(client, app):
    ctx = _seed_referral_context()
    connection = OrganizationConnection.query.first()
    _login(client, app, ctx["admin_a"])

    response = client.post(
        f"/admin/referrals/partners/{connection.id}/suspend",
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(connection)
    assert connection.status == "suspended"

    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    response = b_client.post(
        f"/admin/referrals/partners/{connection.id}/revoke",
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(connection)
    assert connection.status == "revoked"
    assert connection.revoked_at is not None


def test_unrelated_structure_cannot_see_or_act_on_partner_connection(client, app):
    ctx = _seed_referral_context()
    connection = OrganizationConnection.query.first()
    c_client = app.test_client()
    _login(c_client, app, ctx["admin_c"])

    response = c_client.get("/admin/referrals/partners")
    assert response.status_code == 200
    assert "Structure A".encode("utf-8") not in response.data

    response = c_client.post(
        f"/admin/referrals/partners/{connection.id}/suspend",
        follow_redirects=False,
    )
    assert response.status_code == 403
    db.session.refresh(connection)
    assert connection.status == "active"


def test_refer_page_shows_no_partner_empty_state_without_active_connection(client, app):
    ctx = _seed_referral_context(active_connection=False)
    _login(client, app, ctx["admin_a"])

    response = client.get(f"/admin/requests/{ctx['request'].id}/refer")

    assert response.status_code == 200
    assert "Aucune structure partenaire active".encode("utf-8") in response.data
    assert "Configurer une structure partenaire".encode("utf-8") in response.data


def test_refer_page_shows_target_partner_choices_when_active_connection_exists(client, app):
    ctx = _seed_referral_context()
    _login(client, app, ctx["admin_a"])

    response = client.get(f"/admin/requests/{ctx['request'].id}/refer")

    assert response.status_code == 200
    assert "Structure B".encode("utf-8") in response.data


def test_sending_referral_from_structure_a_to_b(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)

    assert referral is not None
    assert referral.from_structure_id == ctx["a"].id
    assert referral.to_structure_id == ctx["b"].id
    assert referral.status == "sent"
    assert referral.reason == "Besoin d'un partenaire spécialisé"


def test_structure_b_can_see_received_referral(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])

    response = b_client.get("/admin/referrals/received")

    assert response.status_code == 200
    assert f"#{referral.id}".encode() in response.data


def test_structure_a_can_see_sent_referral(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    _login(client, app, ctx["admin_a"])

    response = client.get("/admin/referrals/sent")

    assert response.status_code == 200
    assert f"#{referral.id}".encode() in response.data


def test_unrelated_structure_cannot_view_referral(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    c_client = app.test_client()
    _login(c_client, app, ctx["admin_c"])

    response = c_client.get(f"/admin/referrals/{referral.id}")

    assert response.status_code == 403


def test_structure_b_can_accept_and_local_request_is_minimal(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])

    response = b_client.post(f"/admin/referrals/{referral.id}/accept", follow_redirects=False)

    assert response.status_code == 303
    db.session.refresh(referral)
    assert referral.status == "accepted"
    assert referral.operational_status == "accepted"
    assert referral.accepted_at is not None
    local_request = Request.query.filter_by(
        structure_id=ctx["b"].id,
        source_channel="partner_referral",
    ).first()
    assert local_request is not None
    assert local_request.name is None
    assert local_request.email is None
    assert local_request.phone is None
    assert "orientation partenaire" in (local_request.message or "").lower()


def test_receiving_structure_can_mark_in_progress_and_completed(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    b_client.post(f"/admin/referrals/{referral.id}/accept")

    response = b_client.post(
        f"/admin/referrals/{referral.id}/mark-in-progress",
        data={"public_status_note": "Le dossier est actuellement traité par la structure partenaire."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(referral)
    assert referral.operational_status == "in_progress"
    assert referral.in_progress_at is not None
    assert referral.last_public_update_at is not None
    assert "structure partenaire" in referral.public_status_note

    response = b_client.post(
        f"/admin/referrals/{referral.id}/mark-completed",
        data={"public_status_note": "Traitement opérationnel clôturé."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(referral)
    assert referral.operational_status == "completed"
    assert referral.status == "completed"
    assert referral.completed_at is not None


def test_source_structure_cannot_update_operational_status(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    b_client.post(f"/admin/referrals/{referral.id}/accept")

    _login(client, app, ctx["admin_a"])
    response = client.post(
        f"/admin/referrals/{referral.id}/mark-in-progress",
        data={"public_status_note": "Tentative source"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    db.session.refresh(referral)
    assert referral.operational_status == "accepted"


def test_operational_transition_creates_audit_activity(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    b_client.post(f"/admin/referrals/{referral.id}/accept")
    b_client.post(f"/admin/referrals/{referral.id}/mark-in-progress")
    b_client.post(f"/admin/referrals/{referral.id}/mark-completed")

    actions = {
        row.action
        for row in ReferralActivity.query.filter_by(referral_id=referral.id).all()
    }
    assert {"accepted", "in_progress", "completed"}.issubset(actions)


def test_public_status_visible_without_private_request_fields(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    b_client.post(f"/admin/referrals/{referral.id}/accept")
    b_client.post(
        f"/admin/referrals/{referral.id}/mark-in-progress",
        data={"public_status_note": "Le dossier est actuellement traité par la structure partenaire."},
    )

    _login(client, app, ctx["admin_a"])
    response = client.get(f"/admin/referrals/{referral.id}")

    assert response.status_code == 200
    assert "Prise en charge".encode("utf-8") in response.data
    assert "Le dossier est actuellement".encode("utf-8") in response.data
    assert "private@example.test".encode("utf-8") not in response.data
    assert "0102030405".encode("utf-8") not in response.data
    assert "Identité privée".encode("utf-8") not in response.data


def test_receiving_structure_can_add_public_note(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    b_client.post(f"/admin/referrals/{referral.id}/accept")

    response = b_client.post(
        f"/admin/referrals/{referral.id}/public-note",
        data={"public_status_note": "Premier retour public de coordination."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(referral)
    assert referral.public_status_note == "Premier retour public de coordination."
    assert referral.last_public_update_at is not None


def test_structure_b_can_refuse(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])

    response = b_client.post(
        f"/admin/referrals/{referral.id}/refuse",
        data={"refusal_reason": "Hors périmètre"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.session.refresh(referral)
    assert referral.status == "refused"
    assert referral.refusal_reason == "Hors périmètre"


def test_structure_a_can_cancel_before_acceptance(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)
    _login(client, app, ctx["admin_a"])

    response = client.post(f"/admin/referrals/{referral.id}/cancel", follow_redirects=False)

    assert response.status_code == 303
    db.session.refresh(referral)
    assert referral.status == "cancelled"


def test_default_shared_scope_keeps_private_fields_disabled():
    scope = default_referral_shared_scope()

    assert scope["share_identity"] is False
    assert scope["share_contact"] is False
    assert scope["share_internal_notes"] is False
    assert scope["share_documents"] is False
    assert scope["share_summary"] is True


def test_audit_activity_created_for_major_actions(client, app):
    ctx = _seed_referral_context()
    referral = _send_referral(client, app, ctx)

    actions = {
        row.action
        for row in ReferralActivity.query.filter_by(referral_id=referral.id).all()
    }
    assert {"created", "sent"}.issubset(actions)

    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    b_client.post(f"/admin/referrals/{referral.id}/accept")
    actions = {
        row.action
        for row in ReferralActivity.query.filter_by(referral_id=referral.id).all()
    }
    assert "accepted" in actions

    referral_refused = _send_referral(client, app, ctx)
    b_client = app.test_client()
    _login(b_client, app, ctx["admin_b"])
    b_client.post(f"/admin/referrals/{referral_refused.id}/refuse")
    actions = {
        row.action
        for row in ReferralActivity.query.filter_by(referral_id=referral_refused.id).all()
    }
    assert "refused" in actions

    referral_cancelled = _send_referral(client, app, ctx)
    _login(client, app, ctx["admin_a"])
    client.post(f"/admin/referrals/{referral_cancelled.id}/cancel")
    actions = {
        row.action
        for row in ReferralActivity.query.filter_by(referral_id=referral_cancelled.id).all()
    }
    assert "cancelled" in actions


def test_existing_admin_request_page_still_loads(client, app):
    ctx = _seed_referral_context()
    _login(client, app, ctx["admin_a"])

    response = client.get(f"/admin/requests/{ctx['request'].id}")

    assert response.status_code == 200
    assert b"Engager une orientation partenaire" in response.data
