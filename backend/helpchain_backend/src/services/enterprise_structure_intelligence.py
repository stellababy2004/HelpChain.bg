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


UNAVAILABLE = "Not enough operational data"
CLOSED_STATUSES = {"done", "closed", "resolved", "completed", "cancelled", "archived"}
BUSY_ASSIGNMENT_STATUSES = {"active", "assigned", "accepted", "in_progress"}
AVAILABLE_STATUSES = {"available", "disponible"}


ORGANIZATION_TYPES: dict[str, dict[str, Any]] = {
    "municipality": {
        "label": "Municipality",
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
        "label": "NGO",
        "icon": "globe-2",
        "color": "#0891b2",
        "permissions": ["requests.view", "missions.accept", "reports.view"],
        "default_capabilities": ["humanitarian_response", "case_coordination"],
    },
    "hospital": {
        "label": "Hospital",
        "icon": "hospital",
        "color": "#dc2626",
        "permissions": ["requests.view", "medical.route"],
        "default_capabilities": ["medical_assistance", "emergency_triage"],
    },
    "clinic": {
        "label": "Clinic",
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
        "label": "Fire Department",
        "icon": "flame",
        "color": "#b91c1c",
        "permissions": ["requests.view", "emergency.route"],
        "default_capabilities": ["emergency_response"],
    },
    "emergency_medical": {
        "label": "Emergency Medical",
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
        "label": "Prefecture",
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
        "label": "Volunteer Network",
        "icon": "users",
        "color": "#9333ea",
        "permissions": ["missions.accept", "requests.view"],
        "default_capabilities": ["volunteer_dispatch"],
    },
    "food_bank": {
        "label": "Food Bank",
        "icon": "package",
        "color": "#ca8a04",
        "permissions": ["requests.view", "services.fulfill"],
        "default_capabilities": ["food_assistance"],
    },
    "shelter": {
        "label": "Shelter",
        "icon": "home",
        "color": "#0284c7",
        "permissions": ["requests.view", "housing.route"],
        "default_capabilities": ["emergency_housing"],
    },
    "school": {
        "label": "School",
        "icon": "graduation-cap",
        "color": "#4f46e5",
        "permissions": ["requests.view", "child_protection.route"],
        "default_capabilities": ["child_support", "education_support"],
    },
    "social_service": {
        "label": "Social Service",
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
        "name": _display_text(getattr(structure, "name", None), "Unnamed organization"),
        "organization_type": type_def,
        "status": _display_text(getattr(structure, "status", None)),
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


def build_services_catalog(structure_id: int) -> list[dict[str, Any]]:
    rows = (
        StructureService.query.filter(StructureService.structure_id == structure_id)
        .order_by(StructureService.is_active.desc(), StructureService.name.asc())
        .all()
    )
    return [
        {
            "name": _display_text(row.name),
            "category": _display_text(getattr(row, "category", None)),
            "availability": _display_text(getattr(row, "availability", None)),
            "capacity": getattr(row, "capacity", None),
            "capacity_display": (
                str(getattr(row, "capacity", None))
                if getattr(row, "capacity", None) is not None
                else UNAVAILABLE
            ),
            "responsible_professionals": _json_list(getattr(row, "responsible_professionals_json", None)),
            "opening_hours": _display_text(getattr(row, "opening_hours", None)),
            "coverage": _display_text(getattr(row, "coverage", None)),
            "is_active": bool(getattr(row, "is_active", False)),
        }
        for row in rows
    ]


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
            burnout_risk = "Critical"
        elif workload >= 80:
            burnout_risk = "High"
        elif workload >= 60:
            burnout_risk = "Moderate"
        else:
            burnout_risk = "Low"

    return {
        "professionals": _metric("structure.professionals", "Professionals", professionals, source_tables=["intervenants"], query_origin="count intervenants where structure_id = :id", confidence="high", explanation="Total professionals linked to this organization."),
        "available_professionals": _metric("structure.available_professionals", "Available Professionals", available_professionals, source_tables=["intervenants"], query_origin="count active intervenants with available/empty availability", confidence="medium", explanation="Professionals marked active and available."),
        "busy_professionals": _metric("structure.busy_professionals", "Busy Professionals", busy_professionals, source_tables=["assignments"], query_origin="distinct intervenants with active assignments", confidence="medium", explanation="Professionals with active assignment records."),
        "active_cases": _metric("structure.active_cases", "Current Active Cases", active_cases, source_tables=["requests"], query_origin="count requests excluding closed statuses", confidence="high", explanation="Open operational requests owned by this organization."),
        "maximum_capacity": _metric("structure.maximum_capacity", "Maximum Capacity", max_capacity, source_tables=["structure_services"], query_origin="sum capacity from active structure_services", confidence="medium" if max_capacity is not None else "low", explanation="Configured service capacity. Unavailable until service capacity is entered."),
        "available_capacity": _metric("structure.available_capacity", "Available Capacity", available_capacity, source_tables=["structure_services", "requests"], query_origin="sum service capacity minus active requests", confidence="medium" if available_capacity is not None else "low", explanation="Remaining configured capacity after active cases."),
        "average_response_time": _metric("structure.average_response_time", "Average Response Time", avg_response_hours, source_tables=["request_metrics", "requests"], query_origin="avg request_metrics.time_to_assign joined to requests", confidence="medium" if avg_response_hours is not None else "low", explanation="Average time to first assignment.", suffix="h"),
        "workload_percent": _metric("structure.workload_percent", "Current Workload", workload, source_tables=["structure_services", "requests"], query_origin="active request count / configured service capacity", confidence="medium" if workload is not None else "low", explanation="Workload based on configured service capacity.", suffix="%"),
        "burnout_risk": _metric("structure.burnout_risk", "Burnout Risk", burnout_risk, source_tables=["structure_services", "requests"], query_origin="workload percent risk band", confidence="medium" if burnout_risk else "low", explanation="Risk band derived from workload percentage."),
        "monthly_cases": _metric("structure.monthly_cases", "Monthly Cases", monthly_cases, source_tables=["requests"], query_origin="count requests created in last 30 days", confidence="high", explanation="Requests received during the last 30 days."),
        "weekly_cases": _metric("structure.weekly_cases", "Weekly Cases", weekly_cases, source_tables=["requests"], query_origin="count requests created in last 7 days", confidence="high", explanation="Requests received during the last 7 days."),
        "daily_cases": _metric("structure.daily_cases", "Daily Cases", daily_cases, source_tables=["requests"], query_origin="count requests created in last 24 hours", confidence="high", explanation="Requests received during the last 24 hours."),
        "average_resolution_time": _metric("structure.average_resolution_time", "Average Resolution Time", avg_resolution_hours, source_tables=["request_metrics", "requests"], query_origin="avg request_metrics.time_to_complete joined to requests", confidence="medium" if avg_resolution_hours is not None else "low", explanation="Average completion time for resolved requests.", suffix="h"),
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
            "type": _display_text(row.area_type),
            "name": _display_text(row.name),
            "postal_code": _display_text(row.postal_code),
            "department": _display_text(row.department),
            "coverage_radius_km": row.coverage_radius_km,
            "population_served": row.population_served,
        }
        for row in rows
    ]
    return {
        "configured": configured,
        "covered_cities": [item["name"] for item in configured if item["type"].lower() == "city"] or inferred_cities,
        "covered_districts": [item["name"] for item in configured if item["type"].lower() == "district"],
        "departments": sorted({item["department"] for item in configured if item["department"] != UNAVAILABLE}),
        "postal_codes": sorted({item["postal_code"] for item in configured if item["postal_code"] != UNAVAILABLE}),
        "population_served": sum(int(item["population_served"] or 0) for item in configured) or None,
        "source": "structure_coverage_areas" if configured else "requests.city inferred from active data",
        "confidence": "high" if configured else ("medium" if inferred_cities else "low"),
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
            "type": _display_text(row.contact_type),
            "name": _display_text(row.name),
            "role": _display_text(row.role),
            "email": _display_text(row.email),
            "phone": _display_text(row.phone),
            "availability": _display_text(row.availability),
            "escalation_order": row.escalation_order,
        }
        for row in rows
    ]
    by_type = {item["type"].lower().replace(" ", "_"): item for item in contacts}
    return {
        "contacts": contacts,
        "primary": by_type.get("primary") or by_type.get("primary_contact"),
        "secondary": by_type.get("secondary") or by_type.get("secondary_contact"),
        "emergency": by_type.get("emergency") or by_type.get("emergency_contact"),
        "duty_manager": by_type.get("duty_manager"),
        "escalation_chain": contacts,
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
            "confidence": "low",
            "trend": UNAVAILABLE,
            "explanation": "Health cannot be calculated until operational records exist.",
            "positive_factors": [],
            "negative_factors": [],
            "recommendations": ["Enter services, capacity, contacts, and operational coverage."],
        }

    score = 100
    negative: list[str] = []
    positive: list[str] = []
    recommendations: list[str] = []
    if overdue:
        score -= min(30, overdue * 5)
        negative.append(f"{overdue} overdue active case(s)")
        recommendations.append("Review overdue cases and assign owners.")
    if stale:
        score -= min(20, stale * 4)
        negative.append(f"{stale} case(s) without activity for more than 72h")
        recommendations.append("Reopen inactive cases and record next actions.")
    if unassigned:
        score -= min(25, unassigned * 5)
        negative.append(f"{unassigned} active case(s) without owner")
        recommendations.append("Assign owners to unassigned cases.")
    if critical:
        score -= min(20, critical * 6)
        negative.append(f"{critical} critical or high-risk case(s)")
        recommendations.append("Escalate critical cases through the duty chain.")
    if available_capacity is not None and available_capacity <= 0 and (active_cases or 0) > 0:
        score -= 20
        negative.append("No available configured capacity")
        recommendations.append("Increase service capacity or redirect cases to partners.")
    if available_professionals == 0 and (active_cases or 0) > 0:
        score -= 20
        negative.append("No available professional recorded")
        recommendations.append("Update professional availability or add backup coordinators.")
    if not negative:
        positive.append("No overdue, stale, unassigned, or critical active cases detected")
    if (available_professionals or 0) > 0:
        positive.append(f"{available_professionals} available professional(s)")
    if available_capacity is not None and available_capacity > 0:
        positive.append(f"{available_capacity} configured capacity slot(s) available")

    confidence = min(95, 45 + signal_count * 15 + min((active_cases or 0), 10) * 2)
    return {
        "score": max(0, int(score)),
        "display": str(max(0, int(score))),
        "confidence": f"{confidence}%",
        "trend": UNAVAILABLE,
        "explanation": "Health is derived from active workload, overdue cases, ownership, critical risk, capacity, and professional availability.",
        "positive_factors": positive,
        "negative_factors": negative,
        "recommendations": recommendations or ["Continue monitoring operational workload."],
    }


def build_operational_alerts(structure_id: int, capacity: dict[str, MetricValue]) -> list[dict[str, Any]]:
    now = _now()
    active_filter = _active_request_filter()
    stale_cutoff = now - timedelta(hours=72)
    overdue_cutoff = now - timedelta(days=3)
    alerts = []

    checks = [
        ("missing_owner", "Missing owner", Request.query.filter(Request.structure_id == structure_id).filter(Request.owner_id.is_(None)).filter(active_filter), "high"),
        ("urgent_unassigned", "Urgent unassigned", Request.query.filter(Request.structure_id == structure_id).filter(Request.owner_id.is_(None)).filter(func.lower(func.coalesce(Request.priority, "")).in_(["urgent", "critical", "high"])).filter(active_filter), "critical"),
        ("late_requests", "Late requests", Request.query.filter(Request.structure_id == structure_id).filter(active_filter).filter(Request.created_at < overdue_cutoff), "high"),
        ("inactivity_72h", "72h inactivity", Request.query.filter(Request.structure_id == structure_id).filter(or_(Request.updated_at < stale_cutoff, and_(Request.updated_at.is_(None), Request.created_at < stale_cutoff))), "medium"),
        ("high_risk_cases", "High-risk cases", Request.query.filter(Request.structure_id == structure_id).filter(active_filter).filter(func.lower(func.coalesce(Request.risk_level, "")) == "critical"), "critical"),
    ]
    for key, label, query, severity in checks:
        count = _count(query) or 0
        if count:
            alerts.append({"key": key, "label": label, "count": count, "severity": severity, "source": "requests"})

    if capacity["available_capacity"].value is not None and capacity["available_capacity"].value <= 0 and (capacity["active_cases"].value or 0) > 0:
        alerts.append({"key": "capacity_exceeded", "label": "Capacity exceeded", "count": capacity["active_cases"].value, "severity": "critical", "source": "structure_services + requests"})
    if capacity["available_professionals"].value == 0 and (capacity["active_cases"].value or 0) > 0:
        alerts.append({"key": "no_professional_available", "label": "No professional available", "count": capacity["active_cases"].value, "severity": "critical", "source": "intervenants + requests"})

    inactive_services = _count(
        StructureService.query.filter(StructureService.structure_id == structure_id).filter(StructureService.is_active.is_(False))
    ) or 0
    if inactive_services:
        alerts.append({"key": "critical_service_unavailable", "label": "Critical service unavailable", "count": inactive_services, "severity": "high", "source": "structure_services"})

    communication_failures = _count(
        RequestActivity.query.join(Request, RequestActivity.request_id == Request.id)
        .filter(Request.structure_id == structure_id)
        .filter(func.lower(func.coalesce(RequestActivity.action, "")).in_(["notification_failed", "communication_failed"]))
    ) or 0
    if communication_failures:
        alerts.append({"key": "communication_failure", "label": "Communication failure", "count": communication_failures, "severity": "medium", "source": "request_activities"})
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
            "label": _display_text(row.action).replace("_", " ").title(),
            "user": _display_text(getattr(getattr(row, "actor", None), "username", None)),
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
            "label": f"Emergency request received: {_display_text(row.title, 'Untitled request')}",
            "user": _display_text(getattr(getattr(row, "owner", None), "username", None)),
            "timestamp": row.created_at,
            "organization": UNAVAILABLE,
        }
        for row in request_rows
    ]


def build_ai_readiness(structure: Structure, capacity: dict[str, MetricValue], services: list[dict[str, Any]]) -> dict[str, Any]:
    type_def = organization_type_definition(structure)
    configured_capabilities = _json_list(getattr(structure, "capabilities_json", None))
    capabilities = configured_capabilities or type_def.get("default_capabilities", [])
    return {
        "capabilities": capabilities,
        "languages": _json_list(getattr(structure, "languages_json", None)),
        "availability": _display_text(getattr(structure, "opening_hours", None)),
        "response_time": capacity["average_response_time"].display,
        "priority_domains": _json_list(getattr(structure, "priority_domains_json", None)),
        "accepted_case_types": _json_list(getattr(structure, "accepted_case_types_json", None)),
        "required_documents": _json_list(getattr(structure, "required_documents_json", None)),
        "supported_populations": _json_list(getattr(structure, "supported_populations_json", None)),
        "risk_level": _display_text(getattr(structure, "risk_level", None)),
        "matching_score_inputs": {
            "organization_type": type_def.get("key"),
            "services": [item["name"] for item in services],
            "available_capacity": capacity["available_capacity"].value,
            "average_response_time_hours": capacity["average_response_time"].value,
        },
    }


def build_enterprise_structure_dashboard(structure: Structure) -> dict[str, Any]:
    capacity = build_capacity_metrics(int(structure.id))
    services = build_services_catalog(int(structure.id))
    profile = build_organization_profile(structure)
    coverage = build_territorial_coverage(int(structure.id))
    contacts = build_contact_directory(int(structure.id))
    activity = build_recent_activity(int(structure.id))
    if activity:
        profile["last_activity"] = activity[0]["timestamp"]
    health = build_health_explanation(int(structure.id), capacity)
    alerts = build_operational_alerts(int(structure.id), capacity)
    users_count = _count(AdminUser.query.filter(AdminUser.structure_id == structure.id))
    executive_kpis = [
        {"label": "Health", "display": health["display"], "confidence": health["confidence"]},
        {"label": "Capacity", "display": capacity["available_capacity"].display, "confidence": capacity["available_capacity"].confidence},
        {"label": "Cases", "display": capacity["active_cases"].display, "confidence": capacity["active_cases"].confidence},
        {"label": "Professionals", "display": capacity["professionals"].display, "confidence": capacity["professionals"].confidence},
        {"label": "Services", "display": str(len(services)) if services else UNAVAILABLE, "confidence": "high" if services else "low"},
        {"label": "Coverage", "display": str(len(coverage["covered_cities"])) if coverage["covered_cities"] else UNAVAILABLE, "confidence": coverage["confidence"]},
        {"label": "Response Time", "display": capacity["average_response_time"].display, "confidence": capacity["average_response_time"].confidence},
        {"label": "Escalations", "display": str(len([item for item in alerts if item["severity"] in {"high", "critical"}])), "confidence": "medium"},
    ]
    return {
        "profile": profile,
        "organization_type_registry": ORGANIZATION_TYPES,
        "services": services,
        "capacity": capacity,
        "coverage": coverage,
        "contacts": contacts,
        "health": health,
        "alerts": alerts,
        "activity": activity,
        "executive_kpis": executive_kpis,
        "ai_readiness": build_ai_readiness(structure, capacity, services),
        "users_count": users_count,
        "generated_at": _now(),
    }
