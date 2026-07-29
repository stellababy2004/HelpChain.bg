from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_

from backend.extensions import db
from backend.models import (
    AdminUser,
    Assignment,
    Intervenant,
    Request,
    RequestActivity,
    RequestMetric,
    Structure,
    StructureContact,
    StructureCoverageArea,
    StructureService,
)


UNAVAILABLE = "Donnée indisponible"
NO_RECENT_ACTIVITY = "Aucune activité récente. Les événements apparaîtront ici."
CLOSED_STATUSES = {"done", "closed", "resolved", "completed", "cancelled", "archived"}
BUSY_ASSIGNMENT_STATUSES = {"active", "assigned", "accepted", "in_progress"}
AVAILABLE_STATUSES = {"available", "disponible"}

STATUS_LABELS = {
    "pending": "En attente",
    "active": "Actif",
    "inactive": "Inactif",
    "suspended": "Suspendu",
    "available": "Disponible",
    "disponible": "Disponible",
    "open": "Ouvert",
    "ouvert": "Ouvert",
    "unavailable": "Indisponible",
    "indisponible": "Indisponible",
    "limited": "Capacité limitée",
    "saturated": "Saturé",
}

PRIORITY_LABELS = {
    "low": "Faible",
    "medium": "Normale",
    "normal": "Normale",
    "high": "Haute",
    "urgent": "Urgente",
    "critical": "Critique",
}

RISK_LABELS = {
    "low": "Faible",
    "medium": "Modéré",
    "moderate": "Modéré",
    "high": "Élevé",
    "critical": "Critique",
}

BUSINESS_LABELS = {
    "administrative_support": "Accompagnement administratif",
    "benefits_guidance": "Orientation droits et prestations",
    "case_coordination": "Coordination des dossiers",
    "case_management": "Gestion de dossier",
    "child_support": "Soutien à l'enfance",
    "city": "Ville",
    "crisis_coordination": "Coordination de crise",
    "department": "Département",
    "district": "Quartier",
    "education_support": "Soutien éducatif",
    "emergency_assistance": "Assistance d'urgence",
    "emergency_housing": "Hébergement d'urgence",
    "emergency_medical_response": "Urgence médicale",
    "emergency_response": "Réponse d'urgence",
    "emergency_triage": "Triage d'urgence",
    "field_support": "Appui terrain",
    "financial_assistance": "Aides financières",
    "food_assistance": "Aide alimentaire",
    "humanitarian_response": "Réponse humanitaire",
    "legal_assistance": "Aide juridique",
    "medical_assistance": "Assistance médicale",
    "protection_orders": "Mesures de protection",
    "psychological_support": "Soutien psychologique",
    "public_case_intake": "Accueil des demandes publiques",
    "risk_escalation": "Escalade des risques",
    "security_response": "Réponse sécurité",
    "social_support": "Accompagnement social",
    "territorial_coordination": "Coordination territoriale",
    "territorial_governance": "Gouvernance territoriale",
    "volunteer_coordination": "Coordination bénévoles",
    "volunteer_dispatch": "Mobilisation bénévoles",
}

SERVICE_CATEGORIES: dict[str, str] = {
    "social_support": "Accompagnement social",
    "food_assistance": "Aide alimentaire",
    "emergency_housing": "Hébergement d'urgence",
    "psychological_support": "Soutien psychologique",
    "child_protection": "Protection de l'enfance",
    "domestic_violence": "Violences intrafamiliales",
    "administrative_support": "Accompagnement administratif",
    "professional_integration": "Insertion professionnelle",
    "health": "Santé",
    "disability": "Handicap",
    "elderly": "Personnes âgées",
    "solidarity_transport": "Transport solidaire",
    "housing": "Logement",
    "financial_assistance": "Aides financières",
    "orientation": "Orientation",
    "coordination": "Coordination",
}

CONTACT_TYPE_LABELS: dict[str, str] = {
    "primary": "Primary",
    "operational": "Operational",
    "emergency": "Emergency",
    "escalation": "Escalation",
    "after_hours": "After-hours",
}

PREFERRED_COMMUNICATION_LABELS: dict[str, str] = {
    "phone": "Telephone",
    "email": "Email",
    "sms": "SMS",
    "secure_portal": "Secure portal",
}

COVERAGE_AREA_LABELS: dict[str, str] = {
    "city": "City",
    "commune": "Commune",
    "postal_code": "Postal code",
    "department": "Department",
    "region": "Region",
    "district": "District",
}


ORGANIZATION_TYPES: dict[str, dict[str, Any]] = {
    "municipality": {
        "label": "Municipalité",
        "icon": "building-2",
        "color": "#2563eb",
        "permissions": ["requests.view", "requests.assign", "territory.manage"],
        "default_capabilities": ["territorial_coordination", "public_case_intake"],
    },
    "ccas": {
        "label": "CCAS",
        "icon": "landmark",
        "color": "#0f766e",
        "permissions": ["requests.view", "requests.assign", "services.manage"],
        "default_capabilities": ["social_support", "emergency_assistance"],
    },
    "association": {
        "label": "Association",
        "icon": "heart-handshake",
        "color": "#7c3aed",
        "permissions": ["requests.view", "missions.accept"],
        "default_capabilities": ["volunteer_coordination", "field_support"],
    },
    "ngo": {
        "label": "ONG",
        "icon": "globe-2",
        "color": "#0891b2",
        "permissions": ["requests.view", "missions.accept", "reports.view"],
        "default_capabilities": ["humanitarian_response", "case_coordination"],
    },
    "hospital": {
        "label": "Hôpital",
        "icon": "hospital",
        "color": "#dc2626",
        "permissions": ["requests.view", "medical.route"],
        "default_capabilities": ["medical_assistance", "emergency_triage"],
    },
    "clinic": {
        "label": "Clinique",
        "icon": "stethoscope",
        "color": "#ea580c",
        "permissions": ["requests.view", "medical.route"],
        "default_capabilities": ["medical_assistance"],
    },
    "police": {
        "label": "Police",
        "icon": "shield",
        "color": "#1e40af",
        "permissions": ["requests.view", "risk.escalate"],
        "default_capabilities": ["security_response", "risk_escalation"],
    },
    "fire_department": {
        "label": "Pompiers",
        "icon": "flame",
        "color": "#b91c1c",
        "permissions": ["requests.view", "emergency.route"],
        "default_capabilities": ["emergency_response"],
    },
    "emergency_medical": {
        "label": "SAMU / urgence médicale",
        "icon": "siren",
        "color": "#be123c",
        "permissions": ["requests.view", "emergency.route", "medical.route"],
        "default_capabilities": ["emergency_medical_response"],
    },
    "caf": {
        "label": "CAF",
        "icon": "wallet-cards",
        "color": "#16a34a",
        "permissions": ["requests.view", "benefits.route"],
        "default_capabilities": ["financial_assistance", "benefits_guidance"],
    },
    "prefecture": {
        "label": "Préfecture",
        "icon": "landmark",
        "color": "#334155",
        "permissions": ["requests.view", "territory.manage", "reports.view"],
        "default_capabilities": ["territorial_governance", "crisis_coordination"],
    },
    "justice": {
        "label": "Justice",
        "icon": "scale",
        "color": "#713f12",
        "permissions": ["requests.view", "legal.route"],
        "default_capabilities": ["legal_assistance", "protection_orders"],
    },
    "volunteer_network": {
        "label": "Réseau bénévole",
        "icon": "users",
        "color": "#9333ea",
        "permissions": ["missions.accept", "requests.view"],
        "default_capabilities": ["volunteer_dispatch"],
    },
    "food_bank": {
        "label": "Banque alimentaire",
        "icon": "package",
        "color": "#ca8a04",
        "permissions": ["requests.view", "services.fulfill"],
        "default_capabilities": ["food_assistance"],
    },
    "shelter": {
        "label": "Centre d'hébergement",
        "icon": "home",
        "color": "#0284c7",
        "permissions": ["requests.view", "housing.route"],
        "default_capabilities": ["emergency_housing"],
    },
    "school": {
        "label": "Établissement scolaire",
        "icon": "graduation-cap",
        "color": "#4f46e5",
        "permissions": ["requests.view", "child_protection.route"],
        "default_capabilities": ["child_support", "education_support"],
    },
    "social_service": {
        "label": "Service social",
        "icon": "clipboard-list",
        "color": "#059669",
        "permissions": ["requests.view", "requests.assign", "services.manage"],
        "default_capabilities": ["social_support", "case_management"],
    },
}


@dataclass(frozen=True)
class MetricValue:
    key: str
    label: str
    value: Any
    display: str
    source_tables: list[str]
    query_origin: str
    confidence: str
    updated_at: datetime
    explanation: str


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    try:
        parsed = json.loads(str(value))
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _display_text(value: Any, fallback: str = UNAVAILABLE) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"test", "demo", "sample", "cabinet", "structure_locale"}:
        return fallback
    return text


def _label(value: Any, registry: dict[str, str], fallback: str = UNAVAILABLE) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    key = raw.lower().replace(" ", "_").replace("-", "_")
    if key in registry:
        return registry[key]
    if raw in registry.values():
        return raw
    return BUSINESS_LABELS.get(key) or raw.replace("_", " ").capitalize()


def _confidence_label(value: Any) -> str:
    return _label(value, {"high": "élevée", "medium": "moyenne", "low": "faible"}, "faible")


def business_label(value: Any, fallback: str = UNAVAILABLE) -> str:
    return _label(value, {**BUSINESS_LABELS, **SERVICE_CATEGORIES}, fallback)


def status_label(value: Any, fallback: str = UNAVAILABLE) -> str:
    return _label(value, STATUS_LABELS, fallback)


def priority_label(value: Any, fallback: str = UNAVAILABLE) -> str:
    return _label(value, PRIORITY_LABELS, fallback)


def risk_label(value: Any, fallback: str = UNAVAILABLE) -> str:
    return _label(value, RISK_LABELS, fallback)


def contact_type_label(value: Any, fallback: str = UNAVAILABLE) -> str:
    return _label(value, CONTACT_TYPE_LABELS, fallback)


def preferred_communication_label(value: Any, fallback: str = UNAVAILABLE) -> str:
    return _label(value, PREFERRED_COMMUNICATION_LABELS, fallback)


def coverage_area_label(value: Any, fallback: str = UNAVAILABLE) -> str:
    return _label(value, COVERAGE_AREA_LABELS, fallback)


def serialize_json_list(values: list[str]) -> str | None:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def _active_request_filter():
    return or_(
        Request.status.is_(None),
        ~func.lower(func.coalesce(Request.status, "")).in_(list(CLOSED_STATUSES)),
    )


def _count(query) -> int | None:
    try:
        return int(query.count() or 0)
    except Exception:
        return None


def _scalar(query) -> Any:
    try:
        return query.scalar()
    except Exception:
        return None


def _metric(
    key: str,
    label: str,
    value: Any,
    *,
    source_tables: list[str],
    query_origin: str,
    confidence: str,
    explanation: str,
    suffix: str = "",
    updated_at: datetime | None = None,
) -> MetricValue:
    if value is None:
        display = UNAVAILABLE
    elif isinstance(value, float):
        display = f"{value:.1f}{suffix}"
    else:
        display = f"{value}{suffix}"
    return MetricValue(
        key=key,
        label=label,
        value=value,
        display=display,
        source_tables=source_tables,
        query_origin=query_origin,
        confidence=confidence,
        updated_at=updated_at or _now(),
        explanation=explanation,
    )


def organization_type_definition(structure: Structure) -> dict[str, Any]:
    key = _display_text(getattr(structure, "organization_type", None), "").lower()
    key = key.replace(" ", "_").replace("-", "_")
    definition = ORGANIZATION_TYPES.get(key)
    if definition:
        return {"key": key, **definition}
    return {
        "key": key or None,
        "label": _display_text(getattr(structure, "organization_type", None)),
        "icon": "building",
        "color": "#64748b",
        "permissions": [],
        "default_capabilities": [],
    }


def build_organization_profile(structure: Structure) -> dict[str, Any]:
    type_def = organization_type_definition(structure)
    return {
        "name": _display_text(getattr(structure, "name", None), "Organisation sans nom"),
        "organization_type": type_def,
        "status": status_label(getattr(structure, "status", None)),
        "description": _display_text(getattr(structure, "description", None)),
        "legal_name": _display_text(getattr(structure, "legal_name", None)),
        "registration_number": _display_text(getattr(structure, "registration_number", None)),
        "website": _display_text(getattr(structure, "website", None)),
        "email": _display_text(getattr(structure, "email", None)),
        "phone": _display_text(getattr(structure, "phone", None)),
        "emergency_phone": _display_text(getattr(structure, "emergency_phone", None)),
        "opening_hours": _display_text(getattr(structure, "opening_hours", None)),
        "head_office": _display_text(getattr(structure, "head_office", None)),
        "departments": _json_list(getattr(structure, "departments_json", None)),
        "territory": _display_text(getattr(structure, "territory", None)),
        "created_at": getattr(structure, "created_at", None),
        "last_activity": None,
    }


def _service_cases_by_id(structure_id: int) -> dict[int, int]:
    return {
        int(service_id): int(count or 0)
        for service_id, count in db.session.query(Request.service_id, func.count(Request.id))
        .filter(Request.structure_id == structure_id)
        .filter(Request.service_id.isnot(None))
        .filter(_active_request_filter())
        .group_by(Request.service_id)
        .all()
        if service_id is not None
    }


def _service_waiting_hours_by_id(structure_id: int) -> dict[int, float]:
    return {
        int(service_id): round(float(seconds or 0) / 3600.0, 1)
        for service_id, seconds in db.session.query(Request.service_id, func.avg(RequestMetric.time_to_assign))
        .join(RequestMetric, RequestMetric.request_id == Request.id)
        .filter(Request.structure_id == structure_id)
        .filter(Request.service_id.isnot(None))
        .filter(RequestMetric.time_to_assign.isnot(None))
        .group_by(Request.service_id)
        .all()
        if service_id is not None and seconds is not None
    }


def _serialize_service(
    row: StructureService,
    case_counts: dict[int, int],
    waiting_hours: dict[int, float],
) -> dict[str, Any]:
    service_id = int(row.id)
    capacity = getattr(row, "capacity", None)
    active_cases = case_counts.get(service_id, 0)
    available_capacity = max(int(capacity) - active_cases, 0) if capacity is not None else None
    availability_key = str(getattr(row, "availability", "") or "").strip().lower()
    is_available = availability_key in AVAILABLE_STATUSES and bool(getattr(row, "is_active", False))
    status_key = str(getattr(row, "status", "") or "").strip().lower()
    blocking_statuses = {"archived", "inactive", "suspended", "closed", "unavailable", "indisponible"}
    is_routable = (
        bool(getattr(row, "is_active", False))
        and availability_key in {"available", "disponible", "open", "ouvert"}
        and status_key == "active"
        and status_key not in blocking_statuses
    )
    non_routable_reason = None
    if not bool(getattr(row, "is_active", False)):
        non_routable_reason = "Service inactif"
    elif status_key in blocking_statuses or (status_key and status_key != "active"):
        non_routable_reason = "Statut non routable"
    elif not status_key:
        non_routable_reason = "Statut actif non confirmé"
    elif availability_key not in {"available", "disponible", "open", "ouvert"}:
        non_routable_reason = "Disponibilité non confirmée"
    professionals = _json_list(getattr(row, "responsible_professionals_json", None))
    average_waiting_time = waiting_hours.get(service_id)
    return {
        "id": service_id,
        "code": _display_text(getattr(row, "code", None)),
        "name": _display_text(row.name, "Service sans nom"),
        "category_key": _display_text(getattr(row, "category", None), ""),
        "category": business_label(getattr(row, "category", None), "Catégorie non renseignée"),
        "description": _display_text(getattr(row, "description", None), "Description non renseignée"),
        "status": status_label(getattr(row, "status", None), "Statut non renseigné"),
        "priority": priority_label(getattr(row, "priority", None), "Priorité non renseignée"),
        "availability": status_label(getattr(row, "availability", None), "Disponibilité non renseignée"),
        "is_available": is_available,
        "is_routable": is_routable,
        "non_routable_reason": non_routable_reason,
        "capacity": capacity,
        "capacity_display": str(capacity) if capacity is not None else "Capacité non renseignée",
        "available_capacity": available_capacity,
        "available_capacity_display": (
            str(available_capacity)
            if available_capacity is not None
            else "Capacité disponible non calculable"
        ),
        "responsible_professionals": professionals,
        "responsible_professionals_display": (
            ", ".join(professionals) if professionals else "Aucun professionnel affecté."
        ),
        "professionals_count": len(professionals),
        "opening_hours": _display_text(getattr(row, "opening_hours", None), "Horaires non renseignés"),
        "coverage": _display_text(getattr(row, "coverage", None), "Couverture non renseignée"),
        "notes": _display_text(getattr(row, "notes", None), "Notes non renseignées"),
        "response_sla_hours": getattr(row, "response_sla_hours", None),
        "response_sla_display": (
            f"{row.response_sla_hours} min"
            if getattr(row, "response_sla_hours", None) is not None
            else "SLA non renseigné"
        ),
        "target_population": _display_text(
            getattr(row, "target_population", None), "Public cible non renseigné"
        ),
        "eligibility": _display_text(
            getattr(row, "eligibility", None), "Conditions d'éligibilité non renseignées"
        ),
        "required_documents": [
            business_label(item, item)
            for item in _json_list(getattr(row, "required_documents_json", None))
        ],
        "languages": [
            business_label(item, item) for item in _json_list(getattr(row, "languages_json", None))
        ],
        "contact": {
            "name": _display_text(getattr(row, "contact_name", None), "Contact non renseigné"),
            "email": _display_text(getattr(row, "contact_email", None), "Email non renseigné"),
            "phone": _display_text(getattr(row, "contact_phone", None), "Téléphone non renseigné"),
        },
        "tags": [business_label(item, item) for item in _json_list(getattr(row, "tags_json", None))],
        "risk_level": risk_label(getattr(row, "risk_level", None), "Risque non renseigné"),
        "territory": _display_text(
            getattr(row, "territory", None),
            _display_text(getattr(row, "coverage", None), "Territoire non renseigné"),
        ),
        "referral_required": getattr(row, "referral_required", None),
        "referral_required_display": (
            "Orientation requise"
            if getattr(row, "referral_required", None) is True
            else (
                "Orientation non requise"
                if getattr(row, "referral_required", None) is False
                else "Règle d'orientation non renseignée"
            )
        ),
        "emergency_support": getattr(row, "emergency_support", None),
        "emergency_support_display": (
            "Prise en charge d'urgence"
            if getattr(row, "emergency_support", None) is True
            else (
                "Pas de prise en charge d'urgence"
                if getattr(row, "emergency_support", None) is False
                else "Support d'urgence non renseigné"
            )
        ),
        "is_active": bool(getattr(row, "is_active", False)),
        "active_cases": active_cases,
        "average_waiting_time": average_waiting_time,
        "average_waiting_time_display": (
            f"{average_waiting_time} h"
            if average_waiting_time is not None
            else "Temps d'attente non calculable"
        ),
        "created_at": getattr(row, "created_at", None),
        "updated_at": getattr(row, "updated_at", None),
    }


def build_services_catalog(structure_id: int) -> list[dict[str, Any]]:
    rows = (
        StructureService.query.filter(StructureService.structure_id == structure_id)
        .order_by(StructureService.is_active.desc(), StructureService.name.asc())
        .all()
    )
    if not rows:
        return []
    case_counts = _service_cases_by_id(structure_id)
    waiting_hours = _service_waiting_hours_by_id(structure_id)
    return [_serialize_service(row, case_counts, waiting_hours) for row in rows]


def build_services_dashboard(structure_id: int, services: list[dict[str, Any]]) -> dict[str, Any]:
    if not services:
        return {
            "total_services": 0,
            "by_category": {},
            "available_services": 0,
            "unavailable_services": 0,
            "active_services": 0,
            "high_demand_services": [],
            "capacity_per_service": [],
            "average_waiting_time": None,
            "average_waiting_time_display": "Temps d'attente non calculable",
            "professionals_assigned": 0,
            "assigned_operators": 0,
            "services_with_sla": 0,
            "sla_coverage_percent": None,
            "sla_coverage_display": "SLA non renseigné",
            "cases_by_service": [],
            "monthly_evolution": [],
            "response_sla": [],
            "source": "structure_services",
            "confidence": "faible",
            "updated_at": _now(),
        }
    by_category: dict[str, int] = {}
    for item in services:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    high_demand = [
        item
        for item in services
        if item["capacity"] is not None
        and item["active_cases"] >= int(item["capacity"])
        and item["active_cases"] > 0
    ]
    waiting_values = [
        item["average_waiting_time"]
        for item in services
        if item["average_waiting_time"] is not None
    ]
    avg_waiting = round(sum(waiting_values) / len(waiting_values), 1) if waiting_values else None
    monthly_rows = (
        db.session.query(StructureService.name, func.count(Request.id))
        .join(Request, Request.service_id == StructureService.id)
        .filter(Request.structure_id == structure_id)
        .filter(Request.created_at >= _now() - timedelta(days=30))
        .group_by(StructureService.name)
        .all()
    )
    return {
        "total_services": len(services),
        "by_category": by_category,
        "available_services": len([item for item in services if item["is_available"]]),
        "unavailable_services": len([item for item in services if not item["is_available"]]),
        "active_services": len([item for item in services if item["is_active"]]),
        "high_demand_services": high_demand,
        "capacity_per_service": [
            {
                "name": item["name"],
                "capacity": item["capacity"],
                "available_capacity": item["available_capacity"],
                "active_cases": item["active_cases"],
            }
            for item in services
        ],
        "average_waiting_time": avg_waiting,
        "average_waiting_time_display": (
            f"{avg_waiting} h" if avg_waiting is not None else "Temps d'attente non calculable"
        ),
        "professionals_assigned": sum(item["professionals_count"] for item in services),
        "assigned_operators": sum(item["professionals_count"] for item in services),
        "services_with_sla": len([item for item in services if item["response_sla_hours"] is not None]),
        "sla_coverage_percent": round(
            (len([item for item in services if item["response_sla_hours"] is not None]) / len(services)) * 100,
            1,
        )
        if services
        else None,
        "sla_coverage_display": (
            f"{round((len([item for item in services if item['response_sla_hours'] is not None]) / len(services)) * 100, 1)}%"
            if services
            else "SLA non renseigné"
        ),
        "cases_by_service": [{"name": item["name"], "cases": item["active_cases"]} for item in services],
        "monthly_evolution": [
            {"service": _display_text(name, "Service sans nom"), "cases": int(count or 0)}
            for name, count in monthly_rows
        ],
        "response_sla": [{"name": item["name"], "sla": item["response_sla_display"]} for item in services],
        "source": "structure_services, requests, request_metrics",
        "confidence": "élevée" if services else "faible",
        "updated_at": _now(),
    }


def build_capacity_metrics(structure_id: int) -> dict[str, MetricValue]:
    now = _now()
    active_filter = _active_request_filter()
    professionals = _count(Intervenant.query.filter(Intervenant.structure_id == structure_id))
    available_professionals = _count(
        Intervenant.query.filter(Intervenant.structure_id == structure_id)
        .filter(Intervenant.is_active.is_(True))
        .filter(func.lower(func.coalesce(Intervenant.availability, "")).in_(list(AVAILABLE_STATUSES)))
    )
    busy_professionals = _scalar(
        db.session.query(func.count(func.distinct(Assignment.intervenant_id)))
        .filter(Assignment.structure_id == structure_id)
        .filter(func.lower(func.coalesce(Assignment.status, "")).in_(list(BUSY_ASSIGNMENT_STATUSES)))
    )
    busy_professionals = int(busy_professionals) if busy_professionals is not None else None
    active_cases = _count(Request.query.filter(Request.structure_id == structure_id).filter(active_filter))
    weekly_cases = _count(
        Request.query.filter(Request.structure_id == structure_id).filter(
            Request.created_at >= now - timedelta(days=7)
        )
    )
    daily_cases = _count(
        Request.query.filter(Request.structure_id == structure_id).filter(
            Request.created_at >= now - timedelta(days=1)
        )
    )
    monthly_cases = _count(
        Request.query.filter(Request.structure_id == structure_id).filter(
            Request.created_at >= now - timedelta(days=30)
        )
    )
    max_capacity = _scalar(
        db.session.query(func.sum(StructureService.capacity)).filter(
            StructureService.structure_id == structure_id,
            StructureService.is_active.is_(True),
            func.lower(func.coalesce(StructureService.availability, "")).in_(list(AVAILABLE_STATUSES)),
        )
    )
    max_capacity = int(max_capacity) if max_capacity is not None else None
    available_capacity = (
        max(max_capacity - int(active_cases or 0), 0)
        if max_capacity is not None and active_cases is not None
        else None
    )
    workload = (
        round((float(active_cases or 0) / float(max_capacity)) * 100, 1)
        if max_capacity and active_cases is not None
        else None
    )
    avg_response_seconds = _scalar(
        db.session.query(func.avg(RequestMetric.time_to_assign))
        .join(Request, RequestMetric.request_id == Request.id)
        .filter(Request.structure_id == structure_id)
        .filter(RequestMetric.time_to_assign.isnot(None))
    )
    avg_resolution_seconds = _scalar(
        db.session.query(func.avg(RequestMetric.time_to_complete))
        .join(Request, RequestMetric.request_id == Request.id)
        .filter(Request.structure_id == structure_id)
        .filter(RequestMetric.time_to_complete.isnot(None))
    )
    avg_response_hours = round(float(avg_response_seconds) / 3600.0, 1) if avg_response_seconds else None
    avg_resolution_hours = round(float(avg_resolution_seconds) / 3600.0, 1) if avg_resolution_seconds else None
    burnout_risk = None
    if workload is not None:
        if workload >= 100:
            burnout_risk = "Critique"
        elif workload >= 80:
            burnout_risk = "Élevé"
        elif workload >= 60:
            burnout_risk = "Modéré"
        else:
            burnout_risk = "Faible"

    return {
        "professionals": _metric("structure.professionals", "Professionnels", professionals, source_tables=["intervenants"], query_origin="count intervenants where structure_id = :id", confidence="élevée", explanation="Nombre total de professionnels rattachés à l'organisation."),
        "available_professionals": _metric("structure.available_professionals", "Professionnels disponibles", available_professionals, source_tables=["intervenants"], query_origin="count active intervenants with availability in available/disponible", confidence="moyenne", explanation="Professionnels actifs avec une disponibilité explicitement disponible."),
        "busy_professionals": _metric("structure.busy_professionals", "Professionnels occupés", busy_professionals, source_tables=["assignments"], query_origin="distinct intervenants with active assignments", confidence="moyenne", explanation="Professionnels associés à des affectations actives."),
        "active_cases": _metric("structure.active_cases", "Dossiers actifs", active_cases, source_tables=["requests"], query_origin="count requests excluding closed statuses", confidence="élevée", explanation="Demandes opérationnelles ouvertes rattachées à l'organisation."),
        "maximum_capacity": _metric("structure.maximum_capacity", "Capacité maximale disponible", max_capacity, source_tables=["structure_services"], query_origin="sum capacity from active and explicitly available structure_services", confidence="moyenne" if max_capacity is not None else "faible", explanation="Capacité configurée uniquement sur les services explicitement disponibles."),
        "available_capacity": _metric("structure.available_capacity", "Capacité disponible", available_capacity, source_tables=["structure_services", "requests"], query_origin="available service capacity minus active requests", confidence="moyenne" if available_capacity is not None else "faible", explanation="Capacité restante après dossiers actifs."),
        "average_response_time": _metric("structure.average_response_time", "Temps de réponse moyen", avg_response_hours, source_tables=["request_metrics", "requests"], query_origin="avg request_metrics.time_to_assign joined to requests", confidence="moyenne" if avg_response_hours is not None else "faible", explanation="Temps moyen avant première affectation.", suffix=" h"),
        "workload_percent": _metric("structure.workload_percent", "Charge actuelle", workload, source_tables=["structure_services", "requests"], query_origin="active request count / explicitly available configured service capacity", confidence="moyenne" if workload is not None else "faible", explanation="Charge calculée à partir de la capacité de services disponible.", suffix="%"),
        "burnout_risk": _metric("structure.burnout_risk", "Risque de surcharge", burnout_risk, source_tables=["structure_services", "requests"], query_origin="workload percent risk band", confidence="moyenne" if burnout_risk else "faible", explanation="Niveau de risque dérivé du taux de charge."),
        "monthly_cases": _metric("structure.monthly_cases", "Dossiers mensuels", monthly_cases, source_tables=["requests"], query_origin="count requests created in last 30 days", confidence="élevée", explanation="Demandes reçues sur les 30 derniers jours."),
        "weekly_cases": _metric("structure.weekly_cases", "Dossiers hebdomadaires", weekly_cases, source_tables=["requests"], query_origin="count requests created in last 7 days", confidence="élevée", explanation="Demandes reçues sur les 7 derniers jours."),
        "daily_cases": _metric("structure.daily_cases", "Dossiers du jour", daily_cases, source_tables=["requests"], query_origin="count requests created in last 24 hours", confidence="élevée", explanation="Demandes reçues sur les dernières 24 heures."),
        "average_resolution_time": _metric("structure.average_resolution_time", "Temps moyen de résolution", avg_resolution_hours, source_tables=["request_metrics", "requests"], query_origin="avg request_metrics.time_to_complete joined to requests", confidence="moyenne" if avg_resolution_hours is not None else "faible", explanation="Temps moyen de résolution des demandes clôturées.", suffix=" h"),
    }


def build_territorial_coverage(structure_id: int) -> dict[str, Any]:
    rows = (
        StructureCoverageArea.query.filter(StructureCoverageArea.structure_id == structure_id)
        .filter(StructureCoverageArea.is_active.is_(True))
        .order_by(StructureCoverageArea.area_type.asc(), StructureCoverageArea.name.asc())
        .all()
    )
    inferred_cities = [
        str(row[0]).strip()
        for row in db.session.query(Request.city)
        .filter(Request.structure_id == structure_id)
        .filter(Request.city.isnot(None))
        .distinct()
        .limit(50)
        .all()
        if str(row[0] or "").strip()
    ]
    configured = [
        {
            "type_key": _display_text(row.area_type, "").lower(),
            "type": coverage_area_label(row.area_type),
            "name": _display_text(row.name),
            "postal_code": _display_text(row.postal_code),
            "department": _display_text(row.department),
            "region": _display_text(getattr(row, "region", None)),
            "administrative_code": _display_text(getattr(row, "administrative_code", None)),
            "coverage_radius_km": row.coverage_radius_km,
            "population_served": row.population_served,
            "geometry_kind": _display_text(getattr(row, "geometry_kind", None)),
            "geometry_configured": bool(getattr(row, "geometry_data_json", None)),
        }
        for row in rows
    ]
    return {
        "configured": configured,
        "covered_cities": [item["name"] for item in configured if item["type_key"] == "city"] or inferred_cities,
        "covered_communes": [item["name"] for item in configured if item["type_key"] == "commune"],
        "covered_districts": [item["name"] for item in configured if item["type_key"] == "district"],
        "regions": sorted({item["region"] for item in configured if item["region"] != UNAVAILABLE}),
        "departments": sorted({item["department"] for item in configured if item["department"] != UNAVAILABLE}),
        "postal_codes": sorted(
            {item["postal_code"] for item in configured if item["postal_code"] != UNAVAILABLE}
            | {item["name"] for item in configured if item["type_key"] == "postal_code"}
        ),
        "administrative_codes": sorted(
            {
                item["administrative_code"]
                for item in configured
                if item["administrative_code"] != UNAVAILABLE
            }
        ),
        "population_served": sum(int(item["population_served"] or 0) for item in configured) or None,
        "geometry_ready": any(item["geometry_configured"] for item in configured),
        "source": "structure_coverage_areas" if configured else "Données déduites des villes présentes dans les demandes actives",
        "confidence": "élevée" if configured else ("moyenne" if inferred_cities else "faible"),
    }


def build_contact_directory(structure_id: int) -> dict[str, Any]:
    rows = (
        StructureContact.query.filter(StructureContact.structure_id == structure_id)
        .filter(StructureContact.is_active.is_(True))
        .order_by(
            StructureContact.escalation_order.is_(None).asc(),
            StructureContact.escalation_order.asc(),
            StructureContact.id.asc(),
        )
        .all()
    )
    contacts = [
        {
            "type_key": _display_text(row.contact_type, "").lower(),
            "type": contact_type_label(row.contact_type),
            "name": _display_text(row.name),
            "role": _display_text(row.role),
            "email": _display_text(row.email),
            "phone": _display_text(row.phone),
            "availability": _display_text(row.availability),
            "preferred_communication": preferred_communication_label(
                getattr(row, "preferred_communication", None)
            ),
            "escalation_order": row.escalation_order,
        }
        for row in rows
    ]
    by_type = {item["type_key"].replace(" ", "_"): item for item in contacts}
    return {
        "contacts": contacts,
        "primary": by_type.get("primary"),
        "operational": by_type.get("operational"),
        "emergency": by_type.get("emergency"),
        "escalation": by_type.get("escalation"),
        "after_hours": by_type.get("after_hours"),
        "escalation_chain": contacts,
        "types_configured": sorted({item["type"] for item in contacts}),
    }


def build_operational_readiness(
    structure: Structure,
    *,
    contacts: dict[str, Any],
    services: list[dict[str, Any]],
    coverage: dict[str, Any],
    capacity: dict[str, MetricValue],
) -> dict[str, Any]:
    missing: list[str] = []
    recommendations: list[str] = []

    structure_status = str(getattr(structure, "status", "") or "").strip().lower()
    opening_hours = str(getattr(structure, "opening_hours", "") or "").strip()
    communication_channels = [
        value
        for value in (
            getattr(structure, "email", None),
            getattr(structure, "phone", None),
            getattr(structure, "emergency_phone", None),
        )
        if str(value or "").strip()
    ]
    active_service_count = len([item for item in services if item.get("is_active")])
    services_with_capacity = len([item for item in services if item.get("capacity") is not None])
    services_with_hours = len([item for item in services if "non renseign" not in item.get("opening_hours", "").lower()])
    contacts_with_channels = len(
        [
            item
            for item in contacts.get("contacts", [])
            if "non renseign" not in item.get("email", "").lower()
            or "non renseign" not in item.get("phone", "").lower()
        ]
    )
    preferred_communication_count = len(
        [
            item
            for item in contacts.get("contacts", [])
            if "non renseign" not in item.get("preferred_communication", "").lower()
        ]
    )
    coverage_entries = len(coverage.get("configured", []))
    coverage_scope_count = sum(
        1
        for values in (
            coverage.get("covered_cities", []),
            coverage.get("covered_communes", []),
            coverage.get("postal_codes", []),
            coverage.get("departments", []),
            coverage.get("regions", []),
        )
        if values
    )
    has_any_signal = any(
        [
            bool(contacts.get("contacts")),
            bool(services),
            bool(coverage.get("configured")),
            bool(opening_hours),
            bool(communication_channels),
            capacity["maximum_capacity"].value is not None,
        ]
    )

    checks = [
        (
            "Operational status",
            structure_status in {"active", "inactive", "suspended"},
            10,
            "Renseigner le statut opérationnel de l'organisation.",
        ),
        (
            "Operational contacts",
            bool(contacts.get("contacts")),
            15,
            "Ajouter au moins un contact opérationnel ou principal.",
        ),
        (
            "Contact communication preferences",
            preferred_communication_count > 0,
            10,
            "Préciser le canal de communication préféré pour les contacts clés.",
        ),
        (
            "Contact reachability",
            contacts_with_channels > 0 or bool(communication_channels),
            10,
            "Renseigner email, téléphone ou ligne d'urgence.",
        ),
        (
            "Active services",
            active_service_count > 0,
            15,
            "Configurer au moins un service actif.",
        ),
        (
            "Service operating hours",
            services_with_hours > 0 or bool(opening_hours),
            10,
            "Ajouter les horaires d'ouverture de l'organisation ou des services.",
        ),
        (
            "Configured capacity",
            services_with_capacity > 0 or capacity["maximum_capacity"].value is not None,
            10,
            "Renseigner une capacité de service exploitable.",
        ),
        (
            "Coverage configured",
            coverage_entries > 0,
            10,
            "Ajouter au moins une zone de couverture.",
        ),
        (
            "Coverage scope",
            coverage_scope_count > 0,
            5,
            "Préciser villes, communes, codes postaux, départements ou régions desservis.",
        ),
        (
            "Workspace communication channels",
            bool(communication_channels),
            5,
            "Renseigner les canaux de communication de l'organisation.",
        ),
    ]

    if not has_any_signal:
        for label, _, _, recommendation in checks:
            missing.append(label)
            recommendations.append(recommendation)
        recommendations.append(
            "Préparer un format de géométrie de couverture pour de futures zones polygonales."
        )
        return {
            "score": 0,
            "display": "0%",
            "missing_information": missing,
            "recommendations": recommendations,
            "coverage_scope_count": 0,
            "contacts_configured": 0,
            "active_services": 0,
        }

    score = 0
    for label, condition, points, recommendation in checks:
        if condition:
            score += points
        else:
            missing.append(label)
            recommendations.append(recommendation)

    if not coverage.get("geometry_ready"):
        recommendations.append(
            "Préparer un format de géométrie de couverture pour de futures zones polygonales."
        )
    score = max(0, min(100, int(score)))

    return {
        "score": score,
        "display": f"{score}%",
        "missing_information": missing,
        "recommendations": recommendations,
        "coverage_scope_count": coverage_scope_count,
        "contacts_configured": len(contacts.get("contacts", [])),
        "active_services": active_service_count,
    }


def build_health_explanation(structure_id: int, capacity: dict[str, MetricValue]) -> dict[str, Any]:
    now = _now()
    active_filter = _active_request_filter()
    overdue_cutoff = now - timedelta(days=3)
    stale_cutoff = now - timedelta(hours=72)
    overdue = _count(
        Request.query.filter(Request.structure_id == structure_id)
        .filter(active_filter)
        .filter(Request.created_at < overdue_cutoff)
    ) or 0
    stale = _count(
        Request.query.filter(Request.structure_id == structure_id)
        .filter(or_(Request.updated_at < stale_cutoff, and_(Request.updated_at.is_(None), Request.created_at < stale_cutoff)))
    ) or 0
    unassigned = _count(
        Request.query.filter(Request.structure_id == structure_id).filter(Request.owner_id.is_(None)).filter(active_filter)
    ) or 0
    critical = _count(
        Request.query.filter(Request.structure_id == structure_id)
        .filter(active_filter)
        .filter(or_(func.lower(func.coalesce(Request.priority, "")) == "critical", func.lower(func.coalesce(Request.risk_level, "")) == "critical"))
    ) or 0
    available_capacity = capacity["available_capacity"].value
    available_professionals = capacity["available_professionals"].value
    active_cases = capacity["active_cases"].value

    professionals = capacity["professionals"].value
    maximum_capacity = capacity["maximum_capacity"].value
    signal_count = sum(
        1
        for value in (active_cases, professionals, maximum_capacity)
        if value is not None and int(value or 0) > 0
    )
    if not signal_count:
        return {
            "score": None,
            "display": UNAVAILABLE,
            "confidence": "faible",
            "trend": UNAVAILABLE,
            "explanation": "Le score de santé sera calculé dès que des dossiers, services ou capacités réels seront enregistrés.",
            "positive_factors": [],
            "negative_factors": [],
            "recommendations": ["Renseigner les services, capacités, contacts et zones couvertes."],
        }

    score = 100
    negative: list[str] = []
    positive: list[str] = []
    recommendations: list[str] = []
    if overdue:
        score -= min(30, overdue * 5)
        negative.append(f"{overdue} dossier(s) actif(s) en retard")
        recommendations.append("Revoir les dossiers en retard et attribuer un responsable.")
    if stale:
        score -= min(20, stale * 4)
        negative.append(f"{stale} dossier(s) sans activité depuis plus de 72 h")
        recommendations.append("Relancer les dossiers inactifs et enregistrer une prochaine action.")
    if unassigned:
        score -= min(25, unassigned * 5)
        negative.append(f"{unassigned} dossier(s) actif(s) sans responsable")
        recommendations.append("Affecter les dossiers non attribués.")
    if critical:
        score -= min(20, critical * 6)
        negative.append(f"{critical} dossier(s) critique(s) ou à risque élevé")
        recommendations.append("Escalader les dossiers critiques via la chaîne d'astreinte.")
    if available_capacity is not None and available_capacity <= 0 and (active_cases or 0) > 0:
        score -= 20
        negative.append("Aucune capacité configurée disponible")
        recommendations.append("Augmenter la capacité ou réorienter vers un partenaire.")
    if available_professionals == 0 and (active_cases or 0) > 0:
        score -= 20
        negative.append("Aucun professionnel disponible enregistré")
        recommendations.append("Mettre à jour les disponibilités ou ajouter un coordinateur de secours.")
    if not negative:
        positive.append("Aucun dossier actif en retard, inactif, non attribué ou critique détecté")
    if (available_professionals or 0) > 0:
        positive.append(f"{available_professionals} professionnel(s) disponible(s)")
    if available_capacity is not None and available_capacity > 0:
        positive.append(f"{available_capacity} place(s) de capacité disponible(s)")

    confidence = min(95, 45 + signal_count * 15 + min((active_cases or 0), 10) * 2)
    return {
        "score": max(0, int(score)),
        "display": str(max(0, int(score))),
        "confidence": f"{confidence}%",
        "trend": UNAVAILABLE,
        "explanation": "Le score de santé est dérivé de la charge active, des retards, de l'affectation, du risque, de la capacité et de la disponibilité des professionnels.",
        "positive_factors": positive,
        "negative_factors": negative,
        "recommendations": recommendations or ["Poursuivre le suivi de la charge opérationnelle."],
    }


def build_operational_alerts(structure_id: int, capacity: dict[str, MetricValue]) -> list[dict[str, Any]]:
    now = _now()
    active_filter = _active_request_filter()
    stale_cutoff = now - timedelta(hours=72)
    overdue_cutoff = now - timedelta(days=3)
    alerts = []

    checks = [
        ("missing_owner", "Dossier sans responsable", Request.query.filter(Request.structure_id == structure_id).filter(Request.owner_id.is_(None)).filter(active_filter), "high"),
        ("urgent_unassigned", "Urgence non attribuée", Request.query.filter(Request.structure_id == structure_id).filter(Request.owner_id.is_(None)).filter(func.lower(func.coalesce(Request.priority, "")).in_(["urgent", "critical", "high"])).filter(active_filter), "critical"),
        ("late_requests", "Dossiers en retard", Request.query.filter(Request.structure_id == structure_id).filter(active_filter).filter(Request.created_at < overdue_cutoff), "high"),
        ("inactivity_72h", "Inactivité 72 h", Request.query.filter(Request.structure_id == structure_id).filter(or_(Request.updated_at < stale_cutoff, and_(Request.updated_at.is_(None), Request.created_at < stale_cutoff))), "medium"),
        ("high_risk_cases", "Dossiers à risque élevé", Request.query.filter(Request.structure_id == structure_id).filter(active_filter).filter(func.lower(func.coalesce(Request.risk_level, "")) == "critical"), "critical"),
    ]
    for key, label, query, severity in checks:
        count = _count(query) or 0
        if count:
            alerts.append({"key": key, "label": label, "count": count, "severity": severity, "source": "requests"})

    if capacity["available_capacity"].value is not None and capacity["available_capacity"].value <= 0 and (capacity["active_cases"].value or 0) > 0:
        alerts.append({"key": "capacity_exceeded", "label": "Capacité dépassée", "count": capacity["active_cases"].value, "severity": "critical", "source": "structure_services + requests"})
    if capacity["available_professionals"].value == 0 and (capacity["active_cases"].value or 0) > 0:
        alerts.append({"key": "no_professional_available", "label": "Aucun professionnel disponible", "count": capacity["active_cases"].value, "severity": "critical", "source": "intervenants + requests"})

    inactive_services = _count(
        StructureService.query.filter(StructureService.structure_id == structure_id).filter(StructureService.is_active.is_(False))
    ) or 0
    if inactive_services:
        alerts.append({"key": "service_unavailable", "label": "Service indisponible", "count": inactive_services, "severity": "high", "source": "structure_services"})

    communication_failures = _count(
        RequestActivity.query.join(Request, RequestActivity.request_id == Request.id)
        .filter(Request.structure_id == structure_id)
        .filter(func.lower(func.coalesce(RequestActivity.action, "")).in_(["notification_failed", "communication_failed"]))
    ) or 0
    if communication_failures:
        alerts.append({"key": "communication_failure", "label": "Échec de communication", "count": communication_failures, "severity": "medium", "source": "request_activities"})
    return alerts


def build_recent_activity(structure_id: int) -> list[dict[str, Any]]:
    activity_rows = (
        RequestActivity.query.join(Request, RequestActivity.request_id == Request.id)
        .filter(Request.structure_id == structure_id)
        .order_by(RequestActivity.created_at.desc())
        .limit(10)
        .all()
    )
    timeline = [
        {
            "icon": "activity",
            "label": business_label(row.action, _display_text(row.action).replace("_", " ").capitalize()),
            "user": _display_text(getattr(getattr(row, "actor", None), "username", None), "Utilisateur non renseigné"),
            "timestamp": row.created_at,
            "organization": UNAVAILABLE,
        }
        for row in activity_rows
    ]
    if timeline:
        return timeline

    request_rows = (
        Request.query.filter(Request.structure_id == structure_id)
        .order_by(Request.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "icon": "inbox",
            "label": f"Demande reçue : {_display_text(row.title, 'Demande sans titre')}",
            "user": _display_text(getattr(getattr(row, "owner", None), "username", None), "Responsable non attribué"),
            "timestamp": row.created_at,
            "organization": UNAVAILABLE,
        }
        for row in request_rows
    ]


def build_ai_readiness(structure: Structure, capacity: dict[str, MetricValue], services: list[dict[str, Any]]) -> dict[str, Any]:
    type_def = organization_type_definition(structure)
    configured_capabilities = _json_list(getattr(structure, "capabilities_json", None))
    capability_keys = configured_capabilities or type_def.get("default_capabilities", [])
    service_inputs = [
        {
            "service_id": item["id"],
            "name": item["name"],
            "category": item["category"],
            "capacity": item["capacity"],
            "available_capacity": item["available_capacity"],
            "territory": item["territory"],
            "priority": item["priority"],
            "availability": item["availability"],
            "languages": item["languages"],
            "target_population": item["target_population"],
            "required_documents": item["required_documents"],
            "average_waiting_time_hours": item["average_waiting_time"],
            "response_sla_hours": item["response_sla_hours"],
        }
        for item in services
        if item.get("is_routable")
    ]
    non_routable_services = [
        {
            "service_id": item["id"],
            "name": item["name"],
            "category": item["category"],
            "reason": item.get("non_routable_reason") or "Service non routable",
            "availability": item["availability"],
            "status": item["status"],
        }
        for item in services
        if not item.get("is_routable")
    ]
    return {
        "capabilities": [business_label(item, item) for item in capability_keys],
        "languages": [business_label(item, item) for item in _json_list(getattr(structure, "languages_json", None))],
        "availability": _display_text(getattr(structure, "opening_hours", None), "Disponibilité de l'organisation non renseignée"),
        "response_time": capacity["average_response_time"].display,
        "priority_domains": [business_label(item, item) for item in _json_list(getattr(structure, "priority_domains_json", None))],
        "accepted_case_types": [business_label(item, item) for item in _json_list(getattr(structure, "accepted_case_types_json", None))],
        "required_documents": [business_label(item, item) for item in _json_list(getattr(structure, "required_documents_json", None))],
        "supported_populations": [business_label(item, item) for item in _json_list(getattr(structure, "supported_populations_json", None))],
        "risk_level": risk_label(getattr(structure, "risk_level", None)),
        "matching_score_inputs": {
            "organization_type": type_def.get("key"),
            "services": service_inputs,
            "routable_services": service_inputs,
            "non_routable_services": non_routable_services,
            "available_capacity": capacity["available_capacity"].value,
            "average_response_time_hours": capacity["average_response_time"].value,
            "confidence": "moyenne" if service_inputs else "faible",
        },
    }


def build_service_detail(structure_id: int, service_id: int) -> dict[str, Any] | None:
    row = StructureService.query.filter(
        StructureService.structure_id == structure_id,
        StructureService.id == service_id,
    ).first()
    if row is None:
        return None
    service = _serialize_service(row, _service_cases_by_id(structure_id), _service_waiting_hours_by_id(structure_id))
    recent_requests = (
        Request.query.filter(Request.structure_id == structure_id)
        .filter(Request.service_id == service_id)
        .order_by(Request.created_at.desc())
        .limit(10)
        .all()
    )
    activities = (
        RequestActivity.query.join(Request, RequestActivity.request_id == Request.id)
        .filter(Request.structure_id == structure_id)
        .filter(Request.service_id == service_id)
        .order_by(RequestActivity.created_at.desc())
        .limit(10)
        .all()
    )
    alerts = []
    if service["capacity"] is not None and service["active_cases"] >= int(service["capacity"]) and service["active_cases"] > 0:
        alerts.append({"label": "Capacité atteinte", "severity": "critical", "source": "Capacité du service et dossiers actifs"})
    if not service["is_available"]:
        alerts.append({"label": "Service non disponible", "severity": "high", "source": "Disponibilité du service"})
    return {
        "service": service,
        "related_organizations": [],
        "cases": [
            {
                "id": req.id,
                "title": _display_text(req.title, "Demande sans titre"),
                "status": status_label(req.status, "Statut non renseigné"),
                "priority": priority_label(req.priority, "Priorité non renseignée"),
                "created_at": req.created_at,
            }
            for req in recent_requests
        ],
        "performance": {
            "active_cases": service["active_cases"],
            "average_waiting_time": service["average_waiting_time_display"],
            "response_sla": service["response_sla_display"],
            "source": "requests, request_metrics, structure_services",
        },
        "recent_activity": [
            {
                "label": business_label(item.action, _display_text(item.action).replace("_", " ").capitalize()),
                "timestamp": item.created_at,
            }
            for item in activities
        ],
        "alerts": alerts,
        "generated_at": _now(),
    }


def build_enterprise_structure_dashboard(structure: Structure) -> dict[str, Any]:
    capacity = build_capacity_metrics(int(structure.id))
    services = build_services_catalog(int(structure.id))
    services_dashboard = build_services_dashboard(int(structure.id), services)
    profile = build_organization_profile(structure)
    coverage = build_territorial_coverage(int(structure.id))
    contacts = build_contact_directory(int(structure.id))
    readiness = build_operational_readiness(
        structure,
        contacts=contacts,
        services=services,
        coverage=coverage,
        capacity=capacity,
    )
    activity = build_recent_activity(int(structure.id))
    if activity:
        profile["last_activity"] = activity[0]["timestamp"]
    health = build_health_explanation(int(structure.id), capacity)
    alerts = build_operational_alerts(int(structure.id), capacity)
    users_count = _count(AdminUser.query.filter(AdminUser.structure_id == structure.id))
    executive_kpis = [
        {"label": "Readiness", "display": readiness["display"], "confidence": "élevée"},
        {"label": "Santé", "display": health["display"], "confidence": health["confidence"]},
        {"label": "Capacité", "display": capacity["available_capacity"].display, "confidence": capacity["available_capacity"].confidence},
        {"label": "Demandes actives", "display": capacity["active_cases"].display, "confidence": capacity["active_cases"].confidence},
        {"label": "Services actifs", "display": str(services_dashboard["active_services"]), "confidence": "élevée" if services else "faible"},
        {"label": "Opérateurs affectés", "display": str(services_dashboard["assigned_operators"]), "confidence": "moyenne" if services else "faible"},
        {"label": "Couverture", "display": str(readiness["coverage_scope_count"]) if readiness["coverage_scope_count"] else "État neutre", "confidence": coverage["confidence"]},
        {"label": "SLA configuré", "display": services_dashboard["sla_coverage_display"], "confidence": "moyenne" if services else "faible"},
        {"label": "Temps de réponse", "display": capacity["average_response_time"].display, "confidence": capacity["average_response_time"].confidence},
        {"label": "Escalades", "display": str(len([item for item in alerts if item["severity"] in {"high", "critical"}])), "confidence": "moyenne"},
    ]
    return {
        "profile": profile,
        "organization_type_registry": ORGANIZATION_TYPES,
        "service_categories": SERVICE_CATEGORIES,
        "services": services,
        "services_dashboard": services_dashboard,
        "capacity": capacity,
        "coverage": coverage,
        "contacts": contacts,
        "readiness": readiness,
        "health": health,
        "alerts": alerts,
        "activity": activity,
        "executive_kpis": executive_kpis,
        "ai_readiness": build_ai_readiness(structure, capacity, services),
        "users_count": users_count,
        "generated_at": _now(),
    }
