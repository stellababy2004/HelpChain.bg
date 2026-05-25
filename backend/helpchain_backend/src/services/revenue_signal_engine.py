from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Iterable

from .institutional_intent import (
    DEPLOYMENT_OPERATIONS_PATHS,
    TRUST_GOVERNANCE_PATHS,
    build_intent_summary,
    normalize_intent_path,
)
from .territorial_intelligence import normalize_territory_name

PILOT_SIGNAL_KEYWORDS = ("pilot", "demo", "access", "deployment")
GOVERNANCE_SIGNAL_KEYWORDS = ("security", "trust", "governance", "architecture")
OUTREACH_SIGNAL_KEYWORDS = ("contact", "exchange", "outreach", "access")
PILOT_SIGNAL_PATHS = {
    "/demo",
    "/demander-acces",
    "/professionnels/pilote",
    "/deploiement",
}
OUTREACH_SIGNAL_PATHS = {"/contact", "/demo", "/demander-acces"}
SIGNAL_LOOKBACK_DAYS = 30


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_naive(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _read(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_event(row) -> dict[str, object]:
    return {
        "event_type": str(_read(row, "event_type") or "").strip(),
        "page_url": normalize_intent_path(_read(row, "page_url")) or "",
        "created_at": _as_utc_naive(_read(row, "created_at")),
        "user_session": str(_read(row, "user_session") or _read(row, "session_id") or "").strip(),
    }


def _event_matches(event_type: str, page_url: str, keywords: tuple[str, ...], paths: set[str]) -> bool:
    lowered_type = event_type.lower()
    return page_url in paths or any(token in lowered_type for token in keywords)


def _recency_score(last_activity_at: datetime | None, *, now: datetime) -> int:
    if not last_activity_at:
        return 0
    age = now - last_activity_at
    if age <= timedelta(days=2):
        return 24
    if age <= timedelta(days=7):
        return 16
    if age <= timedelta(days=14):
        return 8
    if age <= timedelta(days=30):
        return 3
    return 0


def compute_institutional_intent_score(profile: dict[str, object]) -> int:
    base_intent = int(profile.get("base_intent_score") or 0)
    repeated_cta_clicks = int(profile.get("repeated_cta_clicks") or 0)
    repeated_sessions = int(profile.get("repeated_sessions") or 0)
    pilot_events = int(profile.get("pilot_event_count") or 0)
    governance_events = int(profile.get("governance_event_count") or 0)
    outreach_events = int(profile.get("outreach_event_count") or 0)
    deployment_events = int(profile.get("deployment_event_count") or 0)
    territory_repeat_count = int(profile.get("territory_repeat_count") or 0)
    density = float(profile.get("institutional_intent_density") or 0.0)
    recency = int(profile.get("recency_score") or 0)

    score = min(48, int(base_intent * 0.35))
    score += min(18, repeated_cta_clicks * 6)
    score += min(16, repeated_sessions * 8)
    score += min(18, pilot_events * 4)
    score += min(12, governance_events * 3)
    score += min(12, deployment_events * 3)
    score += min(14, outreach_events * 7)
    score += min(10, max(0, territory_repeat_count - 1) * 5)
    score += min(14, int(density / 7))
    score += recency
    return int(min(100, score))


def build_revenue_signal_profile(
    events: Iterable[object],
    *,
    territory: str | None = None,
    territory_repeat_count: int = 0,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = _as_utc_naive(now) or _now_utc()
    normalized_events = [_normalize_event(row) for row in events or []]
    normalized_events = [row for row in normalized_events if row["event_type"] or row["page_url"]]

    event_type_counts = Counter(
        str(row["event_type"])
        for row in normalized_events
        if str(row["event_type"])
    )
    page_paths = [
        str(row["page_url"])
        for row in normalized_events
        if str(row["page_url"])
    ]
    unique_paths = list(dict.fromkeys(page_paths))
    active_days = sorted(
        {
            row["created_at"].date()
            for row in normalized_events
            if isinstance(row.get("created_at"), datetime)
        }
    )
    last_activity_at = max(
        (row["created_at"] for row in normalized_events if isinstance(row.get("created_at"), datetime)),
        default=None,
    )

    cta_events = sum(
        count for event_type, count in event_type_counts.items() if event_type != "page_view"
    )
    repeated_cta_clicks = sum(
        max(0, count - 1)
        for event_type, count in event_type_counts.items()
        if event_type != "page_view"
    )
    pilot_event_count = sum(
        1
        for row in normalized_events
        if _event_matches(
            str(row["event_type"]),
            str(row["page_url"]),
            PILOT_SIGNAL_KEYWORDS,
            PILOT_SIGNAL_PATHS,
        )
    )
    governance_event_count = sum(
        1
        for row in normalized_events
        if _event_matches(
            str(row["event_type"]),
            str(row["page_url"]),
            GOVERNANCE_SIGNAL_KEYWORDS,
            TRUST_GOVERNANCE_PATHS,
        )
    )
    outreach_event_count = sum(
        1
        for row in normalized_events
        if _event_matches(
            str(row["event_type"]),
            str(row["page_url"]),
            OUTREACH_SIGNAL_KEYWORDS,
            OUTREACH_SIGNAL_PATHS,
        )
    )
    deployment_event_count = sum(
        1
        for row in normalized_events
        if str(row["page_url"]) in DEPLOYMENT_OPERATIONS_PATHS
        or "deployment" in str(row["event_type"]).lower()
        or "structure" in str(row["event_type"]).lower()
    )
    intent_summary = build_intent_summary(
        unique_paths,
        has_submit=any(
            str(row["event_type"]).endswith("_form_submit") for row in normalized_events
        ),
    )
    total_events = len(normalized_events)
    signal_weight = (
        pilot_event_count * 3
        + governance_event_count * 2
        + outreach_event_count * 3
        + deployment_event_count * 2
        + cta_events
    )
    density = round((signal_weight / max(1, total_events)) * 20, 2)
    normalized_territory = normalize_territory_name(territory)

    profile = {
        "session_id": str(
            next(
                (row["user_session"] for row in normalized_events if str(row["user_session"])),
                "",
            )
        ),
        "event_count": total_events,
        "cta_event_count": cta_events,
        "repeated_cta_clicks": repeated_cta_clicks,
        "active_days": len(active_days),
        "repeated_sessions": max(0, len(active_days) - 1),
        "pilot_event_count": pilot_event_count,
        "governance_event_count": governance_event_count,
        "outreach_event_count": outreach_event_count,
        "deployment_event_count": deployment_event_count,
        "last_activity_at": last_activity_at,
        "territory": normalized_territory or None,
        "territory_repeat_count": max(0, int(territory_repeat_count or 0)),
        "institutional_intent_density": density,
        "top_paths": list(intent_summary.get("top_paths") or [])[:6],
        "intent_tier": str(intent_summary.get("tier") or "").strip(),
        "intent_label": str(intent_summary.get("label") or "").strip(),
        "primary_interest": str(intent_summary.get("primary_interest") or "unknown").strip() or "unknown",
        "base_intent_score": int(intent_summary.get("score") or 0),
        "recency_score": _recency_score(last_activity_at, now=current_time),
        "has_outreach": outreach_event_count > 0,
    }
    profile["intent_score"] = compute_institutional_intent_score(profile)
    return profile


def summarize_revenue_signal_profile(
    profiles: Iterable[dict[str, object]],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = _as_utc_naive(now) or _now_utc()
    normalized_profiles = [dict(item) for item in profiles or [] if isinstance(item, dict)]

    for profile in normalized_profiles:
        if "intent_score" not in profile:
            profile["intent_score"] = compute_institutional_intent_score(profile)
        if "recency_score" not in profile:
            profile["recency_score"] = _recency_score(
                _as_utc_naive(profile.get("last_activity_at")),
                now=current_time,
            )

    hot_this_week = [
        profile
        for profile in normalized_profiles
        if int(profile.get("intent_score") or 0) >= 65
        and int(profile.get("recency_score") or 0) >= 16
        and (
            int(profile.get("repeated_sessions") or 0) >= 1
            or int(profile.get("repeated_cta_clicks") or 0) >= 1
        )
    ]
    hot_this_week.sort(
        key=lambda item: (
            int(item.get("intent_score") or 0),
            int(item.get("pilot_event_count") or 0),
            int(item.get("recency_score") or 0),
        ),
        reverse=True,
    )

    silent_high_intent = [
        profile
        for profile in normalized_profiles
        if int(profile.get("intent_score") or 0) >= 60
        and not bool(profile.get("has_outreach"))
        and int(profile.get("pilot_event_count") or 0) + int(profile.get("governance_event_count") or 0) >= 2
    ]
    silent_high_intent.sort(
        key=lambda item: (
            int(item.get("intent_score") or 0),
            int(item.get("repeated_cta_clicks") or 0),
            int(item.get("territory_repeat_count") or 0),
        ),
        reverse=True,
    )

    territory_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for profile in normalized_profiles:
        territory = normalize_territory_name(str(profile.get("territory") or ""))
        if not territory:
            continue
        territory_groups[territory].append(profile)

    territory_acceleration: list[dict[str, object]] = []
    for territory, items in territory_groups.items():
        repeated_sessions = sum(int(item.get("repeated_sessions") or 0) for item in items)
        repeated_cta_clicks = sum(int(item.get("repeated_cta_clicks") or 0) for item in items)
        total_score = sum(int(item.get("intent_score") or 0) for item in items)
        if len(items) < 2 and repeated_sessions < 1 and repeated_cta_clicks < 2:
            continue
        dominant_interest = Counter(
            str(item.get("primary_interest") or "unknown") for item in items
        ).most_common(1)[0][0]
        territory_acceleration.append(
            {
                "territory": territory,
                "session_count": len(items),
                "repeat_session_count": repeated_sessions,
                "repeated_cta_clicks": repeated_cta_clicks,
                "total_intent_score": total_score,
                "dominant_interest": dominant_interest,
                "recommended_action": "Prioritize territory-level founder outreach"
                if total_score >= 140
                else "Continue structured observation",
            }
        )
    territory_acceleration.sort(
        key=lambda item: (
            int(item.get("total_intent_score") or 0),
            int(item.get("session_count") or 0),
            int(item.get("repeated_cta_clicks") or 0),
        ),
        reverse=True,
    )

    return {
        "profiles": normalized_profiles,
        "hot_this_week": hot_this_week[:6],
        "silent_high_intent": silent_high_intent[:6],
        "territory_acceleration": territory_acceleration[:6],
    }
