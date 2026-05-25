from backend.helpchain_backend.src.services.daily_founder_queue import (
    build_daily_founder_queue,
    compute_daily_priority_score,
    explain_daily_priority,
    summarize_daily_founder_queue,
)


def test_compute_daily_priority_score_rewards_high_intent_and_strong_account():
    score = compute_daily_priority_score(
        {
            "intent_score": 120,
            "followup_priority": "high",
            "relationship_stage": "pilot_framing",
            "account_strength": "strong",
            "outreach_stage": "not_contacted",
        }
    )

    assert score >= 200


def test_explain_daily_priority_returns_human_reasons():
    reasons = explain_daily_priority(
        {
            "intent_score": 120,
            "followup_priority": "high",
            "account_strength": "strong",
            "relationship_stage": "pilot_framing",
            "outreach_stage": "not_contacted",
        }
    )

    assert "high institutional intent" in reasons
    assert "strong institutional account" in reasons


def test_build_daily_founder_queue_orders_by_priority():
    queue = build_daily_founder_queue(
        [
            {"uid": "low", "intent_score": 10, "account_strength": "weak"},
            {
                "uid": "hot",
                "intent_score": 150,
                "followup_priority": "high",
                "relationship_stage": "pilot_discussion",
                "account_strength": "strong",
                "outreach_stage": "not_contacted",
            },
        ]
    )

    assert queue[0]["uid"] == "hot"
    assert queue[0]["daily_priority_score"] > queue[1]["daily_priority_score"]


def test_build_daily_founder_queue_uses_existing_recommended_action():
    queue = build_daily_founder_queue(
        [
            {
                "uid": "x",
                "intent_score": 100,
                "recommended_outreach_action": "Send first structured founder outreach",
            }
        ]
    )

    assert queue[0]["daily_recommended_action"] == "Send first structured founder outreach"


def test_summarize_daily_founder_queue_returns_summary():
    summary = summarize_daily_founder_queue(
        [{"uid": "a", "intent_score": 100, "followup_priority": "high"}]
    )

    assert summary["total_candidates"] == 1
    assert summary["top_count"] == 1
    assert summary["highest_score"] > 0
