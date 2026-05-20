from datetime import UTC, datetime, timedelta

import json

from bs4 import BeautifulSoup

from backend.models_with_analytics import AnalyticsEvent, UserBehavior
from backend.helpchain_backend.src.routes.admin import (
    _audience_score_session,
    _audience_source_label,
    _audience_temperature_for_score,
)


def test_admin_audience_map_requires_admin(client):
    response = client.get("/admin/audience-map", follow_redirects=False)

    assert response.status_code != 200


def _audience_payload(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    payload = soup.select_one("#audienceMapPayload")
    assert payload is not None
    return json.loads(payload.get_text())


def test_admin_audience_map_empty_state(authenticated_admin_client):
    response = authenticated_admin_client.get("/admin/audience-map")
    html = response.get_data(as_text=True)
    payload = _audience_payload(html)

    assert response.status_code == 200
    assert "Carte d'interet - France" in html
    assert "Radar des signaux" in html
    assert 'id="audienceRevenueRadar"' in html
    assert payload["revenue_radar_rows"] == []
    assert "Geo enrichissement limite" in html or "Aucune table d'analytics disponible" in html


def test_audience_revenue_scoring_logic():
    now = datetime.now(UTC).replace(tzinfo=None)

    hot_score = _audience_score_session(
        ["/", "/offre", "/deploiement", "/demander-acces"],
        page_count=4,
        last_activity=now - timedelta(minutes=5),
        now=now,
        has_external_referrer=True,
        repeated_same_day=True,
    )
    bounce_score = _audience_score_session(
        ["/"],
        page_count=1,
        last_activity=now - timedelta(days=2),
        now=now,
        has_external_referrer=False,
        repeated_same_day=False,
    )

    assert hot_score == 44
    assert bounce_score == 0


def test_audience_temperature_labels():
    assert _audience_temperature_for_score(7)["label"] == "Froid"
    assert _audience_temperature_for_score(8)["label"] == "Tiede"
    assert _audience_temperature_for_score(16)["label"] == "Chaud"
    assert _audience_temperature_for_score(25)["label"] == "Tres chaud"


def test_audience_source_cleanup():
    assert _audience_source_label(None) == "Direct"
    assert _audience_source_label("https://www.google.fr/search?q=helpchain") == "Google"
    assert _audience_source_label("https://linkedin.com/company/helpchain") == "LinkedIn"
    assert _audience_source_label("https://chat.openai.com/") == "ChatGPT"


def test_admin_audience_map_uses_analytics_rows(authenticated_admin_client, session):
    now = datetime.now(UTC).replace(tzinfo=None)
    session.add_all(
        [
            AnalyticsEvent(
                event_type="page_view",
                user_session="session-paris-1",
                page_url="/demander-acces",
                referrer="https://www.google.com/search?q=helpchain",
                created_at=now - timedelta(hours=2),
            ),
            AnalyticsEvent(
                event_type="page_view",
                user_session="session-paris-1",
                page_url="/offre",
                created_at=now - timedelta(hours=1),
            ),
            AnalyticsEvent(
                event_type="page_view",
                user_session="session-lyon-1",
                page_url="/deploiement",
                created_at=now - timedelta(days=2),
            ),
            UserBehavior(
                session_id="session-paris-1",
                user_type="guest",
                location="Paris, France",
                session_start=now - timedelta(hours=2),
                last_activity=now - timedelta(hours=1),
            ),
            UserBehavior(
                session_id="session-lyon-1",
                user_type="guest",
                location="Lyon, France",
                session_start=now - timedelta(days=2),
                last_activity=now - timedelta(days=2),
            ),
        ]
    )
    session.commit()

    response = authenticated_admin_client.get("/admin/audience-map")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Paris" in html
    assert "Lyon" in html
    assert "/demander-acces" in html
    assert "Google" in html
    assert "visite(s) sur des pages a forte intention" in html


def test_admin_audience_map_renders_revenue_radar(authenticated_admin_client, session):
    now = datetime.now(UTC).replace(tzinfo=None)
    session.add_all(
        [
            AnalyticsEvent(
                event_type="page_view",
                user_session="session-hot-founder",
                page_url="/demander-acces",
                referrer="https://www.google.fr/search?q=helpchain",
                created_at=now - timedelta(minutes=20),
            ),
            AnalyticsEvent(
                event_type="page_view",
                user_session="session-hot-founder",
                page_url="/contact",
                created_at=now - timedelta(minutes=12),
            ),
            AnalyticsEvent(
                event_type="page_view",
                user_session="session-hot-founder",
                page_url="/deploiement",
                created_at=now - timedelta(minutes=8),
            ),
            AnalyticsEvent(
                event_type="page_view",
                user_session="session-hot-founder",
                page_url="/offre",
                created_at=now - timedelta(minutes=3),
            ),
        ]
    )
    session.commit()

    response = authenticated_admin_client.get("/admin/audience-map")
    html = response.get_data(as_text=True)
    payload = _audience_payload(html)
    revenue_rows = payload["revenue_radar_rows"]

    assert response.status_code == 200
    assert "Radar des signaux" in html
    assert any(row["session"] == "session-hot-founder" for row in revenue_rows)
    assert any(row["source"] == "Google" for row in revenue_rows)
    assert any(row["temperature"] == "Tres chaud" for row in revenue_rows)
    assert any(int(row["pages_count"]) == 4 for row in revenue_rows)

