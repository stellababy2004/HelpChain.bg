from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from backend.helpchain_backend.src.routes import admin as admin_routes
from backend.helpchain_backend.src.services.founder_operational_state import (
    build_founder_operational_state,
    compute_pilot_progression,
    compute_relationship_state,
    detect_state_transition,
    merge_founder_memory_with_actions,
    summarize_founder_operational_state,
)


def test_compute_relationship_state_progresses_from_observed_to_contacted():
    now = datetime.now(UTC).replace(tzinfo=None)

    assert (
        compute_relationship_state(
            {"intent_score": 36},
            memory_summary={
                "timeline_events": [
                    {
                        "timestamp": (now - timedelta(days=1)).isoformat(),
                        "event_type": "page_view",
                        "label": "visited /offre",
                        "source": "telemetry",
                    }
                ],
                "relationship_temperature": "warming",
                "has_outreach": False,
            },
            now=now,
        )
        == "observed"
    )
    assert (
        compute_relationship_state(
            {"intent_score": 70},
            memory_summary={
                "timeline_events": [
                    {
                        "timestamp": (now - timedelta(days=3)).isoformat(),
                        "event_type": "founder_outreach_sent",
                        "label": "founder outreach sent",
                        "source": "outreach",
                    }
                ],
                "relationship_temperature": "active",
                "has_outreach": True,
                "last_founder_touch": now - timedelta(days=3),
                "last_activity_at": now - timedelta(days=3),
            },
            now=now,
        )
        == "contacted"
    )


def test_compute_pilot_progression_identifies_pilot_ready_state():
    progression = compute_pilot_progression(
        {"stage": "pilot_proposed", "intent_score": 82, "primary_interest": "deployment_operations"},
        relationship_state="pilot_framing",
        memory_summary={"has_pilot_request": True},
    )

    assert progression == "pilot_ready"


def test_detect_state_transition_reports_relationship_and_action_changes():
    transition = detect_state_transition(
        {
            "uid": "lead-1",
            "organization": "Ville de Paris",
            "relationship_state": "observed",
            "pilot_progression": "interest_detected",
            "next_recommended_action": "Wait before next outreach",
        },
        {
            "uid": "lead-1",
            "organization": "Ville de Paris",
            "relationship_state": "contacted",
            "pilot_progression": "pilot_discussion",
            "next_recommended_action": "Push pilot proposal",
        },
    )

    assert transition is not None
    assert "relationship_state_changed" in transition["transition_labels"]
    assert "pilot_progression_changed" in transition["transition_labels"]
    assert transition["to_action"] == "Push pilot proposal"


def test_merge_founder_memory_with_actions_preserves_touch_history():
    now = datetime.now(UTC).replace(tzinfo=None)
    merged = merge_founder_memory_with_actions(
        {"uid": "lead-merge", "organization": "CCAS Lyon"},
        memory_summary={
            "timeline_events": [
                {
                    "timestamp": (now - timedelta(days=4)).isoformat(),
                    "event_type": "founder_outreach_sent",
                    "label": "founder outreach sent",
                    "source": "outreach",
                },
                {
                    "timestamp": (now - timedelta(days=1)).isoformat(),
                    "event_type": "founder_manual_note",
                    "label": "founder note added",
                    "source": "manual_note",
                },
            ],
            "relationship_temperature": "active",
            "last_founder_touch": now - timedelta(days=1),
        },
        action_payload={"action_label": "Push pilot proposal", "urgency": {"urgency_level": "high"}},
        now=now,
    )

    assert merged["next_recommended_action"] == "Push pilot proposal"
    assert len(merged["founder_touch_history"]) == 2
    assert merged["institutional_temperature"] == "active"


def test_summarize_founder_operational_state_returns_compact_summaries():
    summary = summarize_founder_operational_state(
        [
            {
                "uid": "lead-1",
                "organization": "Ville de Paris",
                "relationship_state": "contacted",
                "pilot_progression": "pilot_discussion",
                "next_recommended_action": "Push pilot proposal",
            },
            {
                "uid": "lead-2",
                "organization": "CCAS Lyon",
                "relationship_state": "observed",
                "pilot_progression": "interest_detected",
                "next_recommended_action": "Send governance/security framing",
            },
        ]
    )

    assert summary["relationship_state_summary"]["contacted"] == 1
    assert summary["pilot_progression_summary"]["pilot_discussion"] == 1
    assert summary["next_founder_actions"][0]["organization"] == "Ville de Paris"


def test_build_founder_operational_state_exposes_persistence_ready_fields():
    now = datetime.now(UTC).replace(tzinfo=None)
    state = build_founder_operational_state(
        {
            "uid": "lead-state",
            "organization": "Association Horizon",
            "city": "Paris",
            "territory": "Paris",
            "kind": "professional_lead",
            "stage": "qualified",
            "intent_score": 76,
            "primary_interest": "deployment_operations",
            "timeline_events": [
                {
                    "timestamp": (now - timedelta(days=2)).isoformat(),
                    "event_type": "page_view",
                    "label": "viewed /deploiement",
                    "source": "telemetry",
                }
            ],
        },
        now=now,
    )

    assert state["organization_state"]["organization"] == "Association Horizon"
    assert state["relationship_state"] in {"observed", "engaged"}
    assert state["pilot_progression"] in {"interest_detected", "qualification_needed"}
    assert "territory_context" in state


def test_dashboard_context_safely_exposes_operational_state(monkeypatch):
    monkeypatch.setattr(admin_routes, "_build_revenue_signal_profiles", lambda **kwargs: [])
    monkeypatch.setattr(admin_routes, "_revenue_founder_activity_map", lambda rows: {})

    context = admin_routes._build_founder_cockpit_context(
        [
            SimpleNamespace(
                uid="professional_lead:42",
                id=42,
                kind="professional_lead",
                organization="Ville de Paris",
                city="Paris",
                territory="Paris",
                stage="pilot_proposed",
                score=84,
                intent_score=84,
                primary_interest="deployment_operations",
                repeated_engagement_detected=True,
                created_at=datetime(2026, 5, 10, 9, 0, 0),
                contacted_at=datetime(2026, 5, 16, 9, 0, 0),
                last_activity=datetime(2026, 5, 18, 9, 0, 0),
                timeline_paths=["/offre", "/deploiement"],
            )
        ]
    )

    assert context["founder_operational_state"]
    assert context["relationship_state_summary"]
    assert context["pilot_progression_summary"]
    assert context["next_founder_actions"]
