from datetime import UTC, datetime, timedelta

from backend.helpchain_backend.src.models import (
    OrganizationAccessRequest,
    ProfessionalLead,
)
from backend.helpchain_backend.src.services.prospect_auto_capture import (
    append_audience_context_to_notes,
)


def test_admin_revenue_requires_admin(client):
    response = client.get("/admin/revenue", follow_redirects=False)

    assert response.status_code != 200


def test_admin_revenue_empty_state_safe(authenticated_admin_client):
    response = authenticated_admin_client.get("/admin/revenue")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Revenue Control Center" in html
    assert "No revenue signals yet" in html
    assert "Weighted Pipeline" in html


def test_admin_revenue_unified_rows_display(authenticated_admin_client, session):
    lead = ProfessionalLead(
        email="director@example.org",
        full_name="Claire Martin",
        city="Boulogne-Billancourt",
        profession="Directrice",
        organization="Association Horizon",
        source="professionnels",
        status="qualified",
        created_at=datetime.now(UTC),
    )
    access_request = OrganizationAccessRequest(
        organization_name="CCAS Revenue",
        contact_name="Marie Dupont",
        email="marie.dupont@example.org",
        city="Nanterre",
        org_type="CCAS",
        estimated_users=12,
        status="new",
        created_at=datetime.now(UTC),
    )
    session.add_all([lead, access_request])
    session.commit()

    response = authenticated_admin_client.get("/admin/revenue")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Association Horizon" in html
    assert "CCAS Revenue" in html
    assert "Access Request" in html
    assert "Lead" in html
    assert "Revenue Forecast" in html


def test_admin_revenue_filters_safe(authenticated_admin_client, session):
    session.add(
        OrganizationAccessRequest(
            organization_name="Centre Social Filtre",
            contact_name="Amine Leroy",
            email="amine@example.org",
            city="Paris",
            org_type="Centre social",
            status="need_info",
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = authenticated_admin_client.get(
        "/admin/revenue?type=access_request&stage=qualified&city=Paris&score_bucket=hot&followup=none&q=Centre"
    )

    assert response.status_code == 200
    assert "Revenue Control Center" in response.get_data(as_text=True)


def test_admin_revenue_quick_action_sets_followup(authenticated_admin_client, session):
    lead = ProfessionalLead(
        email="followup@example.org",
        full_name="Follow Up",
        city="Paris",
        profession="Coordinateur",
        status="new",
        created_at=datetime.now(UTC),
    )
    session.add(lead)
    session.commit()

    response = authenticated_admin_client.post(
        f"/admin/revenue/professional_lead/{lead.id}/quick-action",
        data={"action": "tomorrow"},
        follow_redirects=False,
    )
    session.refresh(lead)

    assert response.status_code in (302, 303)
    assert lead.next_action_at is not None
    assert "Follow up tomorrow" in (lead.next_action_note or "")


def test_admin_revenue_no_crash_if_telemetry_absent(
    authenticated_admin_client, session, monkeypatch
):
    from backend.helpchain_backend.src.routes import admin as admin_routes

    original_table_exists = admin_routes._table_exists

    def fake_table_exists(table_name):
        if table_name in {"analytics_events", "user_behaviors"}:
            return False
        return original_table_exists(table_name)

    monkeypatch.setattr(admin_routes, "_table_exists", fake_table_exists)
    session.add(
        ProfessionalLead(
            email="telemetry-safe@example.org",
            full_name="Telemetry Safe",
            city="Paris",
            profession="Directrice",
            status="contacted",
            contacted_at=datetime.now(UTC) - timedelta(days=1),
            created_at=datetime.now(UTC) - timedelta(days=2),
        )
    )
    session.commit()

    response = authenticated_admin_client.get("/admin/revenue")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Telemetry Safe" in html
    assert "No hot anonymous sessions available" in html


def test_admin_revenue_uses_institutional_intent_recommendation(
    authenticated_admin_client, session
):
    lead = ProfessionalLead(
        email="intent-revenue@example.org",
        full_name="Intent Revenue",
        city="Paris",
        profession="Directrice",
        status="qualified",
        notes=append_audience_context_to_notes(
            None,
            {
                "score": 24,
                "temperature": "Chaud",
                "institutional_intent": {
                    "score": 169,
                    "tier": "pilot_ready",
                    "label": "Pilot-ready",
                    "primary_interest": "deployment_operations",
                    "trust_friction_detected": False,
                    "friction_reason": None,
                    "top_paths": ["/demander-acces", "/deploiement", "/offre"],
                    "recommended_action": "Propose a structured pilot conversation",
                },
                "lead_intent_score": 169,
                "lead_intent_tier": "pilot_ready",
                "lead_intent_label": "Pilot-ready",
                "recommended_action": "Propose a structured pilot conversation",
                "territorial_intelligence": {
                    "territory": "Paris",
                    "priority_level": "High",
                    "confidence": "strong",
                    "dominant_interest": "deployment_operations",
                    "possible_friction": None,
                    "pilot_readiness_estimate": "elevated",
                    "recommended_action": "Pilot discussion opportunity",
                },
            },
        ),
        created_at=datetime.now(UTC),
    )
    session.add(lead)
    session.commit()

    response = authenticated_admin_client.get("/admin/revenue")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Pilot-ready audience" in html
    assert "Propose a structured pilot conversation" in html
