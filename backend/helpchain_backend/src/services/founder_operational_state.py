from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any, Iterable

from .founder_action_engine import (
    compute_followup_urgency,
    recommend_founder_action,
)
from .founder_memory_engine import (
    build_founder_memory_timeline,
    summarize_founder_memory,
)
from .territorial_intelligence import normalize_territory_name

RELATIONSHIP_STATE_ORDER = {
    "unknown": 0,
    "observed": 1,
    "engaged": 2,
    "contacted": 3,
    "replied": 4,
    "meeting_planned": 5,
    "pilot_framing": 6,
    "pilot_active": 7,
    "stalled": 8,
    "archived": 9,
}
PILOT_PROGRESSION_ORDER = {
    "none": 0,
    "interest_detected": 1,
    "qualification_needed": 2,
    "pilot_discussion": 3,
    "pilot_ready": 4,
    "pilot_active": 5,
    "expansion_candidate": 6,
}
ARCHIVED_STAGES = {"lost", "rejected", "archived", "closed", "won"}
MEETING_STAGES = {"demo_booked", "meeting_scheduled"}
PILOT_DISCUSSION_STAGES = {"demo_done", "pilot_proposed", "negotiation"}
PILOT_ACTIVE_STAGES = {"pilot_active", "active", "approved"}


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_naive(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _read(row: Any, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def compute_relationship_state(
    row: dict[str, Any] | None = None,
    *,
    memory_summary: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    payload = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    summary = dict(memory_summary or {})
    if not summary:
        timeline = payload.get("timeline_events") or build_founder_memory_timeline(payload, now=current_time)
        summary = summarize_founder_memory(timeline, row=payload, now=current_time)

    stage = str(payload.get("stage") or payload.get("status") or "").strip().lower()
    intent_score = _int(payload.get("intent_score") or payload.get("score"))
    temperature = str(summary.get("relationship_temperature") or "cold")
    timeline = list(summary.get("timeline_events") or payload.get("timeline_events") or [])
    event_types = {str(event.get("event_type") or "").strip() for event in timeline}
    has_outreach = bool(summary.get("has_outreach"))
    last_founder_touch = _as_utc_naive(summary.get("last_founder_touch"))
    last_activity_at = _as_utc_naive(summary.get("last_activity_at"))

    if stage in ARCHIVED_STAGES:
        return "archived"
    if temperature == "stalled":
        return "stalled"
    if stage in PILOT_ACTIVE_STAGES:
        return "pilot_active"
    if stage in PILOT_DISCUSSION_STAGES:
        return "pilot_framing"
    if stage in MEETING_STAGES:
        return "meeting_planned"
    if "pilot_request" in event_types or "pilot_exchange_requested" in event_types:
        return "pilot_framing"
    if has_outreach and last_founder_touch and last_activity_at and last_activity_at > last_founder_touch:
        return "replied"
    if has_outreach:
        return "contacted"
    if temperature in {"active", "high_intent", "strategic"} or len(timeline) >= 3:
        return "engaged"
    if temperature == "warming" or intent_score >= 25 or timeline:
        return "observed"
    return "unknown"


def compute_pilot_progression(
    row: dict[str, Any] | None = None,
    *,
    relationship_state: str | None = None,
    memory_summary: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    payload = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    summary = dict(memory_summary or {})
    if not summary:
        timeline = payload.get("timeline_events") or build_founder_memory_timeline(payload, now=current_time)
        summary = summarize_founder_memory(timeline, row=payload, now=current_time)

    resolved_relationship_state = str(
        relationship_state or compute_relationship_state(payload, memory_summary=summary, now=current_time)
    )
    stage = str(payload.get("stage") or payload.get("status") or "").strip().lower()
    intent_score = _int(payload.get("intent_score") or payload.get("score"))
    primary_interest = str(payload.get("primary_interest") or "").strip()
    has_pilot_request = bool(summary.get("has_pilot_request"))

    if resolved_relationship_state == "archived":
        return "none"
    if resolved_relationship_state == "pilot_active" and stage == "won":
        return "expansion_candidate"
    if resolved_relationship_state == "pilot_active":
        return "pilot_active"
    if stage in PILOT_DISCUSSION_STAGES or (
        resolved_relationship_state in {"pilot_framing", "meeting_planned"} and intent_score >= 70
    ):
        return "pilot_ready"
    if stage in MEETING_STAGES or has_pilot_request or resolved_relationship_state in {"pilot_framing", "meeting_planned"}:
        return "pilot_discussion"
    if primary_interest == "deployment_operations" and intent_score >= 55:
        return "qualification_needed"
    if intent_score >= 30 or primary_interest in {"deployment_operations", "trust_governance"}:
        return "interest_detected"
    return "none"


def detect_state_transition(
    previous_state: dict[str, Any] | None,
    current_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    previous = dict(previous_state or {})
    current = dict(current_state or {})
    if not previous or not current:
        return None

    transition_labels: list[str] = []
    if previous.get("relationship_state") != current.get("relationship_state"):
        transition_labels.append("relationship_state_changed")
    if previous.get("pilot_progression") != current.get("pilot_progression"):
        transition_labels.append("pilot_progression_changed")
    if previous.get("next_recommended_action") != current.get("next_recommended_action"):
        transition_labels.append("action_changed")

    if not transition_labels:
        return None

    return {
        "uid": str(current.get("uid") or previous.get("uid") or ""),
        "organization": str(
            current.get("organization")
            or previous.get("organization")
            or current.get("organization_state", {}).get("organization")
            or "Institutional account"
        ),
        "transition_labels": transition_labels,
        "from_relationship_state": previous.get("relationship_state"),
        "to_relationship_state": current.get("relationship_state"),
        "from_pilot_progression": previous.get("pilot_progression"),
        "to_pilot_progression": current.get("pilot_progression"),
        "from_action": previous.get("next_recommended_action"),
        "to_action": current.get("next_recommended_action"),
    }


def merge_founder_memory_with_actions(
    row: dict[str, Any] | None = None,
    *,
    memory_summary: dict[str, Any] | None = None,
    action_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    summary = dict(memory_summary or {})
    if not summary:
        timeline = payload.get("timeline_events") or build_founder_memory_timeline(payload, now=current_time)
        summary = summarize_founder_memory(timeline, row=payload, now=current_time)

    action = dict(action_payload or {})
    if not action:
        urgency = compute_followup_urgency(payload, memory_summary=summary, now=current_time)
        action = recommend_founder_action(payload, memory_summary=summary, urgency=urgency, now=current_time)
        action["urgency"] = urgency

    return {
        "uid": str(payload.get("uid") or ""),
        "organization": str(payload.get("organization") or "Institutional account"),
        "timeline_events": list(summary.get("timeline_events") or []),
        "founder_touch_history": [
            event
            for event in list(summary.get("timeline_events") or [])
            if str(event.get("source") or "") in {"outreach", "manual_note"}
        ],
        "last_founder_touch": _as_utc_naive(summary.get("last_founder_touch")),
        "institutional_temperature": str(summary.get("relationship_temperature") or "cold"),
        "next_recommended_action": str(
            action.get("action_label")
            or action.get("recommended_founder_action")
            or payload.get("recommended_founder_action")
            or "Wait before next outreach"
        ),
        "followup_urgency": dict(action.get("urgency") or payload.get("followup_urgency") or {}),
    }


def build_founder_operational_state(
    row: dict[str, Any] | None = None,
    *,
    memory_summary: dict[str, Any] | None = None,
    action_payload: dict[str, Any] | None = None,
    previous_state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    merged = merge_founder_memory_with_actions(
        payload,
        memory_summary=memory_summary,
        action_payload=action_payload,
        now=current_time,
    )
    relationship_state = compute_relationship_state(payload, memory_summary=merged, now=current_time)
    pilot_progression = compute_pilot_progression(
        payload,
        relationship_state=relationship_state,
        memory_summary=merged,
        now=current_time,
    )
    last_founder_touch = _as_utc_naive(merged.get("last_founder_touch"))
    stalled_since = None
    if relationship_state == "stalled" and last_founder_touch:
        stalled_since = last_founder_touch.isoformat()

    state = {
        "uid": str(payload.get("uid") or ""),
        "organization": str(payload.get("organization") or "Institutional account"),
        "organization_state": {
            "uid": str(payload.get("uid") or ""),
            "id": payload.get("id"),
            "kind": str(payload.get("kind") or ""),
            "organization": str(payload.get("organization") or "Institutional account"),
            "stage": str(payload.get("stage") or ""),
            "city": str(payload.get("city") or ""),
            "territory": normalize_territory_name(payload.get("territory") or payload.get("city")) or None,
        },
        "relationship_state": relationship_state,
        "founder_touch_history": list(merged.get("founder_touch_history") or []),
        "institutional_temperature": str(merged.get("institutional_temperature") or "cold"),
        "pilot_progression": pilot_progression,
        "last_founder_touch": last_founder_touch.isoformat() if last_founder_touch else None,
        "next_recommended_action": str(merged.get("next_recommended_action") or "Wait before next outreach"),
        "stalled_since": stalled_since,
        "territory_context": {
            "territory": normalize_territory_name(payload.get("territory") or payload.get("city")) or None,
            "city": str(payload.get("city") or "").strip() or None,
            "priority_level": str(payload.get("priority_level") or "").strip() or None,
            "confidence": str(payload.get("confidence") or "").strip() or None,
            "dominant_interest": str(payload.get("dominant_interest") or payload.get("primary_interest") or "").strip() or None,
            "repeated_engagement_detected": bool(payload.get("repeated_engagement_detected")),
        },
        "timeline_events": list(merged.get("timeline_events") or []),
        "founder_action_state": {
            "relationship_state": relationship_state,
            "pilot_progression": pilot_progression,
            "institutional_temperature": str(merged.get("institutional_temperature") or "cold"),
            "next_recommended_action": str(merged.get("next_recommended_action") or "Wait before next outreach"),
            "last_founder_touch": last_founder_touch.isoformat() if last_founder_touch else None,
        },
    }
    state["state_transition"] = detect_state_transition(previous_state, state)
    return state


def summarize_founder_operational_state(
    rows: Iterable[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = _as_utc_naive(now) or _now_utc()
    states: list[dict[str, Any]] = []
    for row in rows or []:
        payload = dict(row or {})
        if payload.get("relationship_state") and payload.get("pilot_progression"):
            states.append(payload)
            continue
        states.append(build_founder_operational_state(payload, now=current_time))

    relationship_state_summary = dict(
        Counter(str(item.get("relationship_state") or "unknown") for item in states)
    )
    pilot_progression_summary = dict(
        Counter(str(item.get("pilot_progression") or "none") for item in states)
    )
    transitions = [item["state_transition"] for item in states if item.get("state_transition")]
    next_founder_actions = [
        {
            "uid": str(item.get("uid") or ""),
            "organization": str(item.get("organization") or "Institutional account"),
            "relationship_state": str(item.get("relationship_state") or "unknown"),
            "pilot_progression": str(item.get("pilot_progression") or "none"),
            "next_recommended_action": str(item.get("next_recommended_action") or "Wait before next outreach"),
        }
        for item in states
    ]
    next_founder_actions.sort(
        key=lambda item: (
            RELATIONSHIP_STATE_ORDER.get(item["relationship_state"], 0),
            PILOT_PROGRESSION_ORDER.get(item["pilot_progression"], 0),
            item["organization"],
        ),
        reverse=True,
    )

    return {
        "total_items": len(states),
        "relationship_state_summary": relationship_state_summary,
        "pilot_progression_summary": pilot_progression_summary,
        "state_transitions": transitions,
        "next_founder_actions": next_founder_actions[:6],
        "founder_operational_state": states[:8],
    }
