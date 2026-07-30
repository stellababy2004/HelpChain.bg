import pytest


def test_zoho_event_accepts_valid_token(client, monkeypatch):
    monkeypatch.setenv("HELPCHAIN_ZOHO_INTEGRATION_TOKEN", "test-token")

    response = client.post(
        "/api/integrations/zoho/events",
        json={"event": "request.escalated"},
        headers={"X-HelpChain-Integration-Token": "test-token"},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["event"] == "request.escalated"


def test_zoho_event_rejects_missing_token(client, monkeypatch):
    monkeypatch.setenv("HELPCHAIN_ZOHO_INTEGRATION_TOKEN", "test-token")

    response = client.post(
        "/api/integrations/zoho/events",
        json={"event": "request.escalated"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"


def test_zoho_event_rejects_unsupported_event(client, monkeypatch):
    monkeypatch.setenv("HELPCHAIN_ZOHO_INTEGRATION_TOKEN", "test-token")

    response = client.post(
        "/api/integrations/zoho/events",
        json={"event": "random.event"},
        headers={"X-HelpChain-Integration-Token": "test-token"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "unsupported_event"
