from __future__ import annotations

from typing import Any


PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
}


INSTITUTIONAL_KEYWORDS = {
    "ccas": "CCAS / action sociale",
    "mairie": "Collectivité locale",
    "ville": "Collectivité locale",
    "departement": "Département",
    "association": "Association",
    "asso": "Association",
    "social": "Structure sociale",
}


HIGH_INTENT_PATHS = {
    "/deploiement",
    "/offre",
    "/demo",
    "/contact",
    "/demander-acces",
}


def normalize_domain(value: str | None) -> str:
    if not value:
        return ""

    value = value.lower().strip()
    value = value.replace("https://", "")
    value = value.replace("http://", "")
    value = value.replace("www.", "")
    value = value.split("/")[0]

    return value


def extract_email_domain(email: str | None) -> str:
    if not email or "@" not in email:
        return ""

    return normalize_domain(email.split("@")[-1])


def is_public_email_domain(domain: str | None) -> bool:
    return normalize_domain(domain) in PUBLIC_EMAIL_DOMAINS


def infer_account_category(
    *,
    organization: str | None = None,
    domain: str | None = None,
) -> str:
    haystack = f"{organization or ''} {domain or ''}".lower()

    for keyword, label in INSTITUTIONAL_KEYWORDS.items():
        if keyword in haystack:
            return label

    if domain and not is_public_email_domain(domain):
        return "Organisation professionnelle"

    return "Compte non qualifié"


def infer_operational_intent(paths: list[str] | None = None) -> str:
    paths = paths or []

    if "/deploiement" in paths:
        return "Deployment evaluation"

    if "/offre" in paths:
        return "Commercial evaluation"

    if "/securite" in paths or "/confidentialite" in paths:
        return "Governance review"

    return "General exploration"


def compute_account_strength(
    *,
    domain: str | None = None,
    organization: str | None = None,
    paths: list[str] | None = None,
) -> str:
    score = 0

    if domain and not is_public_email_domain(domain):
        score += 40

    if organization:
        score += 20

    if paths:
        score += min(len(paths) * 5, 25)

    if any(p in (paths or []) for p in HIGH_INTENT_PATHS):
        score += 25

    if score >= 75:
        return "strong"

    if score >= 45:
        return "moderate"

    return "weak"


def build_account_intelligence(row: dict[str, Any]) -> dict[str, Any]:
    email = row.get("email")
    organization = row.get("organization")
    paths = row.get("paths") or []

    domain = extract_email_domain(email)

    account_category = infer_account_category(
        organization=organization,
        domain=domain,
    )

    operational_intent = infer_operational_intent(paths)

    account_strength = compute_account_strength(
        domain=domain,
        organization=organization,
        paths=paths,
    )

    if account_strength == "strong":
        recommendation = "Prioritize founder outreach"

    elif operational_intent == "Deployment evaluation":
        recommendation = "Propose structured pilot exchange"

    elif operational_intent == "Governance review":
        recommendation = "Reassure governance and security positioning"

    else:
        recommendation = "Continue qualification"

    return {
        "domain": domain,
        "account_category": account_category,
        "operational_intent": operational_intent,
        "account_strength": account_strength,
        "recommendation": recommendation,
        "is_institutional": bool(domain and not is_public_email_domain(domain)),
    }
