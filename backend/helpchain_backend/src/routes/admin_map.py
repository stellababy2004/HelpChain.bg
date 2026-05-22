from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from backend.extensions import db
from ..admin_actor import resolve_current_admin_actor
from ..admin_policies import scope_case_query, scope_request_query
from backend.helpchain_backend.src.models import Case, Request
from backend.helpchain_backend.src.services.case_risk import score_request_risk
from backend.helpchain_backend.src.services.risk_engine import compute_case_risk
from .admin import (
    _scope_requests,
    admin_required,
    admin_required_404,
    admin_role_required,
)


admin_map_bp = Blueprint("admin_map_api", __name__, url_prefix="/admin")

_ACTIVE_CASE_STATUSES = {"new", "open", "triaged", "assigned", "in_progress", "pending"}
_ACTIVE_REQUEST_STATUSES = {"new", "open", "pending", "in_progress", "assigned", "contacted", "triaged"}


def _safe_iso(dt) -> str | None:
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None).isoformat(timespec="seconds")
    return None


def _risk_level_from_score(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _valid_coordinates(lat_value, lng_value) -> tuple[float, float] | None:
    try:
        lat = float(lat_value)
        lng = float(lng_value)
    except Exception:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return lat, lng


def _scope_case_query(query):
    actor = resolve_current_admin_actor()
    scoped_query = scope_case_query(actor, query)
    if actor and actor.is_platform_global:
        return scoped_query
    if actor and actor.is_authenticated and actor.structure_id is not None:
        try:
            return query.filter(Case.structure_id == int(actor.structure_id))
        except (TypeError, ValueError):
            return query.filter(Case.id.is_(None))
    current_structure = getattr(current_user, "structure_id", None)
    if current_structure is not None:
        try:
            return query.filter(Case.structure_id == int(current_structure))
        except (TypeError, ValueError):
            return query.filter(Case.id.is_(None))
    return scoped_query


def _city_filter_expr(city: str | None):
    value = (city or "").strip().lower()
    if not value:
        return None
    like = f"%{value}%"
    return or_(
        func.lower(func.coalesce(Request.city, "")).like(like),
        func.lower(func.coalesce(Request.location_text, "")).like(like),
        func.lower(func.coalesce(Request.address_line, "")).like(like),
    )


def _serialize_risk_map_item(case: Case) -> dict[str, object]:
    request_row = getattr(case, "request", None)
    coords = _valid_coordinates(
        getattr(request_row, "latitude", None),
        getattr(request_row, "longitude", None),
    ) or _valid_coordinates(
        getattr(case, "latitude", None),
        getattr(case, "longitude", None),
    )
    if coords is None:
        return {}

    risk_score = int(
        getattr(case, "risk_score", None)
        or getattr(request_row, "risk_score", None)
        or 0
    )
    title = (
        getattr(request_row, "title", None)
        or getattr(request_row, "normalized_address", None)
        or getattr(request_row, "city", None)
        or f"Case #{case.id}"
    )
    return {
        "id": int(case.id),
        "title": str(title),
        "latitude": coords[0],
        "longitude": coords[1],
        "risk_level": _risk_level_from_score(risk_score),
        "risk_score": risk_score,
        "category": str(getattr(request_row, "category", None) or ""),
        "status": str(getattr(case, "status", None) or ""),
        "updated_at": _safe_iso(
            getattr(case, "updated_at", None) or getattr(case, "created_at", None)
        ),
        "city": str(getattr(request_row, "city", None) or ""),
        "source_type": "case",
        "request_id": int(getattr(case, "request_id", 0) or 0),
    }


def _serialize_request_risk_map_item(req: Request) -> dict[str, object]:
    coords = _valid_coordinates(
        getattr(req, "latitude", None),
        getattr(req, "longitude", None),
    )
    if coords is None:
        return {}

    triage = score_request_risk(req)
    risk_score = int(
        getattr(req, "risk_score", None) or triage.get("score") or 0
    )
    title = (
        getattr(req, "title", None)
        or getattr(req, "normalized_address", None)
        or getattr(req, "city", None)
        or f"Request #{req.id}"
    )
    return {
        "id": int(req.id),
        "title": str(title),
        "latitude": coords[0],
        "longitude": coords[1],
        "risk_level": _risk_level_from_score(risk_score),
        "risk_score": risk_score,
        "category": str(getattr(req, "category", None) or ""),
        "status": str(getattr(req, "status", None) or ""),
        "updated_at": _safe_iso(
            getattr(req, "updated_at", None) or getattr(req, "created_at", None)
        ),
        "city": str(getattr(req, "city", None) or ""),
        "source_type": "request",
        "request_id": int(req.id),
    }


def _load_request_only_risk_items(limit: int = 1000, city: str | None = None) -> list[dict[str, object]]:
    query = (
        _scope_requests(
            Request.query.outerjoin(Case, Case.request_id == Request.id)
            .filter(Case.id.is_(None))
            .filter(Request.latitude.isnot(None), Request.longitude.isnot(None))
            .filter(Request.latitude.between(-90, 90))
            .filter(Request.longitude.between(-180, 180))
            .filter(func.lower(func.coalesce(Request.status, "")).in_(tuple(_ACTIVE_REQUEST_STATUSES)))
            .order_by(Request.updated_at.desc(), Request.id.desc())
        )
        .options(joinedload(Request.structure))
    )
    city_filter = _city_filter_expr(city)
    if city_filter is not None:
        query = query.filter(city_filter)
    return [
        item
        for item in (_serialize_request_risk_map_item(req) for req in query.limit(limit).all())
        if item
    ]


def _build_scoped_case_map_query(actor, *, include_request_join: bool = True):
    scoped_request_ids_query = _scope_requests(Request.query.with_entities(Request.id))
    scoped_request_ids = scoped_request_ids_query.subquery()
    query = (
        Case.query.join(Request, Case.request_id == Request.id)
        .join(scoped_request_ids, Request.id == scoped_request_ids.c.id)
        .filter(Case.structure_id == Request.structure_id)
    )
    query = _scope_case_query(query)
    if include_request_join:
        return query
    return query.enable_eagerloads(False)


def _load_cases_with_geo():
    bind = db.session.get_bind()
    if not bind:
        return []
    metadata = db.MetaData()
    try:
        cases_table = db.Table("cases", metadata, autoload_with=bind)
    except Exception:
        return []
    if "latitude" not in cases_table.c or "longitude" not in cases_table.c:
        return []

    stmt = (
        select(
            cases_table.c.id,
            cases_table.c.latitude,
            cases_table.c.longitude,
            cases_table.c.status,
            cases_table.c.created_at,
        )
        .where(cases_table.c.latitude.isnot(None))
        .where(cases_table.c.longitude.isnot(None))
    )
    return db.session.execute(stmt).all()


@admin_map_bp.get("/api/risk-map")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_risk_map_api():
    admin_required_404()
    try:
        actor = resolve_current_admin_actor()
        selected_city = (request.args.get("city") or "").strip()
        query = (
            _build_scoped_case_map_query(actor)
            .options(joinedload(Case.request))
            .filter(func.lower(func.coalesce(Case.status, "")).in_(tuple(_ACTIVE_CASE_STATUSES)))
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
            .order_by(Case.risk_score.desc(), Case.updated_at.desc(), Case.id.desc())
        )
        city_filter = _city_filter_expr(selected_city)
        if city_filter is not None:
            query = query.filter(city_filter)
        cases = query.limit(1000).all()
        case_items = [
            item for item in (_serialize_risk_map_item(case) for case in cases) if item
        ]
        request_items = _load_request_only_risk_items(limit=1000, city=selected_city)
        items = sorted(
            case_items + request_items,
            key=lambda item: (
                int(item.get("risk_score") or 0),
                str(item.get("updated_at") or ""),
                int(item.get("request_id") or 0),
            ),
            reverse=True,
        )
        return jsonify(
            {
                "status": "ok",
                "items": items,
                "default_center": {"lat": 46.603354, "lng": 1.888334, "zoom": 6},
                "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(
                    timespec="seconds"
                ),
            }
        )
    except Exception:
        current_app.logger.exception("admin_risk_map_api_failed")
        return (
            jsonify(
                {
                    "status": "error",
                    "items": [],
                    "message": "risk_map_data_unavailable",
                    "default_center": {"lat": 46.603354, "lng": 1.888334, "zoom": 6},
                }
            ),
            500,
        )


@admin_map_bp.get("/api/cases/map")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_cases_map_api():
    admin_required_404()
    actor = resolve_current_admin_actor()
    rows = (
        _build_scoped_case_map_query(actor, include_request_join=False)
        .with_entities(
            Case.id,
            Case.latitude,
            Case.longitude,
            Case.status,
            Case.created_at,
        )
        .filter(Case.latitude.isnot(None), Case.longitude.isnot(None))
        .filter(Case.latitude.between(-90, 90))
        .filter(Case.longitude.between(-180, 180))
        .all()
    )
    payload = []
    for row in rows:
        risk = compute_case_risk(int(row.id)) or {}
        risk_level = risk.get("risk_level", "low")
        if risk_level == "critical":
            risk_level = "high"
        payload.append(
            {
                "id": int(row.id),
                "lat": float(row.latitude),
                "lng": float(row.longitude),
                "status": str(row.status or "open"),
                "risk_level": risk_level,
                "created_at": _safe_iso(row.created_at),
            }
        )
    return jsonify({"cases": payload})


@admin_map_bp.get("/cases/map")
@admin_required
def admin_cases_map_page():
    admin_required_404()
    return render_template("admin_cases_map.html")
