from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, or_

from ..models import Intervenant, ProfessionalLead
from .case_risk import score_request_risk


CATEGORY_PROFESSION_FAMILIES: dict[str, tuple[str, ...]] = {
    "violence": ("avocat", "psychologue", "association", "assistant_social"),
    "food": ("association", "assistant_social"),
    "housing": ("assistant_social", "association", "avocat"),
    "health": ("medecin", "psychologue"),
    "admin_help": ("assistant_social", "avocat", "association"),
    "orientation": ("assistant_social", "association"),
    "isolation": ("assistant_social", "psychologue", "association"),
    "emergency": ("assistant_social", "association", "medecin", "psychologue"),
}

NEARBY_CITY_MAP: dict[str, tuple[str, ...]] = {
    "boulogne billancourt": (
        "issy les moulineaux",
        "suresnes",
        "neuilly sur seine",
        "paris",
    ),
    "issy les moulineaux": (
        "boulogne billancourt",
        "paris",
        "suresnes",
    ),
    "suresnes": (
        "boulogne billancourt",
        "neuilly sur seine",
        "paris",
        "issy les moulineaux",
    ),
    "neuilly sur seine": (
        "boulogne billancourt",
        "suresnes",
        "paris",
    ),
    "paris": (
        "boulogne billancourt",
        "issy les moulineaux",
        "suresnes",
        "neuilly sur seine",
    ),
}


def _normalize_text(value: str | None) -> str:
    txt = (value or "").strip().lower()
    if not txt:
        return ""
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = txt.replace("-", " ").replace("_", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def _is_synthetic_email(email: str | None) -> bool:
    txt = _normalize_text(email)
    return txt.endswith("@esante-fhir.local") or txt.startswith("no-email+")


def _profession_family(lead: ProfessionalLead) -> str:
    profession_text = " ".join(
        part for part in (
            getattr(lead, "profession", None),
            getattr(lead, "organization", None),
            getattr(lead, "notes", None),
        ) if part
    )
    text = _normalize_text(profession_text)
    if any(tok in text for tok in ("psychologue", "psychiatre", "therapeute", "psychother")):
        return "psychologue"
    if any(tok in text for tok in ("avocat", "juriste", "droit", "jurid")):
        return "avocat"
    if any(tok in text for tok in ("medecin", "docteur", "generaliste", "psychiatre", "infirmier")):
        return "medecin"
    if any(tok in text for tok in ("assistant social", "travailleur social", "service social", "ccas")):
        return "assistant_social"
    if any(tok in text for tok in ("association", "fondation", "centre", "maison", "foyer")):
        return "association"
    return "autre"


def _lead_has_website(lead: ProfessionalLead) -> bool:
    notes = _normalize_text(getattr(lead, "notes", None))
    message = _normalize_text(getattr(lead, "message", None))
    return "http://" in notes or "https://" in notes or "www." in notes or "http://" in message or "https://" in message or "www." in message


def _same_city(case_city: str | None, lead_city: str | None) -> bool:
    case_txt = _normalize_text(case_city)
    lead_txt = _normalize_text(lead_city)
    return bool(case_txt and lead_txt and case_txt == lead_txt)


def _same_department_hint(case_city: str | None, lead_city: str | None) -> bool:
    case_txt = _normalize_text(case_city)
    lead_txt = _normalize_text(lead_city)
    if not case_txt or not lead_txt or case_txt == lead_txt:
        return False
    hauts_de_seine = {
        "boulogne billancourt",
        "issy les moulineaux",
        "suresnes",
        "neuilly sur seine",
    }
    return case_txt in hauts_de_seine and lead_txt in hauts_de_seine


def _nearby_city(case_city: str | None, lead_city: str | None) -> bool:
    case_txt = _normalize_text(case_city)
    lead_txt = _normalize_text(lead_city)
    if not case_txt or not lead_txt or case_txt == lead_txt:
        return False
    nearby = NEARBY_CITY_MAP.get(case_txt, ())
    return lead_txt in nearby


def _wanted_families(category_code: str | None) -> tuple[str, ...]:
    return CATEGORY_PROFESSION_FAMILIES.get((category_code or "").strip().lower(), ())


def eligible_professional_leads_query():
    status_key = func.trim(func.lower(func.coalesce(ProfessionalLead.status, "")))
    return ProfessionalLead.query.filter(~status_key.in_(("invalid", "spam")))


def _score_contact_quality(lead: ProfessionalLead, high_risk: bool) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if getattr(lead, "email", None) and not _is_synthetic_email(lead.email):
        score += 12 if high_risk else 8
        reasons.append("contact direct")
    if getattr(lead, "phone", None):
        score += 12 if high_risk else 8
        reasons.append("téléphone disponible")
    if _lead_has_website(lead):
        score += 4
        reasons.append("site web")
    return score, reasons


def _score_lead(case_row, request_obj, lead: ProfessionalLead, triage: dict) -> dict:
    score = 0
    reasons: list[str] = []
    category_code = (
        getattr(request_obj, "category", None)
        or triage.get("suggested_category_code")
        or ""
    )
    wanted_families = _wanted_families(category_code)
    family = _profession_family(lead)
    high_risk = (triage.get("derived_priority") or "").lower() in {"high", "critical"}

    if family in wanted_families:
        boost = 40 if high_risk else 28
        score += boost
        reasons.append("categorie compatible")
    elif family != "autre" and wanted_families:
        score += 8
        reasons.append("profession connexe")

    profession_text = _normalize_text(getattr(lead, "profession", None))
    for token in wanted_families:
        if token.replace("_", " ") in profession_text:
            score += 10
            reasons.append("profession cible")
            break

    case_city = getattr(request_obj, "city", None)
    lead_city = getattr(lead, "city", None)
    if _same_city(case_city, lead_city):
        score += 18
        reasons.append("même ville")
    elif _same_department_hint(case_city, lead_city):
        score += 10
        reasons.append("proche geographiquement")
    elif _nearby_city(case_city, lead_city):
        score += 6
        reasons.append("proche geographiquement")

    contact_score, contact_reasons = _score_contact_quality(lead, high_risk=high_risk)
    score += contact_score
    reasons.extend(contact_reasons)

    if getattr(lead, "organization", None):
        score += 4
        reasons.append("organisation renseignee")

    if getattr(lead, "status", None) == "imported":
        score += 2

    deduped_reasons = list(dict.fromkeys(reasons))
    return {
        "lead": lead,
        "score": int(score),
        "reason_tags": deduped_reasons[:5],
        "profession_family": family,
    }


def suggest_professional_leads_for_case(
    case_row,
    request_obj,
    limit: int = 8,
    *,
    include_intervenant_fallback: bool = False,
) -> list[dict]:
    triage = score_request_risk(request_obj)

    excluded_lead_ids: set[int] = set()

    assigned_lead_id = getattr(case_row, "assigned_professional_lead_id", None)
    if assigned_lead_id is not None:
        try:
            excluded_lead_ids.add(int(assigned_lead_id))
        except Exception:
            pass

    for participant in getattr(case_row, "participants", []) or []:
        participant_type = (getattr(participant, "participant_type", None) or "").strip().lower()
        professional_lead_id = getattr(participant, "professional_lead_id", None)

        if participant_type == "professional_lead" and professional_lead_id is not None:
            try:
                excluded_lead_ids.add(int(professional_lead_id))
            except Exception:
                pass

    leads = (
        eligible_professional_leads_query()
        .order_by(ProfessionalLead.created_at.desc(), ProfessionalLead.id.desc())
        .limit(500)
        .all()
    )

    scored: list[dict] = []
    for lead in leads:
        try:
            lead_id = int(getattr(lead, "id", 0) or 0)
        except Exception:
            lead_id = 0

        if lead_id and lead_id in excluded_lead_ids:
            continue

        row = _score_lead(case_row, request_obj, lead, triage)
        if row["score"] <= 0:
            continue
        scored.append(row)

    scored.sort(
        key=lambda item: (
            -int(item["score"]),
            0 if _same_city(getattr(request_obj, "city", None), getattr(item["lead"], "city", None)) else 1,
            0 if _same_department_hint(getattr(request_obj, "city", None), getattr(item["lead"], "city", None)) else 1,
            0 if _nearby_city(getattr(request_obj, "city", None), getattr(item["lead"], "city", None)) else 1,
            item["lead"].full_name or "",
            item["lead"].id,
        )
    )
    if not scored and include_intervenant_fallback:
        intervenants = (
            Intervenant.query.filter(Intervenant.is_active.is_(True))
            .order_by(Intervenant.created_at.desc(), Intervenant.id.desc())
            .limit(500)
            .all()
        )

        for intervenant in intervenants:
            score = 20
            reasons = ["intervenant actif"]

            if _same_city(getattr(request_obj, "city", None), getattr(intervenant, "location", None)):
                score += 25
                reasons.append("même ville")

            if getattr(intervenant, "email", None):
                score += 10
                reasons.append("contact direct")

            if getattr(intervenant, "phone", None):
                score += 10
                reasons.append("téléphone disponible")

            scored.append({
                "lead": intervenant,
                "score": score,
                "reason_tags": reasons,
                "profession_family": getattr(intervenant, "actor_type", None) or "intervenant",
            })

        scored.sort(key=lambda item: -int(item["score"]))
    return scored[: max(1, int(limit or 8))]

