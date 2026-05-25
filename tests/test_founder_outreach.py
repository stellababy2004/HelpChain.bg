from backend.helpchain_backend.src.services.founder_outreach import (
    build_outreach_status,
    normalize_outreach_stage,
    recommend_next_outreach_action,
)


def test_normalize_outreach_stage_accepts_known_values():
    assert normalize_outreach_stage("meeting scheduled") == "meeting_scheduled"
    assert normalize_outreach_stage("pilot-started") == "pilot_started"


def test_normalize_outreach_stage_defaults_unknown():
    assert normalize_outreach_stage("banana") == "not_contacted"


def test_recommend_first_outreach_for_high_priority_not_contacted():
    assert (
        recommend_next_outreach_action(
            {
                "outreach_stage": "not_contacted",
                "followup_priority": "high",
            }
        )
        == "Send first structured founder outreach"
    )


def test_recommend_call_after_reply():
    assert (
        recommend_next_outreach_action({"outreach_stage": "replied"})
        == "Propose a short qualification call"
    )


def test_build_outreach_status_returns_actionable_payload():
    payload = build_outreach_status(
        {
            "outreach_stage": "contacted",
            "relationship_stage": "pilot_framing",
        }
    )

    assert payload["outreach_stage"] == "contacted"
    assert payload["outreach_label"] == "Contacted"
    assert payload["recommended_outreach_action"] == "Schedule follow-up if no response"
    assert payload["generated_at"]
