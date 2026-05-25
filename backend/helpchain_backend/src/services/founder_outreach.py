from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


OUTREACH_STAGES = {
    "not_contacted",
    "contacted",
    "replied",
    "meeting_scheduled",
    "pilot_started",
    "rejected",
    "dormant",
}


def normalize_outreach_stage(value: str | None) -> str:
    stage = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return stage if stage in OUTREACH_STAGES else "not_contacted"


def recommend_next_outreach_action(row: dict[str, Any]) -> str:
    stage = normalize_outreach_stage(row.get("outreach_stage"))
    relationship_stage = str(row.get("relationship_stage") or "")
    followup_priority = str(row.get("followup_priority") or "")
    account_strength = str(row.get("account_strength") or "")

    if stage == "not_contacted" and followup_priority == "high":
        return "Send first structured founder outreach"

    if stage == "contacted":
        return "Schedule follow-up if no response"

    if stage == "replied":
        return "Propose a short qualification call"

    if stage == "meeting_scheduled":
        return "Prepare pilot framing notes"

    if stage == "pilot_started":
        return "Track pilot success criteria"

    if stage == "rejected":
        return "Archive or revisit later"

    if stage == "dormant":
        return "Re-engage only if new activity appears"

    if relationship_stage in {"pilot_framing", "pilot_discussion"} or account_strength == "strong":
        return "Prepare personalized institutional outreach"

    return "Continue observing"


def build_outreach_status(row: dict[str, Any]) -> dict[str, Any]:
    stage = normalize_outreach_stage(row.get("outreach_stage"))
    now = datetime.now(timezone.utc).isoformat()

    return {
        "outreach_stage": stage,
        "outreach_label": stage.replace("_", " ").title(),
        "recommended_outreach_action": recommend_next_outreach_action(row),
        "generated_at": now,
    }
