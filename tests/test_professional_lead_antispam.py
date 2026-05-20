import re
from unittest.mock import MagicMock

from backend.helpchain_backend.src.models import ProfessionalLead


def _contact_payload(**overrides):
    payload = {
        "full_name": "Marie Martin",
        "fonction": "Coordinatrice",
        "structure": "CCAS Paris Centre",
        "structure_type": "CCAS / CIAS",
        "city": "Paris",
        "email": "marie.martin@collectivite.fr",
        "phone": "+33 6 12 34 56 78",
        "objet_echange": "Phase pilote",
        "message": "Besoin d'un cadrage sur la coordination territoriale.",
    }
    payload.update(overrides)
    return payload


def _pilot_payload(**overrides):
    payload = {
        "profession": "Psychologue",
        "email": "pilot@collectivite.fr",
        "full_name": "Marie Martin",
        "phone": "+33 6 12 34 56 78",
        "city": "Paris",
        "organization": "Cabinet Martin",
        "availability": "2h/semaine",
        "message": "Disponible pour la phase pilote.",
    }
    payload.update(overrides)
    return payload


def _extract_demo_kpi_counts(html: str) -> dict[str, int]:
    matches = re.findall(
        r'hc-demo-leads-kpi__label">([^<]+)</span>\s*<strong class="hc-demo-leads-kpi__value">(\d+)</strong>',
        html,
    )
    return {label.strip(): int(value) for label, value in matches}


def _assert_contact_submission_status(response) -> None:
    assert response.status_code in (200, 303, 502)
    if response.status_code == 502:
        html = response.get_data(as_text=True)
        assert "Votre demande a bien été enregistrée" in html


def test_contact_honeypot_submission_is_saved_as_screened_spam(
    client,
    db_session,
    monkeypatch,
):
    mock_send = MagicMock()
    monkeypatch.setattr("backend.mail_service.send_notification_email", mock_send)

    response = client.post(
        "/contact",
        data=_contact_payload(website="https://spam.example"),
    )

    assert response.status_code == 200
    lead = ProfessionalLead.query.one()
    assert lead.status == "spam"
    assert "honeypot:website" in (lead.notes or "")
    assert mock_send.call_count == 0


def test_contact_message_only_dummy_signal_does_not_invalidate_lead(
    client,
    db_session,
    monkeypatch,
):
    mock_send = MagicMock(return_value=True)
    monkeypatch.setattr("backend.mail_service.send_notification_email", mock_send)

    response = client.post("/contact", data=_contact_payload(message="test"))

    _assert_contact_submission_status(response)
    lead = ProfessionalLead.query.one()
    assert lead.status == "new"
    assert "dummy_fields:message" not in (lead.notes or "")
    assert mock_send.call_count == 1


def test_contact_mailinator_submission_is_saved_as_spam_without_notification(
    client,
    db_session,
    monkeypatch,
):
    mock_send = MagicMock()
    monkeypatch.setattr("backend.mail_service.send_notification_email", mock_send)

    response = client.post(
        "/contact",
        data=_contact_payload(email="robot@mailinator.com"),
    )

    _assert_contact_submission_status(response)
    lead = ProfessionalLead.query.one()
    assert lead.status == "spam"
    assert "mailinator.com" in (lead.notes or "")
    assert mock_send.call_count == 0


def test_contact_obviously_invalid_phone_is_saved_as_invalid(
    client,
    db_session,
    monkeypatch,
):
    mock_send = MagicMock()
    monkeypatch.setattr("backend.mail_service.send_notification_email", mock_send)

    response = client.post(
        "/contact",
        data=_contact_payload(phone="abc123"),
    )

    _assert_contact_submission_status(response)
    lead = ProfessionalLead.query.one()
    assert lead.status == "invalid"
    assert "phone:invalid" in (lead.notes or "")
    assert mock_send.call_count == 0


def test_contact_zaproxy_signal_is_saved_as_spam(client, db_session, monkeypatch):
    mock_send = MagicMock()
    monkeypatch.setattr("backend.mail_service.send_notification_email", mock_send)

    response = client.post(
        "/contact",
        data=_contact_payload(),
        headers={"User-Agent": "Mozilla/5.0 ZAPROXY"},
    )

    _assert_contact_submission_status(response)
    lead = ProfessionalLead.query.one()
    assert lead.status == "spam"
    assert "marker:zap,zaproxy" in (lead.notes or "")
    assert mock_send.call_count == 0


def test_admin_professional_leads_hide_screened_statuses_by_default(
    authenticated_admin_client,
    db_session,
):
    db_session.add_all(
        [
            ProfessionalLead(
                email="good@collectivite.fr",
                full_name="Good Lead",
                profession="Coordinatrice",
                source="contact_echange",
                status="new",
            ),
            ProfessionalLead(
                email="bad@example.com",
                full_name="Invalid Lead",
                profession="Coordinatrice",
                source="contact_echange",
                status="invalid",
            ),
            ProfessionalLead(
                email="spam@mailinator.com",
                full_name="Spam Lead",
                profession="Coordinatrice",
                source="demo_page",
                status="spam",
            ),
        ]
    )
    db_session.commit()

    response = authenticated_admin_client.get("/admin/professional-leads")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "good@collectivite.fr" in html
    assert "bad@example.com" not in html
    assert "spam@mailinator.com" not in html

    spam_response = authenticated_admin_client.get("/admin/professional-leads?status=spam")
    spam_html = spam_response.get_data(as_text=True)
    assert spam_response.status_code == 200
    assert "spam@mailinator.com" in spam_html

    invalid_response = authenticated_admin_client.get("/admin/professional-leads?status=invalid")
    invalid_html = invalid_response.get_data(as_text=True)
    assert invalid_response.status_code == 200
    assert "bad@example.com" in invalid_html


def test_admin_professional_lead_badges_render_for_screened_statuses(
    authenticated_admin_client,
    db_session,
):
    spam_lead = ProfessionalLead(
        email="badge-spam@mailinator.com",
        full_name="Spam Badge",
        profession="Coordinatrice",
        source="contact_echange",
        status="spam",
    )
    invalid_lead = ProfessionalLead(
        email="badge-invalid@example.com",
        full_name="Invalid Badge",
        profession="Coordinatrice",
        source="contact_echange",
        status="invalid",
    )
    db_session.add_all([spam_lead, invalid_lead])
    db_session.commit()

    spam_html = authenticated_admin_client.get(
        "/admin/professional-leads?status=spam"
    ).get_data(as_text=True)
    invalid_html = authenticated_admin_client.get(
        "/admin/professional-leads?status=invalid"
    ).get_data(as_text=True)
    detail_html = authenticated_admin_client.get(
        f"/admin/professional-leads/{invalid_lead.id}"
    ).get_data(as_text=True)

    assert 'badge text-bg-danger">spam<' in spam_html
    assert 'badge text-bg-warning text-dark">invalid<' in invalid_html
    assert 'badge text-bg-warning text-dark">invalid<' in detail_html


def test_admin_demo_leads_hide_screened_statuses_by_default(
    authenticated_admin_client,
    db_session,
):
    db_session.add_all(
        [
            ProfessionalLead(
                email="demo.good@collectivite.fr",
                full_name="Demo Good",
                profession="Coordinatrice",
                source="demo_page",
                status="new",
            ),
            ProfessionalLead(
                email="demo.spam@mailinator.com",
                full_name="Demo Spam",
                profession="Coordinatrice",
                source="demo_page",
                status="spam",
            ),
            ProfessionalLead(
                email="demo.invalid@example.com",
                full_name="Demo Invalid",
                profession="Coordinatrice",
                source="demo_page",
                status="invalid",
            ),
        ]
    )
    db_session.commit()

    response = authenticated_admin_client.get("/admin/professional-leads/demo")
    html = response.get_data(as_text=True)
    kpi_counts = _extract_demo_kpi_counts(html)

    assert response.status_code == 200
    assert "demo.good@collectivite.fr" in html
    assert "demo.spam@mailinator.com" not in html
    assert "demo.invalid@example.com" not in html
    assert kpi_counts.get("new", 1) == 1
    assert kpi_counts.get("contacted", 0) == 0
    assert kpi_counts.get("demo_scheduled", 0) == 0
    assert kpi_counts.get("pilot_discussion", 0) == 0
    assert kpi_counts.get("closed", 0) == 0


def test_admin_demo_leads_explicit_screened_filters_remain_auditable(
    authenticated_admin_client,
    db_session,
):
    db_session.add_all(
        [
            ProfessionalLead(
                email="demo.filter.spam@mailinator.com",
                full_name="Demo Filter Spam",
                profession="Coordinatrice",
                source="demo_page",
                status="spam",
            ),
            ProfessionalLead(
                email="demo.filter.invalid@example.com",
                full_name="Demo Filter Invalid",
                profession="Coordinatrice",
                source="demo_page",
                status="invalid",
            ),
        ]
    )
    db_session.commit()

    spam_response = authenticated_admin_client.get("/admin/professional-leads/demo?status=spam")
    invalid_response = authenticated_admin_client.get(
        "/admin/professional-leads/demo?status=invalid"
    )
    spam_html = spam_response.get_data(as_text=True)
    invalid_html = invalid_response.get_data(as_text=True)
    spam_counts = _extract_demo_kpi_counts(spam_html)
    invalid_counts = _extract_demo_kpi_counts(invalid_html)

    assert "demo.filter.spam@mailinator.com" in spam_html
    assert "hc-demo-leads-status-badge--spam" in spam_html
    assert "demo.filter.invalid@example.com" in invalid_html
    assert "hc-demo-leads-status-badge--invalid" in invalid_html
    assert spam_counts.get("new", 0) == 0
    assert invalid_counts.get("new", 0) == 0


def test_screened_lead_detail_can_still_be_updated_to_contacted(
    authenticated_admin_client,
    db_session,
):
    lead = ProfessionalLead(
        email="screened-route@example.com",
        full_name="Screened Route",
        profession="Coordinatrice",
        source="contact_echange",
        status="invalid",
    )
    db_session.add(lead)
    db_session.commit()

    response = authenticated_admin_client.post(
        f"/admin/professional-leads/{lead.id}",
        data={"status": "contacted", "notes": "Reviewed by ops"},
    )

    _assert_contact_submission_status(response)
    db_session.refresh(lead)
    assert lead.status == "contacted"
    assert lead.contacted_at is not None


def test_contact_turnstile_disabled_does_not_block_submission(
    client,
    db_session,
    monkeypatch,
):
    mock_send = MagicMock()
    monkeypatch.setattr("backend.mail_service.send_notification_email", mock_send)

    response = client.post("/contact", data=_contact_payload())

    _assert_contact_submission_status(response)
    assert ProfessionalLead.query.one().status == "new"
    assert mock_send.call_count == 1


def test_contact_turnstile_enabled_and_mocked_success_allows_submission(
    app,
    client,
    db_session,
    monkeypatch,
):
    app.config["HC_TURNSTILE_ENABLED"] = True
    app.config["HC_TURNSTILE_SITE_KEY"] = "site-key"
    app.config["HC_TURNSTILE_SECRET_KEY"] = "secret-key"
    mock_send = MagicMock()
    monkeypatch.setattr("backend.mail_service.send_notification_email", mock_send)
    monkeypatch.setattr(
        "backend.helpchain_backend.src.routes.main._verify_turnstile_token",
        lambda **kwargs: True,
    )

    response = client.post("/contact", data=_contact_payload())

    assert response.status_code in (200, 303)
    assert ProfessionalLead.query.one().status == "new"
    assert mock_send.call_count == 1


def test_contact_turnstile_enabled_and_failed_verification_blocks_submission(
    app,
    client,
    db_session,
    monkeypatch,
):
    app.config["HC_TURNSTILE_ENABLED"] = True
    app.config["HC_TURNSTILE_SITE_KEY"] = "site-key"
    app.config["HC_TURNSTILE_SECRET_KEY"] = "secret-key"
    monkeypatch.setattr(
        "backend.helpchain_backend.src.routes.main._verify_turnstile_token",
        lambda **kwargs: False,
    )

    response = client.post("/contact", data=_contact_payload())

    assert response.status_code == 400
    assert ProfessionalLead.query.count() == 0


def test_turnstile_enabled_adds_csp_allowlist_to_response_headers(app, client):
    app.config["HC_TURNSTILE_ENABLED"] = True
    app.config["HC_TURNSTILE_SITE_KEY"] = "site-key"

    response = client.get("/contact")
    csp = response.headers.get("Content-Security-Policy", "")

    assert response.status_code == 200
    assert "script-src" in csp
    assert "connect-src" in csp
    assert "frame-src" in csp
    assert "https://challenges.cloudflare.com" in csp


def test_professionnels_pilote_turnstile_failure_preserves_values_and_saves_no_lead(
    app,
    client,
    db_session,
    monkeypatch,
):
    app.config["HC_TURNSTILE_ENABLED"] = True
    app.config["HC_TURNSTILE_SITE_KEY"] = "site-key"
    app.config["HC_TURNSTILE_SECRET_KEY"] = "secret-key"
    monkeypatch.setattr(
        "backend.helpchain_backend.src.routes.main._verify_turnstile_token",
        lambda **kwargs: False,
    )

    response = client.post("/professionnels/pilote", data=_pilot_payload())
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "Bot verification failed. Please try again." in html
    assert 'value="Psychologue"' in html
    assert 'value="pilot@collectivite.fr"' in html
    assert 'value="Marie Martin"' in html
    assert 'value="Cabinet Martin"' in html
    assert "Disponible pour la phase pilote." in html
    assert ProfessionalLead.query.count() == 0


def test_public_search_excludes_screened_professional_lead_rows(client, db_session):
    db_session.add_all(
        [
            ProfessionalLead(
                email="search.good@collectivite.fr",
                full_name="Valid Search Lead",
                profession="Coordinatrice",
                source="contact_echange",
                status="new",
                message="territorial-coordination-search-token",
            ),
            ProfessionalLead(
                email="search.spam@mailinator.com",
                full_name="Spam Search Lead",
                profession="Coordinatrice",
                source="contact_echange",
                status="spam",
                message="territorial-coordination-search-token",
            ),
            ProfessionalLead(
                email="search.invalid@example.com",
                full_name="Invalid Search Lead",
                profession="Coordinatrice",
                source="contact_echange",
                status="invalid",
                message="territorial-coordination-search-token",
            ),
        ]
    )
    db_session.commit()

    response = client.get("/search?q=territorial-coordination-search-token")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Valid Search Lead" in html
    assert "Spam Search Lead" not in html
    assert "Invalid Search Lead" not in html


def test_local_professionnels_pilote_get_drops_secure_cookie_flag_for_http(app, client):
    app.config["SESSION_COOKIE_SECURE"] = True

    response = client.get(
        "/professionnels/pilote",
        base_url="http://127.0.0.1:5000",
    )
    cookie_header = response.headers.get("Set-Cookie", "")

    assert response.status_code == 200
    assert "csrf_token" in response.get_data(as_text=True)
    assert "Secure" not in cookie_header



