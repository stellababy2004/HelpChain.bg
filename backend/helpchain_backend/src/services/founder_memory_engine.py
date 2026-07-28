from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any, Iterable

from .display_safety import safe_organization, safe_territory
from .institutional_intent import TRUST_GOVERNANCE_PATHS, normalize_intent_path
from .territorial_intelligence import normalize_territory_name

TEMPERATURE_RANK = {
    "cold": 1,
    "warming": 2,
    "active": 3,
    "high_intent": 4,
    "stalled": 5,
    "strategic": 6,
}
PILOT_EVENT_TYPES = {
    "pilot_exchange_requested",
    "deployment_pilot_cta_clicked",
    "request_pilot_access",
    "pilot_request",
}
OUTREACH_EVENT_TYPES = {
    "founder_outreach_sent",
    "founder_followup_sent",
    "contacted",
    "mark_contacted",
}
MANUAL_NOTE_EVENT_TYPES = {"founder_manual_note", "manual_note", "note_added"}
DEMO_SUBMISSION_EVENT_TYPES = {"demo_submission", "contact_submission", "demo_requested"}


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


def _read(row: Any, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_path(path: str | None) -> str | None:
    normalized = normalize_intent_path(path)
    if normalized:
        return normalized
    raw = (path or "").strip()
    return raw or None


def _event_timestamp(payload: dict[str, Any]) -> datetime | None:
    return _as_utc_naive(
        payload.get("timestamp")
        or payload.get("created_at")
        or payload.get("submitted_at")
        or payload.get("last_activity_at")
    )


def _append_event(
    bucket: list[dict[str, Any]],
    *,
    timestamp: datetime | None,
    event_type: str,
    label: str,
    source: str,
    path: str | None = None,
    note: str | None = None,
) -> None:
    bucket.append(
        {
            "timestamp": timestamp.isoformat() if timestamp else None,
            "timestamp_dt": timestamp,
            "event_type": event_type,
            "label": label,
            "source": source,
            "path": path,
            "note": (note or "").strip() or None,
        }
    )


def _telemetry_label(event_type: str, path: str | None) -> str:
    normalized_path = _normalize_path(path)
    if event_type == "deployment_pilot_cta_clicked":
        return "clicked deployment CTA"
    if event_type == "security_trust_cta_clicked":
        return "clicked security trust CTA"
    if event_type == "governance_contact_cta_clicked":
        return "requested governance contact"
    if event_type == "pilot_exchange_requested":
        return "requested pilot access"
    if event_type == "professional_access_interest":
        return "expressed professional access interest"
    if event_type == "structure_deployment_interest":
        return "expressed deployment interest"
    if normalized_path == "/deploiement":
        return "viewed /deploiement"
    if normalized_path in TRUST_GOVERNANCE_PATHS:
        return f"viewed {normalized_path}"
    if normalized_path:
        return f"visited {normalized_path}"
    return "visited public page"


def _row_paths(row: dict[str, Any]) -> list[str]:
    raw_paths = (
        row.get("timeline_paths")
        or row.get("top_paths")
        or row.get("pages_viewed")
        or row.get("paths")
        or []
    )
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    normalized: list[str] = []
    for path in raw_paths:
        candidate = _normalize_path(str(path or ""))
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _row_telemetry_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    created_at = _as_utc_naive(row.get("created_at"))
    last_activity_at = _as_utc_naive(row.get("last_activity") or row.get("last_activity_at"))
    for index, path in enumerate(_row_paths(row)):
        timestamp = created_at if index == 0 else last_activity_at or created_at
        _append_event(
            events,
            timestamp=timestamp,
            event_type="page_view",
            label=_telemetry_label("page_view", path),
            source="telemetry",
            path=path,
        )
    if row.get("repeated_engagement_detected") and "/deploiement" in _row_paths(row):
        _append_event(
            events,
            timestamp=last_activity_at,
            event_type="revisited_deployment_page",
            label="revisited deployment page",
            source="telemetry",
            path="/deploiement",
        )
    return events


def build_founder_memory_timeline(
    row: dict[str, Any] | None = None,
    *,
    telemetry_events: Iterable[dict[str, Any]] | None = None,
    outreach_actions: Iterable[dict[str, Any]] | None = None,
    pilot_requests: Iterable[dict[str, Any]] | None = None,
    submissions: Iterable[dict[str, Any]] | None = None,
    manual_notes: Iterable[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    source_row = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    timeline: list[dict[str, Any]] = []

    explicit_telemetry = list(telemetry_events or []) or _row_telemetry_events(source_row)
    for event in explicit_telemetry:
        event_type = str(event.get("event_type") or "page_view").strip()
        path = _normalize_path(event.get("page_url") or event.get("path"))
        _append_event(
            timeline,
            timestamp=_event_timestamp(event),
            event_type=event_type,
            label=str(event.get("label") or _telemetry_label(event_type, path)),
            source="telemetry",
            path=path,
        )

    explicit_outreach = list(outreach_actions or [])
    if not explicit_outreach and _as_utc_naive(source_row.get("contacted_at")):
        explicit_outreach = [
            {
                "timestamp": source_row.get("contacted_at"),
                "event_type": "founder_outreach_sent",
                "label": "founder outreach sent",
            }
        ]
    for action in explicit_outreach:
        event_type = str(action.get("event_type") or "founder_outreach_sent").strip()
        label = str(action.get("label") or "founder outreach sent").strip() or "founder outreach sent"
        _append_event(
            timeline,
            timestamp=_event_timestamp(action),
            event_type=event_type,
            label=label,
            source="outreach",
            note=action.get("note"),
        )

    explicit_pilot_requests = list(pilot_requests or [])
    if not explicit_pilot_requests and str(source_row.get("kind") or "") == "access_request":
        explicit_pilot_requests = [
            {
                "timestamp": source_row.get("created_at") or source_row.get("last_activity"),
                "event_type": "pilot_request",
                "label": "requested pilot access",
            }
        ]
    for request in explicit_pilot_requests:
        _append_event(
            timeline,
            timestamp=_event_timestamp(request),
            event_type=str(request.get("event_type") or "pilot_request").strip() or "pilot_request",
            label=str(request.get("label") or "requested pilot access").strip() or "requested pilot access",
            source="pilot_request",
            note=request.get("note"),
        )

    explicit_submissions = list(submissions or [])
    if not explicit_submissions and str(source_row.get("stage") or "") in {
        "demo_booked",
        "demo_done",
        "pilot_proposed",
    }:
        explicit_submissions = [
            {
                "timestamp": source_row.get("last_activity") or source_row.get("created_at"),
                "event_type": "demo_submission",
                "label": "submitted demo request",
            }
        ]
    for submission in explicit_submissions:
        kind = str(submission.get("kind") or submission.get("event_type") or "").strip()
        default_label = "submitted contact request" if "contact" in kind else "submitted demo request"
        _append_event(
            timeline,
            timestamp=_event_timestamp(submission),
            event_type=str(submission.get("event_type") or kind or "submission").strip() or "submission",
            label=str(submission.get("label") or default_label).strip() or default_label,
            source="submission",
            note=submission.get("note"),
        )

    explicit_notes = list(manual_notes or [])
    if not explicit_notes and str(source_row.get("next_action_note") or "").strip():
        explicit_notes = [
            {
                "timestamp": source_row.get("next_action_at") or source_row.get("last_activity"),
                "event_type": "founder_manual_note",
                "label": "founder note added",
                "note": source_row.get("next_action_note"),
            }
        ]
    for note in explicit_notes:
        _append_event(
            timeline,
            timestamp=_event_timestamp(note),
            event_type=str(note.get("event_type") or "founder_manual_note").strip() or "founder_manual_note",
            label=str(note.get("label") or "founder note added").strip() or "founder note added",
            source="manual_note",
            note=note.get("note"),
        )

    contacted_at = _as_utc_naive(source_row.get("contacted_at"))
    last_activity_at = _as_utc_naive(source_row.get("last_activity") or source_row.get("last_activity_at"))
    if contacted_at:
        reference_activity = last_activity_at if last_activity_at and last_activity_at > contacted_at else None
        if reference_activity is None:
            gap_days = max(0, (current_time - contacted_at).days)
            if gap_days >= 9:
                _append_event(
                    timeline,
                    timestamp=current_time,
                    event_type="no_response",
                    label=f"no response for {gap_days} days",
                    source="relationship_state",
                )

    timeline.sort(
        key=lambda item: (
            item.get("timestamp_dt") or datetime.min,
            str(item.get("label") or ""),
            str(item.get("event_type") or ""),
        )
    )
    for item in timeline:
        item.pop("timestamp_dt", None)
    return timeline


def detect_relationship_temperature(
    row: dict[str, Any] | None = None,
    *,
    timeline: Iterable[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> str:
    payload = dict(row or {})
    events = list(timeline or payload.get("timeline_events") or [])
    current_time = _as_utc_naive(now) or _now_utc()
    intent_score = int(payload.get("intent_score") or payload.get("score") or 0)
    priority_level = str(payload.get("priority_level") or "").strip().lower()
    repeated = bool(payload.get("repeated_engagement_detected"))
    labels = [str(event.get("label") or "") for event in events]
    event_types = {str(event.get("event_type") or "") for event in events}
    founder_touch_at = max(
        (
            _as_utc_naive(event.get("timestamp"))
            for event in events
            if str(event.get("source") or "") in {"outreach", "manual_note"}
        ),
        default=_as_utc_naive(payload.get("last_founder_touch") or payload.get("contacted_at")),
    )
    last_activity_at = max(
        (
            _as_utc_naive(event.get("timestamp"))
            for event in events
            if _as_utc_naive(event.get("timestamp")) is not None
        ),
        default=_as_utc_naive(payload.get("last_activity") or payload.get("last_activity_at")),
    )
    stalled_gap = 0
    if founder_touch_at:
        stalled_gap = max(0, (current_time - founder_touch_at).days)

    if "no_response" in event_types or (
        founder_touch_at and stalled_gap >= 9 and (last_activity_at is None or last_activity_at <= founder_touch_at)
    ):
        return "stalled"
    if priority_level == "strategic" or (
        intent_score >= 85 and repeated and normalize_territory_name(payload.get("territory"))
    ):
        return "strategic"
    if any(event_type in PILOT_EVENT_TYPES for event_type in event_types) or intent_score >= 80:
        return "high_intent"
    if any(event_type in OUTREACH_EVENT_TYPES | DEMO_SUBMISSION_EVENT_TYPES for event_type in event_types) or len(events) >= 4:
        return "active"
    if repeated or len(events) >= 2 or intent_score >= 40 or any("/deploiement" in label for label in labels):
        return "warming"
    return "cold"


def summarize_founder_memory(
    timeline: Iterable[dict[str, Any]] | None = None,
    *,
    row: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    events = list(timeline or [])
    payload = dict(row or {})
    current_time = _as_utc_naive(now) or _now_utc()
    timestamps = [
        _as_utc_naive(event.get("timestamp"))
        for event in events
        if _as_utc_naive(event.get("timestamp")) is not None
    ]
    last_activity_at = max(
        timestamps,
        default=_as_utc_naive(payload.get("last_activity") or payload.get("last_activity_at")),
    )
    last_founder_touch = max(
        (
            _as_utc_naive(event.get("timestamp"))
            for event in events
            if str(event.get("source") or "") in {"outreach", "manual_note"}
            and _as_utc_naive(event.get("timestamp")) is not None
        ),
        default=_as_utc_naive(payload.get("last_founder_touch") or payload.get("contacted_at")),
    )
    relationship_temperature = detect_relationship_temperature(
        payload,
        timeline=events,
        now=current_time,
    )
    category_counts = Counter(str(event.get("source") or "unknown") for event in events)
    days_since_founder_touch = (
        max(0, (current_time - last_founder_touch).days) if last_founder_touch else None
    )
    return {
        "timeline_events": events,
        "timeline_event_count": len(events),
        "last_timeline_event": events[-1]["label"] if events else None,
        "last_activity_at": last_activity_at,
        "last_founder_touch": last_founder_touch,
        "relationship_temperature": relationship_temperature,
        "timeline_sources": dict(category_counts),
        "has_outreach": bool(category_counts.get("outreach")),
        "has_pilot_request": bool(category_counts.get("pilot_request")),
        "has_manual_note": bool(category_counts.get("manual_note")),
        "days_since_founder_touch": days_since_founder_touch,
        "founder_action_state": {
            "relationship_temperature": relationship_temperature,
            "last_founder_touch": last_founder_touch.isoformat() if last_founder_touch else None,
            "timeline_events": len(events),
        },
    }


def detect_stalled_opportunities(
    rows: Iterable[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current_time = _as_utc_naive(now) or _now_utc()
    stalled: list[dict[str, Any]] = []
    for row in rows or []:
        payload = dict(row or {})
        timeline = list(payload.get("timeline_events") or [])
        memory = summarize_founder_memory(timeline, row=payload, now=current_time)
        temperature = str(memory.get("relationship_temperature") or "cold")
        intent_score = int(payload.get("intent_score") or payload.get("score") or 0)
        last_founder_touch = memory.get("last_founder_touch")
        if not isinstance(last_founder_touch, datetime):
            continue
        stalled_days = max(0, (current_time - last_founder_touch).days)
        if temperature != "stalled" and not (
            intent_score >= 60 and stalled_days >= 9 and not memory.get("has_manual_note")
        ):
            continue
        stalled.append(
            {
                "uid": str(payload.get("uid") or ""),
                "organization": safe_organization(payload.get("organization")),
                "territory": safe_territory(normalize_territory_name(payload.get("city") or payload.get("territory"))),
                "relationship_temperature": "stalled",
                "stalled_days": stalled_days,
                "reason": f"No response for {stalled_days} days after founder touch",
                "last_founder_touch": last_founder_touch,
                "intent_score": intent_score,
                "timeline_events": timeline,
            }
        )
    stalled.sort(
        key=lambda item: (
            int(item.get("intent_score") or 0),
            int(item.get("stalled_days") or 0),
            str(item.get("organization") or ""),
        ),
        reverse=True,
    )
    return stalled
