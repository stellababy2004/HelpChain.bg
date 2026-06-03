from backend.helpchain_backend.src.services.institutional_intent_engine import (
    build_institutional_intent_phase1,
    build_territorial_opportunity_summary,
    summarize_profile_labels,
)


def test_phase1_intent_engine_detects_high_value_journey():
    result = build_institutional_intent_phase1(
        [
            "/",
            "/deploiement",
            "/securite",
            "/contact",
        ],
        has_submit=True,
        repeat_visit=True,
        repeat_visit_count=2,
        page_count=4,
        territory="Paris",
    )

    assert result["institutional_intent_score"] >= 150
    assert result["intent_level"] in {
        "signal_institutionnel_eleve",
        "signal_institutionnel_tres_eleve",
    }
    assert result["recurrence_level"] in {"medium", "high"}
    assert result["deployment_probability"] in {"high", "very_high"}
    assert result["journey_matches"]
    assert result["probable_organization_profiles"]
    assert result["gdpr_positioning"] == "aggregated_probabilistic_operational_reading"
    assert "signal observe sur /contact" in result["recommendation_reasons"]


def test_territorial_opportunity_summary_stays_probabilistic():
    summary = build_territorial_opportunity_summary(
        {
            "territory": "Saint-Denis",
            "intensity": "High",
            "priority_level": "High",
            "confidence": "moderate",
            "repeated_engagement_detected": True,
            "observed_signal_count": 4,
            "pilot_readiness_estimate": "elevated",
            "recommended_action": "Prioriser Saint-Denis pour une lecture operationnelle.",
        },
        probable_profiles=summarize_profile_labels(
            [
                {"label": "CCAS probable"},
                {"label": "CCAS probable"},
                {"label": "Coordination municipale probable"},
            ]
        ),
    )

    assert summary["territory"] == "Saint-Denis"
    assert summary["recurrence_level"] in {"medium", "high"}
    assert summary["deployment_probability"] == "elevated"
    assert "CCAS probable" in summary["probable_organization_profiles"]
    assert any("priorite territoriale" in item for item in summary["recommendation_reasons"])


def test_admin_home_renders_with_phase1_enrichment(authenticated_admin_client):
    response = authenticated_admin_client.get("/admin/home", follow_redirects=True)

    assert response.status_code == 200
