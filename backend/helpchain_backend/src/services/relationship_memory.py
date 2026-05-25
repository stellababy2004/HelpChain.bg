from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


STAGE_ORDER = {
    "discovery": 1,
    "institutional_evaluation": 2,
    "governance_review": 3,
    "pilot_framing": 4,
    "pilot_discussion": 5,
    "operational_validation": 6,
    "dormant": 0,
}


def _as_paths(row: dict[str, Any]) -> list[str]:
    paths = row.get("paths") or row.get("visited_paths") or row.get("top_paths") or []
    if isinstance(paths, str):
        return [paths]
    return [str(path) for path in paths if path]


def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def infer_relationship_stage(row: dict[str, Any]) -> str:
    paths = set(_as_paths(row))
    score = int(row.get("intent_score") or row.get("score") or 0)
    opportunity = str(row.get("opportunity_level") or "").lower()
    friction = str(row.get("possible_friction") or row.get("friction_reason") or "").lower()

    if row.get("contacted_at") and not row.get("last_activity_at"):
        return "dormant"

    if "high-priority" in opportunity or score >= 200:
        return "operational_validation"

    if row.get("has_demo") or "/demo" in paths or "/demander-acces" in paths:
        return "pilot_discussion"

    if "/deploiement" in paths and ("/offre" in paths or score >= 100):
        return "pilot_framing"

    if friction or {"/securite", "/confidentialite", "/architecture"} & paths:
        return "governance_review"

    if score >= 40 or {"/professionnels", "/pour-les-structures", "/collectivites-associations"} & paths:
        return "institutional_evaluation"

    return "discovery"


def build_account_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []

    for event in events or []:
        ts = _as_dt(event.get("created_at") or event.get("timestamp") or event.get("last_activity_at"))
        paths = _as_paths(event)

        timeline.append(
            {
                "timestamp": ts.isoformat() if ts else None,
                "event_type": event.get("event_type") or event.get("type") or "activity",
                "paths": paths,
                "intent_score": int(event.get("intent_score") or event.get("score") or 0),
                "relationship_stage": infer_relationship_stage(event),
            }
        )

    return sorted(timeline, key=lambda item: item.get("timestamp") or "")


def detect_followup_status(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    last_activity = _as_dt(row.get("last_activity_at") or row.get("last_seen_at"))
    contacted_at = _as_dt(row.get("contacted_at"))

    score = int(row.get("intent_score") or row.get("score") or 0)
    stage = infer_relationship_stage(row)

    if contacted_at:
        return {
            "followup_status": "contacted",
            "followup_priority": "normal",
            "reason": "Founder outreach already recorded",
        }

    if score >= 100 or stage in {"pilot_framing", "pilot_discussion", "operational_validation"}:
        return {
            "followup_status": "due",
            "followup_priority": "high",
            "reason": "High institutional intent without recorded outreach",
        }

    if last_activity:
        age_days = max((now - last_activity).days, 0)
        if age_days >= 7 and score >= 40:
            return {
                "followup_status": "stale",
                "followup_priority": "medium",
                "reason": "Qualified activity is getting older without outreach",
            }

    return {
        "followup_status": "observe",
        "followup_priority": "low",
        "reason": "Not enough qualified relationship signal yet",
    }


def build_relationship_memory(row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    stage = infer_relationship_stage(row)
    followup = detect_followup_status(row)

    return {
        "relationship_stage": stage,
        "relationship_stage_rank": STAGE_ORDER.get(stage, 0),
        "timeline": build_account_timeline(events or []),
        "followup": followup,
        "recommended_relationship_action": recommend_relationship_action(stage, followup),
    }


def recommend_relationship_action(stage: str, followup: dict[str, Any]) -> str:
    if followup.get("followup_status") == "contacted":
        return "Continue structured follow-up"

    if stage in {"pilot_framing", "pilot_discussion", "operational_validation"}:
        return "Send structured pilot outreach"

    if stage == "governance_review":
        return "Send governance and trust reassurance"

    if stage == "institutional_evaluation":
        return "Share institutional fit and deployment framing"

    return "Continue observation"
