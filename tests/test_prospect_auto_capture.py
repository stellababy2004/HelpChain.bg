import json
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from flask import render_template
from flask import session as flask_session

from backend.helpchain_backend.src.models import (
    AdminUser,
    OrganizationAccessRequest,
    ProfessionalLead,
)
from backend.helpchain_backend.src.services.prospect_auto_capture import (
    attach_session_intelligence_to_professional_lead,
    extract_audience_context,
    notes_without_audience_context,
)
from backend.models_with_analytics import AnalyticsEvent

PUBLIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Forwarded-For": "203.0.113.25",
}


def _post_access_request(client, suffix="capture"):
    return client.post(
        "/demander-acces",
        data={
            "organization_name": f"CCAS Auto {suffix}",
            "contact_name": "Marie Dupont",
            "email": f"marie.{suffix}@ccas-auto.test",
            "phone": "01 02 03 04 05",
            "city": "Boulogne-Billancourt",
            "org_type": "CCAS",
            "estimated_users": "12",
            "message": "Besoin de qualifier un deploiement HelpChain.",
        },
        follow_redirects=False,
    )


def _audience_payload(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    payload = soup.select_one("#audienceMapPayload")
    assert payload is not None
    return json.loads(payload.get_text())


def _login_superadmin(client, session, suffix="prospect"):
    admin = AdminUser(
        username=f"prospect_admin_{suffix}",
        email=f"prospect-admin-{suffix}@test.local",
        password_hash="x",
        role="superadmin",
        is_active=True,
    )
    session.add(admin)
    session.commit()

    with client.session_transaction() as flask_session:
        flask_session["_user_id"] = str(admin.id)
        flask_session["user_id"] = admin.id
        flask_session["role"] = admin.role
        flask_session["is_authenticated"] = True
        flask_session["is_admin"] = True
        flask_session["admin_logged_in"] = True
        flask_session["admin_id"] = admin.id
    return admin


def test_access_request_auto_captures_prior_audience_session(client, session):
    client.get(
        "/offre",
        headers={**PUBLIC_HEADERS, "Referer": "https://www.google.fr/search?q=helpchain"},
    )
    client.get("/deploiement", headers=PUBLIC_HEADERS)
    client.get("/demander-acces", headers=PUBLIC_HEADERS)

    response = _post_access_request(client, "linked")

    assert response.status_code == 303
    row = OrganizationAccessRequest.query.one()
    context = extract_audience_context(row.internal_notes)

    assert context is not None
    assert context["session_id"].startswith("aud_")
    assert context["score"] >= 25
    assert context["temperature"] == "Tres chaud"
    assert context["source"] == "Google"
    assert context["page_count"] == 3
    assert "/offre" in context["pages_viewed"]
    assert "/deploiement" in context["pages_viewed"]
    assert "/demander-acces" in context["pages_viewed"]
    assert context["first_seen_at"]
    assert context["last_seen_at"]
    assert context["intent_flags"]["visited_offre"] is True
    assert context["lead_intent_tier"] == "operationally_interested"
    assert context["primary_interest"] == "institutional_fit"
    assert context["institutional_intent"]["recommended_action"] == "Invite toward pilot framing"
    assert context["territorial_intelligence"]["territory"] == "Boulogne-Billancourt"
    assert context["territorial_intelligence"]["confidence"] == "strong"
    assert context["territorial_intelligence"]["recommended_action"] in {
        "Pilot discussion opportunity",
        "Prioritize founder outreach this week",
        "Confirm institutional fit and pilot perimeter",
    }


def test_access_request_without_prior_session_still_succeeds(client):
    response = _post_access_request(client, "nolink")

    assert response.status_code == 303
    row = OrganizationAccessRequest.query.one()
    assert extract_audience_context(row.internal_notes) is None


def test_professional_lead_can_receive_session_intelligence(client, session):
    session.add(
        AnalyticsEvent(
            event_type="page_view",
            user_session="aud_professional_test",
            page_url="/professionnels",
            referrer="https://chat.openai.com/",
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    lead = ProfessionalLead(
        email="pro.capture@test.local",
        city="Paris",
        profession="Coordinatrice",
        status="new",
    )
    session.add(lead)
    session.flush()

    with client.application.test_request_context("/"):
        flask_session["hc_audience_sid"] = "aud_professional_test"
        summary = attach_session_intelligence_to_professional_lead(lead)
    session.commit()
    session.expire_all()

    lead = (
        session.query(ProfessionalLead)
        .filter_by(email="pro.capture@test.local")
        .order_by(ProfessionalLead.id.desc())
        .first()
    )

    if lead is None:
        lead = session.query(ProfessionalLead).order_by(ProfessionalLead.id.desc()).first()
    assert lead is not None

    context = extract_audience_context(lead.notes)
    assert summary is not None
    assert context["session_id"] == "aud_professional_test"
    assert context["source"] == "ChatGPT"
    assert "/professionnels" in context["pages_viewed"]
    assert context["institutional_intent"]["primary_interest"] == "institutional_fit"
    assert context["territorial_intelligence"]["territory"] == "Paris"


def test_access_request_detail_renders_captured_audience(app, client):
    client.get(
        "/offre",
        headers={**PUBLIC_HEADERS, "Referer": "https://www.google.fr/search?q=helpchain"},
    )
    client.get("/demander-acces", headers=PUBLIC_HEADERS)
    _post_access_request(client, "detail")
    row = OrganizationAccessRequest.query.one()

    with app.test_request_context(f"/admin/organizations/requests/{row.id}"):
        html = render_template(
            "admin/organization_access_request_detail.html",
            access_request=row,
            audience_context=extract_audience_context(row.internal_notes),
            review_notes=notes_without_audience_context(row.internal_notes),
            credentials=None,
        )

    assert "Audience avant conversion" in html
    assert "Score radar" in html
    assert "Google" in html
    assert "/offre" in html


def test_revenue_radar_marks_captured_access_request(client, app):
    from backend.helpchain_backend.src.routes.admin import _build_audience_map_context

    client.get(
        "/offre",
        headers={**PUBLIC_HEADERS, "Referer": "https://www.linkedin.com/company/helpchain"},
    )
    client.get("/deploiement", headers=PUBLIC_HEADERS)
    client.get("/demander-acces", headers=PUBLIC_HEADERS)
    _post_access_request(client, "radar")

    with app.app_context():
        payload = _build_audience_map_context()
    revenue_rows = payload["revenue_radar_rows"]

    assert revenue_rows

    captured_row = next(
        row
        for row in revenue_rows
        if row["source"] == "LinkedIn"
        and "demande d'acces" in row["captured_label"].lower()
    )

    assert captured_row["session"].startswith("aud_")
    assert int(captured_row["pages_count"]) >= 3
    assert captured_row["temperature"] == "Tres chaud"
    assert any(
        repeat_row["label"] == captured_row["session"]
        for repeat_row in payload["repeat_rows"]
    )
