from datetime import datetime, timezone

from backend.helpchain_backend.src.services.relationship_memory import (
    build_account_timeline,
    build_relationship_memory,
    detect_followup_status,
    infer_relationship_stage,
)


def test_infer_relationship_stage_detects_governance_review():
    assert (
        infer_relationship_stage(
            {"paths": ["/securite", "/confidentialite"], "score": 55}
        )
        == "governance_review"
    )


def test_infer_relationship_stage_detects_pilot_framing():
    assert (
        infer_relationship_stage(
            {"paths": ["/deploiement", "/offre"], "score": 110}
        )
        == "pilot_framing"
    )


def test_detect_followup_status_due_for_high_intent_without_contact():
    status = detect_followup_status({"paths": ["/deploiement", "/offre"], "score": 120})

    assert status["followup_status"] == "due"
    assert status["followup_priority"] == "high"


def test_detect_followup_status_contacted_when_outreach_recorded():
    status = detect_followup_status(
        {
            "paths": ["/deploiement"],
            "score": 120,
            "contacted_at": "2026-05-25T10:00:00+00:00",
        }
    )

    assert status["followup_status"] == "contacted"


def test_build_account_timeline_sorts_events():
    timeline = build_account_timeline(
        [
            {
                "created_at": "2026-05-25T12:00:00+00:00",
                "paths": ["/offre"],
                "score": 60,
            },
            {
                "created_at": "2026-05-24T12:00:00+00:00",
                "paths": ["/"],
                "score": 5,
            },
        ]
    )

    assert timeline[0]["paths"] == ["/"]
    assert timeline[1]["paths"] == ["/offre"]


def test_build_relationship_memory_returns_actionable_summary():
    memory = build_relationship_memory(
        {"paths": ["/deploiement", "/offre"], "score": 120},
        events=[{"created_at": "2026-05-25T12:00:00+00:00", "paths": ["/offre"]}],
    )

    assert memory["relationship_stage"] == "pilot_framing"
    assert memory["followup"]["followup_status"] == "due"
    assert memory["recommended_relationship_action"] == "Send structured pilot outreach"
