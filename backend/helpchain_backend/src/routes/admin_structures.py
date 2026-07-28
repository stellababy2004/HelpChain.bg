from __future__ import annotations

import math
import re
from types import SimpleNamespace
from datetime import datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import and_, func, or_

from backend.extensions import db
from backend.models import StructureService
from ..models import AdminUser, Intervenant, OrganizationAccessRequest, Request, Structure
from ..services.organization_onboarding import (
    AccessRequestAlreadyApproved,
    AccessRequestEmailAlreadyUsed,
    AccessRequestNotApprovable,
    approve_access_request,
    mark_access_request_need_info,
    reject_access_request,
)
from ..services.enterprise_structure_intelligence import (
    MetricValue,
    ORGANIZATION_TYPES,
    PRIORITY_LABELS,
    RISK_LABELS,
    SERVICE_CATEGORIES,
    STATUS_LABELS,
    build_service_detail,
    build_enterprise_structure_dashboard,
    serialize_json_list,
)
from ..services.prospect_auto_capture import (
    append_audience_context_to_notes,
    extract_audience_context,
    notes_without_audience_context,
)
from .admin import (
    CLOSED_STATUSES,
    _intervenant_availability,
    _intervenant_availability_badge,
    _intervenant_availability_label,
    _intervenant_actor_type_label,
    _is_global_admin,
    _require_global_admin,
    admin_bp,
    admin_required,
    admin_role_required,
    audit_admin_action,
)


def compute_structure_health(structure_id: int) -> int:
    score = 100
    now = datetime.utcnow()

    unassigned = (
        Request.query.filter(Request.structure_id == structure_id)
        .filter(Request.owner_id.is_(None))
        .count()
    )
    if unassigned > 0:
        score -= 30

    stale_cutoff = now - timedelta(hours=48)
    stale = (
        Request.query.filter(Request.structure_id == structure_id)
        .filter(
            or_(
                Request.updated_at < stale_cutoff,
                and_(Request.updated_at.is_(None), Request.created_at < stale_cutoff),
            )
        )
        .count()
    )
    if stale > 0:
        score -= 20

    overdue_cutoff = now - timedelta(days=3)
    overdue = (
        Request.query.filter(Request.structure_id == structure_id)
        .filter(Request.created_at < overdue_cutoff)
        .filter(or_(Request.status.is_(None), ~Request.status.in_(list(CLOSED_STATUSES))))
        .count()
    )
    if overdue > 0:
        score -= 20

    return max(score, 0)


def compute_structure_alerts(structure_id: int) -> dict[str, int]:
    now = datetime.utcnow()
    base = Request.query.filter(Request.structure_id == structure_id)

    unassigned_count = base.filter(Request.owner_id.is_(None)).count()

    urgent_priorities = {"high", "critical", "urgent"}
    urgent_unassigned_count = (
        base.filter(Request.owner_id.is_(None))
        .filter(func.lower(func.coalesce(Request.priority, "")).in_(urgent_priorities))
        .count()
    )

    stale_cutoff = now - timedelta(hours=72)
    stale_count = base.filter(
        (Request.updated_at < stale_cutoff)
        | (Request.updated_at.is_(None) & (Request.created_at < stale_cutoff))
    ).count()

    overdue_cutoff = now - timedelta(days=3)
    active_filter = or_(
        Request.status.is_(None),
        ~func.lower(func.coalesce(Request.status, "")).in_(list(CLOSED_STATUSES)),
    )
    overdue_count = base.filter(active_filter).filter(Request.created_at < overdue_cutoff).count()

    return {
        "unassigned_count": int(unassigned_count or 0),
        "urgent_unassigned_count": int(urgent_unassigned_count or 0),
        "stale_count": int(stale_count or 0),
        "overdue_count": int(overdue_count or 0),
    }


def _structure_or_403(structure_id: int) -> Structure:
    if not _is_global_admin():
        current_sid = getattr(current_user, "structure_id", None)
        if current_sid is None or int(current_sid) != int(structure_id):
            abort(403)
    return Structure.query.get_or_404(structure_id)


def _safe_count(query) -> int | None:
    try:
        return int(query.count())
    except Exception:
        return None


def _structure_capacity_metrics(structure_id: int) -> dict[str, int | None]:
    active_intervenants = _safe_count(
        Intervenant.query.filter(Intervenant.structure_id == structure_id).filter(
            Intervenant.is_active.is_(True)
        )
    )
    services_available = _safe_count(
        StructureService.query.filter(StructureService.structure_id == structure_id).filter(
            StructureService.is_active.is_(True)
        )
    )

    try:
        coverage_rows = (
            db.session.query(func.count(func.distinct(Intervenant.location)))
            .filter(Intervenant.structure_id == structure_id)
            .filter(Intervenant.location.isnot(None))
            .scalar()
        )
        territorial_coverage = int(coverage_rows or 0)
    except Exception:
        territorial_coverage = None

    return {
        "active_intervenants": active_intervenants,
        "services_available": services_available,
        "territorial_coverage": territorial_coverage,
        "estimated_capacity": None,
    }


def _structure_dashboard_mode() -> str:
    mode = (request.args.get("mode") or "").strip().lower()
    if mode in {"operations", "developer"}:
        return "operations"
    return "executive"


SERVICE_STATUS_VALUES = {"active", "inactive", "available", "unavailable", "limited", "saturated"}
SERVICE_AVAILABILITY_VALUES = {"available", "disponible", "unavailable", "indisponible", "limited", "saturated"}
SERVICE_PRIORITY_VALUES = set(PRIORITY_LABELS)
SERVICE_RISK_VALUES = set(RISK_LABELS)
SERVICE_CAPACITY_MAX = 100000
SERVICE_SLA_MINUTES_MAX = 525600


def _slug_code(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-")[:64] or "service"


def _bounded_int_or_none(value: str, *, minimum: int, maximum: int, field_label: str) -> int | None:
    value = str(value or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"{field_label} doit être un entier compris entre {minimum} et {maximum}.")
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field_label} doit être compris entre {minimum} et {maximum}.")
    return parsed


def _bool_or_none(value: str | None, *, field_label: str) -> bool | None:
    cleaned = str(value or "").strip()
    if cleaned == "":
        return None
    if cleaned == "yes":
        return True
    if cleaned == "no":
        return False
    raise ValueError(f"{field_label} doit valoir oui, non ou rester vide.")


def _service_select_options() -> dict[str, object]:
    return {
        "service_categories": SERVICE_CATEGORIES,
        "service_statuses": STATUS_LABELS,
        "service_priorities": PRIORITY_LABELS,
        "service_risks": RISK_LABELS,
    }


def _serialize_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, MetricValue):
        return {
            "key": value.key,
            "label": value.label,
            "value": value.value,
            "display": value.display,
            "source_tables": value.source_tables,
            "query_origin": value.query_origin,
            "confidence": value.confidence,
            "updated_at": value.updated_at.isoformat() if value.updated_at else None,
            "explanation": value.explanation,
        }
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def _intervenant_display_name(row: Intervenant) -> str:
    return getattr(row, "name", None) or f"Intervenant #{row.id}"


def _intervenant_profession(row: Intervenant) -> str:
    return _intervenant_actor_type_label(getattr(row, "actor_type", None))


def _split_intervenant_location(row: Intervenant) -> tuple[str, str]:
    location = (getattr(row, "location", None) or "").strip()
    if not location:
        return "—", "—"
    parts = [part.strip() for part in location.split("·", 1)]
    if len(parts) == 2:
        return parts[0] or "—", parts[1] or "—"
    return location, "—"


@admin_bp.get("/structures")
@admin_required
@admin_role_required("superadmin")
def admin_structures():
    _require_global_admin()
    rows = Structure.query.order_by(Structure.name.asc(), Structure.id.asc()).all()
    structure_ids = [int(row.id) for row in rows]
    open_filter = or_(
        Request.status.is_(None),
        ~func.lower(func.coalesce(Request.status, "")).in_(list(CLOSED_STATUSES)),
    )
    latest_activity_by_structure = {}
    users_by_structure = {}
    active_requests_by_structure = {}
    services_by_structure = {}
    if structure_ids:
        latest_activity_by_structure = {
            int(structure_id): latest_activity
            for structure_id, latest_activity in db.session.query(
                Request.structure_id,
                func.max(Request.created_at),
            )
            .filter(Request.structure_id.in_(structure_ids))
            .group_by(Request.structure_id)
            .all()
            if structure_id is not None
        }
        users_by_structure = {
            int(structure_id): int(count or 0)
            for structure_id, count in db.session.query(
                AdminUser.structure_id,
                func.count(AdminUser.id),
            )
            .filter(AdminUser.structure_id.in_(structure_ids))
            .group_by(AdminUser.structure_id)
            .all()
            if structure_id is not None
        }
        active_requests_by_structure = {
            int(structure_id): int(count or 0)
            for structure_id, count in db.session.query(
                Request.structure_id,
                func.count(Request.id),
            )
            .filter(Request.structure_id.in_(structure_ids))
            .filter(open_filter)
            .group_by(Request.structure_id)
            .all()
            if structure_id is not None
        }
        services_by_structure = {
            int(structure_id): int(count or 0)
            for structure_id, count in db.session.query(
                StructureService.structure_id,
                func.count(StructureService.id),
            )
            .filter(StructureService.structure_id.in_(structure_ids))
            .filter(StructureService.is_active.is_(True))
            .group_by(StructureService.structure_id)
            .all()
            if structure_id is not None
        }
    structure_rows = []
    for row in rows:
        organization_type_key = getattr(row, "organization_type", None)
        organization_type_label = (
            ORGANIZATION_TYPES.get(organization_type_key or "", {}).get("label")
            if organization_type_key
            else None
        )
        structure_rows.append(
            {
                "structure": row,
                "organization_type": organization_type_label or "Type non renseigné",
                "users_count": users_by_structure.get(int(row.id), 0),
                "active_requests": active_requests_by_structure.get(int(row.id), 0),
                "services_count": services_by_structure.get(int(row.id), 0),
                "last_activity": latest_activity_by_structure.get(int(row.id)),
            }
        )
    return render_template("admin/structures_enterprise.html", structures=rows, structure_rows=structure_rows), 200


@admin_bp.get("/organizations/requests")
@admin_required
@admin_role_required("superadmin")
def admin_organization_access_requests():
    _require_global_admin()
    rows = (
        OrganizationAccessRequest.query.order_by(
            OrganizationAccessRequest.created_at.desc(),
            OrganizationAccessRequest.id.desc(),
        )
        .limit(200)
        .all()
    )
    return (
        render_template(
            "admin/organization_access_requests.html",
            access_requests=rows,
        ),
        200,
    )


@admin_bp.get("/organizations/requests/<int:req_id>")
@admin_required
@admin_role_required("superadmin")
def admin_organization_access_request_detail(req_id: int):
    _require_global_admin()
    row = OrganizationAccessRequest.query.get_or_404(req_id)

    credentials = session.pop("organization_access_credentials", None)
    if not credentials or int(credentials.get("request_id") or 0) != int(req_id):
        credentials = None

    return (
        render_template(
            "admin/organization_access_request_detail.html",
            access_request=row,
            audience_context=extract_audience_context(row.internal_notes),
            review_notes=notes_without_audience_context(row.internal_notes),
            credentials=credentials,
        ),
        200,
    )


def _reviewer_admin_id() -> int | None:
    try:
        return int(getattr(current_user, "id", None))
    except (TypeError, ValueError):
        return None


def _review_notes(row: OrganizationAccessRequest) -> str | None:
    notes = (request.form.get("internal_notes") or "").strip()
    context = extract_audience_context(row.internal_notes)
    return append_audience_context_to_notes(notes or None, context)


@admin_bp.post("/organizations/requests/<int:req_id>/approve")
@admin_required
@admin_role_required("superadmin")
def admin_organization_access_request_approve(req_id: int):
    _require_global_admin()
    row = OrganizationAccessRequest.query.get_or_404(req_id)

    try:
        structure, admin_user, temporary_password = approve_access_request(
            row,
            reviewer_admin_id=_reviewer_admin_id(),
            internal_notes=_review_notes(row),
        )
    except AccessRequestAlreadyApproved:
        flash(
            "Cette demande a deja ete approuvee. Aucune structure supplementaire n'a ete creee.",
            "warning",
        )
    except AccessRequestEmailAlreadyUsed:
        flash("Un administrateur utilise deja cet email. Approbation interrompue.", "danger")
    except AccessRequestNotApprovable:
        flash("Cette demande ne peut pas etre approuvee dans son statut actuel.", "danger")
    except Exception:
        flash("L'approbation a echoue. Aucune creation partielle n'a ete conservee.", "danger")
        raise
    else:
        session["organization_access_credentials"] = {
            "request_id": int(row.id),
            "structure_id": int(structure.id),
            "structure_name": structure.name,
            "email": admin_user.email,
            "username": admin_user.username,
            "temporary_password": temporary_password,
            "login_url": url_for("admin.admin_login_legacy", _external=True),
        }

        audit_admin_action(
            action="ORGANIZATION_ACCESS_APPROVED",
            target_type="OrganizationAccessRequest",
            target_id=row.id,
            payload={
                "structure": {
                    "id": structure.id,
                    "name": structure.name,
                    "slug": structure.slug,
                },
                "admin_user_id": admin_user.id,
                "actor": {
                    "admin_user_id": getattr(current_user, "id", None),
                    "username": getattr(current_user, "username", None),
                },
            },
        )
        flash(
            "Demande approuvee. Structure et administrateur crees. "
            "Copiez les acces affiches ci-dessous.",
            "success",
        )

    return redirect(
        url_for("admin.admin_organization_access_request_detail", req_id=row.id),
        code=303,
    )


@admin_bp.post("/organizations/requests/<int:req_id>/reject")
@admin_required
@admin_role_required("superadmin")
def admin_organization_access_request_reject(req_id: int):
    _require_global_admin()
    row = OrganizationAccessRequest.query.get_or_404(req_id)
    try:
        reject_access_request(
            row,
            reviewer_admin_id=_reviewer_admin_id(),
            internal_notes=_review_notes(row),
        )
    except AccessRequestAlreadyApproved:
        flash("Cette demande est deja approuvee et ne peut plus etre rejetee.", "warning")
    else:
        flash("Demande rejetee.", "success")

    return redirect(
        url_for("admin.admin_organization_access_request_detail", req_id=row.id),
        code=303,
    )


@admin_bp.post("/organizations/requests/<int:req_id>/need-info")
@admin_required
@admin_role_required("superadmin")
def admin_organization_access_request_need_info(req_id: int):
    _require_global_admin()
    row = OrganizationAccessRequest.query.get_or_404(req_id)
    try:
        mark_access_request_need_info(
            row,
            reviewer_admin_id=_reviewer_admin_id(),
            internal_notes=_review_notes(row),
        )
    except AccessRequestAlreadyApproved:
        flash("Cette demande est deja approuvee et ne peut plus etre modifiee.", "warning")
    else:
        flash("Demande marquee comme information complementaire requise.", "success")

    return redirect(
        url_for("admin.admin_organization_access_request_detail", req_id=row.id),
        code=303,
    )


@admin_bp.get("/structures/new")
@admin_required
@admin_role_required("superadmin")
def admin_structure_new():
    _require_global_admin()
    return render_template(
        "admin/structure_new.html",
        organization_types=ORGANIZATION_TYPES,
    ), 200


@admin_bp.post("/structures/new")
@admin_required
@admin_role_required("superadmin")
def admin_structure_create():
    _require_global_admin()
    name = (request.form.get("name") or "").strip()
    slug = (request.form.get("slug") or "").strip()
    organization_type = (request.form.get("organization_type") or "").strip()
    status = (request.form.get("status") or "pending").strip().lower()
    description = (request.form.get("description") or "").strip()
    legal_name = (request.form.get("legal_name") or "").strip()
    registration_number = (request.form.get("registration_number") or "").strip()
    website = (request.form.get("website") or "").strip()
    email = (request.form.get("email") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    emergency_phone = (request.form.get("emergency_phone") or "").strip()
    opening_hours = (request.form.get("opening_hours") or "").strip()
    head_office = (request.form.get("head_office") or "").strip()
    territory = (request.form.get("territory") or "").strip()

    errors = {}
    if not name:
        errors["name"] = "Le nom est requis."
    if not slug:
        errors["slug"] = "Le slug est requis."
    if status not in {"pending", "active", "inactive", "suspended"}:
        errors["status"] = "Statut invalide."
    if organization_type and organization_type not in ORGANIZATION_TYPES:
        errors["organization_type"] = "Type d'organisation invalide."

    if slug:
        existing = Structure.query.filter(Structure.slug == slug).first()
        if existing:
            errors["slug"] = "Ce slug est deja utilise."

    if errors:
        for msg in errors.values():
            flash(msg, "danger")
        return (
            render_template(
                "admin/structure_new.html",
                form_data=request.form,
                form_errors=errors,
                organization_types=ORGANIZATION_TYPES,
            ),
            400,
        )

    row = Structure(
        name=name,
        slug=slug,
        organization_type=organization_type or None,
        status=status,
        description=description or None,
        legal_name=legal_name or None,
        registration_number=registration_number or None,
        website=website or None,
        email=email or None,
        phone=phone or None,
        emergency_phone=emergency_phone or None,
        opening_hours=opening_hours or None,
        head_office=head_office or None,
        territory=territory or None,
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.commit()

    audit_admin_action(
        action="STRUCTURE_CREATED",
        target_type="Structure",
        target_id=row.id,
        payload={
            "structure": {"id": row.id, "name": row.name, "slug": row.slug},
            "actor": {
                "admin_user_id": getattr(current_user, "id", None),
                "username": getattr(current_user, "username", None),
            },
        },
    )
    flash("Structure creee.", "success")
    return redirect(url_for("admin.admin_structure_detail", structure_id=row.id), code=303)


@admin_bp.get("/structures/<int:structure_id>")
@admin_required
@admin_role_required("superadmin")
def admin_structure_detail(structure_id: int):
    structure = _structure_or_403(structure_id)
    intelligence = build_enterprise_structure_dashboard(structure)

    return (
        render_template(
            "admin/structure_enterprise_dashboard.html",
            structure=structure,
            enterprise=intelligence,
            dashboard_mode=_structure_dashboard_mode(),
        ),
        200,
    )


@admin_bp.get("/structures/<int:structure_id>/services")
@admin_required
@admin_role_required("superadmin")
def admin_structure_services(structure_id: int):
    structure = _structure_or_403(structure_id)
    intelligence = build_enterprise_structure_dashboard(structure)
    return (
        render_template(
            "admin/structure_services.html",
            structure=structure,
            enterprise=intelligence,
        ),
        200,
    )


@admin_bp.get("/structures/<int:structure_id>/services/new")
@admin_required
@admin_role_required("superadmin")
def admin_structure_service_new(structure_id: int):
    structure = _structure_or_403(structure_id)
    return (
        render_template(
            "admin/structure_service_new.html",
            structure=structure,
            **_service_select_options(),
        ),
        200,
    )


@admin_bp.post("/structures/<int:structure_id>/services/new")
@admin_required
@admin_role_required("superadmin")
def admin_structure_service_create(structure_id: int):
    structure = _structure_or_403(structure_id)
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "").strip()
    status = (request.form.get("status") or "").strip().lower()
    priority = (request.form.get("priority") or "").strip().lower()
    availability = (request.form.get("availability") or "").strip().lower()
    risk_level = (request.form.get("risk_level") or "").strip().lower()
    errors = {}
    if not name:
        errors["name"] = "Le nom du service est requis."
    if not category or category not in SERVICE_CATEGORIES:
        errors["category"] = "Catégorie de service invalide."
    if status and status not in SERVICE_STATUS_VALUES:
        errors["status"] = "Statut de service invalide."
    if availability and availability not in SERVICE_AVAILABILITY_VALUES:
        errors["availability"] = "Disponibilité invalide."
    if priority and priority not in SERVICE_PRIORITY_VALUES:
        errors["priority"] = "Priorité invalide."
    if risk_level and risk_level not in SERVICE_RISK_VALUES:
        errors["risk_level"] = "Niveau de risque invalide."
    try:
        capacity = _bounded_int_or_none(
            request.form.get("capacity") or "",
            minimum=0,
            maximum=SERVICE_CAPACITY_MAX,
            field_label="La capacité",
        )
    except ValueError as exc:
        errors["capacity"] = str(exc)
        capacity = None
    try:
        response_sla_minutes = _bounded_int_or_none(
            request.form.get("response_sla_hours") or "",
            minimum=0,
            maximum=SERVICE_SLA_MINUTES_MAX,
            field_label="Le SLA",
        )
    except ValueError as exc:
        errors["response_sla_hours"] = str(exc)
        response_sla_hours = None
    else:
        response_sla_hours = response_sla_minutes
    try:
        referral_required = _bool_or_none(
            request.form.get("referral_required"),
            field_label="Orientation requise",
        )
    except ValueError as exc:
        errors["referral_required"] = str(exc)
        referral_required = None
    try:
        emergency_support = _bool_or_none(
            request.form.get("emergency_support"),
            field_label="Prise en charge d'urgence",
        )
    except ValueError as exc:
        errors["emergency_support"] = str(exc)
        emergency_support = None

    if errors:
        for msg in errors.values():
            flash(msg, "danger")
        return (
            render_template(
                "admin/structure_service_new.html",
                structure=structure,
                form_data=request.form,
                form_errors=errors,
                **_service_select_options(),
            ),
            400,
        )

    code_base = _slug_code(name)
    code = code_base
    suffix = 2
    while StructureService.query.filter(
        StructureService.structure_id == structure.id,
        StructureService.code == code,
    ).first():
        code = f"{code_base[:58]}-{suffix}"
        suffix += 1

    service = StructureService(
        structure_id=structure.id,
        code=code,
        name=name,
        category=category,
        description=(request.form.get("description") or "").strip() or None,
        status=status or None,
        priority=priority or None,
        availability=availability or None,
        opening_hours=(request.form.get("opening_hours") or "").strip() or None,
        capacity=capacity,
        responsible_professionals_json=serialize_json_list(
            (request.form.get("professionals") or "").splitlines()
        ),
        response_sla_hours=response_sla_hours,
        target_population=(request.form.get("target_population") or "").strip() or None,
        eligibility=(request.form.get("eligibility") or "").strip() or None,
        required_documents_json=serialize_json_list(
            (request.form.get("required_documents") or "").splitlines()
        ),
        languages_json=serialize_json_list((request.form.get("languages") or "").splitlines()),
        contact_name=(request.form.get("contact_name") or "").strip() or None,
        contact_email=(request.form.get("contact_email") or "").strip() or None,
        contact_phone=(request.form.get("contact_phone") or "").strip() or None,
        tags_json=serialize_json_list((request.form.get("tags") or "").splitlines()),
        risk_level=risk_level or None,
        territory=(request.form.get("territory") or "").strip() or None,
        coverage=(request.form.get("territory") or "").strip() or None,
        referral_required=referral_required,
        emergency_support=emergency_support,
        is_active=(status or "active") != "inactive",
        created_at=datetime.utcnow(),
    )
    db.session.add(service)
    db.session.commit()
    audit_admin_action(
        action="STRUCTURE_SERVICE_CREATED",
        target_type="StructureService",
        target_id=service.id,
        payload={"structure_id": structure.id, "service": {"id": service.id, "name": service.name}},
    )
    flash("Service créé.", "success")
    return redirect(
        url_for("admin.admin_structure_service_detail", structure_id=structure.id, service_id=service.id),
        code=303,
    )


@admin_bp.get("/structures/<int:structure_id>/services/<int:service_id>")
@admin_required
@admin_role_required("superadmin")
def admin_structure_service_detail(structure_id: int, service_id: int):
    structure = _structure_or_403(structure_id)
    detail = build_service_detail(int(structure.id), int(service_id))
    if detail is None:
        abort(404)
    return (
        render_template(
            "admin/structure_service_detail.html",
            structure=structure,
            detail=detail,
        ),
        200,
    )


@admin_bp.get("/structures/<int:structure_id>/operational-intelligence")
@admin_required
@admin_role_required("superadmin")
def admin_structure_operational_intelligence(structure_id: int):
    structure = _structure_or_403(structure_id)
    intelligence = build_enterprise_structure_dashboard(structure)
    return jsonify(_serialize_value(intelligence)), 200


@admin_bp.get("/structures/<int:structure_id>/intervenants")
@admin_required
@admin_role_required("superadmin", "admin")
def admin_structure_intervenants(structure_id: int):
    structure = _structure_or_403(structure_id)

    search = (request.args.get("search") or "").strip()
    location_filter = (request.args.get("location") or "").strip()
    sort_by = (request.args.get("sort") or "created_at").strip().lower()
    sort_order = (request.args.get("order") or "desc").strip().lower()
    page = max(int(request.args.get("page") or 1), 1)
    per_page = max(min(int(request.args.get("per_page") or 25), 100), 10)

    query = Intervenant.query.filter(Intervenant.structure_id == structure.id)
    if search:
        q = f"%{search}%"
        query = query.filter(
            or_(
                Intervenant.name.ilike(q),
                Intervenant.email.ilike(q),
                Intervenant.phone.ilike(q),
            )
        )
    if location_filter:
        query = query.filter(Intervenant.location.ilike(f"%{location_filter}%"))

    sort_map = {
        "name": Intervenant.name,
        "email": Intervenant.email,
        "location": Intervenant.location,
        "created_at": Intervenant.created_at,
    }
    sort_col = sort_map.get(sort_by, Intervenant.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc(), Intervenant.id.asc())
    else:
        query = query.order_by(sort_col.desc(), Intervenant.id.desc())

    total_intervenants = query.count()
    total_pages = max(1, int(math.ceil(total_intervenants / float(per_page)))) if total_intervenants else 1
    if page > total_pages:
        page = total_pages

    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    intervenants = []
    for intervenant in rows:
        city, address = _split_intervenant_location(intervenant)
        availability = _intervenant_availability(intervenant)
        intervenants.append(
            SimpleNamespace(
                id=intervenant.id,
                legacy_volunteer_id=intervenant.legacy_volunteer_id,
                full_name=_intervenant_display_name(intervenant),
                profession=_intervenant_profession(intervenant),
                email=intervenant.email,
                phone=intervenant.phone,
                city=city,
                address=address,
                location=intervenant.location or "",
                availability=availability,
                availability_label=_intervenant_availability_label(availability),
                availability_badge_class=_intervenant_availability_badge(availability),
                is_active=bool(getattr(intervenant, "is_active", False)),
                created_at=intervenant.created_at,
                current_workload=0,
            )
        )

    return render_template(
        "admin/intervenants_list.html",
        structure=structure,
        intervenants=intervenants,
        total_intervenants=total_intervenants,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        search=search,
        location_filter=location_filter,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@admin_bp.post("/structures/<int:structure_id>/assign-admin")
@admin_required
@admin_role_required("superadmin")
def admin_structure_assign_admin(structure_id: int):
    _require_global_admin()
    row = Structure.query.get_or_404(structure_id)
    admin_id_raw = (request.form.get("admin_id") or "").strip()

    if not admin_id_raw:
        flash("Veuillez selectionner un administrateur.", "danger")
        return redirect(url_for("admin.admin_structure_detail", structure_id=row.id), code=303)

    try:
        admin_id = int(admin_id_raw)
    except Exception:
        flash("Administrateur invalide.", "danger")
        return redirect(url_for("admin.admin_structure_detail", structure_id=row.id), code=303)

    admin_user = db.session.get(AdminUser, admin_id)
    if not admin_user:
        flash("Administrateur introuvable.", "danger")
        return redirect(url_for("admin.admin_structure_detail", structure_id=row.id), code=303)

    admin_user.structure_id = row.id
    db.session.commit()

    audit_admin_action(
        action="STRUCTURE_ADMIN_ASSIGNED",
        target_type="AdminUser",
        target_id=admin_user.id,
        payload={
            "structure": {"id": row.id, "name": row.name, "slug": row.slug},
            "admin_user_id": admin_user.id,
            "admin_username": getattr(admin_user, "username", None),
            "actor": {
                "admin_user_id": getattr(current_user, "id", None),
                "username": getattr(current_user, "username", None),
            },
        },
    )
    flash("Administrateur assigne a la structure.", "success")
    return redirect(url_for("admin.admin_structure_detail", structure_id=row.id), code=303)
