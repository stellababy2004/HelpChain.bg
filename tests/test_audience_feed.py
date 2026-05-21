import json

from bs4 import BeautifulSoup

from backend.models_with_analytics import AnalyticsEvent, UserBehavior


def _audience_payload(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    payload = soup.select_one("#audienceMapPayload")
    assert payload is not None
    return json.loads(payload.get_text())


def test_tracked_public_page_creates_page_view(client):
    response = client.get("/offre")

    assert response.status_code == 200
    event = AnalyticsEvent.query.filter_by(page_url="/offre").one()
    assert event.event_type == "page_view"
    assert event.event_category == "audience"
    assert event.user_session

    behavior = UserBehavior.query.filter_by(session_id=event.user_session).one()
    assert behavior.entry_page == "/offre"
    assert behavior.pages_visited == 1


def test_static_assets_do_not_create_page_view(client):
    client.get("/static/css/pages/admin-ui.css")

    assert AnalyticsEvent.query.filter_by(event_type="page_view").count() == 0


def test_referrer_is_captured(client):
    client.get("/deploiement", headers={"Referer": "https://www.linkedin.com/company/helpchain"})

    event = AnalyticsEvent.query.filter_by(page_url="/deploiement").one()
    assert event.referrer == "https://www.linkedin.com/company/helpchain"


def test_high_intent_page_view_is_stored(client):
    client.get("/demander-acces")

    event = AnalyticsEvent.query.filter_by(page_url="/demander-acces").one()
    assert event.event_label == "high_intent"


def test_audience_map_reads_feed_metrics(authenticated_admin_client):
    authenticated_admin_client.get("/offre")
    authenticated_admin_client.get("/demander-acces")

    response = authenticated_admin_client.get("/admin/audience-map")
    html = response.get_data(as_text=True)
    payload = _audience_payload(html)
    pages = {row["label"]: int(row["count"]) for row in payload["page_rows"]}
    revenue_rows = payload["revenue_radar_rows"]

    assert response.status_code == 200
    assert "Offre" in pages
    assert any("demander" in label.lower() for label in pages)
    assert pages["Offre"] >= 1
    assert any(count >= 1 for label, count in pages.items() if "demander" in label.lower())
    assert any(int(row["pages_count"]) >= 2 for row in revenue_rows)


def test_feed_failure_does_not_break_page_rendering(client, monkeypatch):
    from backend.helpchain_backend.src.services import audience_feed

    def fail_tracking():
        raise RuntimeError("tracking unavailable")

    monkeypatch.setattr(audience_feed, "track_audience_page_view", fail_tracking)

    response = client.get("/offre")

    assert response.status_code == 200
