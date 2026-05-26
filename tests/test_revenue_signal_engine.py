from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from backend.helpchain_backend.src.routes import admin as admin_routes
from backend.helpchain_backend.src.services.revenue_signal_engine import (
    build_revenue_signal_profile,
    compute_institutional_intent_score,
    summarize_revenue_signal_profile,
)


def test_compute_institutional_intent_score_is_deterministic():
    profile = {
        "base_intent_score": 100,
        "repeated_cta_clicks": 2,
        "repeated_sessions": 1,
        "pilot_event_count": 1,
        "governance_event_count": 1,
        "outreach_event_count": 0,
        "deployment_event_count": 1,
        "territory_repeat_count": 2,
        "institutional_intent_density": 35,
        "recency_score": 16,
    }

    assert compute_institutional_intent_score(profile) == 91


def test_build_revenue_signal_profile_aggregates_behavioral_signals():
    now = datetime.now(UTC).replace(tzinfo=None)
    events = [
        {
            "event_type": "deployment_pilot_cta_clicked",
            "page_url": "/deploiement",
            "created_at": now - timedelta(days=4),
            "user_session": "session-paris",
        },
        {
            "event_type": "deployment_pilot_cta_clicked",
            "page_url": "/deploiement",
            "created_at": now - timedelta(days=1),
            "user_session": "session-paris",
        },
        {
            "event_type": "security_trust_cta_clicked",
            "page_url": "/securite",
            "created_at": now - timedelta(days=1),
            "user_session": "session-paris",
        },
        {
            "event_type": "governance_contact_cta_clicked",
            "page_url": "/contact",
            "created_at": now,
            "user_session": "session-paris",
        },
    ]

    profile = build_revenue_signal_profile(
        events,
        territory="Paris",
        territory_repeat_count=3,
        now=now,
    )

    assert profile["session_id"] == "session-paris"
    assert profile["repeated_cta_clicks"] == 1
    assert profile["repeated_sessions"] == 2
    assert profile["pilot_event_count"] >= 2
    assert profile["governance_event_count"] >= 1
    assert profile["outreach_event_count"] == 1
    assert profile["territory"] == "Paris"
    assert profile["territory_repeat_count"] == 3
    assert profile["institutional_intent_density"] > 0
    assert profile["intent_score"] >= 60


def test_summarize_revenue_signal_profile_groups_territories_and_silent_intent():
    summary = summarize_revenue_signal_profile(
        [
            {
                "session_id": "s1",
                "territory": "Paris",
                "primary_interest": "deployment_operations",
                "intent_label": "Pilot-ready",
                "intent_score": 76,
                "recency_score": 24,
                "repeated_sessions": 1,
                "repeated_cta_clicks": 1,
                "pilot_event_count": 2,
                "governance_event_count": 1,
                "outreach_event_count": 1,
                "has_outreach": True,
                "top_paths": ["/deploiement", "/demo"],
            },
            {
                "session_id": "s2",
                "territory": "Paris",
                "primary_interest": "trust_governance",
                "intent_label": "Operationally interested",
                "intent_score": 68,
                "recency_score": 16,
                "repeated_sessions": 1,
                "repeated_cta_clicks": 2,
                "pilot_event_count": 1,
                "governance_event_count": 2,
                "outreach_event_count": 0,
                "has_outreach": False,
                "top_paths": ["/securite", "/deploiement"],
            },
            {
                "session_id": "s3",
                "territory": "Lyon",
                "primary_interest": "institutional_fit",
                "intent_label": "Evaluating",
                "intent_score": 42,
                "recency_score": 8,
                "repeated_sessions": 0,
                "repeated_cta_clicks": 0,
                "pilot_event_count": 0,
                "governance_event_count": 1,
                "outreach_event_count": 0,
                "has_outreach": False,
                "top_paths": ["/pour-les-structures"],
            },
        ]
    )

    assert [item["session_id"] for item in summary["hot_this_week"]] == ["s1", "s2"]
    assert [item["session_id"] for item in summary["silent_high_intent"]] == ["s2"]
    assert summary["territory_acceleration"][0]["territory"] == "Paris"
    assert summary["territory_acceleration"][0]["session_count"] == 2


def test_founder_cockpit_context_includes_revenue_signal_sections(monkeypatch):
    monkeypatch.setattr(
        admin_routes,
        "_build_revenue_signal_profiles",
        lambda **kwargs: [
            {
                "session_id": "s1",
                "territory": "Paris",
                "primary_interest": "deployment_operations",
                "intent_label": "Pilot-ready",
                "intent_score": 76,
                "recency_score": 24,
                "repeated_sessions": 1,
                "repeated_cta_clicks": 1,
                "pilot_event_count": 2,
                "governance_event_count": 1,
                "outreach_event_count": 1,
                "has_outreach": True,
                "top_paths": ["/deploiement", "/demo"],
            },
            {
                "session_id": "s2",
                "territory": "Paris",
                "primary_interest": "trust_governance",
                "intent_label": "Operationally interested",
                "intent_score": 68,
                "recency_score": 16,
                "repeated_sessions": 1,
                "repeated_cta_clicks": 2,
                "pilot_event_count": 1,
                "governance_event_count": 2,
                "outreach_event_count": 0,
                "has_outreach": False,
                "top_paths": ["/securite", "/deploiement"],
            },
        ],
    )

    context = admin_routes._build_founder_cockpit_context([])

    assert context["hot_this_week"]
    assert context["silent_high_intent"]
    assert context["territory_acceleration"]
    assert context["territory_acceleration"][0]["territory"] == "Paris"


def test_founder_cockpit_context_includes_operational_founder_sections(monkeypatch):
    monkeypatch.setattr(admin_routes, "_build_revenue_signal_profiles", lambda **kwargs: [])
    monkeypatch.setattr(admin_routes, "_revenue_founder_activity_map", lambda rows: {})

    context = admin_routes._build_founder_cockpit_context(
        [
            SimpleNamespace(
                uid="professional_lead:1",
                id=1,
                kind="professional_lead",
                organization="Ville de Paris",
                city="Paris",
                territory="Paris",
                stage="qualified",
                score=88,
                intent_score=88,
                primary_interest="deployment_operations",
                repeated_engagement_detected=True,
                created_at=datetime(2026, 5, 10, 9, 0, 0),
                contacted_at=datetime(2026, 5, 14, 9, 0, 0),
                last_activity=datetime(2026, 5, 14, 9, 0, 0),
                timeline_paths=["/offre", "/deploiement"],
            )
        ]
    )

    assert context["stalled_opportunities"]
    assert context["recommended_founder_actions"]
    assert context["relationship_temperature_rows"]
    assert context["institutional_memory_timeline"]
    assert context["founder_operational_state"]
    assert context["relationship_state_summary"]
    assert context["pilot_progression_summary"]
    assert context["next_founder_actions"]
    assert context["recommended_founder_actions"][0]["recommended_founder_action"] == "Re-contact deployment lead"
