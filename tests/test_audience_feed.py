import json
from bs4 import BeautifulSoup

from backend.models_with_analytics import AnalyticsEvent, UserBehavior

PUBLIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Forwarded-For": "203.0.113.10",
}


def _audience_payload(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    payload = soup.select_one("#audienceMapPayload")
    assert payload is not None
    return json.loads(payload.get_text())


def test_tracked_public_page_creates_page_view(client):
    response = client.get("/offre", headers=PUBLIC_HEADERS)

    assert response.status_code == 200
    event = AnalyticsEvent.query.filter_by(page_url="/offre").one()
    assert event.event_type == "page_view"
    assert event.event_category == "audience"
    assert event.user_session

    behavior = UserBehavior.query.filter_by(session_id=event.user_session).one()
    assert behavior.entry_page == "/offre"
    assert behavior.pages_visited == 1


def test_static_assets_do_not_create_page_view(client):
    client.get("/static/css/pages/admin-ui.css", headers=PUBLIC_HEADERS)

    assert AnalyticsEvent.query.filter_by(event_type="page_view").count() == 0


def test_referrer_is_captured(client):
    client.get(
        "/deploiement",
        headers={
            **PUBLIC_HEADERS,
            "Referer": "https://www.linkedin.com/company/helpchain",
        },
    )

    event = AnalyticsEvent.query.filter_by(page_url="/deploiement").one()
    assert event.referrer == "https://www.linkedin.com/company/helpchain"


def test_high_intent_page_view_is_stored(client):
    client.get("/demander-acces", headers=PUBLIC_HEADERS)

    event = AnalyticsEvent.query.filter_by(page_url="/demander-acces").one()
    assert event.event_label == "high_intent"


def test_audience_map_reads_feed_metrics(app, client):
    from backend.helpchain_backend.src.routes.admin import _build_audience_map_context

    client.get("/offre", headers=PUBLIC_HEADERS)
    client.get("/demander-acces", headers=PUBLIC_HEADERS)

    with app.app_context():
        payload = _build_audience_map_context()
    pages = {row["label"]: int(row["count"]) for row in payload["page_rows"]}
    revenue_rows = payload["revenue_radar_rows"]

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

    response = client.get("/offre", headers=PUBLIC_HEADERS)

    assert response.status_code == 200


def test_events_public_commercial_page_is_persisted(client):
    response = client.post(
        "/events",
        json={"event": "page_view", "props": {"url": "/offre", "category": "audience"}},
        headers=PUBLIC_HEADERS,
    )

    assert response.status_code == 201
    assert response.get_json() == {"ok": True}
    event = AnalyticsEvent.query.one()
    assert event.page_url == "/offre"
    assert event.event_type == "page_view"


def test_events_admin_path_is_ignored(client):
    response = client.post(
        "/events",
        json={"event": "page_view", "props": {"url": "/admin/revenue", "category": "audience"}},
        headers=PUBLIC_HEADERS,
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "ignored": True}
    assert AnalyticsEvent.query.count() == 0
    assert UserBehavior.query.count() == 0


def test_events_static_asset_is_ignored(client):
    response = client.post(
        "/events",
        json={"event": "page_view", "props": {"url": "/static/app.js", "category": "audience"}},
        headers=PUBLIC_HEADERS,
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "ignored": True}
    assert AnalyticsEvent.query.count() == 0


def test_events_bot_user_agent_is_ignored(client):
    response = client.post(
        "/events",
        json={"event": "page_view", "props": {"url": "/offre", "category": "audience"}},
        headers={
            "User-Agent": "Mozilla/5.0 compatible; Googlebot/2.1",
            "X-Forwarded-For": "203.0.113.15",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "ignored": True}
    assert AnalyticsEvent.query.count() == 0


def test_events_local_traffic_is_ignored_when_marker_present(client):
    response = client.post(
        "/events",
        json={"event": "page_view", "props": {"url": "/offre", "category": "audience"}},
        headers={
            "User-Agent": PUBLIC_HEADERS["User-Agent"],
            "X-Forwarded-For": "127.0.0.1",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "ignored": True}
    assert AnalyticsEvent.query.count() == 0


def test_events_founder_marker_is_ignored(client):
    response = client.post(
        "/events",
        json={"event": "page_view", "props": {"url": "/offre", "category": "audience"}},
        headers={
            "User-Agent": PUBLIC_HEADERS["User-Agent"],
            "X-Forwarded-For": "176.187.42.10",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "ignored": True}
    assert AnalyticsEvent.query.count() == 0


def test_events_admin_session_is_ignored(authenticated_admin_client):
    response = authenticated_admin_client.post(
        "/events",
        json={"event": "page_view", "props": {"url": "/offre", "category": "audience"}},
        headers=PUBLIC_HEADERS,
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "ignored": True}
    assert AnalyticsEvent.query.count() == 0


def test_admin_page_requests_do_not_pollute_audience_feed(authenticated_admin_client):
    response = authenticated_admin_client.get("/offre", headers=PUBLIC_HEADERS)

    assert response.status_code == 200
    assert AnalyticsEvent.query.count() == 0
    assert UserBehavior.query.count() == 0
