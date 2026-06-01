from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, load_only

from backend.extensions import db
from backend.helpchain_backend.src.models import (
    Case,
    Intervenant,
    OrganizationAccessRequest,
    ProfessionalLead,
    Request,
)
from backend.helpchain_backend.src.services.early_warning import detect_ews
from backend.helpchain_backend.src.services.pilotage_territorial_maps import (
    build_territorial_snapshots,
)
from .admin import (
    _assignment_workload_subquery,
    _audience_department_code,
    _build_audience_map_context,
    _current_structure_id,
    _intervenant_address,
    _intervenant_availability,
    _intervenant_city,
    _intervenant_display_name,
    _intervenant_profession,
    _is_global_admin,
    _resolve_intervenant_coordinates,
    _scope_requests,
    _table_exists,
    admin_required,
    admin_required_404,
    admin_role_required,
)
from .admin_cases import build_case_copilot
from .admin_map import _scope_case_query, _valid_coordinates


admin_command_bp = Blueprint("admin_command", __name__, url_prefix="/admin")

_OPEN_REQUEST_STATUSES = {"new", "open", "pending", "in_progress", "assigned", "contacted"}
_OPEN_CASE_STATUSES = {"new", "open", "in_progress", "assigned", "pending"}
_STALE_THRESHOLD = timedelta(hours=72)
_DEFAULT_CENTER = {"lat": 46.603354, "lng": 1.888334, "zoom": 6}


def _world_bbox():
    return (-90.0, -180.0, 90.0, 180.0)


def _safe_iso(dt) -> str | None:
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None).isoformat(timespec="seconds")
    return None


def _now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _dt_to_naive_utc(dt) -> datetime | None:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        try:
            return dt.astimezone(UTC).replace(tzinfo=None)
        except Exception:
            return dt.replace(tzinfo=None)
    return dt


def _first_dt(*values) -> datetime | None:
    for value in values:
        normalized = _dt_to_naive_utc(value)
        if normalized is not None:
            return normalized
    return None


def _norm_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _owner_label(user) -> str:
    if not user:
        return "Non assigne"
    username = (getattr(user, "username", None) or "").strip()
    email = (getattr(user, "email", None) or "").strip()
    return username or email or "Admin"


def _lead_label(lead) -> str:
    if not lead:
        return "Non assigne"
    full_name = (getattr(lead, "full_name", None) or "").strip()
    email = (getattr(lead, "email", None) or "").strip()
    return full_name or email or f"Lead #{getattr(lead, 'id', '?')}"


def _city_key(city: str | None) -> str:
    return _norm_text(city)


def _structure_category(org_type: str | None) -> str:
    key = _norm_text(org_type)
    if "ccas" in key:
        return "ccas"
    if "centre social" in key:
        return "centre_social"
    if "mairie" in key:
        return "mairie"
    if "territorial" in key:
        return "service_territorial"
    return "structure_partenaire"


def _actor_category(actor_type: str | None, profession: str | None) -> str:
    actor_key = _norm_text(actor_type)
    profession_key = _norm_text(profession)
    if actor_key in {"association", "partner", "partenaire"}:
        return "association" if actor_key == "association" else "partenaire"
    if "association" in profession_key:
        return "association"
    if "partenaire" in profession_key:
        return "partenaire"
    return "professionnel"


def _risk_level(score: int) -> str:
    if int(score or 0) >= 80:
        return "critical"
    if int(score or 0) >= 50:
        return "watch"
    return "calm"


def _priority_key(value: str | None, score: int = 0) -> str:
    key = _norm_text(value)
    if key in {"critical", "urgent", "high"} or int(score or 0) >= 85:
        return "critical"
    if key in {"medium", "attention"} or int(score or 0) >= 60:
        return "watch"
    return key or "normal"


def _status_key(value: str | None) -> str:
    return _norm_text(value) or "unknown"


def _is_blocked_status(value: str | None) -> bool:
    return _status_key(value) in {"blocked", "bloquee", "on_hold", "hold", "paused"}


def _is_stale(dt: datetime | None, now: datetime | None = None) -> bool:
    ref = _dt_to_naive_utc(dt)
    if ref is None:
        return False
    current = now or _now_naive_utc()
    return ref <= current - _STALE_THRESHOLD


def _request_title(req, fallback: str) -> str:
    return (
        str(
            getattr(req, "title", None)
            or getattr(req, "normalized_address", None)
            or getattr(req, "city", None)
            or fallback
        )
        .strip()
    )


def _structure_name(case_row, request_row) -> str:
    case_structure = getattr(getattr(case_row, "structure", None), "name", None)
    request_structure = getattr(getattr(request_row, "structure", None), "name", None)
    return (case_structure or request_structure or "").strip()


def _append_city_point(index: dict[str, list[float]], city: str | None, lat, lng) -> None:
    key = _city_key(city)
    if not key:
        return
    try:
        lat_value = float(lat)
        lng_value = float(lng)
    except Exception:
        return
    if not (-90.0 <= lat_value <= 90.0 and -180.0 <= lng_value <= 180.0):
        return
    bucket = index.setdefault(key, [0.0, 0.0, 0.0])
    bucket[0] += lat_value
    bucket[1] += lng_value
    bucket[2] += 1.0


def _city_point(index: dict[str, list[float]], city: str | None) -> tuple[float, float] | None:
    bucket = index.get(_city_key(city))
    if not bucket or bucket[2] <= 0:
        return None
    return (bucket[0] / bucket[2], bucket[1] / bucket[2])


def _build_warning_reason(alert: dict) -> str:
    reasons = []
    if (alert.get("growth_ratio") or 0) > 2:
        reasons.append("growth_ratio > 2")
    if (alert.get("critical_cases") or 0) >= 3:
        reasons.append("critical_cases >= 3")
    if (alert.get("avg_risk") or 0) >= 75:
        reasons.append("avg_risk >= 75")
    return ", ".join(reasons) if reasons else "risk threshold exceeded"


def _compute_command_kpis(structure_id: int) -> dict:
    try:
        now = datetime.now(UTC)
        since_7 = now - timedelta(days=7)
        base = Case.query.filter(Case.structure_id == structure_id)
        active_cases = base.filter(func.lower(Case.status) != "closed").count()
        critical_cases = base.filter(func.coalesce(Case.risk_score, 0) >= 90).count()
        avg_risk = base.with_entities(func.avg(func.coalesce(Case.risk_score, 0))).scalar() or 0
        cases_last_7d = base.filter(Case.created_at >= since_7).count()
        early_warnings = len(detect_ews(structure_id, _world_bbox()))
        return {
            "active_cases": int(active_cases),
            "critical_cases": int(critical_cases),
            "avg_risk": int(round(avg_risk)),
            "early_warnings": int(early_warnings),
            "cases_last_7d": int(cases_last_7d),
        }
    except Exception:
        return {
            "active_cases": 0,
            "critical_cases": 0,
            "avg_risk": 0,
            "early_warnings": 0,
            "cases_last_7d": 0,
        }


def _load_command_situations(city_points: dict[str, list[float]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    now = _now_naive_utc()
    query = (
        Case.query.options(
            joinedload(Case.request),
            joinedload(Case.owner_user),
            joinedload(Case.assigned_professional_lead),
            joinedload(Case.structure),
        )
        .join(Request, Case.request_id == Request.id)
        .filter(
            or_(
                (
                    Case.latitude.isnot(None)
                    & Case.longitude.isnot(None)
                    & Case.latitude.between(-90, 90)
                    & Case.longitude.between(-180, 180)
                ),
                (
                    Request.latitude.isnot(None)
                    & Request.longitude.isnot(None)
                    & Request.latitude.between(-90, 90)
                    & Request.longitude.between(-180, 180)
                ),
            )
        )
        .order_by(
            func.coalesce(Case.risk_score, 0).desc(),
            Case.updated_at.desc(),
            Case.id.desc(),
        )
    )
    cases = _scope_case_query(query).limit(1500).all()
    for case_row in cases:
        request_row = getattr(case_row, "request", None)
        coords = _valid_coordinates(
            getattr(request_row, "latitude", None),
            getattr(request_row, "longitude", None),
        ) or _valid_coordinates(
            getattr(case_row, "latitude", None),
            getattr(case_row, "longitude", None),
        )
        if coords is None:
            continue
        last_activity_at = _first_dt(
            getattr(case_row, "last_activity_at", None),
            getattr(case_row, "updated_at", None),
            getattr(request_row, "updated_at", None),
            getattr(request_row, "created_at", None),
        )
        risk_score = int(
            getattr(case_row, "risk_score", None)
            or getattr(request_row, "risk_score", None)
            or 0
        )
        status_value = getattr(case_row, "status", None) or getattr(request_row, "status", None)
        priority_value = getattr(case_row, "priority", None) or getattr(request_row, "priority", None)
        is_stale_item = _is_stale(last_activity_at, now=now)
        has_assignment = bool(getattr(case_row, "owner_user_id", None))
        is_blocked = _is_blocked_status(status_value)
        if risk_score >= 85 or _priority_key(priority_value, risk_score) == "critical":
            marker_type = "urgence"
        elif is_blocked:
            marker_type = "bloquee"
        elif is_stale_item:
            marker_type = "stale"
        elif not has_assignment:
            marker_type = "sans_assignation"
        else:
            marker_type = "situation"

        copilot = build_case_copilot(case_row, request_row)
        city = (
            getattr(request_row, "city", None)
            or getattr(request_row, "location_text", None)
            or "Non localise"
        )
        _append_city_point(city_points, city, coords[0], coords[1])
        items.append(
            {
                "id": f"case:{int(case_row.id)}",
                "entity_id": int(case_row.id),
                "kind": "situation",
                "title": _request_title(request_row, f"Case #{case_row.id}"),
                "subtitle": "Situation terrain",
                "city": str(city),
                "department": _audience_department_code(city),
                "lat": coords[0],
                "lng": coords[1],
                "risk_score": risk_score,
                "risk_level": _risk_level(risk_score),
                "priority_key": _priority_key(priority_value, risk_score),
                "status_key": _status_key(status_value),
                "structure_name": _structure_name(case_row, request_row),
                "actor_type": "",
                "marker_type": marker_type,
                "has_assignment": has_assignment,
                "is_stale": is_stale_item,
                "is_blocked": is_blocked,
                "assigned_label": _owner_label(getattr(case_row, "owner_user", None)),
                "assigned_professional": _lead_label(getattr(case_row, "assigned_professional_lead", None)),
                "location_label": str(
                    getattr(request_row, "normalized_address", None)
                    or getattr(request_row, "location_text", None)
                    or city
                ),
                "last_activity_at": _safe_iso(last_activity_at),
                "next_action": copilot.get("recommended_action") or "Poursuivre le suivi",
                "timeline_summary": list(copilot.get("summary_points") or []),
                "detail_url": url_for("admin.admin_case_detail", case_id=int(case_row.id)),
            }
        )

    request_query = (
        _scope_requests(
            Request.query.outerjoin(Case, Case.request_id == Request.id)
            .filter(Case.id.is_(None))
            .filter(Request.latitude.isnot(None), Request.longitude.isnot(None))
            .filter(Request.latitude.between(-90, 90))
            .filter(Request.longitude.between(-180, 180))
            .options(joinedload(Request.owner))
            .order_by(Request.updated_at.desc(), Request.id.desc())
        )
        .limit(800)
        .all()
    )
    for request_row in request_query:
        coords = _valid_coordinates(
            getattr(request_row, "latitude", None),
            getattr(request_row, "longitude", None),
        )
        if coords is None:
            continue
        last_activity_at = _first_dt(
            getattr(request_row, "updated_at", None),
            getattr(request_row, "created_at", None),
        )
        risk_score = int(getattr(request_row, "risk_score", None) or 0)
        priority_value = getattr(request_row, "priority", None)
        status_value = getattr(request_row, "status", None)
        has_assignment = bool(
            getattr(request_row, "owner_id", None) or getattr(request_row, "assigned_volunteer_id", None)
        )
        is_stale_item = _is_stale(last_activity_at, now=now)
        is_blocked = _is_blocked_status(status_value)
        if risk_score >= 85 or _priority_key(priority_value, risk_score) == "critical":
            marker_type = "urgence"
        elif is_blocked:
            marker_type = "bloquee"
        elif is_stale_item:
            marker_type = "stale"
        elif not has_assignment:
            marker_type = "sans_assignation"
        else:
            marker_type = "situation"
        city = getattr(request_row, "city", None) or getattr(request_row, "location_text", None) or "Non localise"
        _append_city_point(city_points, city, coords[0], coords[1])
        timeline_summary = [
            f"Statut actuel : {str(status_value or 'non defini').replace('_', ' ')}.",
            f"Priorite : {str(priority_value or 'normale').replace('_', ' ')}.",
            f"Responsable : {'attribue' if has_assignment else 'non attribue'}.",
        ]
        if is_stale_item:
            timeline_summary.append("Aucune action recente depuis plus de 72h.")
        items.append(
            {
                "id": f"request:{int(request_row.id)}",
                "entity_id": int(request_row.id),
                "kind": "situation",
                "title": _request_title(request_row, f"Request #{request_row.id}"),
                "subtitle": "Situation non convertie en cas",
                "city": str(city),
                "department": _audience_department_code(city),
                "lat": coords[0],
                "lng": coords[1],
                "risk_score": risk_score,
                "risk_level": _risk_level(risk_score),
                "priority_key": _priority_key(priority_value, risk_score),
                "status_key": _status_key(status_value),
                "structure_name": "",
                "actor_type": "",
                "marker_type": marker_type,
                "has_assignment": has_assignment,
                "is_stale": is_stale_item,
                "is_blocked": is_blocked,
                "assigned_label": _owner_label(getattr(request_row, "owner", None)),
                "assigned_professional": "Non assigne",
                "location_label": str(
                    getattr(request_row, "normalized_address", None)
                    or getattr(request_row, "location_text", None)
                    or city
                ),
                "last_activity_at": _safe_iso(last_activity_at),
                "next_action": (
                    "Attribuer un referent rapidement."
                    if not has_assignment
                    else "Verifier la prochaine etape terrain."
                ),
                "timeline_summary": timeline_summary,
                "detail_url": url_for("admin.admin_request_details", req_id=int(request_row.id)),
            }
        )
    return items


def _load_command_intervenants(city_points: dict[str, list[float]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    workload_sq = _assignment_workload_subquery()
    query = (
        db.session.query(
            Intervenant,
            func.coalesce(workload_sq.c.workload, 0).label("workload"),
        )
        .outerjoin(workload_sq, workload_sq.c.intervenant_id == Intervenant.id)
        .filter(Intervenant.is_active.is_(True))
    )
    if not _is_global_admin():
        query = query.filter(Intervenant.structure_id == _current_structure_id())
    rows = query.order_by(Intervenant.name.asc(), Intervenant.id.asc()).limit(1200).all()
    for intervenant, workload in rows:
        lat, lng, _has_exact_coordinates = _resolve_intervenant_coordinates(intervenant)
        city = _intervenant_city(intervenant) or "Paris"
        _append_city_point(city_points, city, lat, lng)
        actor_type = _actor_category(
            getattr(intervenant, "actor_type", None),
            _intervenant_profession(intervenant),
        )
        items.append(
            {
                "id": f"intervenant:{int(intervenant.id)}",
                "entity_id": int(intervenant.id),
                "kind": "intervenant",
                "title": _intervenant_display_name(intervenant),
                "subtitle": _intervenant_profession(intervenant),
                "city": city,
                "department": _audience_department_code(city),
                "lat": float(lat),
                "lng": float(lng),
                "risk_score": 0,
                "risk_level": "calm",
                "priority_key": "normal",
                "status_key": "active" if getattr(intervenant, "is_active", False) else "inactive",
                "structure_name": (getattr(getattr(intervenant, "structure", None), "name", None) or "").strip(),
                "actor_type": actor_type,
                "marker_type": actor_type,
                "has_assignment": int(workload or 0) > 0,
                "is_stale": False,
                "is_blocked": False,
                "assigned_label": "Disponible",
                "assigned_professional": _intervenant_availability(intervenant),
                "location_label": _intervenant_address(intervenant) or city,
                "last_activity_at": _safe_iso(getattr(intervenant, "created_at", None)),
                "next_action": "Mobilisable selon charge et secteur.",
                "timeline_summary": [
                    f"Type d'acteur : {actor_type.replace('_', ' ')}.",
                    f"Disponibilite : {_intervenant_availability(intervenant)}.",
                    f"Charge active : {int(workload or 0)} attribution(s).",
                ],
                "detail_url": url_for("admin.admin_intervenants"),
            }
        )
    return items


def _load_partner_structures(city_points: dict[str, list[float]]) -> list[dict[str, object]]:
    if not _table_exists("organization_access_requests") or not _is_global_admin():
        return []
    items: list[dict[str, object]] = []
    query = (
        OrganizationAccessRequest.query.options(
            load_only(
                OrganizationAccessRequest.id,
                OrganizationAccessRequest.organization_name,
                OrganizationAccessRequest.contact_name,
                OrganizationAccessRequest.city,
                OrganizationAccessRequest.org_type,
                OrganizationAccessRequest.status,
                OrganizationAccessRequest.next_action_at,
                OrganizationAccessRequest.next_action_note,
                OrganizationAccessRequest.reviewed_at,
                OrganizationAccessRequest.created_at,
            )
        )
        .filter(OrganizationAccessRequest.city.isnot(None))
        .filter(OrganizationAccessRequest.city != "")
        .order_by(OrganizationAccessRequest.created_at.desc(), OrganizationAccessRequest.id.desc())
        .limit(400)
    )
    for row in query.all():
        coords = _city_point(city_points, getattr(row, "city", None))
        if coords is None:
            continue
        category = _structure_category(getattr(row, "org_type", None))
        last_activity_at = _first_dt(
            getattr(row, "reviewed_at", None),
            getattr(row, "created_at", None),
        )
        next_action_note = (getattr(row, "next_action_note", None) or "").strip()
        items.append(
            {
                "id": f"structure:{int(row.id)}",
                "entity_id": int(row.id),
                "kind": "structure",
                "title": (getattr(row, "organization_name", None) or "Structure").strip(),
                "subtitle": (getattr(row, "contact_name", None) or "").strip(),
                "city": (getattr(row, "city", None) or "").strip(),
                "department": _audience_department_code(getattr(row, "city", None)),
                "lat": coords[0],
                "lng": coords[1],
                "risk_score": 0,
                "risk_level": "watch" if _status_key(getattr(row, "status", None)) == "new" else "calm",
                "priority_key": "watch" if _status_key(getattr(row, "status", None)) == "new" else "normal",
                "status_key": _status_key(getattr(row, "status", None)),
                "structure_name": (getattr(row, "organization_name", None) or "").strip(),
                "actor_type": category,
                "marker_type": category,
                "has_assignment": bool(getattr(row, "reviewed_at", None)),
                "is_stale": _is_stale(last_activity_at),
                "is_blocked": False,
                "assigned_label": "Demande d'acces",
                "assigned_professional": "Non applicable",
                "location_label": (getattr(row, "city", None) or "").strip(),
                "last_activity_at": _safe_iso(last_activity_at),
                "next_action": next_action_note or "Verifier la suite de qualification territoriale.",
                "timeline_summary": [
                    f"Type : {category.replace('_', ' ')}.",
                    f"Statut : {_status_key(getattr(row, 'status', None)).replace('_', ' ')}.",
                    f"Contact : {(getattr(row, 'contact_name', None) or '-').strip()}.",
                ],
                "detail_url": url_for("admin.admin_organization_access_request_detail", req_id=int(row.id)),
            }
        )
    return items


def _load_operational_alerts(city_points: dict[str, list[float]], situations: list[dict[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for row in situations:
        reasons = []
        marker_type = ""
        if row.get("risk_level") == "critical":
            reasons.append("Risque critique sur la situation.")
            marker_type = "sla_risk"
        if row.get("is_stale"):
            reasons.append("Aucune activite recente.")
            marker_type = marker_type or "no_activity"
        if not row.get("has_assignment"):
            reasons.append("Affectation manquante.")
            marker_type = marker_type or "no_assignment"
        if not reasons:
            continue
        items.append(
            {
                "id": f"alert:{row['id']}",
                "entity_id": row.get("entity_id"),
                "kind": "alert",
                "title": row.get("title") or "Alerte operationnelle",
                "subtitle": "Alerte situation",
                "city": row.get("city") or "",
                "department": row.get("department") or "",
                "lat": row.get("lat"),
                "lng": row.get("lng"),
                "risk_score": row.get("risk_score") or 0,
                "risk_level": row.get("risk_level") or "watch",
                "priority_key": row.get("priority_key") or "watch",
                "status_key": row.get("status_key") or "alert",
                "structure_name": row.get("structure_name") or "",
                "actor_type": "alert",
                "marker_type": marker_type or "alert",
                "has_assignment": row.get("has_assignment"),
                "is_stale": row.get("is_stale"),
                "is_blocked": row.get("is_blocked"),
                "assigned_label": row.get("assigned_label") or "Non assigne",
                "assigned_professional": row.get("assigned_professional") or "Non assigne",
                "location_label": row.get("location_label") or row.get("city") or "",
                "last_activity_at": row.get("last_activity_at"),
                "next_action": row.get("next_action") or "Verifier l'alerte.",
                "timeline_summary": reasons + list(row.get("timeline_summary") or [])[:3],
                "detail_url": row.get("detail_url") or "",
            }
        )

    if _table_exists("professional_leads"):
        query = (
            ProfessionalLead.query.options(
                load_only(
                    ProfessionalLead.id,
                    ProfessionalLead.full_name,
                    ProfessionalLead.email,
                    ProfessionalLead.city,
                    ProfessionalLead.organization,
                    ProfessionalLead.profession,
                    ProfessionalLead.status,
                    ProfessionalLead.created_at,
                    ProfessionalLead.contacted_at,
                    ProfessionalLead.last_touched_at,
                    ProfessionalLead.next_action_at,
                    ProfessionalLead.next_action_note,
                )
            )
            .order_by(ProfessionalLead.created_at.desc(), ProfessionalLead.id.desc())
            .limit(400)
        )
        for lead in query.all():
            city = getattr(lead, "city", None)
            coords = _city_point(city_points, city)
            if coords is None:
                continue
            last_activity_at = _first_dt(
                getattr(lead, "last_touched_at", None),
                getattr(lead, "contacted_at", None),
                getattr(lead, "created_at", None),
            )
            next_action_at = _dt_to_naive_utc(getattr(lead, "next_action_at", None))
            is_followup_due = next_action_at is not None and next_action_at <= _now_naive_utc()
            is_stale_item = _is_stale(last_activity_at)
            if not is_followup_due and not is_stale_item:
                continue
            marker_type = "relance_overdue" if is_followup_due else "no_activity"
            title = (getattr(lead, "full_name", None) or getattr(lead, "email", None) or f"Lead #{lead.id}").strip()
            items.append(
                {
                    "id": f"lead-alert:{int(lead.id)}",
                    "entity_id": int(lead.id),
                    "kind": "alert",
                    "title": title,
                    "subtitle": (getattr(lead, "organization", None) or getattr(lead, "profession", None) or "Lead").strip(),
                    "city": city or "",
                    "department": _audience_department_code(city),
                    "lat": coords[0],
                    "lng": coords[1],
                    "risk_score": 65 if is_followup_due else 50,
                    "risk_level": "watch" if is_followup_due else "calm",
                    "priority_key": "watch",
                    "status_key": _status_key(getattr(lead, "status", None)),
                    "structure_name": (getattr(lead, "organization", None) or "").strip(),
                    "actor_type": "lead",
                    "marker_type": marker_type,
                    "has_assignment": False,
                    "is_stale": is_stale_item,
                    "is_blocked": False,
                    "assigned_label": "Pipeline leads",
                    "assigned_professional": "Non assigne",
                    "location_label": city or "",
                    "last_activity_at": _safe_iso(last_activity_at),
                    "next_action": (getattr(lead, "next_action_note", None) or "").strip() or "Relancer le lead.",
                    "timeline_summary": [
                        "Relance echeance atteinte." if is_followup_due else "Activite commerciale stale.",
                        f"Statut : {_status_key(getattr(lead, 'status', None)).replace('_', ' ')}.",
                        f"Ville : {city or '-'}.",
                    ],
                    "detail_url": url_for("admin.admin_professional_lead_detail", lead_id=int(lead.id)),
                }
            )
    return items


def _build_pressure_layers(
    situations: list[dict[str, object]],
    intervenants: list[dict[str, object]],
    structures: list[dict[str, object]],
    alerts: list[dict[str, object]],
) -> list[dict[str, object]]:
    buckets: dict[str, dict[str, object]] = {}

    def ensure_bucket(city: str | None, department: str | None, lat, lng) -> dict[str, object] | None:
        if not city:
            return None
        key = _city_key(city)
        if not key:
            return None
        bucket = buckets.setdefault(
            key,
            {
                "city": city,
                "department": department or "",
                "lat_sum": 0.0,
                "lng_sum": 0.0,
                "point_count": 0,
                "situation_count": 0,
                "urgent_count": 0,
                "stale_count": 0,
                "unassigned_count": 0,
                "blocked_count": 0,
                "professional_count": 0,
                "structure_count": 0,
                "alert_count": 0,
            },
        )
        try:
            bucket["lat_sum"] += float(lat)
            bucket["lng_sum"] += float(lng)
            bucket["point_count"] += 1
        except Exception:
            return bucket
        return bucket

    for item in situations:
        bucket = ensure_bucket(item.get("city"), item.get("department"), item.get("lat"), item.get("lng"))
        if not bucket:
            continue
        bucket["situation_count"] += 1
        if item.get("risk_level") == "critical":
            bucket["urgent_count"] += 1
        if item.get("is_stale"):
            bucket["stale_count"] += 1
        if not item.get("has_assignment"):
            bucket["unassigned_count"] += 1
        if item.get("is_blocked"):
            bucket["blocked_count"] += 1

    for item in intervenants:
        bucket = ensure_bucket(item.get("city"), item.get("department"), item.get("lat"), item.get("lng"))
        if bucket:
            bucket["professional_count"] += 1

    for item in structures:
        bucket = ensure_bucket(item.get("city"), item.get("department"), item.get("lat"), item.get("lng"))
        if bucket:
            bucket["structure_count"] += 1

    for item in alerts:
        bucket = ensure_bucket(item.get("city"), item.get("department"), item.get("lat"), item.get("lng"))
        if bucket:
            bucket["alert_count"] += 1

    pressure_items: list[dict[str, object]] = []
    for key, bucket in buckets.items():
        if bucket["point_count"] <= 0:
            continue
        pressure_score = (
            bucket["urgent_count"] * 3
            + bucket["stale_count"] * 2
            + bucket["unassigned_count"] * 2
            + bucket["blocked_count"] * 2
            + bucket["alert_count"]
            - min(bucket["professional_count"], bucket["situation_count"])
        )
        if bucket["professional_count"] <= 0 and bucket["situation_count"] > 0:
            pressure_score += 2
        if pressure_score >= 7 or bucket["urgent_count"] >= 3:
            level = "critical"
            marker_type = "saturation"
        elif pressure_score >= 4 or bucket["stale_count"] >= 2 or bucket["alert_count"] >= 2:
            level = "elevated"
            marker_type = "pressure"
        else:
            level = "calm"
            marker_type = "stable"
        if bucket["situation_count"] <= 0 and bucket["professional_count"] <= 0 and bucket["structure_count"] <= 0:
            continue
        pressure_items.append(
            {
                "id": f"pressure:{key}",
                "entity_id": key,
                "kind": "pressure",
                "title": str(bucket["city"]),
                "subtitle": "Lecture territoriale",
                "city": str(bucket["city"]),
                "department": str(bucket["department"] or ""),
                "lat": float(bucket["lat_sum"] / bucket["point_count"]),
                "lng": float(bucket["lng_sum"] / bucket["point_count"]),
                "risk_score": int(max(0, pressure_score) * 10),
                "risk_level": level,
                "priority_key": level,
                "status_key": level,
                "structure_name": "",
                "actor_type": "territory",
                "marker_type": marker_type,
                "has_assignment": bucket["professional_count"] > 0,
                "is_stale": bucket["stale_count"] > 0,
                "is_blocked": bucket["blocked_count"] > 0,
                "assigned_label": f"{bucket['professional_count']} intervenant(s)",
                "assigned_professional": "Lecture de couverture",
                "location_label": str(bucket["city"]),
                "last_activity_at": None,
                "next_action": (
                    "Reallouer la couverture et traiter les alertes."
                    if level == "critical"
                    else "Surveiller les relances et l'affectation."
                    if level == "elevated"
                    else "Maintenir la couverture territoriale."
                ),
                "timeline_summary": [
                    f"{bucket['situation_count']} situation(s) visibles.",
                    f"{bucket['professional_count']} intervenant(s) disponibles.",
                    f"{bucket['alert_count']} alerte(s) operationnelle(s).",
                ],
                "radius": 1200 + max(0, pressure_score) * 220,
            }
        )
    pressure_items.sort(
        key=lambda item: (
            0 if item["risk_level"] == "critical" else 1 if item["risk_level"] == "elevated" else 2,
            -(int(item.get("risk_score") or 0)),
            item.get("city") or "",
        )
    )
    return pressure_items


def _build_filter_options(*layers: list[dict[str, object]]) -> dict[str, list[str]]:
    departments = sorted({str(item.get("department") or "").strip() for layer in layers for item in layer if str(item.get("department") or "").strip()})
    cities = sorted({str(item.get("city") or "").strip() for layer in layers for item in layer if str(item.get("city") or "").strip()})
    priorities = sorted({str(item.get("priority_key") or "").strip() for layer in layers for item in layer if str(item.get("priority_key") or "").strip()})
    statuses = sorted({str(item.get("status_key") or "").strip() for layer in layers for item in layer if str(item.get("status_key") or "").strip()})
    structures = sorted({str(item.get("structure_name") or "").strip() for layer in layers for item in layer if str(item.get("structure_name") or "").strip()})
    actor_types = sorted({str(item.get("actor_type") or "").strip() for layer in layers for item in layer if str(item.get("actor_type") or "").strip()})
    return {
        "departments": departments,
        "cities": cities,
        "priorities": priorities,
        "statuses": statuses,
        "structures": structures,
        "actor_types": actor_types,
    }


def _build_default_center(*layers: list[dict[str, object]]) -> dict[str, float | int]:
    lat_total = 0.0
    lng_total = 0.0
    count = 0
    for layer in layers:
        for item in layer:
            try:
                lat_total += float(item.get("lat"))
                lng_total += float(item.get("lng"))
                count += 1
            except Exception:
                continue
    if count <= 0:
        return dict(_DEFAULT_CENTER)
    return {
        "lat": round(lat_total / count, 6),
        "lng": round(lng_total / count, 6),
        "zoom": 8 if count < 60 else 7,
    }


def _build_command_map_payload() -> dict[str, object]:
    city_points: dict[str, list[float]] = {}
    situations = _load_command_situations(city_points)
    intervenants = _load_command_intervenants(city_points)

    audience_available = bool(_is_global_admin())
    audience_context = _build_audience_map_context() if audience_available else {}
    if audience_available:
        for point in audience_context.get("map_locations", []):
            _append_city_point(city_points, point.get("label"), point.get("lat"), point.get("lng"))

    structures = _load_partner_structures(city_points)
    alerts = _load_operational_alerts(city_points, situations)
    pressure = _build_pressure_layers(situations, intervenants, structures, alerts)
    territorial_professionals = [
        {
            "id": item.get("id"),
            "city": item.get("city"),
            "latitude": item.get("lat"),
            "longitude": item.get("lng"),
            "availability": item.get("assigned_professional"),
            "profession": item.get("subtitle"),
            "actor_type": item.get("actor_type"),
            "workload": 1 if item.get("has_assignment") else 0,
            "has_exact_coordinates": True,
            "is_active": item.get("status_key") == "active",
        }
        for item in intervenants
    ]
    territorial_professionals.extend(
        {
            "id": item.get("id"),
            "city": item.get("city"),
            "latitude": item.get("lat"),
            "longitude": item.get("lng"),
            "availability": "unavailable",
            "profession": item.get("subtitle"),
            # Heuristic only: this counts visible partner-structure markers, not
            # a fully modelled partner topology.
            "actor_type": item.get("actor_type"),
            "workload": 0,
            "has_exact_coordinates": False,
            "is_active": False,
        }
        for item in structures
    )
    territories = build_territorial_snapshots(
        risk_items=[
            {
                "city": item.get("city"),
                "latitude": item.get("lat"),
                "longitude": item.get("lng"),
                "risk_level": item.get("risk_level"),
                "has_assignment": item.get("has_assignment"),
                "is_stale": item.get("is_stale"),
                "last_activity": item.get("last_activity_at"),
                "updated_at": item.get("last_activity_at"),
            }
            for item in situations
        ],
        professional_items=territorial_professionals,
    )

    counters = {
        "urgent_situations": sum(1 for item in situations if item.get("risk_level") == "critical"),
        "stale_situations": sum(1 for item in situations if item.get("is_stale")),
        "followups_due": sum(1 for item in alerts if item.get("marker_type") == "relance_overdue"),
        "pressured_territories": sum(1 for item in pressure if item.get("risk_level") in {"critical", "elevated"}),
    }

    return {
        "status": "ok",
        "generated_at": _safe_iso(_now_naive_utc()),
        "meta": {
            "audience_available": audience_available,
            "structures_available": bool(structures),
        },
        "default_center": _build_default_center(situations, intervenants, structures, alerts, pressure),
        "filters": _build_filter_options(situations, intervenants, structures, alerts, pressure),
        "counters": counters,
        "layers": {
            "situations": situations,
            "intervenants": intervenants,
            "pressure": pressure,
            "structures": structures,
            "alerts": alerts,
        },
        "territories": territories,
        "territorial_contract": {
            "version": "pilotage-v1",
            "scope": "city",
            "semantics": "territorial_operational_intelligence",
        },
        "audience_summary": {
            "kpis": (audience_context or {}).get("kpis", {}),
            "city_rows": (audience_context or {}).get("city_rows", []),
            "map_locations": (audience_context or {}).get("map_locations", []),
        },
    }


@admin_command_bp.get("/command")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_command_dashboard():
    admin_required_404()
    sid = _current_structure_id()
    if not sid:
        return render_template(
            "admin/command_dashboard.html",
            kpis={"active_cases": 0, "critical_cases": 0, "early_warnings": 0, "avg_risk": 0, "cases_last_7d": 0},
            critical_cases=[],
            early_warnings=[],
        )

    critical_cases = (
        Case.query.filter(Case.structure_id == sid)
        .order_by(func.coalesce(Case.risk_score, 0).desc(), Case.id.desc())
        .limit(10)
        .all()
    )

    warnings_raw = detect_ews(sid, _world_bbox())
    early_warnings = []
    for alert in warnings_raw:
        early_warnings.append(
            {
                "district": f"{alert['lat']:.2f}, {alert['lon']:.2f}",
                "alert_level": alert.get("level", "warning"),
                "reason": _build_warning_reason(alert),
                "growth_ratio": alert.get("growth_ratio", 0),
                "avg_risk": alert.get("avg_risk", 0),
                "critical_cases": alert.get("critical_cases", 0),
            }
        )

    kpis = _compute_command_kpis(sid)
    return render_template(
        "admin/command_dashboard.html",
        kpis=kpis,
        critical_cases=critical_cases,
        early_warnings=early_warnings,
    )


@admin_command_bp.get("/command-map")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_command_map():
    admin_required_404()
    return render_template(
        "admin/command_map.html",
        audience_available=_is_global_admin(),
    )


@admin_command_bp.get("/api/command-map")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_command_map_api():
    admin_required_404()
    try:
        return jsonify(_build_command_map_payload())
    except Exception:
        return jsonify(
            {
                "status": "error",
                "message": "command_map_unavailable",
                "default_center": dict(_DEFAULT_CENTER),
                "filters": {
                    "departments": [],
                    "cities": [],
                    "priorities": [],
                    "statuses": [],
                    "structures": [],
                    "actor_types": [],
                },
                "counters": {
                    "urgent_situations": 0,
                    "stale_situations": 0,
                    "followups_due": 0,
                    "pressured_territories": 0,
                },
                "layers": {
                    "situations": [],
                    "intervenants": [],
                    "pressure": [],
                    "structures": [],
                    "alerts": [],
                },
                "territories": [],
                "territorial_contract": {
                    "version": "pilotage-v1",
                    "scope": "city",
                    "semantics": "territorial_operational_intelligence",
                },
                "meta": {
                    "audience_available": False,
                    "structures_available": False,
                },
            }
        ), 500


@admin_command_bp.get("/api/command-kpis")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_command_kpis():
    admin_required_404()
    sid = _current_structure_id()
    if not sid:
        return jsonify(
            {
                "active_cases": 0,
                "critical_cases": 0,
                "avg_risk": 0,
                "early_warnings": 0,
                "cases_last_7d": 0,
            }
        )
    return jsonify(_compute_command_kpis(sid))
