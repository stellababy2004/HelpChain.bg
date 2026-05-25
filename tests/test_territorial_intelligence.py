from backend.helpchain_backend.src.services.territorial_intelligence import (
    build_territory_summary,
    classify_territorial_intensity,
    compute_repeated_engagement,
    compute_territory_signals,
    detect_priority_territories,
    normalize_territory_name,
    recommend_founder_action,
)


def test_normalize_territory_name_handles_accents_and_hyphens():
    assert normalize_territory_name("  boulogne billancourt ") == "Boulogne-Billancourt"
    assert normalize_territory_name("Île-de-France") == "Ile-de-France"
    assert normalize_territory_name("saint denis") == "Saint-Denis"


def test_classify_territorial_intensity_is_deterministic():
    assert classify_territorial_intensity(0) == "Low"
    assert classify_territorial_intensity(20) == "Moderate"
    assert classify_territorial_intensity(45) == "High"
    assert classify_territorial_intensity(75) == "Strategic"


def test_compute_repeated_engagement_detects_repeat_pilot_navigation():
    repeated = compute_repeated_engagement(
        [
            {
                "session_id": "aud_repeat",
                "repeat_visit": True,
                "paths": ["/deploiement", "/demander-acces"],
            },
            {
                "session_id": "aud_repeat",
                "repeat_visit": True,
                "paths": ["/offre", "/professionnels/pilote"],
            },
        ]
    )

    assert repeated["repeated_engagement_detected"] is True
    assert repeated["engagement_strength"] == "strong"
    assert repeated["engagement_pattern_label"] == "repeat_pilot_navigation"


def test_compute_territory_signals_uses_conservative_confidence_levels():
    weak = compute_territory_signals(
        [
            {
                "territory": "Paris",
                "territory_source": "email_domain_hint",
                "source_kind": "anonymous_session",
                "paths": ["/offre"],
                "intent_tier": "curious",
                "primary_interest": "pricing_offer",
                "repeat_visit": False,
            }
        ]
    )
    strong = compute_territory_signals(
        [
            {
                "territory": "Boulogne-Billancourt",
                "territory_source": "access_request_city",
                "source_kind": "access_request",
                "paths": ["/demander-acces", "/deploiement"],
                "intent_tier": "pilot_ready",
                "primary_interest": "institutional_fit",
                "repeat_visit": True,
            }
        ]
    )

    assert weak["confidence"] == "weak"
    assert strong["confidence"] == "strong"


def test_build_territory_summary_generates_priority_and_recommendation():
    summary = build_territory_summary(
        [
            {
                "territory": "Boulogne-Billancourt",
                "territory_source": "access_request_city",
                "source_kind": "access_request",
                "session_id": "aud_1",
                "paths": ["/demander-acces", "/deploiement", "/offre"],
                "intent_tier": "pilot_ready",
                "primary_interest": "deployment_operations",
                "trust_friction_detected": False,
                "repeat_visit": True,
            },
            {
                "territory": "Boulogne-Billancourt",
                "territory_source": "professional_lead_city",
                "source_kind": "professional_lead",
                "session_id": "aud_2",
                "paths": ["/professionnels/pilote", "/deploiement"],
                "intent_tier": "high_conversion_probability",
                "primary_interest": "institutional_fit",
                "trust_friction_detected": False,
                "repeat_visit": True,
            },
        ]
    )

    assert summary["territory"] == "Boulogne-Billancourt"
    assert summary["intensity"] in {"High", "Strategic"}
    assert summary["priority_level"] in {"High", "Strategic"}
    assert summary["confidence"] == "strong"
    assert summary["dominant_interest"] in {"deployment_operations", "institutional_fit", "mixed"}
    assert summary["repeated_engagement_detected"] is True
    assert summary["pilot_readiness_estimate"] in {"elevated", "strong"}
    assert summary["recommended_action"] in {
        "Pilot discussion opportunity",
        "Prioritize founder outreach this week",
    }


def test_build_territory_summary_detects_possible_trust_friction():
    summary = build_territory_summary(
        [
            {
                "territory": "Paris",
                "territory_source": "behavior_location",
                "source_kind": "anonymous_session",
                "session_id": "aud_trust_1",
                "paths": ["/securite", "/confidentialite", "/architecture"],
                "intent_tier": "evaluating",
                "primary_interest": "trust_governance",
                "trust_friction_detected": True,
                "repeat_visit": True,
            },
            {
                "territory": "Paris",
                "territory_source": "behavior_location",
                "source_kind": "anonymous_session",
                "session_id": "aud_trust_2",
                "paths": ["/securite", "/architecture"],
                "intent_tier": "evaluating",
                "primary_interest": "trust_governance",
                "trust_friction_detected": True,
                "repeat_visit": True,
            },
        ]
    )

    assert summary["possible_friction"] == "trust_governance_review_without_conversion"
    assert summary["recommended_action"] == "Governance reassurance recommended"


def test_detect_priority_territories_sorts_stronger_evidence_first():
    rows = [
        {
            "territory": "Paris",
            "territory_source": "behavior_location",
            "source_kind": "anonymous_session",
            "paths": ["/offre", "/deploiement"],
            "intent_tier": "evaluating",
            "primary_interest": "deployment_operations",
            "repeat_visit": True,
        },
        {
            "territory": "Boulogne-Billancourt",
            "territory_source": "access_request_city",
            "source_kind": "access_request",
            "paths": ["/demander-acces", "/professionnels/pilote"],
            "intent_tier": "high_conversion_probability",
            "primary_interest": "institutional_fit",
            "repeat_visit": True,
        },
    ]

    summaries = detect_priority_territories(rows)

    assert summaries
    assert summaries[0]["territory"] == "Boulogne-Billancourt"


def test_recommend_founder_action_defaults_to_observation():
    assert (
        recommend_founder_action(
            {
                "priority_level": "Low",
                "confidence": "weak",
                "dominant_interest": "unknown",
                "pilot_readiness_estimate": "early",
                "possible_friction": None,
            }
        )
        == "Observe and monitor"
    )
