from datetime import UTC, datetime, timedelta

from backend.helpchain_backend.src.services.founder_action_engine import (
    build_founder_action_queue,
    compute_followup_urgency,
    recommend_founder_action,
    summarize_founder_actions,
)


def test_compute_followup_urgency_scores_stalled_high_intent_accounts():
    now = datetime.now(UTC).replace(tzinfo=None)
    urgency = compute_followup_urgency(
        {
            "intent_score": 84,
            "stage": "qualified",
        },
        memory_summary={
            "relationship_temperature": "stalled",
            "last_founder_touch": now - timedelta(days=12),
            "last_activity_at": now - timedelta(days=12),
            "has_outreach": True,
        },
        now=now,
    )

    assert urgency["urgency_level"] in {"high", "critical"}
    assert urgency["urgency_score"] >= 85
    assert urgency["cadence"] == "within_24h"


def test_recommend_founder_action_prefers_governance_framing_when_needed():
    action = recommend_founder_action(
        {
            "primary_interest": "trust_governance",
            "possible_friction": "trust_governance_review_without_conversion",
        },
        memory_summary={
            "relationship_temperature": "warming",
            "has_outreach": False,
            "has_pilot_request": False,
        },
        urgency={
            "urgency_level": "medium",
            "urgency_score": 44,
            "cadence": "this_week",
        },
    )

    assert action["action_code"] == "send_governance_security_framing"
    assert action["action_label"] == "Send governance/security framing"


def test_build_founder_action_queue_prioritizes_stalled_accounts():
    now = datetime.now(UTC).replace(tzinfo=None)
    queue = build_founder_action_queue(
        [
            {
                "uid": "lead-stalled",
                "organization": "Ville de Paris",
                "intent_score": 88,
                "primary_interest": "deployment_operations",
                "timeline_events": [
                    {
                        "timestamp": (now - timedelta(days=10)).isoformat(),
                        "event_type": "founder_outreach_sent",
                        "label": "founder outreach sent",
                        "source": "outreach",
                    }
                ],
                "contacted_at": now - timedelta(days=10),
            },
            {
                "uid": "lead-observe",
                "organization": "Association Locale",
                "intent_score": 34,
                "primary_interest": "institutional_fit",
                "timeline_events": [
                    {
                        "timestamp": (now - timedelta(days=1)).isoformat(),
                        "event_type": "page_view",
                        "label": "visited /offre",
                        "source": "telemetry",
                    }
                ],
            },
        ],
        now=now,
    )

    assert queue[0]["organization"] == "Ville de Paris"
    assert queue[0]["recommended_founder_action"] == "Re-contact deployment lead"
    assert queue[0]["relationship_temperature"] == "stalled"


def test_summarize_founder_actions_reports_top_action():
    now = datetime.now(UTC).replace(tzinfo=None)
    summary = summarize_founder_actions(
        [
            {
                "uid": "lead-1",
                "organization": "CCAS Lyon",
                "intent_score": 91,
                "primary_interest": "deployment_operations",
                "timeline_events": [
                    {
                        "timestamp": (now - timedelta(days=11)).isoformat(),
                        "event_type": "founder_outreach_sent",
                        "label": "founder outreach sent",
                        "source": "outreach",
                    }
                ],
                "contacted_at": now - timedelta(days=11),
            }
        ],
        now=now,
    )

    assert summary["total_items"] == 1
    assert summary["stalled_count"] == 1
    assert summary["top_action"] == "Re-contact deployment lead"
