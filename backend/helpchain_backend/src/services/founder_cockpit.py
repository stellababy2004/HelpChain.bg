from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from .institutional_intent import (
    DEPLOYMENT_OPERATIONS_PATHS,
    INSTITUTIONAL_FIT_PATHS,
    TRUST_GOVERNANCE_PATHS,
)

PILOT_PATHS = {"/professionnels/pilote", "/demander-acces", "/demo", "/contact"}
OPPORTUNITY_THRESHOLDS = (
    (190, "High-priority founder follow-up"),
    (140, "Pilot opportunity"),
    (90, "Active evaluation"),
    (50, "Monitor"),
    (0, "Observe"),
)
OPPORTUNITY_RANK = {
    "Observe": 1,
    "Monitor": 2,
    "Active evaluation": 3,
    "Pilot opportunity": 4,
    "High-priority founder follow-up": 5,
}
TERRITORY_PRIORITY_POINTS = {
    "Low": 5,
    "Moderate": 15,
    "High": 30,
    "Strategic": 45,
}
PILOT_READINESS_POINTS = {
    "early": 0,
    "emerging": 8,
    "elevated": 18,
    "strong": 28,
}
CONFIDENCE_RANK = {"weak": 1, "moderate": 2, "strong": 3}


def _read(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalized_paths(row) -> list[str]:
    paths = _as_list(_read(row, "top_paths"))
    if paths:
        return paths
    paths = _as_list(_read(row, "pages_viewed"))
    if paths:
        return paths
    paths = _as_list(_read(row, "evidence_paths"))
    if paths:
        return paths
    return []


def _territory_name(row) -> str:
    return (
        str(_read(row, "territory") or "").strip()
        or str(_read(row, "territory_hint") or "").strip()
        or str(_read(row, "city") or "").strip()
        or "Unknown"
    )


def _bool(value) -> bool:
    return bool(value)


def _int(value) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _primary_interest(row) -> str:
    return str(
        _read(row, "dominant_interest")
        or _read(row, "primary_interest")
        or "unknown"
    ).strip() or "unknown"


def _pilot_signal(row, paths: list[str]) -> bool:
    if any(path in PILOT_PATHS for path in paths):
        return True
    if str(_read(row, "intent_tier") or "").strip() in {
        "pilot_ready",
        "high_conversion_probability",
    }:
        return True
    if str(_read(row, "pilot_readiness_estimate") or "").strip() in {"elevated", "strong"}:
        return True
    if str(_read(row, "stage") or "").strip() in {
        "qualified",
        "demo_booked",
        "demo_done",
        "pilot_proposed",
        "negotiation",
    }:
        return True
    if str(_read(row, "kind") or "").strip() == "access_request":
        return True
    return False


def _deployment_signal(row, paths: list[str]) -> bool:
    return _primary_interest(row) == "deployment_operations" or any(
        path in DEPLOYMENT_OPERATIONS_PATHS for path in paths
    )


def _trust_signal(row, paths: list[str]) -> bool:
    return _primary_interest(row) == "trust_governance" or any(
        path in TRUST_GOVERNANCE_PATHS for path in paths
    )


def _institutional_fit_signal(row, paths: list[str]) -> bool:
    return _primary_interest(row) == "institutional_fit" or any(
        path in INSTITUTIONAL_FIT_PATHS for path in paths
    )


def _classify_opportunity(score: int) -> str:
    for threshold, label in OPPORTUNITY_THRESHOLDS:
        if score >= threshold:
            return label
    return "Observe"


def detect_founder_followup_priority(row) -> dict[str, object]:
    paths = _normalized_paths(row)
    intent_score = max(_int(_read(row, "intent_score")), _int(_read(row, "lead_intent_score")))
    if not intent_score:
        intent_score = _int(_read(row, "score"))
    territory_priority = str(_read(row, "priority_level") or "").strip() or "Low"
    repeated = _bool(_read(row, "repeated_engagement_detected")) or _bool(_read(row, "repeat_visit"))
    pilot_signal = _pilot_signal(row, paths)
    deployment_signal = _deployment_signal(row, paths)
    trust_friction = _bool(_read(row, "trust_friction_detected")) or str(
        _read(row, "possible_friction") or _read(row, "friction_reason") or ""
    ).strip() == "trust_governance_review_without_conversion"
    source_kind = str(_read(row, "kind") or _read(row, "source_kind") or "").strip()
    confidence = str(_read(row, "territory_confidence") or _read(row, "confidence") or "weak").strip() or "weak"
    pilot_readiness = str(_read(row, "pilot_readiness_estimate") or "").strip() or "early"

    score = min(90, intent_score)
    score += TERRITORY_PRIORITY_POINTS.get(territory_priority, 5)
    score += PILOT_READINESS_POINTS.get(pilot_readiness, 0)
    if repeated:
        score += 18
    if pilot_signal:
        score += 30
    if deployment_signal:
        score += 12
    if _institutional_fit_signal(row, paths):
        score += 10
    if trust_friction:
        score += 10
    if source_kind in {"access_request", "professional_lead"}:
        score += 24 if source_kind == "access_request" else 16
    score += max(0, CONFIDENCE_RANK.get(confidence, 1) - 1) * 6

    opportunity_level = _classify_opportunity(score)
    return {
        "opportunity_score": int(score),
        "opportunity_level": opportunity_level,
        "priority_rank": OPPORTUNITY_RANK[opportunity_level],
        "pilot_signal": pilot_signal,
        "deployment_signal": deployment_signal,
        "trust_friction": trust_friction,
        "repeated_engagement": repeated,
    }


def infer_operational_maturity(row) -> str:
    paths = _normalized_paths(row)
    intent_tier = str(_read(row, "intent_tier") or "").strip()
    territory_priority = str(_read(row, "priority_level") or "").strip()
    repeated = _bool(_read(row, "repeated_engagement_detected")) or _bool(_read(row, "repeat_visit"))
    has_submit = str(_read(row, "kind") or _read(row, "source_kind") or "").strip() == "access_request"
    trust_signal = _trust_signal(row, paths)

    if has_submit and (
        _pilot_signal(row, paths) or territory_priority in {"High", "Strategic"}
    ):
        return "Structured pilot opportunity"
    if _pilot_signal(row, paths) or intent_tier in {"pilot_ready", "high_conversion_probability"}:
        return "Pilot framing"
    if trust_signal and intent_tier in {"evaluating", "operationally_interested", "curious"}:
        return "Institutional evaluation"
    if _deployment_signal(row, paths) or repeated or territory_priority in {"High", "Strategic"}:
        return "Operational consideration"
    if _institutional_fit_signal(row, paths) or trust_signal or intent_tier in {"evaluating", "operationally_interested"}:
        return "Institutional evaluation"
    return "Early exploration"


def _recommend_action(row, *, opportunity_level: str, operational_maturity: str) -> str:
    paths = _normalized_paths(row)
    trust_friction = _bool(_read(row, "trust_friction_detected")) or str(
        _read(row, "possible_friction") or _read(row, "friction_reason") or ""
    ).strip() == "trust_governance_review_without_conversion"
    primary_interest = _primary_interest(row)

    if opportunity_level == "High-priority founder follow-up":
        return "Prioritize direct founder outreach"
    if trust_friction and _bool(_read(row, "repeated_engagement_detected")):
        return "Re-engage after trust/governance review"
    if trust_friction or primary_interest == "trust_governance" or _trust_signal(row, paths):
        return "Reinforce governance reassurance"
    if opportunity_level == "Pilot opportunity" or operational_maturity in {
        "Pilot framing",
        "Structured pilot opportunity",
    }:
        return "Suggest structured pilot exchange"
    if primary_interest == "deployment_operations" or _deployment_signal(row, paths):
        return "Improve deployment clarity"
    if primary_interest == "institutional_fit" or _institutional_fit_signal(row, paths):
        return "Clarify institutional fit"
    return "Continue observing"


def _pilot_readiness_label(row, opportunity_level: str, operational_maturity: str) -> str:
    readiness = str(_read(row, "pilot_readiness_estimate") or "").strip()
    if readiness == "strong":
        return "strong"
    if readiness == "elevated" or opportunity_level in {
        "Pilot opportunity",
        "High-priority founder follow-up",
    }:
        return "elevated"
    if operational_maturity in {"Operational consideration", "Pilot framing"}:
        return "developing"
    return "early"


def _evidence_summary(row, *, repeated: bool, pilot_signal: bool, deployment_signal: bool, trust_friction: bool) -> list[str]:
    paths = _normalized_paths(row)
    evidence: list[str] = []
    if repeated:
        evidence.append("repeated operational sessions")
    if deployment_signal:
        evidence.append("repeated deployment review")
    if any(path in TRUST_GOVERNANCE_PATHS for path in paths):
        evidence.append("governance pages consulted")
    if pilot_signal:
        evidence.append("pilot navigation observed")
    if _institutional_fit_signal(row, paths):
        evidence.append("institutional-fit exploration")
    if trust_friction:
        evidence.append("possible trust friction observed")
    deduped: list[str] = []
    for item in evidence:
        if item not in deduped:
            deduped.append(item)
    return deduped[:5]


def rank_founder_opportunities(rows: Iterable) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for row in rows or []:
        if row is None:
            continue
        priority = detect_founder_followup_priority(row)
        maturity = infer_operational_maturity(row)
        action = _recommend_action(
            row,
            opportunity_level=str(priority["opportunity_level"]),
            operational_maturity=maturity,
        )
        evidence = _evidence_summary(
            row,
            repeated=bool(priority["repeated_engagement"]),
            pilot_signal=bool(priority["pilot_signal"]),
            deployment_signal=bool(priority["deployment_signal"]),
            trust_friction=bool(priority["trust_friction"]),
        )
        ranked.append(
            {
                "uid": str(_read(row, "uid") or ""),
                "kind": str(_read(row, "kind") or _read(row, "source_kind") or ""),
                "organization": str(_read(row, "organization") or _read(row, "account_name") or _read(row, "organization_name") or "Observed signal").strip(),
                "contact": str(_read(row, "contact") or "").strip(),
                "territory": _territory_name(row),
                "opportunity_level": str(priority["opportunity_level"]),
                "opportunity_score": int(priority["opportunity_score"]),
                "operational_maturity": maturity,
                "dominant_interest": _primary_interest(row),
                "confidence": str(_read(row, "territory_confidence") or _read(row, "confidence") or "weak").strip() or "weak",
                "repeated_engagement": bool(priority["repeated_engagement"]),
                "possible_friction": (
                    str(_read(row, "possible_friction") or _read(row, "friction_reason") or "").strip() or None
                ),
                "recommended_action": action,
                "pilot_readiness_estimate": _pilot_readiness_label(row, str(priority["opportunity_level"]), maturity),
                "evidence_summary": evidence,
                "intent_score": max(_int(_read(row, "intent_score")), _int(_read(row, "lead_intent_score")), _int(_read(row, "score"))),
                "priority_level": str(_read(row, "priority_level") or "Low").strip() or "Low",
                "territorial_intensity": str(_read(row, "territorial_intensity") or _read(row, "priority_level") or "Low").strip() or "Low",
                "source_label": str(_read(row, "type_label") or _read(row, "source_label") or "").strip(),
                "next_best_action": str(_read(row, "next_best_action") or "").strip(),
                "why_hot": str(_read(row, "why_hot") or "").strip(),
            }
        )
    ranked.sort(
        key=lambda item: (
            OPPORTUNITY_RANK.get(str(item.get("opportunity_level") or "Observe"), 1),
            int(item.get("opportunity_score") or 0),
            CONFIDENCE_RANK.get(str(item.get("confidence") or "weak"), 1),
            int(item.get("intent_score") or 0),
            str(item.get("organization") or ""),
        ),
        reverse=True,
    )
    return ranked


def build_founder_priority_queue(rows: Iterable) -> list[dict[str, object]]:
    return rank_founder_opportunities(rows)


def summarize_founder_actions(rows: Iterable) -> dict[str, object]:
    queue = build_founder_priority_queue(rows)
    action_counts = Counter(str(item.get("recommended_action") or "") for item in queue)
    opportunity_counts = Counter(str(item.get("opportunity_level") or "") for item in queue)
    maturity_counts = Counter(str(item.get("operational_maturity") or "") for item in queue)
    return {
        "total_items": len(queue),
        "top_actions": [
            {"label": label, "count": count}
            for label, count in action_counts.most_common(3)
            if label
        ],
        "opportunity_levels": dict(opportunity_counts),
        "maturity_levels": dict(maturity_counts),
    }


def build_founder_alerts(rows: Iterable) -> list[dict[str, object]]:
    queue = build_founder_priority_queue(rows)
    alerts: list[dict[str, object]] = []
    for item in queue:
        level = str(item.get("opportunity_level") or "")
        territory = str(item.get("territory") or "Unknown")
        interest = str(item.get("dominant_interest") or "unknown")
        friction = str(item.get("possible_friction") or "").strip()
        evidence = list(item.get("evidence_summary") or [])
        if friction == "trust_governance_review_without_conversion":
            alerts.append(
                {
                    "kind": "trust_review",
                    "level": "attention",
                    "territory": territory,
                    "message": f"Repeated governance review observed in {territory} without a clear conversion step.",
                }
            )
        if level in {"Pilot opportunity", "High-priority founder follow-up"} and any(
            "pilot navigation observed" == note for note in evidence
        ):
            alerts.append(
                {
                    "kind": "pilot_navigation",
                    "level": "opportunity",
                    "territory": territory,
                    "message": f"Pilot-oriented navigation observed in {territory}.",
                }
            )
        if str(item.get("priority_level") or "") == "Strategic" and _bool(item.get("repeated_engagement")):
            alerts.append(
                {
                    "kind": "strategic_territory",
                    "level": "elevated",
                    "territory": territory,
                    "message": f"Strategic territory engagement is elevated in {territory}.",
                }
            )
        if interest == "institutional_fit" and _bool(item.get("repeated_engagement")):
            alerts.append(
                {
                    "kind": "institutional_fit",
                    "level": "watch",
                    "territory": territory,
                    "message": f"Repeated institutional-fit evaluation inferred in {territory}.",
                }
            )
        if interest == "deployment_operations" and friction != "trust_governance_review_without_conversion" and not any(
            "pilot navigation observed" == note for note in evidence
        ):
            alerts.append(
                {
                    "kind": "deployment_interest",
                    "level": "watch",
                    "territory": territory,
                    "message": f"Strong deployment interest observed in {territory} without a direct contact step.",
                }
            )
    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    level_rank = {"opportunity": 4, "elevated": 3, "attention": 2, "watch": 1}
    alerts.sort(
        key=lambda item: (
            level_rank.get(str(item.get("level") or "watch"), 1),
            str(item.get("territory") or ""),
            str(item.get("kind") or ""),
        ),
        reverse=True,
    )
    for item in alerts:
        key = (str(item.get("kind") or ""), str(item.get("territory") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:6]


def group_founder_signals_by_territory(rows: Iterable) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in build_founder_priority_queue(rows):
        grouped[str(item.get("territory") or "Unknown")].append(item)

    output: list[dict[str, object]] = []
    for territory, items in grouped.items():
        top_item = items[0]
        output.append(
            {
                "territory": territory,
                "opportunity_count": len(items),
                "top_opportunity_level": top_item.get("opportunity_level"),
                "recommended_action": top_item.get("recommended_action"),
                "confidence": top_item.get("confidence"),
                "dominant_interest": Counter(
                    str(item.get("dominant_interest") or "unknown") for item in items
                ).most_common(1)[0][0],
            }
        )
    output.sort(
        key=lambda item: (
            OPPORTUNITY_RANK.get(str(item.get("top_opportunity_level") or "Observe"), 1),
            int(item.get("opportunity_count") or 0),
            CONFIDENCE_RANK.get(str(item.get("confidence") or "weak"), 1),
        ),
        reverse=True,
    )
    return output
