from __future__ import annotations

from typing import Iterable

from .telemetry_policy import canonical_public_commercial_path


PUBLIC_PATH_WEIGHTS = {
    "/": 1,
    "/comment-ca-marche": 8,
    "/pourquoi-helpchain": 10,
    "/professionnels": 12,
    "/pour-les-structures": 16,
    "/collectivites-associations": 18,
    "/confidentialite": 18,
    "/securite": 20,
    "/cas-usage": 22,
    "/pilotage-indicateurs": 24,
    "/architecture": 24,
    "/offre": 28,
    "/deploiement": 32,
    "/contact": 35,
    "/demo": 45,
    "/professionnels/pilote": 50,
    "/demander-acces": 55,
}

PRICING_OFFER_PATHS = {"/offre", "/demo", "/contact"}
DEPLOYMENT_OPERATIONS_PATHS = {
    "/deploiement",
    "/cas-usage",
    "/pilotage-indicateurs",
    "/comment-ca-marche",
}
INSTITUTIONAL_FIT_PATHS = {
    "/pour-les-structures",
    "/collectivites-associations",
    "/professionnels",
    "/professionnels/pilote",
    "/demander-acces",
}
TRUST_GOVERNANCE_PATHS = {"/securite", "/confidentialite", "/architecture"}
CONVERSION_STEP_PATHS = {
    "/contact",
    "/demo",
    "/demander-acces",
    "/professionnels/pilote",
}

INTEREST_PATH_GROUPS = {
    "pricing_offer": PRICING_OFFER_PATHS,
    "deployment_operations": DEPLOYMENT_OPERATIONS_PATHS,
    "institutional_fit": INSTITUTIONAL_FIT_PATHS,
    "trust_governance": TRUST_GOVERNANCE_PATHS,
}

SCORE_CLASSIFICATIONS = (
    (200, "high_conversion_probability", "High-conversion probability"),
    (130, "pilot_ready", "Pilot-ready"),
    (80, "operationally_interested", "Operationally interested"),
    (40, "evaluating", "Evaluating"),
    (15, "curious", "Curious"),
    (0, "cold", "Cold"),
)

TIER_RECOMMENDED_ACTIONS = {
    "cold": "Continue observing",
    "curious": "Improve orientation toward use cases",
    "evaluating": "Surface deployment and offer information",
    "operationally_interested": "Invite toward pilot framing",
    "pilot_ready": "Propose a structured pilot conversation",
    "high_conversion_probability": "Prioritize direct founder follow-up",
}


def normalize_intent_path(path: str | None) -> str | None:
    return canonical_public_commercial_path(path)


def score_public_path(path: str | None) -> int:
    normalized = normalize_intent_path(path)
    if not normalized:
        return 0
    return int(PUBLIC_PATH_WEIGHTS.get(normalized) or 0)


def classify_intent_score(score: int | float | None) -> dict[str, str | int]:
    try:
        normalized_score = max(0, int(score or 0))
    except Exception:
        normalized_score = 0
    for threshold, tier, label in SCORE_CLASSIFICATIONS:
        if normalized_score >= threshold:
            return {
                "score": normalized_score,
                "tier": tier,
                "label": label,
                "recommended_action": TIER_RECOMMENDED_ACTIONS[tier],
            }
    return {
        "score": normalized_score,
        "tier": "cold",
        "label": "Cold",
        "recommended_action": TIER_RECOMMENDED_ACTIONS["cold"],
    }


def _normalized_unique_paths(paths: Iterable[str | None]) -> list[str]:
    normalized_paths: list[str] = []
    seen: set[str] = set()
    for path in paths or []:
        normalized = normalize_intent_path(path)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_paths.append(normalized)
    return normalized_paths


def score_session_paths(paths: Iterable[str | None]) -> int:
    return sum(score_public_path(path) for path in _normalized_unique_paths(paths))


def infer_primary_interest(paths: Iterable[str | None]) -> str:
    normalized_paths = _normalized_unique_paths(paths)
    if not normalized_paths:
        return "unknown"

    scores = {
        interest: sum(PUBLIC_PATH_WEIGHTS.get(path, 0) for path in normalized_paths if path in interest_paths)
        for interest, interest_paths in INTEREST_PATH_GROUPS.items()
    }
    best_interest = max(scores, key=scores.get)
    best_score = int(scores.get(best_interest) or 0)
    if best_score <= 0:
        return "unknown"
    leaders = [interest for interest, value in scores.items() if int(value or 0) == best_score]
    if len(leaders) > 1:
        return "mixed"
    second_best = max(
        (int(value or 0) for interest, value in scores.items() if interest != best_interest),
        default=0,
    )
    if second_best > 0 and (best_score - second_best) <= 10:
        return "mixed"
    return best_interest


def infer_trust_friction(
    paths: Iterable[str | None],
    *,
    has_submit: bool = False,
) -> dict[str, bool | str | None]:
    normalized_paths = _normalized_unique_paths(paths)
    trust_paths = [path for path in normalized_paths if path in TRUST_GOVERNANCE_PATHS]
    has_conversion_step = any(path in CONVERSION_STEP_PATHS for path in normalized_paths)
    detected = len(trust_paths) >= 2 and not has_submit and not has_conversion_step
    return {
        "trust_friction_detected": detected,
        "friction_reason": (
            "trust_governance_review_without_conversion" if detected else None
        ),
    }


def build_intent_summary(
    paths: Iterable[str | None],
    *,
    has_submit: bool = False,
) -> dict[str, object]:
    normalized_paths = _normalized_unique_paths(paths)
    score = score_session_paths(normalized_paths)
    classification = classify_intent_score(score)
    friction = infer_trust_friction(normalized_paths, has_submit=has_submit)
    top_paths = sorted(
        normalized_paths,
        key=lambda path: (PUBLIC_PATH_WEIGHTS.get(path, 0), path),
        reverse=True,
    )
    return {
        "score": int(classification["score"]),
        "tier": str(classification["tier"]),
        "label": str(classification["label"]),
        "primary_interest": infer_primary_interest(normalized_paths),
        "trust_friction_detected": bool(friction["trust_friction_detected"]),
        "friction_reason": friction["friction_reason"],
        "top_paths": top_paths,
        "recommended_action": str(classification["recommended_action"]),
    }
