from __future__ import annotations

import math
from types import SimpleNamespace
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, session, url_for
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
    return render_template("admin/structures.html", structures=rows), 200


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
    return render_template("admin/structure_new.html"), 200


@admin_bp.post("/structures/new")
@admin_required
@admin_role_required("superadmin")
def admin_structure_create():
    _require_global_admin()
    name = (request.form.get("name") or "").strip()
    slug = (request.form.get("slug") or "").strip()

    errors = {}
    if not name:
        errors["name"] = "Le nom est requis."
    if not slug:
        errors["slug"] = "Le slug est requis."

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
                form_data={"name": name, "slug": slug},
                form_errors=errors,
            ),
            400,
        )

    row = Structure(name=name, slug=slug, created_at=datetime.utcnow())
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
    users_count = AdminUser.query.filter(AdminUser.structure_id == structure_id).count()

    open_filter = or_(
        Request.status.is_(None),
        ~func.lower(Request.status).in_(list(CLOSED_STATUSES)),
    )
    active_requests = (
        Request.query.filter(Request.structure_id == structure_id).filter(open_filter).count()
    )
    done_requests = (
        Request.query.filter(Request.structure_id == structure_id)
        .filter(func.lower(Request.status) == "done")
        .count()
    )
    recent_requests = (
        Request.query.filter_by(structure_id=structure_id)
        .order_by(Request.created_at.desc())
        .limit(10)
        .all()
    )

    return (
        render_template(
            "admin/structure_dashboard.html",
            structure=structure,
            users_count=users_count,
            active_requests=active_requests,
            done_requests=done_requests,
            recent_requests=recent_requests,
            health_score=compute_structure_health(structure_id),
            alerts=compute_structure_alerts(structure_id),
            capacity_metrics=_structure_capacity_metrics(structure_id),
        ),
        200,
    )


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
