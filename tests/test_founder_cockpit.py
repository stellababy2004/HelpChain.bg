from backend.helpchain_backend.src.services.founder_cockpit import (
    build_founder_alerts,
    build_founder_priority_queue,
    detect_founder_followup_priority,
    group_founder_signals_by_territory,
    infer_operational_maturity,
    rank_founder_opportunities,
    summarize_founder_actions,
)


def test_detect_founder_followup_priority_rewards_pilot_and_territory_strength():
    result = detect_founder_followup_priority(
        {
            "uid": "lead:1",
            "kind": "access_request",
            "territory": "Boulogne-Billancourt",
            "intent_score": 169,
            "intent_tier": "pilot_ready",
            "primary_interest": "deployment_operations",
            "priority_level": "Strategic",
            "territory_confidence": "strong",
            "pilot_readiness_estimate": "strong",
            "repeated_engagement_detected": True,
            "top_paths": ["/demander-acces", "/deploiement", "/offre"],
        }
    )

    assert result["opportunity_level"] == "High-priority founder follow-up"
    assert result["repeated_engagement"] is True
    assert result["pilot_signal"] is True


def test_infer_operational_maturity_stays_conservative_for_governance_review():
    maturity = infer_operational_maturity(
        {
            "uid": "session:1",
            "territory": "Paris",
            "intent_tier": "evaluating",
            "primary_interest": "trust_governance",
            "top_paths": ["/securite", "/confidentialite", "/architecture"],
            "trust_friction_detected": True,
            "repeated_engagement_detected": True,
            "priority_level": "Moderate",
        }
    )

    assert maturity == "Institutional evaluation"


def test_rank_founder_opportunities_orders_stronger_signals_first():
    rows = [
        {
            "uid": "lead:pilot",
            "kind": "access_request",
            "organization": "CCAS Horizon",
            "territory": "Boulogne-Billancourt",
            "intent_score": 169,
            "intent_tier": "pilot_ready",
            "primary_interest": "deployment_operations",
            "priority_level": "Strategic",
            "territory_confidence": "strong",
            "pilot_readiness_estimate": "strong",
            "repeated_engagement_detected": True,
            "top_paths": ["/demander-acces", "/deploiement", "/offre"],
        },
        {
            "uid": "lead:observe",
            "kind": "professional_lead",
            "organization": "Association Locale",
            "territory": "Paris",
            "intent_score": 22,
            "intent_tier": "curious",
            "primary_interest": "institutional_fit",
            "priority_level": "Low",
            "territory_confidence": "weak",
            "pilot_readiness_estimate": "early",
            "repeated_engagement_detected": False,
            "top_paths": ["/professionnels"],
        },
    ]

    ranked = rank_founder_opportunities(rows)

    assert ranked[0]["organization"] == "CCAS Horizon"
    assert ranked[0]["recommended_action"] == "Prioritize direct founder outreach"
    assert ranked[1]["opportunity_level"] in {"Observe", "Monitor"}


def test_build_founder_priority_queue_produces_evidence_summary():
    queue = build_founder_priority_queue(
        [
            {
                "uid": "lead:2",
                "kind": "professional_lead",
                "organization": "Association Pilotage",
                "territory": "Nanterre",
                "intent_score": 132,
                "intent_tier": "pilot_ready",
                "primary_interest": "deployment_operations",
                "priority_level": "High",
                "territory_confidence": "moderate",
                "pilot_readiness_estimate": "elevated",
                "repeated_engagement_detected": True,
                "top_paths": ["/deploiement", "/pilotage-indicateurs", "/demo"],
            }
        ]
    )

    assert queue[0]["operational_maturity"] in {
        "Pilot framing",
        "Structured pilot opportunity",
    }
    assert "repeated deployment review" in queue[0]["evidence_summary"]
    assert "pilot navigation observed" in queue[0]["evidence_summary"]


def test_build_founder_alerts_flags_possible_friction_and_deployment_interest():
    alerts = build_founder_alerts(
        [
            {
                "uid": "session:trust",
                "territory": "Paris",
                "intent_score": 96,
                "intent_tier": "operationally_interested",
                "primary_interest": "trust_governance",
                "priority_level": "High",
                "territory_confidence": "moderate",
                "pilot_readiness_estimate": "developing",
                "repeated_engagement_detected": True,
                "trust_friction_detected": True,
                "friction_reason": "trust_governance_review_without_conversion",
                "top_paths": ["/securite", "/confidentialite", "/architecture"],
            },
            {
                "uid": "session:deploy",
                "territory": "Lyon",
                "intent_score": 88,
                "intent_tier": "operationally_interested",
                "primary_interest": "deployment_operations",
                "priority_level": "Moderate",
                "territory_confidence": "moderate",
                "pilot_readiness_estimate": "developing",
                "repeated_engagement_detected": True,
                "top_paths": ["/deploiement", "/cas-usage", "/offre"],
            },
        ]
    )

    messages = [alert["message"] for alert in alerts]
    assert any("governance review observed" in message for message in messages)
    assert any("Strong deployment interest observed" in message for message in messages)


def test_group_founder_signals_by_territory_groups_and_sorts():
    grouped = group_founder_signals_by_territory(
        [
            {
                "uid": "lead:1",
                "kind": "access_request",
                "territory": "Boulogne-Billancourt",
                "intent_score": 169,
                "intent_tier": "pilot_ready",
                "primary_interest": "deployment_operations",
                "priority_level": "Strategic",
                "territory_confidence": "strong",
                "pilot_readiness_estimate": "strong",
                "repeated_engagement_detected": True,
                "top_paths": ["/demander-acces", "/deploiement"],
            },
            {
                "uid": "lead:2",
                "kind": "professional_lead",
                "territory": "Paris",
                "intent_score": 58,
                "intent_tier": "evaluating",
                "primary_interest": "institutional_fit",
                "priority_level": "Moderate",
                "territory_confidence": "moderate",
                "pilot_readiness_estimate": "emerging",
                "repeated_engagement_detected": True,
                "top_paths": ["/pour-les-structures", "/offre"],
            },
        ]
    )

    assert grouped[0]["territory"] == "Boulogne-Billancourt"
    assert grouped[0]["top_opportunity_level"] in {
        "Pilot opportunity",
        "High-priority founder follow-up",
    }


def test_summarize_founder_actions_is_deterministic():
    summary = summarize_founder_actions(
        [
            {
                "uid": "lead:1",
                "kind": "access_request",
                "territory": "Boulogne-Billancourt",
                "intent_score": 169,
                "intent_tier": "pilot_ready",
                "primary_interest": "deployment_operations",
                "priority_level": "Strategic",
                "territory_confidence": "strong",
                "pilot_readiness_estimate": "strong",
                "repeated_engagement_detected": True,
                "top_paths": ["/demander-acces", "/deploiement"],
            },
            {
                "uid": "lead:2",
                "kind": "professional_lead",
                "territory": "Paris",
                "intent_score": 66,
                "intent_tier": "evaluating",
                "primary_interest": "institutional_fit",
                "priority_level": "Moderate",
                "territory_confidence": "moderate",
                "pilot_readiness_estimate": "emerging",
                "repeated_engagement_detected": True,
                "top_paths": ["/pour-les-structures", "/offre"],
            },
        ]
    )

    assert summary["total_items"] == 2
    assert summary["top_actions"]
    assert "High-priority founder follow-up" in summary["opportunity_levels"]
