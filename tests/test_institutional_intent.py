from backend.helpchain_backend.src.services.institutional_intent import (
    build_intent_summary,
    classify_intent_score,
    infer_primary_interest,
    infer_trust_friction,
    normalize_intent_path,
    score_public_path,
    score_session_paths,
)


def test_normalize_intent_path_supports_canonical_and_variant_routes():
    assert normalize_intent_path("/cas_usage") == "/cas-usage"
    assert normalize_intent_path("/pilotage_indicateurs") == "/pilotage-indicateurs"
    assert normalize_intent_path("/pour_les_structures") == "/pour-les-structures"
    assert normalize_intent_path("/collectivites_associations") == "/collectivites-associations"
    assert normalize_intent_path("/pourquoi_helpchain") == "/pourquoi-helpchain"
    assert normalize_intent_path("/comment_ca_marche") == "/comment-ca-marche"


def test_score_public_path_uses_explicit_page_weights():
    assert score_public_path("/") == 1
    assert score_public_path("/offre") == 28
    assert score_public_path("/deploiement") == 32
    assert score_public_path("/demander-acces") == 55
    assert score_public_path("/architecture") == 24
    assert score_public_path("/unknown") == 0


def test_classify_intent_score_uses_expected_tiers():
    assert classify_intent_score(0)["tier"] == "cold"
    assert classify_intent_score(15)["tier"] == "curious"
    assert classify_intent_score(40)["tier"] == "evaluating"
    assert classify_intent_score(80)["tier"] == "operationally_interested"
    assert classify_intent_score(130)["tier"] == "pilot_ready"
    assert classify_intent_score(200)["tier"] == "high_conversion_probability"


def test_score_session_paths_sums_unique_public_paths_only():
    score = score_session_paths(
        [
            "/offre",
            "/offre",
            "/deploiement",
            "/demander-acces",
            "/cas_usage",
        ]
    )

    assert score == 28 + 32 + 55 + 22


def test_infer_primary_interest_returns_expected_label():
    assert infer_primary_interest(["/offre", "/contact"]) == "pricing_offer"
    assert (
        infer_primary_interest(["/deploiement", "/pilotage_indicateurs", "/cas_usage"])
        == "deployment_operations"
    )
    assert (
        infer_primary_interest(["/professionnels", "/pour-les-structures", "/demander-acces"])
        == "institutional_fit"
    )
    assert infer_primary_interest(["/securite", "/architecture"]) == "trust_governance"
    assert infer_primary_interest(["/offre", "/deploiement"]) == "mixed"
    assert infer_primary_interest(["/unknown"]) == "unknown"


def test_infer_trust_friction_is_conservative():
    detected = infer_trust_friction(
        ["/securite", "/confidentialite", "/architecture"],
        has_submit=False,
    )
    not_detected = infer_trust_friction(
        ["/securite", "/confidentialite", "/contact"],
        has_submit=False,
    )
    submitted = infer_trust_friction(
        ["/securite", "/confidentialite", "/architecture"],
        has_submit=True,
    )

    assert detected["trust_friction_detected"] is True
    assert detected["friction_reason"] == "trust_governance_review_without_conversion"
    assert not_detected["trust_friction_detected"] is False
    assert submitted["trust_friction_detected"] is False


def test_build_intent_summary_returns_actionable_server_side_summary():
    summary = build_intent_summary(
        [
            "/comment_ca_marche",
            "/cas_usage",
            "/pilotage_indicateurs",
            "/deploiement",
            "/offre",
            "/demander-acces",
        ],
        has_submit=False,
    )

    assert summary["score"] == 169
    assert summary["tier"] == "pilot_ready"
    assert summary["label"] == "Pilot-ready"
    assert summary["primary_interest"] == "deployment_operations"
    assert summary["trust_friction_detected"] is False
    assert summary["recommended_action"] == "Propose a structured pilot conversation"
    assert summary["top_paths"][0] == "/demander-acces"


def test_build_intent_summary_handles_trust_friction_variants():
    summary = build_intent_summary(
        ["/securite", "/confidentialite", "/architecture", "/pourquoi_helpchain"],
        has_submit=False,
    )

    assert summary["score"] == 72
    assert summary["tier"] == "evaluating"
    assert summary["primary_interest"] == "trust_governance"
    assert summary["trust_friction_detected"] is True
    assert summary["friction_reason"] == "trust_governance_review_without_conversion"
