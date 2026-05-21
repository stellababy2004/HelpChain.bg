from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from flask import current_app, has_app_context
from sqlalchemy import func, or_, select, union_all

from backend.extensions import db
from backend.models import (
    AdminUser,
    Request,
    RequestActivity,
    RequestLog,
    canonical_role,
    utc_now,
)
from ..models.case import Case
from ..models.case_event import CaseEvent
from ..statuses import (
    canonical_request_status,
    is_terminal_request_status,
    request_status_read_values,
    request_terminal_status_read_values,
)

from .notification_jobs import enqueue_email_notification

logger = logging.getLogger(__name__)


SLA_OWNER_REMINDER_SENT = "sla_owner_reminder_sent"
SLA_INACTIVITY_REMINDER_SENT = "sla_inactivity_reminder_sent"
SLA_INACTIVITY_ESCALATION_SENT = "sla_inactivity_escalation_sent"

SLA_MARKER_ACTIONS = {
    SLA_OWNER_REMINDER_SENT,
    SLA_INACTIVITY_REMINDER_SENT,
    SLA_INACTIVITY_ESCALATION_SENT,
}

# Keep the default rule set explicit and small for v1.
DEFAULT_UNOWNED_THRESHOLD_HOURS = 48
DEFAULT_INACTIVITY_THRESHOLD_HOURS = 24
DEFAULT_ESCALATION_THRESHOLD_HOURS = 72
SLA_MARKER_COOLDOWN_HOURS = 24
REQUEST_DASHBOARD_ACTIONABLE_STATUSES = (
    "new",
    *request_status_read_values("open", active_as="open"),
    *request_status_read_values("in_progress", active_as="in_progress"),
)

# The repo currently has no dedicated SLA email template. Reuse an existing
# operational notification template so the service can enqueue real jobs without
# introducing template/UI changes in this pass.
SLA_EMAIL_TEMPLATE = "emails/professional_lead_notify.html"

_CLOSED_REQUEST_STATUSES = set(request_terminal_status_read_values())


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _now_naive(now: datetime | None = None) -> datetime:
    return _to_utc_naive(now) or _to_utc_naive(utc_now()) or datetime.now(UTC).replace(
        tzinfo=None
    )


def _request_status_value(request_obj: Request) -> str:
    raw_status = getattr(request_obj, "status", None)
    return canonical_request_status(
        raw_status,
        active_as="open",
        fallback=((raw_status or "").strip().lower() or ""),
    )


def _is_open_request(request_obj: Request) -> bool:
    if getattr(request_obj, "deleted_at", None) is not None:
        return False
    if bool(getattr(request_obj, "is_archived", False)):
        return False
    return not is_terminal_request_status(getattr(request_obj, "status", None), active_as="open")


def _request_label(request_obj: Request) -> str:
    return (
        getattr(request_obj, "title", None)
        or getattr(request_obj, "name", None)
        or f"Request #{getattr(request_obj, 'id', 'unknown')}"
    )


def _request_admin_url(request_obj: Request) -> str | None:
    if not has_app_context():
        return None
    base_url = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base_url:
        return None
    request_id = getattr(request_obj, "id", None)
    if not request_id:
        return None
    return f"{base_url}/admin/requests/{int(request_id)}"


def _meaningful_activity_query(request_id: int):
    return (
        db.session.query(func.max(RequestActivity.created_at))
        .filter(RequestActivity.request_id == int(request_id))
        .filter(~RequestActivity.action.in_(tuple(SLA_MARKER_ACTIONS)))
    )


def build_request_meaningful_activity_subquery():
    activity_union = union_all(
        select(
            RequestActivity.request_id.label("request_id"),
            RequestActivity.created_at.label("activity_at"),
        ).where(~RequestActivity.action.in_(tuple(SLA_MARKER_ACTIONS))),
        select(
            RequestLog.request_id.label("request_id"),
            RequestLog.timestamp.label("activity_at"),
        ).where(RequestLog.timestamp.is_not(None)),
        select(
            Case.request_id.label("request_id"),
            Case.last_activity_at.label("activity_at"),
        ).where(Case.last_activity_at.is_not(None)),
        select(
            Case.request_id.label("request_id"),
            Case.updated_at.label("activity_at"),
        ).where(Case.updated_at.is_not(None)),
        select(
            Case.request_id.label("request_id"),
            Case.created_at.label("activity_at"),
        ).where(Case.created_at.is_not(None)),
        select(
            Case.request_id.label("request_id"),
            CaseEvent.created_at.label("activity_at"),
        )
        .select_from(CaseEvent)
        .join(Case, Case.id == CaseEvent.case_id)
        .where(CaseEvent.created_at.is_not(None)),
    ).subquery()
    return (
        db.session.query(
            activity_union.c.request_id.label("request_id"),
            func.max(activity_union.c.activity_at).label("last_activity_at"),
        )
        .group_by(activity_union.c.request_id)
        .subquery()
    )


def request_dashboard_actionable_filter():
    status_expr = func.lower(func.coalesce(Request.status, ""))
    return or_(
        Request.status.is_(None),
        status_expr.in_(REQUEST_DASHBOARD_ACTIONABLE_STATUSES),
    )


def build_request_inactivity_components(
    now: datetime | None = None,
    *,
    hours: int = DEFAULT_ESCALATION_THRESHOLD_HOURS,
):
    activity_sq = build_request_meaningful_activity_subquery()
    activity_expr = func.coalesce(
        activity_sq.c.last_activity_at,
        Request.updated_at,
        Request.created_at,
    )
    threshold = (now or utc_now()) - timedelta(hours=hours)
    return activity_sq, activity_expr, threshold, activity_expr <= threshold


def touch_request_case_activity(
    *,
    request_obj: Request | None = None,
    case_row=None,
    when: datetime | None = None,
) -> datetime:
    touched_at = when or utc_now()
    if case_row is not None:
        if hasattr(case_row, "last_activity_at"):
            case_row.last_activity_at = touched_at
        if hasattr(case_row, "updated_at"):
            case_row.updated_at = touched_at
        if request_obj is None:
            request_obj = getattr(case_row, "request", None)
    if request_obj is not None and hasattr(request_obj, "updated_at"):
        request_obj.updated_at = touched_at
    return touched_at


def _base_open_requests_query():
    status_expr = func.lower(func.coalesce(Request.status, ""))
    return (
        Request.query.filter(Request.deleted_at.is_(None))
        .filter(Request.is_archived.is_(False))
        .filter(or_(Request.status.is_(None), ~status_expr.in_(tuple(_CLOSED_REQUEST_STATUSES))))
        .order_by(Request.created_at.asc(), Request.id.asc())
    )


def _get_structure_admin_recipients(
    structure_id: int | None,
    *,
    include_global_superadmins: bool = False,
) -> list[str]:
    rows = (
        AdminUser.query.filter(AdminUser.is_active.is_(True))
        .filter(AdminUser.email.isnot(None))
        .all()
    )
    emails: list[str] = []
    seen: set[str] = set()
    for admin in rows:
        email = (getattr(admin, "email", None) or "").strip()
        if not email:
            continue
        role = canonical_role(getattr(admin, "role", None))
        admin_sid = getattr(admin, "structure_id", None)
        include = False
        if structure_id is not None and admin_sid == structure_id and role in {"admin", "ops"}:
            include = True
        if include_global_superadmins and role == "superadmin" and admin_sid is None:
            include = True
        if include and email not in seen:
            emails.append(email)
            seen.add(email)
    return emails


def _get_owner_recipient(request_obj: Request) -> str | None:
    owner = getattr(request_obj, "owner", None)
    if owner is None:
        owner_id = getattr(request_obj, "owner_id", None)
        owner = db.session.get(AdminUser, owner_id) if owner_id else None
    if not owner or not bool(getattr(owner, "is_active", False)):
        return None
    email = (getattr(owner, "email", None) or "").strip()
    return email or None


def _record_sla_marker(request_obj: Request, action: str, *, now: datetime, note: str | None) -> None:
    db.session.add(
        RequestActivity(
            request_id=int(getattr(request_obj, "id")),
            actor_admin_id=None,
            action=action,
            old_value=None,
            new_value=(note or "")[:500] or None,
            created_at=now,
        )
    )
    db.session.commit()


def _build_email_context(
    request_obj: Request,
    *,
    recipient: str,
    message: str,
    category_label: str,
) -> dict:
    created_at = _to_utc_naive(getattr(request_obj, "created_at", None))
    return {
        "lead_id": int(getattr(request_obj, "id", 0) or 0),
        "profession": category_label,
        "email": recipient,
        "full_name": _request_label(request_obj),
        "organization": f"structure:{getattr(request_obj, 'structure_id', None) or 'n/a'}",
        "message": message,
        "admin_url": _request_admin_url(request_obj),
        "created_at": created_at.isoformat() if created_at else "",
    }


def _enqueue_sla_notice(
    request_obj: Request,
    *,
    marker_action: str,
    now: datetime,
    subject: str,
    event_type: str,
    message: str,
    recipients: list[str],
    cooldown_hours: int = SLA_MARKER_COOLDOWN_HOURS,
) -> bool:
    request_id = getattr(request_obj, "id", None)
    if not request_id or not recipients:
        return False
    if has_recent_sla_marker(int(request_id), marker_action, cooldown_hours, now=now):
        return False

    queued = False
    sent_to: list[str] = []
    for recipient in recipients:
        email = (recipient or "").strip()
        if not email:
            continue
        try:
            enqueue_email_notification(
                recipient=email,
                subject=subject,
                template=SLA_EMAIL_TEMPLATE,
                context=_build_email_context(
                    request_obj,
                    recipient=email,
                    message=message,
                    category_label=event_type,
                ),
                purpose=event_type,
                structure_id=getattr(request_obj, "structure_id", None),
            )
            queued = True
            sent_to.append(email)
        except Exception:
            logger.exception(
                "[SLA] failed to enqueue %s for request=%s recipient=%s",
                marker_action,
                request_id,
                email,
            )
    if not queued:
        return False

    _record_sla_marker(
        request_obj,
        marker_action,
        now=now,
        note=";".join(sent_to[:10]),
    )
    return True


def get_request_last_meaningful_activity(request_obj: Request) -> datetime | None:
    request_id = getattr(request_obj, "id", None)
    if not request_id:
        return _to_utc_naive(getattr(request_obj, "updated_at", None)) or _to_utc_naive(
            getattr(request_obj, "created_at", None)
        )
    activity_sq = build_request_meaningful_activity_subquery()
    last_activity = (
        db.session.query(activity_sq.c.last_activity_at)
        .filter(activity_sq.c.request_id == int(request_id))
        .scalar()
    )
    return (
        _to_utc_naive(last_activity)
        or _to_utc_naive(getattr(request_obj, "updated_at", None))
        or _to_utc_naive(getattr(request_obj, "created_at", None))
    )


def has_recent_sla_marker(
    request_id: int,
    action: str,
    within_hours: int,
    *,
    now: datetime | None = None,
) -> bool:
    if not request_id or not action or within_hours <= 0:
        return False
    cutoff = _now_naive(now) - timedelta(hours=int(within_hours))
    return (
        RequestActivity.query.filter(RequestActivity.request_id == int(request_id))
        .filter(RequestActivity.action == action)
        .filter(RequestActivity.created_at >= cutoff)
        .first()
        is not None
    )


def find_unowned_requests_due(
    now: datetime | None = None,
    threshold_hours: int = DEFAULT_UNOWNED_THRESHOLD_HOURS,
) -> list[Request]:
    cutoff = _now_naive(now) - timedelta(hours=int(threshold_hours))
    rows = (
        _base_open_requests_query()
        .filter(Request.owner_id.is_(None))
        .filter(Request.created_at.isnot(None))
        .filter(Request.created_at <= cutoff)
        .all()
    )
    return [row for row in rows if _is_open_request(row)]


def find_inactive_owned_requests_due(
    now: datetime | None = None,
    threshold_hours: int = DEFAULT_INACTIVITY_THRESHOLD_HOURS,
) -> list[Request]:
    now_naive = _now_naive(now)
    cutoff = now_naive - timedelta(hours=int(threshold_hours))
    rows = _base_open_requests_query().filter(Request.owner_id.isnot(None)).all()
    out: list[Request] = []
    for row in rows:
        if not _is_open_request(row):
            continue
        last_activity = get_request_last_meaningful_activity(row)
        if last_activity and last_activity <= cutoff:
            out.append(row)
    return out


def find_inactive_escalation_candidates(
    now: datetime | None = None,
    threshold_hours: int = DEFAULT_ESCALATION_THRESHOLD_HOURS,
) -> list[Request]:
    now_naive = _now_naive(now)
    cutoff = now_naive - timedelta(hours=int(threshold_hours))
    rows = _base_open_requests_query().filter(Request.owner_id.isnot(None)).all()
    out: list[Request] = []
    for row in rows:
        if not _is_open_request(row):
            continue
        if not RequestActivity.query.filter(
            RequestActivity.request_id == int(getattr(row, "id", 0) or 0),
            RequestActivity.action == SLA_INACTIVITY_REMINDER_SENT,
        ).first():
            continue
        last_activity = get_request_last_meaningful_activity(row)
        if last_activity and last_activity <= cutoff:
            out.append(row)
    return out


def enqueue_owner_reminder(request_obj: Request, now: datetime | None = None) -> bool:
    now_naive = _now_naive(now)
    recipients = _get_structure_admin_recipients(
        getattr(request_obj, "structure_id", None),
        include_global_superadmins=True,
    )
    hours_open = 0.0
    created_at = _to_utc_naive(getattr(request_obj, "created_at", None))
    if created_at:
        hours_open = max(0.0, (now_naive - created_at).total_seconds() / 3600.0)
    subject = f"[HelpChain] Request #{getattr(request_obj, 'id', '?')} needs an owner"
    message = (
        f"Request {_request_label(request_obj)} has no owner after {hours_open:.1f} hours. "
        "Please review and assign an owner."
    )
    return _enqueue_sla_notice(
        request_obj,
        marker_action=SLA_OWNER_REMINDER_SENT,
        now=now_naive,
        subject=subject,
        event_type="request_sla_owner_reminder",
        message=message,
        recipients=recipients,
    )


def enqueue_inactivity_reminder(request_obj: Request, now: datetime | None = None) -> bool:
    now_naive = _now_naive(now)
    last_activity = get_request_last_meaningful_activity(request_obj)
    inactive_hours = 0.0
    if last_activity:
        inactive_hours = max(0.0, (now_naive - last_activity).total_seconds() / 3600.0)
    owner_email = _get_owner_recipient(request_obj)
    if owner_email:
        recipients = [owner_email]
    else:
        # Conservative fallback when owner email lookup is unavailable:
        # notify structure admins/ops instead of broadening scope further.
        recipients = _get_structure_admin_recipients(
            getattr(request_obj, "structure_id", None),
            include_global_superadmins=False,
        )
    subject = f"[HelpChain] Request #{getattr(request_obj, 'id', '?')} needs follow-up"
    message = (
        f"Request {_request_label(request_obj)} has no meaningful activity for "
        f"{inactive_hours:.1f} hours. Please review and take action."
    )
    return _enqueue_sla_notice(
        request_obj,
        marker_action=SLA_INACTIVITY_REMINDER_SENT,
        now=now_naive,
        subject=subject,
        event_type="request_sla_inactivity_reminder",
        message=message,
        recipients=recipients,
    )


def enqueue_inactivity_escalation(request_obj: Request, now: datetime | None = None) -> bool:
    now_naive = _now_naive(now)
    last_activity = get_request_last_meaningful_activity(request_obj)
    inactive_hours = 0.0
    if last_activity:
        inactive_hours = max(0.0, (now_naive - last_activity).total_seconds() / 3600.0)
    recipients = _get_structure_admin_recipients(
        getattr(request_obj, "structure_id", None),
        include_global_superadmins=True,
    )
    subject = f"[HelpChain] Escalation for request #{getattr(request_obj, 'id', '?')}"
    message = (
        f"Request {_request_label(request_obj)} remains inactive after "
        f"{inactive_hours:.1f} hours. Escalation is required."
    )
    return _enqueue_sla_notice(
        request_obj,
        marker_action=SLA_INACTIVITY_ESCALATION_SENT,
        now=now_naive,
        subject=subject,
        event_type="request_sla_inactivity_escalation",
        message=message,
        recipients=recipients,
    )


def process_request_sla(now: datetime | None = None) -> dict:
    now_naive = _now_naive(now)
    stats = {
        "unowned_due": 0,
        "inactive_due": 0,
        "escalation_due": 0,
        "owner_reminders_enqueued": 0,
        "inactivity_reminders_enqueued": 0,
        "inactivity_escalations_enqueued": 0,
        "suppressed": 0,
        "errors": 0,
    }

    unowned_due = find_unowned_requests_due(now=now_naive)
    inactive_due = find_inactive_owned_requests_due(now=now_naive)
    escalation_due = find_inactive_escalation_candidates(now=now_naive)
    escalation_ids = {
        int(getattr(row, "id", 0) or 0)
        for row in escalation_due
        if getattr(row, "id", None) is not None
    }

    stats["unowned_due"] = len(unowned_due)
    stats["inactive_due"] = len(inactive_due)
    stats["escalation_due"] = len(escalation_due)

    for row in unowned_due:
        try:
            if enqueue_owner_reminder(row, now=now_naive):
                stats["owner_reminders_enqueued"] += 1
            else:
                stats["suppressed"] += 1
        except Exception:
            stats["errors"] += 1
            logger.exception("[SLA] owner reminder failed request=%s", getattr(row, "id", None))

    for row in inactive_due:
        if int(getattr(row, "id", 0) or 0) in escalation_ids:
            continue
        try:
            if enqueue_inactivity_reminder(row, now=now_naive):
                stats["inactivity_reminders_enqueued"] += 1
            else:
                stats["suppressed"] += 1
        except Exception:
            stats["errors"] += 1
            logger.exception(
                "[SLA] inactivity reminder failed request=%s",
                getattr(row, "id", None),
            )

    for row in escalation_due:
        try:
            if enqueue_inactivity_escalation(row, now=now_naive):
                stats["inactivity_escalations_enqueued"] += 1
            else:
                stats["suppressed"] += 1
        except Exception:
            stats["errors"] += 1
            logger.exception(
                "[SLA] inactivity escalation failed request=%s",
                getattr(row, "id", None),
            )

    return stats
