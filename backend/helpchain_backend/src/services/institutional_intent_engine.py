from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Iterable

from .institutional_intent import (
    CONVERSION_STEP_PATHS,
    DEPLOYMENT_OPERATIONS_PATHS,
    INSTITUTIONAL_FIT_PATHS,
    TRUST_GOVERNANCE_PATHS,
    build_intent_summary,
    normalize_intent_path,
)


PATH_SIGNAL_WEIGHTS = {
    "/": 5,
    "/deploiement": 40,
    "/securite": 35,
    "/gouvernance": 30,
    "/professionnels": 25,
    "/pour-les-structures": 25,
    "/contact": 50,
    "/demo": 60,
    "/acces-pilote": 80,
    "/demander-acces": 80,
    "/professionnels/pilote": 80,
}

JOURNEY_DEFINITIONS = (
    {
        "code": "institutional_deployment_security_contact",
        "label": "Accueil -> Deploiement -> Securite -> Contact",
        "paths": ("/", "/deploiement", "/securite", "/contact"),
        "boost": 35,
    },
    {
        "code": "professionals_governance_deployment",
        "label": "Professionnels -> Gouvernance -> Deploiement",
        "paths": ("/professionnels", "/gouvernance", "/deploiement"),
        "boost": 28,
    },
    {
        "code": "structures_security_pilot",
        "label": "Pour les structures -> Securite -> Acces pilote",
        "paths": ("/pour-les-structures", "/securite", "/demander-acces"),
        "boost": 40,
    },
)

PROFILE_RULES = (
    {
        "code": "ccas_probable",
        "label": "CCAS probable",
        "signals": (
            "/pour-les-structures",
            "/deploiement",
            "/securite",
            "/contact",
        ),
        "interests": {"deployment_operations", "institutional_fit"},
    },
    {
        "code": "coordination_municipale_probable",
        "label": "Coordination municipale probable",
        "signals": (
            "/gouvernance",
            "/deploiement",
            "/securite",
        ),
        "interests": {"trust_governance", "deployment_operations"},
    },
    {
        "code": "association_insertion_probable",
        "label": "Association insertion probable",
        "signals": (
            "/professionnels",
            "/pour-les-structures",
            "/cas-usage",
        ),
        "interests": {"institutional_fit", "deployment_operations"},
    },
    {
        "code": "structure_alimentaire_probable",
        "label": "Structure alimentaire probable",
        "signals": (
            "/deploiement",
            "/professionnels",
            "/contact",
        ),
        "interests": {"deployment_operations"},
    },
    {
        "code": "reseau_social_local_probable",
        "label": "Reseau social local probable",
        "signals": (
            "/professionnels",
            "/gouvernance",
            "/pour-les-structures",
        ),
        "interests": {"institutional_fit", "trust_governance"},
    },
    {
        "code": "association_jeunesse_probable",
        "label": "Association jeunesse probable",
        "signals": (
            "/professionnels",
            "/cas-usage",
            "/contact",
        ),
        "interests": {"institutional_fit"},
    },
)

INTENT_LEVELS = (
    (180, "signal_institutionnel_tres_eleve", "Signal institutionnel tres eleve"),
    (120, "signal_institutionnel_eleve", "Signal institutionnel eleve"),
    (70, "signal_institutionnel_significatif", "Signal institutionnel significatif"),
    (30, "signal_institutionnel_initial", "Signal institutionnel initial"),
    (0, "signal_limite", "Signal limite"),
)

DEPLOYMENT_PROBABILITY_LEVELS = (
    (150, "very_high", "Estimation tres elevee"),
    (100, "high", "Estimation elevee"),
    (55, "medium", "Estimation moyenne"),
    (0, "low", "Estimation initiale"),
)

RECURRENCE_LEVELS = (
    (3, "high", "Activite observee recurrente"),
    (1, "medium", "Activite observee en reprise"),
    (0, "low", "Activite observee ponctuelle"),
)


def _normalized_path_sequence(paths: Iterable[str | None]) -> list[str]:
    sequence: list[str] = []
    previous = None
    for path in paths or []:
        normalized = normalize_intent_path(path)
        if not normalized:
            continue
        if normalized == previous:
            continue
        sequence.append(normalized)
        previous = normalized
    return sequence


def _ordered_contains(sequence: list[str], pattern: Iterable[str]) -> bool:
    if not sequence:
        return False
    index = 0
    targets = list(pattern)
    for path in sequence:
        if path == targets[index]:
            index += 1
            if index >= len(targets):
                return True
    return False


def detect_institutional_journeys(paths: Iterable[str | None]) -> list[dict[str, object]]:
    sequence = _normalized_path_sequence(paths)
    matches: list[dict[str, object]] = []
    for journey in JOURNEY_DEFINITIONS:
        if _ordered_contains(sequence, journey["paths"]):
            matches.append(
                {
                    "code": str(journey["code"]),
                    "label": str(journey["label"]),
                    "score_boost": int(journey["boost"]),
                    "observed_paths": [path for path in sequence if path in set(journey["paths"])],
                }
            )
    return matches


def _classify_intent_level(score: int) -> dict[str, str]:
    for threshold, code, label in INTENT_LEVELS:
        if score >= threshold:
            return {"code": code, "label": label}
    return {"code": "signal_limite", "label": "Signal limite"}


def _classify_deployment_probability(score: int) -> dict[str, str]:
    for threshold, code, label in DEPLOYMENT_PROBABILITY_LEVELS:
        if score >= threshold:
            return {"code": code, "label": label}
    return {"code": "low", "label": "Estimation initiale"}


def _classify_recurrence_level(strength_points: int) -> dict[str, str]:
    for threshold, code, label in RECURRENCE_LEVELS:
        if strength_points >= threshold:
            return {"code": code, "label": label}
    return {"code": "low", "label": "Activite observee ponctuelle"}


def infer_probable_organization_profiles(
    paths: Iterable[str | None],
    *,
    primary_interest: str | None = None,
    territory: str | None = None,
) -> list[dict[str, object]]:
    normalized_paths = set(_normalized_path_sequence(paths))
    interest = str(primary_interest or "").strip()
    territory_hint = str(territory or "").strip()
    profiles: list[dict[str, object]] = []
    for rule in PROFILE_RULES:
        matched_signals = [path for path in rule["signals"] if path in normalized_paths]
        score = len(matched_signals) * 18
        if interest and interest in set(rule["interests"]):
            score += 16
        if territory_hint and "municipale" in str(rule["label"]).lower():
            score += 6
        if score < 30:
            continue
        confidence = "low"
        if score >= 70:
            confidence = "high"
        elif score >= 45:
            confidence = "medium"
        signal_reasons = [f"signal observe sur {path}" for path in matched_signals[:3]]
        if interest and interest in set(rule["interests"]):
            signal_reasons.append(f"lecture operationnelle dominante: {interest}")
        profiles.append(
            {
                "profile_code": str(rule["code"]),
                "label": str(rule["label"]),
                "confidence": confidence,
                "score": score,
                "signal_reasons": signal_reasons,
            }
        )
    profiles.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            str(item.get("label") or ""),
        ),
        reverse=True,
    )
    return profiles[:3]


def build_territorial_opportunity_summary(
    territorial_summary: dict[str, object] | None,
    *,
    probable_profiles: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    summary = territorial_summary if isinstance(territorial_summary, dict) else {}
    repeated = bool(summary.get("repeated_engagement_detected"))
    recurrence = _classify_recurrence_level(1 if repeated else 0)
    profiles = probable_profiles or []
    top_profiles = [str(item.get("label") or "").strip() for item in profiles if str(item.get("label") or "").strip()]
    return {
        "territory": str(summary.get("territory") or "").strip(),
        "territorial_intensity": str(summary.get("intensity") or "Low").strip() or "Low",
        "priority_level": str(summary.get("priority_level") or "Low").strip() or "Low",
        "confidence": str(summary.get("confidence") or "weak").strip() or "weak",
        "recurrence_level": recurrence["code"],
        "recurrence_label": recurrence["label"],
        "observed_signal_count": int(summary.get("observed_signal_count") or 0),
        "deployment_probability": str(summary.get("pilot_readiness_estimate") or "early").strip() or "early",
        "recommended_next_action": (
            str(summary.get("recommended_action") or "").strip()
            or "Maintenir une lecture operationnelle prudente."
        ),
        "recommendation_reasons": [
            reason
            for reason in (
                f"priorite territoriale {str(summary.get('priority_level') or 'Low').lower()}",
                (
                    "activite observee recurrente"
                    if repeated
                    else "activite observee ponctuelle"
                ),
                (
                    f"profil probable: {top_profiles[0]}"
                    if top_profiles
                    else ""
                ),
            )
            if reason
        ],
        "probable_organization_profiles": top_profiles[:3],
    }


def build_institutional_intent_phase1(
    paths: Iterable[str | None],
    *,
    has_submit: bool = False,
    repeat_visit: bool = False,
    repeat_visit_count: int = 0,
    first_seen_at: str | datetime | None = None,
    last_seen_at: str | datetime | None = None,
    page_count: int | None = None,
    time_on_operational_pages_seconds: int | None = None,
    territorial_summary: dict[str, object] | None = None,
    territory: str | None = None,
) -> dict[str, object]:
    normalized_sequence = _normalized_path_sequence(paths)
    normalized_paths = set(normalized_sequence)
    intent_summary = build_intent_summary(normalized_sequence, has_submit=has_submit)
    signal_breakdown: list[dict[str, object]] = []

    score = 0
    for path in normalized_paths:
        weight = int(PATH_SIGNAL_WEIGHTS.get(path) or 0)
        if weight:
            score += weight
            signal_breakdown.append(
                {
                    "signal": path,
                    "weight": weight,
                    "kind": "page",
                    "label": f"signal observe sur {path}",
                }
            )

    if normalized_paths == {"/"}:
        signal_breakdown.append(
            {
                "signal": "homepage_only",
                "weight": 5,
                "kind": "low_signal",
                "label": "activite observee limitee a la page d'accueil",
            }
        )
        score += 5

    institutional_pages = normalized_paths & (
        set(DEPLOYMENT_OPERATIONS_PATHS)
        | set(INSTITUTIONAL_FIT_PATHS)
        | set(TRUST_GOVERNANCE_PATHS)
        | set(CONVERSION_STEP_PATHS)
    )
    if len(institutional_pages) >= 3:
        score += 25
        signal_breakdown.append(
            {
                "signal": "multi_page_institutional_flow",
                "weight": 25,
                "kind": "medium_signal",
                "label": "lecture operationnelle multi-pages observee",
            }
        )
    if repeat_visit:
        score += 20
        signal_breakdown.append(
            {
                "signal": "repeat_visit",
                "weight": 20,
                "kind": "medium_signal",
                "label": "retour observe sur une fenetre courte",
            }
        )
    if page_count and page_count >= 2:
        score += 20
        signal_breakdown.append(
            {
                "signal": "repeated_session",
                "weight": 20,
                "kind": "medium_signal",
                "label": "session recurrente observee",
            }
        )
    if (
        any(path in DEPLOYMENT_OPERATIONS_PATHS for path in normalized_paths)
        and any(path in TRUST_GOVERNANCE_PATHS for path in normalized_paths)
    ):
        score += 35
        signal_breakdown.append(
            {
                "signal": "security_deployment_combo",
                "weight": 35,
                "kind": "medium_signal",
                "label": "croisement securite et deploiement observe",
            }
        )
    if time_on_operational_pages_seconds and time_on_operational_pages_seconds >= 120:
        score += 15
        signal_breakdown.append(
            {
                "signal": "time_on_operational_pages",
                "weight": 15,
                "kind": "medium_signal",
                "label": "temps d'attention operationnelle observe",
            }
        )
    if len(normalized_sequence) == 1 and not repeat_visit and not has_submit:
        score -= 10
        signal_breakdown.append(
            {
                "signal": "single_isolated_hit",
                "weight": -10,
                "kind": "negative_signal",
                "label": "signal isole observe",
            }
        )

    journeys = detect_institutional_journeys(normalized_sequence)
    for journey in journeys:
        boost = int(journey.get("score_boost") or 0)
        score += boost
        signal_breakdown.append(
            {
                "signal": str(journey.get("code") or "journey"),
                "weight": boost,
                "kind": "journey",
                "label": f"parcours institutionnel observe: {journey.get('label')}",
            }
        )

    score = max(0, score)
    recurrence_points = max(0, int(repeat_visit_count or 0))
    if repeat_visit:
        recurrence_points += 1
    if page_count and page_count >= 3:
        recurrence_points += 1
    if journeys:
        recurrence_points += 1
    recurrence = _classify_recurrence_level(recurrence_points)
    deployment_estimate = _classify_deployment_probability(score)
    probable_profiles = infer_probable_organization_profiles(
        normalized_sequence,
        primary_interest=str(intent_summary.get("primary_interest") or "unknown"),
        territory=territory,
    )
    territorial_opportunity = build_territorial_opportunity_summary(
        territorial_summary,
        probable_profiles=probable_profiles,
    )
    intent_level = _classify_intent_level(score)

    recommendation_reasons = [
        str(item.get("label") or "").strip()
        for item in sorted(
            signal_breakdown,
            key=lambda item: int(item.get("weight") or 0),
            reverse=True,
        )
        if str(item.get("label") or "").strip()
    ][:5]

    top_profile = probable_profiles[0]["label"] if probable_profiles else None
    recommended_next_action = "Maintenir une observation prudente."
    if score >= 150:
        recommended_next_action = (
            "Prioriser une lecture operationnelle territoriale et proposer un cadrage de deploiement."
        )
    elif score >= 100:
        recommended_next_action = (
            "Approfondir la qualification institutionnelle et confirmer le perimetre de deploiement."
        )
    elif score >= 55:
        recommended_next_action = (
            "Surveiller les signaux observes et renforcer l'information de gouvernance et de deploiement."
        )
    if territorial_summary and territorial_summary.get("recommended_action"):
        recommended_next_action = str(territorial_summary.get("recommended_action"))

    return {
        "institutional_intent_score": score,
        "intent_level": intent_level["code"],
        "intent_label": intent_level["label"],
        "recurrence_level": recurrence["code"],
        "recurrence_label": recurrence["label"],
        "deployment_probability": deployment_estimate["code"],
        "deployment_probability_label": deployment_estimate["label"],
        "probable_organization_profiles": probable_profiles,
        "territorial_opportunity_summary": territorial_opportunity,
        "recommendation_reasons": recommendation_reasons,
        "recommended_next_action": recommended_next_action,
        "journey_matches": journeys,
        "signal_breakdown": signal_breakdown,
        "observed_paths": normalized_sequence,
        "observed_signal_count": len(signal_breakdown),
        "primary_interest": intent_summary.get("primary_interest"),
        "trust_friction_detected": bool(intent_summary.get("trust_friction_detected")),
        "friction_reason": intent_summary.get("friction_reason"),
        "top_profile_label": top_profile,
        "gdpr_positioning": "aggregated_probabilistic_operational_reading",
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
    }


def summarize_profile_labels(rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    counter = Counter()
    for row in rows or []:
        label = str(row.get("label") or "").strip()
        if label:
            counter[label] += 1
    return [
        {"label": label, "count": count}
        for label, count in counter.most_common(3)
    ]
