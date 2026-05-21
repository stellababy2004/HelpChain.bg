from datetime import datetime

from flask import Blueprint, jsonify
from flask_login import current_user
from sqlalchemy import func

from backend.extensions import db
from backend.helpchain_backend.src.statuses import (
    request_status_read_values,
)
from backend.models import Request

ops_api = Blueprint("ops_api", __name__)


def _resolved_date_column():
    # Keep compatibility with evolving request schema naming.
    if hasattr(Request, "resolved_at"):
        return getattr(Request, "resolved_at")
    if hasattr(Request, "completed_at"):
        return getattr(Request, "completed_at")
    return None


@ops_api.route("/admin/api/ops-metrics")
def ops_metrics():
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"error": "authentication_required"}), 401
    if not getattr(current_user, "is_admin", False):
        return jsonify({"error": "forbidden"}), 403

    now = datetime.utcnow()

    active = (
        db.session.query(func.count(Request.id))
        .filter(
            func.lower(func.coalesce(Request.status, "")).in_(
                request_status_read_values("open", active_as="open")
            )
        )
        .scalar()
        or 0
    )

    high_risk = (
        db.session.query(func.count(Request.id))
        .filter(Request.risk_score >= 70)
        .scalar()
        or 0
    )

    unassigned = (
        db.session.query(func.count(Request.id))
        .filter(Request.owner_id.is_(None))
        .scalar()
        or 0
    )

    resolved_today = 0
    resolved_col = _resolved_date_column()
    if resolved_col is not None:
        resolved_today = (
            db.session.query(func.count(Request.id))
            .filter(
                func.lower(func.coalesce(Request.status, "")).in_(
                    request_status_read_values("done")
                )
            )
            .filter(func.date(resolved_col) == now.date())
            .scalar()
            or 0
        )

    return jsonify(
        {
            "active_requests": active,
            "high_risk_cases": high_risk,
            "unassigned_cases": unassigned,
            "resolved_today": resolved_today,
        }
    )

