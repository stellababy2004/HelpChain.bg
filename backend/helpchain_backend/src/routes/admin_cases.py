from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import false, func, or_

from backend.extensions import db
from ..admin_actor import resolve_current_admin_actor
from ..admin_policies import scope_case_query
from ..models import (
    AdminUser,
    Case,
    CaseCollaborator,
    CaseEvent,
    CaseParticipant,
    ProfessionalLead,
    Intervenant,
    Request,
    Structure,
    User,
)
from ..services.case_matching import suggest_professional_leads_for_case
from ..services.risk_alerts import evaluate_case_alerts
from ..services.risk_engine import update_case_risk
from .admin import (
    CATEGORY_CASE_STATUSES,
    CASE_PARTICIPANT_ROLES,
    CASE_PARTICIPANT_TYPES,
    CASE_PRIORITIES,
    _append_case_event,
    _build_operational_blockages,
    _build_risk_ai_suggestion,
    _cases_enabled,
    _current_structure_id,
    _is_global_admin,
    _now_utc,
    _render_cases_list,
    _scope_requests,
    admin_bp,
    admin_required,
    admin_required_404,
    admin_role_required,
)

def _group_events_by_day(events):
    groups = defaultdict(list)

    now = datetime.now(timezone.utc).date()

    for ev in events:
        dt = ev.created_at
        if not dt:
            key = "Autre"
        else:
            d = dt.date()

            if d == now:
                key = "Aujourd’hui"
            elif (now - d).days == 1:
                key = "Hier"
            else:
                key = d.strftime("%d %B %Y")

        groups[key].append(ev)

    return dict(groups)

# Legacy participant type labels that still resolve through the generic
# `User` model (`users.id`). These names are historical and must not be
# interpreted as canonical auth families such as `AdminUser`.
LEGACY_USER_PARTICIPANT_TYPES = {"admin_user", "professional_user"}

CASE_STATUS_TRANSITIONS = {
    "new": {"triaged", "assigned", "in_progress", "resolved", "cancelled"},
    "triaged": {"assigned", "in_progress", "resolved", "cancelled"},
    "assigned": {"in_progress", "resolved", "cancelled"},
    "in_progress": {"resolved", "cancelled"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
    "cancelled": set(),
}


def _safe_json_dict(raw: str | None) -> dict:
    txt = (raw or "").strip()
    if not txt:
        return {}
    try:
        val = json.loads(txt)
        if isinstance(val, dict):
            return val
    except Exception:
        return {}
    return {}


def _coerce_dt(value):
    if not value:
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _first_dt(*values):
    for value in values:
        dt = _coerce_dt(value)
        if dt is not None:
            return dt
    return None


def _human_elapsed(value, now=None):
    dt = _coerce_dt(value)
    if dt is None:
        return None
    current = now or _now_utc()
    delta_seconds = max(0, int((current - dt).total_seconds()))
    if delta_seconds < 3600:
        minutes = max(1, delta_seconds // 60)
        return f"{minutes} min"
    if delta_seconds < 86400:
        hours = max(1, delta_seconds // 3600)
        return f"{hours} h"
    days = max(1, delta_seconds // 86400)
    return f"{days} jours"


def _status_label(value):
    labels = {
        "new": "Nouveau",
        "open": "Ouvert",
        "triaged": "Oriente",
        "assigned": "Assigne",
        "in_progress": "En cours",
        "resolved": "Resolu",
        "done": "Termine",
        "closed": "Cloture",
        "blocked": "Bloque",
        "cancelled": "Annule",
        "canceled": "Annule",
    }
    key = (value or "").strip().lower()
    if not key:
        return "Non renseigne"
    return labels.get(key, key.replace("_", " ").title())


def _priority_label(value):
    labels = {
        "low": "Basse",
        "standard": "Standard",
        "medium": "Moyenne",
        "high": "Haute",
        "urgent": "Urgente",
        "critical": "Critique",
    }
    key = (value or "").strip().lower()
    if not key:
        return "Non renseignee"
    return labels.get(key, key.replace("_", " ").title())


def build_case_copilot(case, request, events=None, assignments=None):
    now = _now_utc()
    opened_at = _first_dt(
        getattr(case, "opened_at", None),
        getattr(case, "created_at", None),
        getattr(request, "created_at", None),
    )
    latest_event_at = None
    if events:
        latest_event_at = _first_dt(*(getattr(ev, "created_at", None) for ev in events[:5]))
    last_activity_at = _first_dt(
        getattr(case, "last_activity_at", None),
        getattr(case, "updated_at", None),
        latest_event_at,
        opened_at,
    )

    case_status = getattr(case, "status", None) or getattr(request, "status", None)
    case_priority = getattr(case, "priority", None) or getattr(request, "priority", None)
    risk_score = getattr(case, "risk_score", None)
    if risk_score is None:
        risk_score = getattr(request, "risk_score", None)
    try:
        risk_score_value = int(risk_score) if risk_score is not None else None
    except Exception:
        risk_score_value = None

    owner_assigned = bool(getattr(case, "owner_user_id", None))
    city_label = (
        getattr(request, "city", None)
        or getattr(request, "location_text", None)
        or getattr(case, "city", None)
    )

    opened_hours = None
    if opened_at is not None:
        opened_hours = max(0.0, (now - opened_at).total_seconds() / 3600)

    inactivity_hours = None
    if last_activity_at is not None:
        inactivity_hours = max(0.0, (now - last_activity_at).total_seconds() / 3600)

    if risk_score_value is not None and risk_score_value >= 80:
        attention_level = "red"
        attention_label = "Rouge - action prioritaire"
        reason = "Score de risque eleve."
    elif (case_priority or "").strip().lower() in {"critical", "urgent", "high"}:
        attention_level = "red"
        attention_label = "Rouge - action prioritaire"
        reason = "Priorite urgente, critique ou haute."
    elif inactivity_hours is not None and inactivity_hours > 72:
        attention_level = "orange"
        attention_label = "Orange - vigilance recommandee"
        reason = "Aucune action recente depuis plus de 72h."
    elif not owner_assigned and opened_hours is not None and opened_hours <= 2:
        attention_level = "green"
        attention_label = "Vert - suivi standard"
        reason = "Dossier recent en cours de prise en charge."
    elif not owner_assigned and opened_hours is not None and opened_hours > 24:
        attention_level = "orange"
        attention_label = "Orange - vigilance recommandee"
        reason = "Aucun responsable attribue au dela de 24h."
    elif risk_score_value is not None and risk_score_value >= 50:
        attention_level = "orange"
        attention_label = "Orange - vigilance recommandee"
        reason = "Score de risque intermediaire."
    elif not owner_assigned:
        attention_level = "green"
        attention_label = "Vert - suivi standard"
        reason = "Attribution encore en cours."
    elif inactivity_hours is not None and inactivity_hours > 24:
        attention_level = "orange"
        attention_label = "Orange - vigilance recommandee"
        reason = "Derniere activite ancienne."
    else:
        attention_level = "green"
        attention_label = "Vert - suivi standard"
        reason = "Suivi courant sans signal critique."

    status_key = (case_status or "").strip().lower()
    priority_key = (case_priority or "").strip().lower()
    if not owner_assigned:
        recommended_action = "Attribuer un référent aujourd'hui."
    elif inactivity_hours is not None and inactivity_hours > 72:
        recommended_action = "Relancer le dossier et confirmer la prochaine étape."
    elif priority_key in {"critical", "urgent", "high"} or (risk_score_value is not None and risk_score_value >= 80):
        recommended_action = "Traiter en priorité et vérifier les besoins immédiats."
    elif status_key in {"done", "closed", "resolved"}:
        recommended_action = "Conserver l'historique et vérifier la clôture."
    else:
        recommended_action = "Poursuivre le suivi opérationnel standard."

    summary_points = []
    opened_age = _human_elapsed(opened_at, now=now)
    if opened_age:
        summary_points.append(f"Dossier ouvert depuis {opened_age}.")
    summary_points.append(f"Statut actuel : {_status_label(case_status)}.")
    summary_points.append(f"Priorité : {_priority_label(case_priority)}.")
    summary_points.append(f"Responsable : {'attribué' if owner_assigned else 'non attribué'}.")
    last_activity_age = _human_elapsed(last_activity_at, now=now)
    if last_activity_age:
        summary_points.append(f"Dernière activité : il y a {last_activity_age}.")
    elif city_label:
        summary_points.append(f"Ville / territoire : {city_label}.")
    if risk_score_value is not None:
        risk_level = getattr(request, "risk_level", None)
        risk_fragment = f"Risque : score {risk_score_value}"
        if risk_level:
            risk_fragment += f" ({str(risk_level).replace('_', ' ')})"
        summary_points.append(risk_fragment + ".")
    elif city_label and len(summary_points) < 5:
        summary_points.append(f"Ville / territoire : {city_label}.")

    return {
        "attention_level": attention_level,
        "attention_label": attention_label,
        "summary_points": summary_points[:5],
        "recommended_action": recommended_action,
        "reason": reason,
    }


def _actor_case_scope_id(actor) -> int | None:
    if not actor or not actor.is_authenticated:
        return None
    if actor.is_platform_global:
        return None
    try:
        return int(actor.structure_id) if actor.structure_id is not None else None
    except (TypeError, ValueError):
        return None


def _build_actor_owner_scoped_case_query(actor, query):
    if actor and actor.is_platform_global:
        return query
    tenant_scope_id = _actor_case_scope_id(actor)
    if tenant_scope_id is None:
        return query.filter(false())
    if actor and actor.is_admin:
        return scope_case_query(actor, query)
    return query.filter(Case.structure_id == tenant_scope_id)


def build_actor_scoped_case_query(actor, query, *, include_collaborator_cases: bool = False):
    owner_scoped_query = _build_actor_owner_scoped_case_query(actor, query)
    if not include_collaborator_cases:
        return owner_scoped_query
    if not actor or not actor.is_authenticated or actor.is_platform_global:
        return owner_scoped_query

    tenant_scope_id = _actor_case_scope_id(actor)
    if tenant_scope_id is None:
        return owner_scoped_query

    collaborator_case_ids = CaseCollaborator.query.with_entities(CaseCollaborator.case_id).filter(
        CaseCollaborator.structure_id == tenant_scope_id
    )
    owner_case_ids = owner_scoped_query.with_entities(Case.id)
    return query.filter(
        or_(
            Case.id.in_(owner_case_ids),
            Case.id.in_(collaborator_case_ids),
        )
    )


def get_actor_visible_case(actor, case_id: int) -> Case | None:
    try:
        normalized_case_id = int(case_id)
    except (TypeError, ValueError):
        return None
    return (
        build_actor_scoped_case_query(
            actor,
            Case.query,
            include_collaborator_cases=True,
        )
        .filter(Case.id == normalized_case_id)
        .first()
    )


def _actor_has_owner_case_visibility(actor, case_row: Case) -> bool:
    if actor and actor.is_platform_global:
        return True

    tenant_scope_id = getattr(actor, "tenant_scope_id", None)
    case_structure_id = getattr(case_row, "structure_id", None)
    if tenant_scope_id is None or case_structure_id is None:
        return False

    try:
        return int(tenant_scope_id) == int(case_structure_id)
    except (TypeError, ValueError):
        return False


def _get_scoped_case_or_404(case_id: int) -> tuple[Case, Request]:
    actor = resolve_current_admin_actor()
    case_row = get_actor_visible_case(actor, case_id)
    if not case_row:
        abort(404)
    if _actor_has_owner_case_visibility(actor, case_row):
        if actor and actor.is_admin:
            req = _scope_requests(Request.query).filter(Request.id == case_row.request_id).first()
        else:
            req = (
                Request.query.filter(Request.id == case_row.request_id)
                .filter(Request.structure_id == _actor_case_scope_id(actor))
                .first()
            )
    else:
        req = db.session.get(Request, case_row.request_id)
    if not req:
        abort(404)
    if getattr(case_row, "structure_id", None) != getattr(req, "structure_id", None):
        abort(404)
    return case_row, req


def _case_status_transition_allowed(old_status: str, new_status: str) -> bool:
    old_key = (old_status or "new").strip().lower()
    new_key = (new_status or "").strip().lower()
    if old_key == new_key:
        return True
    if old_key not in CASE_STATUS_TRANSITIONS:
        return False
    return new_key in CASE_STATUS_TRANSITIONS[old_key]


def _owner_query_for_current_scope():
    actor = resolve_current_admin_actor()
    query = AdminUser.query.with_entities(AdminUser.id, AdminUser.username)
    if not actor.is_platform_global:
        query = query.filter(AdminUser.structure_id == actor.tenant_scope_id)
    return query.order_by(AdminUser.username.asc())


def _owner_allowed_for_current_scope(owner_id: int) -> bool:
    actor = resolve_current_admin_actor()
    query = AdminUser.query.filter(AdminUser.id == int(owner_id))
    if not actor.is_platform_global:
        query = query.filter(AdminUser.structure_id == actor.tenant_scope_id)
    return db.session.query(query.exists()).scalar()


def _upsert_case_participant(
    case_id: int,
    participant_type: str,
    role: str,
    user_id: int | None = None,
    admin_user_id: int | None = None,
    professional_lead_id: int | None = None,
    external_name: str | None = None,
    status: str = "active",
) -> CaseParticipant:
    q = CaseParticipant.query.filter(CaseParticipant.case_id == int(case_id))
    q = q.filter(CaseParticipant.participant_type == participant_type)

    if user_id is not None:
        q = q.filter(CaseParticipant.user_id == int(user_id))
    elif admin_user_id is not None:
        q = q.filter(CaseParticipant.admin_user_id == int(admin_user_id))
    elif professional_lead_id is not None:
        q = q.filter(CaseParticipant.professional_lead_id == int(professional_lead_id))
    else:
        q = q.filter(CaseParticipant.external_name == (external_name or "").strip())

    row = q.first()
    if row:
        row.role = role
        row.status = status
        if external_name:
            row.external_name = external_name.strip()
        return row

    row = CaseParticipant(
        case_id=int(case_id),
        participant_type=participant_type,
        user_id=user_id,
        admin_user_id=admin_user_id,
        professional_lead_id=professional_lead_id,
        external_name=(external_name or "").strip() or None,
        role=role,
        status=status,
        added_at=_now_utc(),
    )
    db.session.add(row)
    return row


@admin_bp.get("/cases")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_cases_list():
    admin_required_404()
    return _render_cases_list()


@admin_bp.get("/cases/<int:case_id>")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_case_detail(case_id: int):
    admin_required_404()
    if not _cases_enabled():
        flash("Case system tables are not available yet. Run migrations first.", "warning")
        return redirect(url_for("admin.admin_requests"), code=303)

    case_row, req = _get_scoped_case_or_404(case_id)
    risk_ai_suggestion = _build_risk_ai_suggestion(req)
    operational_blockages = _build_operational_blockages(req, case_row)
    suggested_professionals = suggest_professional_leads_for_case(case_row, req, limit=8)
    events = (
        CaseEvent.query.filter(CaseEvent.case_id == case_row.id)
        .order_by(CaseEvent.created_at.desc(), CaseEvent.id.desc())
        .limit(200)
        .all()
    )
    coordination_events = (
        CaseEvent.query.filter(CaseEvent.case_id == case_row.id)
        .filter(
            or_(
                CaseEvent.event_type.in_(("coordination_note", "comment", "internal_note")),
                CaseEvent.visibility == "internal",
            )
        )
        .filter(CaseEvent.message.isnot(None))
        .order_by(CaseEvent.created_at.desc(), CaseEvent.id.desc())
        .limit(20)
        .all()
    )
    case_copilot = build_case_copilot(case_row, req, events=events, assignments=None)
    grouped_events = _group_events_by_day(events)
   
    participants = (
        CaseParticipant.query.filter(CaseParticipant.case_id == case_row.id)
        .order_by(CaseParticipant.added_at.desc(), CaseParticipant.id.desc())
        .all()
    )
    collaborators = (
        CaseCollaborator.query.filter(CaseCollaborator.case_id == case_row.id)
        .join(Structure, CaseCollaborator.structure_id == Structure.id)
        .with_entities(Structure.name, CaseCollaborator.role)
        .order_by(Structure.name.asc())
        .all()
    )
    owners = _owner_query_for_current_scope().all()
    legacy_users_query = User.query.with_entities(User.id, User.username, User.email)
    if not _is_global_admin():
        legacy_users_query = legacy_users_query.filter(
            User.structure_id == getattr(case_row, "structure_id", None)
        )
    legacy_users = (
        legacy_users_query.order_by(User.username.asc())
        .limit(300)
        .all()
    )
    professionals = (
        ProfessionalLead.query.filter(
            or_(
                ProfessionalLead.status.is_(None),
                ~func.lower(ProfessionalLead.status).in_(("invalid", "spam")),
            )
        )
        .order_by(
            ProfessionalLead.created_at.desc(),
            ProfessionalLead.id.desc(),
        )
        .limit(300)
        .all()
    )

    return render_template(
        "admin/case_detail.html",
        case_row=case_row,
        req=req,
        events=events,
        coordination_events=coordination_events,
        grouped_events=grouped_events,
        statuses=list(CATEGORY_CASE_STATUSES),
        priorities=list(CASE_PRIORITIES),
        participant_types=list(CASE_PARTICIPANT_TYPES),
        participant_roles=list(CASE_PARTICIPANT_ROLES),
        owners=owners,
        users=legacy_users,
        professionals=professionals,
        participants=participants,
        collaborators=collaborators,
        case_copilot=case_copilot,
        risk_ai_suggestion=risk_ai_suggestion,
        operational_blockages=operational_blockages,
        suggested_professionals=suggested_professionals,
    )


@admin_bp.post("/cases/<int:case_id>/coordination-note")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_case_add_coordination_note(case_id: int):
    admin_required_404()
    case_row, _req = _get_scoped_case_or_404(case_id)

    message = (request.form.get("message") or "").strip()
    if not message:
        flash("La note de coordination ne peut pas être vide.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    if len(message) > 1000:
        flash("La note de coordination ne doit pas dépasser 1000 caractères.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    created_at = _now_utc()
    case_row.last_activity_at = created_at
    db.session.add(
        CaseEvent(
            case_id=case_row.id,
            actor_user_id=getattr(current_user, "id", None),
            event_type="coordination_note",
            message=message,
            metadata_json="{}",
            visibility="internal",
            created_at=created_at,
        )
    )
    db.session.commit()
    flash("Note de coordination ajoutée.", "success")
    return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)


@admin_bp.post("/cases/<int:case_id>/status")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_case_set_status(case_id: int):
    admin_required_404()
    case_row, _req = _get_scoped_case_or_404(case_id)

    new_status = (request.form.get("status") or "").strip().lower()
    if new_status not in CATEGORY_CASE_STATUSES:
        flash("Invalid case status.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    old_status = (case_row.status or "").strip().lower()
    if not _case_status_transition_allowed(old_status, new_status):
        flash("Invalid case status transition.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    if old_status != new_status:
        now = _now_utc()
        case_row.status = new_status
        case_row.last_activity_at = now

        if new_status == "assigned" and not case_row.assigned_at:
            case_row.assigned_at = now

        if new_status == "resolved":
            case_row.resolved_at = now
            _append_case_event(
                case_id=case_row.id,
                actor_user_id=getattr(current_user, "id", None),
                event_type="case_resolved",
                message="Dossier marqué comme résolu",
            )

        if new_status == "closed":
            case_row.closed_at = now
            if not case_row.resolved_at:
                case_row.resolved_at = now
            _append_case_event(
                case_id=case_row.id,
                actor_user_id=getattr(current_user, "id", None),
                event_type="case_closed",
                message="Dossier marqué comme clôturé",
            )

        if new_status == "cancelled" and not case_row.closed_at:
            case_row.closed_at = now

        # ✅ STATUS LABELS
        STATUS_LABELS = {
            "new": "Nouveau",
            "triaged": "Trié",
            "assigned": "Assigné",
            "resolved": "Résolu",
            "closed": "Clôturé",
            "cancelled": "Annulé",
        }

        old_label = STATUS_LABELS.get(old_status, old_status or "—")
        new_label = STATUS_LABELS.get(new_status, new_status)

        _append_case_event(
            case_id=case_row.id,
            actor_user_id=getattr(current_user, "id", None),
            event_type="status_changed",
            message=f"Statut changé de {old_label} vers {new_label}",
            metadata={"old_status": old_status, "new_status": new_status},
        )

        update_case_risk(case_row)
        evaluate_case_alerts(case_row)
        db.session.commit()
        flash("Case status updated.", "success")

    return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)


@admin_bp.post("/cases/<int:case_id>/assign-owner")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_case_assign_owner(case_id: int):
    admin_required_404()
    case_row, _req = _get_scoped_case_or_404(case_id)
    owner_raw = (request.form.get("owner_user_id") or "").strip()
    owner_id = None
    if owner_raw:
        try:
            owner_id = int(owner_raw)
        except Exception:
            owner_id = None
    if owner_id is not None and not _owner_allowed_for_current_scope(owner_id):
        flash("Selected owner does not exist.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    old_owner_id = case_row.owner_user_id
    if old_owner_id != owner_id:
        now = _now_utc()
        case_row.owner_user_id = owner_id
        case_row.last_activity_at = now

        if _req:
            _req.owner_id = owner_id
            _req.owned_at = now if owner_id else None

        if owner_id and not case_row.assigned_at:
            case_row.assigned_at = now
            if case_row.status in {"new", "triaged"}:
                case_row.status = "assigned"

        # NOTE: Do NOT upsert CaseParticipant for admin owner.
        # owner_user_id references AdminUser, while CaseParticipant.user_id references User.
        # Mixing them breaks identity semantics.
        _append_case_event(
            case_id=case_row.id,
            actor_user_id=getattr(current_user, "id", None),
            event_type="owner_assigned",
            message="Responsable attribué ou mis à jour",
            metadata={"old_owner_user_id": old_owner_id, "new_owner_user_id": owner_id},
        )
        update_case_risk(case_row)
        evaluate_case_alerts(case_row)
        db.session.commit()
        flash("Case owner updated.", "success")
    return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)


@admin_bp.post("/cases/<int:case_id>/assign-professional")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_case_assign_professional(case_id: int):
    admin_required_404()
    case_row, _req = _get_scoped_case_or_404(case_id)
    lead_raw = (
        request.form.get("assigned_professional_lead_id")
        or request.form.get("primary_professional_lead_id")
        or ""
    ).strip()
    lead_id = None
    if lead_raw:
        try:
            lead_id = int(lead_raw)
        except Exception:
            lead_id = None
    if lead_id is not None and not (db.session.get(ProfessionalLead, lead_id) or db.session.get(Intervenant, lead_id)):
        flash("Selected professional lead does not exist.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    old_lead_id = case_row.assigned_professional_lead_id
    if old_lead_id != lead_id:
        now = _now_utc()
        case_row.assigned_professional_lead_id = lead_id
        case_row.last_activity_at = now
        if lead_id and not case_row.assigned_at:
            case_row.assigned_at = now
            if case_row.status in {"new", "triaged"}:
                case_row.status = "assigned"
        if lead_id:
            _upsert_case_participant(
                case_id=case_row.id,
                participant_type="professional_lead",
                role="primary_professional",
                professional_lead_id=lead_id,
                status="active",
            )
        _append_case_event(
            case_id=case_row.id,
            actor_user_id=getattr(current_user, "id", None),
            event_type="professional_assigned",
            message="Professionnel principal attribué ou mis à jour",
            metadata={
                "old_professional_lead_id": old_lead_id,
                "new_professional_lead_id": lead_id,
            },
        )
        update_case_risk(case_row)
        evaluate_case_alerts(case_row)
        db.session.commit()
        flash("Case professional assignment updated.", "success")
    return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)


@admin_bp.post("/cases/<int:case_id>/participants")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_case_add_participant(case_id: int):
    admin_required_404()
    case_row, _req = _get_scoped_case_or_404(case_id)

    participant_type = (request.form.get("participant_type") or "").strip().lower()
    role = (request.form.get("role") or "contributor").strip().lower()
    participant_status = (request.form.get("status") or "active").strip().lower()
    user_raw = (request.form.get("user_id") or "").strip()
    lead_raw = (request.form.get("professional_lead_id") or "").strip()
    external_name = (request.form.get("external_name") or "").strip()

    if participant_type not in CASE_PARTICIPANT_TYPES:
        flash("Invalid participant type.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)
    if role not in CASE_PARTICIPANT_ROLES:
        role = "contributor"
    if participant_status not in {"active", "inactive"}:
        participant_status = "active"

    user_id = None
    lead_id = None
    if user_raw:
        try:
            user_id = int(user_raw)
        except Exception:
            user_id = None
    if lead_raw:
        try:
            lead_id = int(lead_raw)
        except Exception:
            lead_id = None

    selected_user = db.session.get(User, user_id) if user_id is not None else None
    if user_id is not None and not selected_user:
        flash("Selected legacy user record does not exist.", "warning")
        return redirect(
            url_for("admin.admin_case_detail", case_id=case_row.id),
            code=303,
        )
    if user_id is not None and getattr(selected_user, "structure_id", None) != getattr(
        case_row, "structure_id", None
    ):
        flash("Selected user is outside this case structure.", "warning")
        return redirect(
            url_for("admin.admin_case_detail", case_id=case_row.id),
            code=303,
        )
    if lead_id is not None and not (db.session.get(ProfessionalLead, lead_id) or db.session.get(Intervenant, lead_id)):
        flash("Selected professional lead does not exist.", "warning")
        return redirect(
            url_for("admin.admin_case_detail", case_id=case_row.id),
            code=303,
        )

    if participant_type == "professional_lead" and not lead_id:
        flash("professional_lead participant requires professional_lead_id.", "warning")
        return redirect(
            url_for("admin.admin_case_detail", case_id=case_row.id),
            code=303,
        )

    # Historical participant labels `admin_user` / `professional_user`
    # are still backed by the generic `User` model (`users.id`), not by
    # the canonical `AdminUser` auth family.
    if participant_type in LEGACY_USER_PARTICIPANT_TYPES and not user_id:
        flash("Selected legacy user-backed participant type requires user_id.", "warning")
        return redirect(
            url_for("admin.admin_case_detail", case_id=case_row.id),
            code=303,
        )
    if participant_type in {"association", "external_contact"} and not external_name:
        flash("External participant requires external name.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    _upsert_case_participant(
        case_id=case_row.id,
        participant_type=participant_type,
        role=role,
        user_id=user_id,
        professional_lead_id=lead_id,
        external_name=external_name or None,
        status=participant_status,
    )
    case_row.last_activity_at = _now_utc()
    _append_case_event(
        case_id=case_row.id,
        actor_user_id=getattr(current_user, "id", None),
        event_type="participant_added",
        message="Participant ajouté ou mis à jour",
        metadata={
            "participant_type": participant_type,
            "role": role,
            "status": participant_status,
            "user_id": user_id,
            "professional_lead_id": lead_id,
            "external_name": external_name or None,
        },
    )
    update_case_risk(case_row)
    evaluate_case_alerts(case_row)
    db.session.commit()
    flash("Case participant updated.", "success")
    return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)


@admin_bp.post("/cases/<int:case_id>/events")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_case_add_event(case_id: int):
    admin_required_404()
    case_row, _req = _get_scoped_case_or_404(case_id)
    event_type = (request.form.get("event_type") or "note_added").strip().lower()
    message = (request.form.get("message") or "").strip()
    metadata = _safe_json_dict(request.form.get("metadata_json"))
    visibility = (request.form.get("visibility") or "internal").strip().lower()
    if visibility not in {"internal", "public"}:
        visibility = "internal"

    if not event_type:
        event_type = "note_added"
    if not message and not metadata:
        flash("Event is empty.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    case_row.last_activity_at = _now_utc()
    _append_case_event(
        case_id=case_row.id,
        actor_user_id=getattr(current_user, "id", None),
        event_type=event_type,
        message=message,
        metadata=metadata or None,
        visibility=visibility,
    )
    update_case_risk(case_row)
    evaluate_case_alerts(case_row)
    db.session.commit()
    flash("Case event added.", "success")
    return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)
    
@admin_bp.post("/cases/<int:case_id>/priority")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_case_set_priority(case_id: int):
    admin_required_404()
    case_row, _req = _get_scoped_case_or_404(case_id)
    new_priority = (request.form.get("priority") or "").strip().lower()

    if new_priority not in CASE_PRIORITIES:
        flash("Invalid case priority.", "warning")
        return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

    old_priority = (case_row.priority or "").strip().lower()

    if old_priority != new_priority:
        PRIORITY_LABELS = {
            "low": "Basse",
            "standard": "Standard",
            "medium": "Moyenne",
            "high": "Haute",
            "urgent": "Urgente",
            "critical": "Critique",
        }

        old_label = PRIORITY_LABELS.get(old_priority, old_priority or "—")
        new_label = PRIORITY_LABELS.get(new_priority, new_priority)

        case_row.priority = new_priority
        case_row.last_activity_at = _now_utc()

        _append_case_event(
            case_id=case_row.id,
            actor_user_id=getattr(current_user, "id", None),
            event_type="priority_changed",
            message=f"Priorité changée de {old_label} vers {new_label}",
            metadata={"old_priority": old_priority, "new_priority": new_priority},
        )

        update_case_risk(case_row)
        evaluate_case_alerts(case_row)
        db.session.commit()
        flash("Case priority updated.", "success")

    return redirect(url_for("admin.admin_case_detail", case_id=case_row.id), code=303)

