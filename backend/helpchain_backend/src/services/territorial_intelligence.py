from __future__ import annotations

from collections import Counter
import re
import unicodedata
from typing import Iterable

from .institutional_intent import (
    DEPLOYMENT_OPERATIONS_PATHS,
    INSTITUTIONAL_FIT_PATHS,
    TRUST_GOVERNANCE_PATHS,
)

PILOT_PATHS = {"/professionnels/pilote", "/demander-acces", "/demo"}
STRONG_TERRITORY_SOURCES = {"access_request_city", "professional_lead_city"}
MODERATE_TERRITORY_SOURCES = {
    "behavior_location",
    "organization_city",
    "organization_location",
}
WEAK_TERRITORY_SOURCES = {"organization_hint", "email_domain_hint", "inferred_location"}
INTENSITY_THRESHOLDS = (
    (75, "Strategic"),
    (45, "High"),
    (20, "Moderate"),
    (0, "Low"),
)
PRIORITY_RANK = {"Strategic": 4, "High": 3, "Moderate": 2, "Low": 1}
PILOT_READINESS_BY_PRIORITY = {
    "Low": "early",
    "Moderate": "emerging",
    "High": "elevated",
    "Strategic": "strong",
}
LIKELY_MATURITY_BY_PRIORITY = {
    "Low": "early_observation",
    "Moderate": "evaluating",
    "High": "operational_review",
    "Strategic": "pilot_window",
}


def _normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_territory_name(name: str | None) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    normalized = _normalize_text(raw)
    if not normalized:
        return ""
    aliases = {
        "ile de france": "Ile-de-France",
        "boulogne billancourt": "Boulogne-Billancourt",
        "saint denis": "Saint-Denis",
    }
    if normalized in aliases:
        return aliases[normalized]
    words = []
    for token in normalized.split():
        if token in {"ccas", "ehpad", "ssiad"}:
            words.append(token.upper())
        else:
            words.append(token.title())
    return " ".join(words)


def classify_territorial_intensity(score: int | float | None) -> str:
    try:
        normalized_score = max(0, int(score or 0))
    except Exception:
        normalized_score = 0
    for threshold, label in INTENSITY_THRESHOLDS:
        if normalized_score >= threshold:
            return label
    return "Low"


def compute_repeated_engagement(rows: Iterable[dict]) -> dict[str, object]:
    evidence = [row for row in rows or [] if isinstance(row, dict)]
    session_counts = Counter(
        str(row.get("session_id") or "").strip()
        for row in evidence
        if str(row.get("session_id") or "").strip()
    )
    repeated_session_count = sum(1 for count in session_counts.values() if count >= 2)
    repeat_visit_rows = sum(1 for row in evidence if bool(row.get("repeat_visit")))
    pilot_rows = sum(
        1
        for row in evidence
        if any(path in PILOT_PATHS for path in list(row.get("paths") or []))
    )
    trust_rows = sum(
        1
        for row in evidence
        if any(path in TRUST_GOVERNANCE_PATHS for path in list(row.get("paths") or []))
    )
    deployment_offer_rows = sum(
        1
        for row in evidence
        if any(path in DEPLOYMENT_OPERATIONS_PATHS or path == "/offre" for path in list(row.get("paths") or []))
    )
    detected = bool(repeated_session_count or repeat_visit_rows >= 2 or len(evidence) >= 3)
    pattern = "single_touch"
    if pilot_rows >= 2:
        pattern = "repeat_pilot_navigation"
    elif trust_rows >= 2 and repeat_visit_rows:
        pattern = "repeat_trust_review"
    elif deployment_offer_rows >= 2 and repeat_visit_rows:
        pattern = "repeat_offer_deployment_review"
    elif detected:
        pattern = "repeat_commercial_visits"
    strength_score = repeated_session_count + repeat_visit_rows + max(0, len(evidence) - 1)
    if pattern == "repeat_pilot_navigation":
        strength_score += 2
    if strength_score >= 5:
        strength = "strong"
    elif strength_score >= 2:
        strength = "moderate"
    else:
        strength = "weak"
    return {
        "repeated_engagement_detected": detected,
        "engagement_strength": strength,
        "engagement_pattern_label": pattern,
        "repeated_session_count": repeated_session_count,
        "return_session_count": repeat_visit_rows,
    }


def _dominant_interest(rows: Iterable[dict]) -> str:
    counts = Counter()
    for row in rows or []:
        interest = str(row.get("primary_interest") or "").strip()
        if interest:
            counts[interest] += 1
    if not counts:
        return "unknown"
    top_interest, top_count = counts.most_common(1)[0]
    leaders = [label for label, count in counts.items() if count == top_count]
    if len(leaders) > 1:
        return "mixed"
    return top_interest


def _confidence_for_rows(rows: list[dict], repeated: dict[str, object]) -> str:
    sources = {
        str(row.get("territory_source") or "").strip()
        for row in rows
        if str(row.get("territory_source") or "").strip()
    }
    if sources & STRONG_TERRITORY_SOURCES:
        return "strong"
    if (
        sources & MODERATE_TERRITORY_SOURCES
        and (
            bool(repeated.get("repeated_engagement_detected"))
            or len(rows) >= 2
            or len(sources) >= 2
        )
    ):
        return "moderate"
    if sources & WEAK_TERRITORY_SOURCES:
        return "weak"
    if len(rows) >= 3 and bool(repeated.get("repeated_engagement_detected")):
        return "moderate"
    return "weak"


def compute_territory_signals(events: Iterable[dict]) -> dict[str, object]:
    rows = [row for row in events or [] if isinstance(row, dict)]
    repeated = compute_repeated_engagement(rows)
    intent_counter = Counter(
        str(row.get("intent_tier") or "").strip()
        for row in rows
        if str(row.get("intent_tier") or "").strip()
    )
    lead_count = sum(1 for row in rows if row.get("source_kind") == "professional_lead")
    access_request_count = sum(1 for row in rows if row.get("source_kind") == "access_request")
    pilot_navigation_count = sum(
        1
        for row in rows
        if any(path in PILOT_PATHS for path in list(row.get("paths") or []))
    )
    deployment_interest_count = sum(
        1
        for row in rows
        if (
            row.get("primary_interest") == "deployment_operations"
            or any(path in DEPLOYMENT_OPERATIONS_PATHS for path in list(row.get("paths") or []))
        )
    )
    governance_interest_count = sum(
        1
        for row in rows
        if (
            row.get("primary_interest") == "trust_governance"
            or any(path in TRUST_GOVERNANCE_PATHS for path in list(row.get("paths") or []))
        )
    )
    institutional_fit_count = sum(
        1
        for row in rows
        if (
            row.get("primary_interest") == "institutional_fit"
            or any(path in INSTITUTIONAL_FIT_PATHS for path in list(row.get("paths") or []))
        )
    )
    trust_friction_count = sum(
        1 for row in rows if bool(row.get("trust_friction_detected"))
    )
    return_session_count = int(repeated.get("return_session_count") or 0)
    repeated_session_count = int(repeated.get("repeated_session_count") or 0)
    high_intent_count = sum(
        1
        for row in rows
        if str(row.get("intent_tier") or "").strip()
        in {"pilot_ready", "high_conversion_probability"}
    )
    evaluating_count = sum(
        1
        for row in rows
        if str(row.get("intent_tier") or "").strip()
        in {"evaluating", "operationally_interested"}
    )
    score = 0
    score += min(24, (repeated_session_count * 10) + (return_session_count * 5))
    score += min(24, (pilot_navigation_count * 8) + (access_request_count * 12))
    score += min(18, deployment_interest_count * 6)
    score += min(12, governance_interest_count * 4)
    score += min(30, (lead_count * 10) + (access_request_count * 14))
    score += min(12, (high_intent_count * 6) + (evaluating_count * 3))
    repeated_detected = bool(repeated.get("repeated_engagement_detected"))
    confidence = _confidence_for_rows(rows, repeated)
    dominant_interest = _dominant_interest(rows)
    possible_friction = (
        "trust_governance_review_without_conversion" if trust_friction_count else None
    )
    sources = sorted(
        {
            str(row.get("territory_source") or "").strip()
            for row in rows
            if str(row.get("territory_source") or "").strip()
        }
    )
    return {
        "score": int(score),
        "confidence": confidence,
        "dominant_interest": dominant_interest,
        "repeated_engagement_detected": repeated_detected,
        "engagement_strength": repeated.get("engagement_strength"),
        "engagement_pattern_label": repeated.get("engagement_pattern_label"),
        "observed_signal_count": len(rows),
        "lead_count": lead_count,
        "access_request_count": access_request_count,
        "pilot_navigation_count": pilot_navigation_count,
        "deployment_interest_count": deployment_interest_count,
        "governance_interest_count": governance_interest_count,
        "institutional_fit_count": institutional_fit_count,
        "return_session_count": return_session_count,
        "repeated_session_count": repeated_session_count,
        "high_intent_count": high_intent_count,
        "possible_friction": possible_friction,
        "sources": sources,
        "intent_tiers": dict(intent_counter),
    }


def recommend_founder_action(summary: dict[str, object]) -> str:
    priority = str(summary.get("priority_level") or "Low")
    friction = str(summary.get("possible_friction") or "").strip()
    dominant_interest = str(summary.get("dominant_interest") or "").strip()
    pilot_readiness = str(summary.get("pilot_readiness_estimate") or "").strip()
    confidence = str(summary.get("confidence") or "weak")
    if priority == "Strategic" and confidence in {"moderate", "strong"}:
        return "Prioritize founder outreach this week"
    if friction == "trust_governance_review_without_conversion":
        return "Governance reassurance recommended"
    if pilot_readiness in {"elevated", "strong"}:
        return "Pilot discussion opportunity"
    if dominant_interest == "deployment_operations":
        return "Strengthen deployment messaging"
    if dominant_interest == "institutional_fit":
        return "Confirm institutional fit and pilot perimeter"
    return "Observe and monitor"


def build_territory_summary(rows: Iterable[dict]) -> dict[str, object]:
    evidence = [row for row in rows or [] if isinstance(row, dict)]
    territory = normalize_territory_name(
        next((row.get("territory") for row in evidence if row.get("territory")), "")
    )
    signals = compute_territory_signals(evidence)
    intensity = classify_territorial_intensity(signals["score"])
    summary = {
        "territory": territory,
        "intensity": intensity,
        "priority_level": intensity,
        "confidence": signals["confidence"],
        "dominant_interest": signals["dominant_interest"],
        "repeated_engagement_detected": signals["repeated_engagement_detected"],
        "engagement_strength": signals["engagement_strength"],
        "engagement_pattern_label": signals["engagement_pattern_label"],
        "possible_friction": signals["possible_friction"],
        "pilot_readiness_estimate": PILOT_READINESS_BY_PRIORITY[intensity],
        "likely_maturity": LIKELY_MATURITY_BY_PRIORITY[intensity],
        "observed_signal_count": signals["observed_signal_count"],
        "lead_count": signals["lead_count"],
        "access_request_count": signals["access_request_count"],
        "return_session_count": signals["return_session_count"],
        "repeated_session_count": signals["repeated_session_count"],
        "score": signals["score"],
        "sources": signals["sources"],
        "intent_tiers": signals["intent_tiers"],
    }
    summary["recommended_action"] = recommend_founder_action(summary)
    return summary


def detect_priority_territories(rows: Iterable[dict]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        territory = normalize_territory_name(row.get("territory"))
        if not territory:
            continue
        payload = dict(row)
        payload["territory"] = territory
        grouped.setdefault(territory, []).append(payload)
    summaries = [build_territory_summary(items) for items in grouped.values()]
    summaries.sort(
        key=lambda item: (
            PRIORITY_RANK.get(str(item.get("priority_level") or "Low"), 1),
            int(item.get("score") or 0),
            int(item.get("observed_signal_count") or 0),
            str(item.get("territory") or ""),
        ),
        reverse=True,
    )
    return summaries
