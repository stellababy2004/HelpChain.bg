from __future__ import annotations

from flask import current_app, flash, redirect, request, url_for

from backend.audit import log_activity
from backend.extensions import db
from backend.models import NotificationJob, utc_now
from ..services.notification_jobs import deliver_notification_job

from .admin import (
    _render_notifications_list,
    _scoped_notification_jobs_query,
    _table_exists,
    admin_bp,
    admin_required,
    admin_required_404,
    admin_role_required,
    operator_required,
    ops_bp,
)


@admin_bp.get("/notifications")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_notifications_list():
    admin_required_404()
    return _render_notifications_list()


def _notifications_redirect_target() -> str:
    return (
        "ops.ops_notifications_list"
        if request.path.startswith("/ops")
        else "admin.admin_notifications_list"
    )


def _notification_retry_impl(job_id: int):
    if not _table_exists("notification_jobs"):
        flash("La table des notifications n'est pas encore disponible. Exécutez d'abord les migrations.", "warning")
        return redirect(
            url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
            code=303,
        )

    try:
        query = _scoped_notification_jobs_query()
    except Exception as exc:
        current_app.logger.warning("notification_retry_scope_failed: %s", exc)
        query = NotificationJob.query

    job = query.filter(NotificationJob.id == int(job_id)).first()
    if not job:
        flash("Notification introuvable.", "warning")
        return redirect(
            url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
            code=303,
        )

    if (job.status or "").lower() not in {"dead_letter", "failed"}:
        flash("Seules les notifications en échec peuvent être relancées manuellement.", "warning")
        return redirect(
            url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
            code=303,
        )

    try:
        job.status = "pending"
        job.attempts = 0
        job.next_retry_at = utc_now()
        job.sent_at = None
        job.last_error = None
        job.locked_at = None
        job.processed_at = None
        job.updated_at = utc_now()
        db.session.commit()
        log_activity(
            entity_type="notification_job",
            entity_id=int(job.id),
            action="notification.retry",
            metadata={
                "status": "pending",
                "channel": job.channel,
                "event_type": job.event_type,
            },
        )
        flash(f"La notification #{job.id} a été remise en file d'attente.", "success")
    except Exception:
        db.session.rollback()
        flash(f"Impossible de remettre la notification #{job.id} en file d'attente.", "danger")

    return redirect(
        url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
        code=303,
    )


def _notification_retry_impl_sync(job_id: int):
    if not _table_exists("notification_jobs"):
        flash(
            "La table des notifications n'est pas encore disponible. Exécutez d'abord les migrations.",
            "warning",
        )
        return redirect(
            url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
            code=303,
        )

    try:
        query = _scoped_notification_jobs_query()
    except Exception as exc:
        current_app.logger.warning("notification_retry_scope_failed: %s", exc)
        query = NotificationJob.query

    job = query.filter(NotificationJob.id == int(job_id)).first()
    if not job:
        flash("Notification introuvable.", "warning")
        return redirect(
            url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
            code=303,
        )

    if (job.status or "").lower() not in {"dead_letter", "failed"}:
        flash(
            "Seules les notifications en échec peuvent être relancées manuellement.",
            "warning",
        )
        return redirect(
            url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
            code=303,
        )

    try:
        job.status = "pending"
        job.attempts = 0
        job.next_retry_at = utc_now()
        job.sent_at = None
        job.last_error = None
        job.locked_at = None
        job.processed_at = None
        job.updated_at = utc_now()
        db.session.commit()
        delivered = deliver_notification_job(job)
        log_activity(
            entity_type="notification_job",
            entity_id=int(job.id),
            action="notification.retry",
            metadata={
                "status": job.status,
                "channel": job.channel,
                "event_type": job.event_type,
                "delivered": bool(delivered),
            },
        )
        if delivered:
            flash(f"La notification #{job.id} a été renvoyée.", "success")
        else:
            flash(
                f"La notification #{job.id} a été relancée mais l'envoi a échoué.",
                "warning",
            )
    except Exception:
        db.session.rollback()
        flash(f"Impossible de relancer la notification #{job.id}.", "danger")

    return redirect(
        url_for(_notifications_redirect_target(), **request.form.to_dict(flat=True)),
        code=303,
    )


@admin_bp.post("/notifications/<int:job_id>/retry")
@admin_required
@admin_role_required("ops", "superadmin")
def admin_notifications_retry(job_id: int):
    admin_required_404()
    return _notification_retry_impl_sync(job_id)


@ops_bp.post("/notifications/<int:job_id>/retry")
@operator_required
@admin_role_required("ops", "superadmin")
def ops_notifications_retry(job_id: int):
    return _notification_retry_impl_sync(job_id)
