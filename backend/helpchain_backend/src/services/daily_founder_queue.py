from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


URGENCY_WEIGHTS = {
    "high": 40,
    "medium": 20,
    "low": 5,
}


STAGE_WEIGHTS = {
    "operational_validation": 45,
    "pilot_discussion": 40,
    "pilot_framing": 35,
    "governance_review": 22,
    "institutional_evaluation": 18,
    "discovery": 5,
    "dormant": 0,
}


ACCOUNT_WEIGHTS = {
    "strong": 35,
    "moderate": 18,
    "weak": 5,
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def compute_daily_priority_score(row: dict[str, Any]) -> int:
    score = 0

    score += min(_int(row.get("intent_score") or row.get("score")), 120)

    score += URGENCY_WEIGHTS.get(_lower(row.get("followup_priority")), 0)
    score += STAGE_WEIGHTS.get(_lower(row.get("relationship_stage")), 0)
    score += ACCOUNT_WEIGHTS.get(_lower(row.get("account_strength")), 0)

    if _lower(row.get("outreach_stage")) == "not_contacted":
        score += 20

    if _lower(row.get("possible_friction")):
        score += 12

    if _lower(row.get("territorial_intensity")) in {"high", "strategic"}:
        score += 20

    if row.get("repeated_engagement_detected"):
        score += 18

    return score


def explain_daily_priority(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    if _int(row.get("intent_score") or row.get("score")) >= 100:
        reasons.append("high institutional intent")

    if _lower(row.get("followup_priority")) == "high":
        reasons.append("follow-up priority is high")

    if _lower(row.get("account_strength")) == "strong":
        reasons.append("strong institutional account")

    if _lower(row.get("relationship_stage")) in {"pilot_framing", "pilot_discussion", "operational_validation"}:
        reasons.append("advanced relationship stage")

    if _lower(row.get("outreach_stage")) == "not_contacted":
        reasons.append("no outreach recorded yet")

    if _lower(row.get("possible_friction")):
        reasons.append("possible friction needs handling")

    if row.get("repeated_engagement_detected"):
        reasons.append("repeated engagement observed")

    return reasons[:5]


def recommended_daily_action(row: dict[str, Any]) -> str:
    if row.get("recommended_outreach_action"):
        return str(row["recommended_outreach_action"])

    if row.get("recommended_relationship_action"):
        return str(row["recommended_relationship_action"])

    if row.get("account_recommendation"):
        return str(row["account_recommendation"])

    if row.get("recommended_action"):
        return str(row["recommended_action"])

    return "Continue observing"


def build_daily_founder_queue(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []

    for row in rows or []:
        priority_score = compute_daily_priority_score(row)
        item = {
            **row,
            "daily_priority_score": priority_score,
            "daily_priority_reasons": explain_daily_priority(row),
            "daily_recommended_action": recommended_daily_action(row),
        }
        queue.append(item)

    queue.sort(
        key=lambda item: (
            item.get("daily_priority_score") or 0,
            item.get("intent_score") or item.get("score") or 0,
        ),
        reverse=True,
    )

    return queue[:limit]


def summarize_daily_founder_queue(rows: list[dict[str, Any]]) -> dict[str, Any]:
    queue = build_daily_founder_queue(rows, limit=10)

    return {
        "total_candidates": len(rows or []),
        "top_count": len(queue),
        "highest_score": queue[0]["daily_priority_score"] if queue else 0,
        "top_action": queue[0]["daily_recommended_action"] if queue else "Continue observing",
    }
