from backend.helpchain_backend.src.services.account_intelligence import (
    build_account_intelligence,
    compute_account_strength,
    extract_email_domain,
    infer_account_category,
)


def test_extract_email_domain():
    assert extract_email_domain("contact@ccas-nanterre.fr") == "ccas-nanterre.fr"


def test_detect_ccas_category():
    assert (
        infer_account_category(
            organization="CCAS Nanterre",
            domain="ccas-nanterre.fr",
        )
        == "CCAS / action sociale"
    )


def test_compute_account_strength():
    result = compute_account_strength(
        domain="ccas-nanterre.fr",
        organization="CCAS Nanterre",
        paths=["/", "/deploiement", "/offre"],
    )

    assert result in {"moderate", "strong"}


def test_build_account_intelligence():
    result = build_account_intelligence(
        {
            "email": "contact@ccas-nanterre.fr",
            "organization": "CCAS Nanterre",
            "paths": ["/", "/deploiement", "/offre"],
        }
    )

    assert result["is_institutional"] is True
    assert result["account_category"] == "CCAS / action sociale"
