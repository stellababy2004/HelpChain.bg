from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any, Iterable

from .founder_memory_engine import (
    TEMPERATURE_RANK,
    build_founder_memory_timeline,
    detect_relationship_temperature,
    detect_stalled_opportunities,
    summarize_founder_memory,
)


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_naive(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
    return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def compute_followup_urgency(
    row: dict[str, Any] | None = None,
    *,
    memory_summary: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    summary = dict(memory_summary or {})
    if not summary:
        timeline = payload.get("timeline_events") or build_founder_memory_timeline(payload, now=current_time)
        summary = summarize_founder_memory(timeline, row=payload, now=current_time)

    temperature = str(summary.get("relationship_temperature") or detect_relationship_temperature(payload))
    intent_score = _int(payload.get("intent_score") or payload.get("score"))
    stage = str(payload.get("stage") or "").strip()
    last_founder_touch = summary.get("last_founder_touch")
    last_activity_at = summary.get("last_activity_at")
    urgency_score = min(100, intent_score)
    reason = "Continue observation"

    if temperature == "strategic":
        urgency_score += 18
        reason = "Strategic institutional opportunity"
    elif temperature == "stalled":
        urgency_score += 28
        reason = "Qualified relationship is stalled"
    elif temperature == "high_intent":
        urgency_score += 16
        reason = "High-intent institutional signal is active"
    elif temperature == "active":
        urgency_score += 10
        reason = "Ongoing founder conversation needs continuity"
    elif temperature == "warming":
        urgency_score += 4
        reason = "Institutional interest is warming"

    if stage in {"demo_booked", "demo_done", "pilot_proposed", "negotiation"}:
        urgency_score += 8

    if isinstance(last_founder_touch, datetime):
        idle_days = max(0, (current_time - last_founder_touch).days)
        if idle_days >= 14:
            urgency_score += 20
            reason = "Founder follow-up is overdue"
        elif idle_days >= 9:
            urgency_score += 14
            reason = "No response after recent founder outreach"
        elif idle_days <= 2:
            urgency_score -= 12
            reason = "Recent founder touch already recorded"

    if isinstance(last_activity_at, datetime) and isinstance(last_founder_touch, datetime):
        if last_activity_at > last_founder_touch:
            urgency_score += 8
            reason = "Prospect activity resumed after founder touch"

    urgency_score = max(0, min(100, urgency_score))
    if urgency_score >= 85:
        level = "critical"
    elif urgency_score >= 65:
        level = "high"
    elif urgency_score >= 35:
        level = "medium"
    else:
        level = "low"

    cadence = {
        "critical": "within_24h",
        "high": "within_72h",
        "medium": "this_week",
        "low": "observe",
    }[level]

    return {
        "urgency_score": urgency_score,
        "urgency_level": level,
        "cadence": cadence,
        "reason": reason,
    }


def recommend_founder_action(
    row: dict[str, Any] | None = None,
    *,
    memory_summary: dict[str, Any] | None = None,
    urgency: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    summary = dict(memory_summary or {})
    if not summary:
        timeline = payload.get("timeline_events") or build_founder_memory_timeline(payload, now=current_time)
        summary = summarize_founder_memory(timeline, row=payload, now=current_time)
    urgency_payload = dict(urgency or compute_followup_urgency(payload, memory_summary=summary, now=current_time))

    temperature = str(summary.get("relationship_temperature") or "cold")
    primary_interest = str(payload.get("primary_interest") or "unknown")
    stage = str(payload.get("stage") or "").strip()
    has_outreach = bool(summary.get("has_outreach"))
    has_pilot_request = bool(summary.get("has_pilot_request"))
    trust_friction = bool(payload.get("trust_friction_detected")) or str(payload.get("possible_friction") or "").strip() == "trust_governance_review_without_conversion"

    action_code = "observe"
    action_label = "Wait before next outreach"

    if temperature == "strategic" and urgency_payload.get("urgency_level") in {"high", "critical"}:
        action_code = "escalate_institutional_opportunity"
        action_label = "Escalate institutional opportunity"
    elif temperature == "stalled" and primary_interest == "deployment_operations":
        action_code = "recontact_deployment_lead"
        action_label = "Re-contact deployment lead"
    elif trust_friction or primary_interest == "trust_governance":
        action_code = "send_governance_security_framing"
        action_label = "Send governance/security framing"
    elif has_pilot_request or stage in {"demo_booked", "demo_done", "pilot_proposed", "negotiation"}:
        action_code = "push_pilot_proposal"
        action_label = "Push pilot proposal"
    elif not has_outreach and urgency_payload.get("urgency_level") in {"high", "critical"}:
        action_code = "send_first_founder_outreach"
        action_label = "Send first structured founder outreach"
    elif has_outreach and urgency_payload.get("urgency_level") in {"high", "critical"}:
        action_code = "send_followup_outreach"
        action_label = "Send structured follow-up outreach"

    return {
        "action_code": action_code,
        "action_label": action_label,
        "cadence": urgency_payload.get("cadence"),
        "urgency_score": urgency_payload.get("urgency_score"),
        "urgency_level": urgency_payload.get("urgency_level"),
    }


def build_founder_action_queue(
    rows: Iterable[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    current_time = _as_utc_naive(now) or _now_utc()
    queue: list[dict[str, Any]] = []
    for row in rows or []:
        payload = dict(row or {})
        timeline = list(payload.get("timeline_events") or build_founder_memory_timeline(payload, now=current_time))
        memory_summary = summarize_founder_memory(timeline, row=payload, now=current_time)
        urgency = compute_followup_urgency(payload, memory_summary=memory_summary, now=current_time)
        action = recommend_founder_action(payload, memory_summary=memory_summary, urgency=urgency, now=current_time)
        queue.append(
            {
                **payload,
                "timeline_events": timeline,
                "founder_memory": memory_summary,
                "relationship_temperature": memory_summary.get("relationship_temperature"),
                "last_founder_touch": memory_summary.get("last_founder_touch"),
                "founder_action_state": {
                    "relationship_temperature": memory_summary.get("relationship_temperature"),
                    "urgency_score": urgency.get("urgency_score"),
                    "last_founder_touch": (
                        memory_summary.get("last_founder_touch").isoformat()
                        if isinstance(memory_summary.get("last_founder_touch"), datetime)
                        else None
                    ),
                },
                "recommended_founder_action": action.get("action_label"),
                "recommended_founder_action_code": action.get("action_code"),
                "followup_urgency": urgency,
                "urgency_score": urgency.get("urgency_score"),
                "urgency_level": urgency.get("urgency_level"),
                "outreach_cadence": action.get("cadence"),
            }
        )

    queue.sort(
        key=lambda item: (
            _int(item.get("urgency_score")),
            TEMPERATURE_RANK.get(str(item.get("relationship_temperature") or "cold"), 1),
            _int(item.get("intent_score") or item.get("score")),
        ),
        reverse=True,
    )
    return queue[:limit]


def summarize_founder_actions(
    rows: Iterable[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    queue = build_founder_action_queue(rows, now=now, limit=limit)
    stalled = detect_stalled_opportunities(queue, now=now)
    return {
        "total_items": len(queue),
        "stalled_count": len(stalled),
        "top_action": queue[0]["recommended_founder_action"] if queue else "Wait before next outreach",
        "urgency_levels": dict(Counter(str(item.get("urgency_level") or "low") for item in queue)),
        "relationship_temperatures": dict(Counter(str(item.get("relationship_temperature") or "cold") for item in queue)),
        "top_actions": dict(Counter(str(item.get("recommended_founder_action") or "") for item in queue)),
    }
