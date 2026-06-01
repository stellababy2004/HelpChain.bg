from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import re
import unicodedata

from sqlalchemy import func

from backend.extensions import db
from backend.helpchain_backend.src.models import Intervenant

_STALE_THRESHOLD = timedelta(hours=72)
_OVERLOADED_WORKLOAD_THRESHOLD = 5
_UNAVAILABLE_AVAILABILITIES = {"unavailable", "indisponible", "capped", "full", "sature"}
_OVERLOADED_AVAILABILITIES = {"busy", "capped", "full", "sature"}


def _normalize_city(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _safe_iso(dt: datetime | None) -> str | None:
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None).isoformat(timespec="seconds")
    return None


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        try:
            return value.astimezone(UTC).replace(tzinfo=None)
        except Exception:
            return value.replace(tzinfo=None)
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).replace(tzinfo=None)
    except Exception:
        return None


def _is_available_professional(item: dict[str, object]) -> bool:
    key = str(item.get("availability") or "").strip().lower()
    return key not in _UNAVAILABLE_AVAILABILITIES


def _is_overloaded_professional(item: dict[str, object]) -> bool:
    workload = _safe_int(item.get("workload"))
    availability = str(item.get("availability") or "").strip().lower()
    return workload >= _OVERLOADED_WORKLOAD_THRESHOLD or availability in _OVERLOADED_AVAILABILITIES


def _risk_rank(level: str | None) -> int:
    key = str(level or "").strip().lower()
    if key in {"critical", "high"}:
        return 3
    if key in {"elevated", "watch", "medium"}:
        return 2
    return 1


def _territorial_risk_level(snapshot: dict[str, object]) -> str:
    if _safe_int(snapshot.get("critical_requests")) > 0:
        return "high"
    if _safe_int(snapshot.get("stale_requests")) > 0 or _safe_int(snapshot.get("unassigned_requests")) > 0:
        return "medium"
    return "low"


def _territorial_status_label(snapshot: dict[str, object]) -> str:
    if _safe_int(snapshot.get("critical_requests")) > 0 and _safe_int(snapshot.get("available_intervenants")) == 0:
        return "Couverture insuffisante"
    if _safe_int(snapshot.get("critical_requests")) > 0:
        return "Pression critique"
    if _safe_int(snapshot.get("stale_requests")) > 0:
        return "Relance requise"
    if _safe_int(snapshot.get("unassigned_requests")) > 0:
        return "Affectation requise"
    if _safe_int(snapshot.get("active_requests")) > 0:
        return "Activite visible"
    return "Couverture visible"


def _territorial_recommended_action(snapshot: dict[str, object]) -> str:
    city = str(snapshot.get("city") or "la zone")
    if _safe_int(snapshot.get("critical_requests")) > 0 and _safe_int(snapshot.get("available_intervenants")) == 0:
        return f"Renforcer la couverture intervenants sur {city}."
    if _safe_int(snapshot.get("critical_requests")) > 0:
        return f"Prioriser les situations critiques sur {city}."
    if _safe_int(snapshot.get("unassigned_requests")) > 0:
        return f"Affecter un responsable operationnel sur {city}."
    if _safe_int(snapshot.get("stale_requests")) > 0:
        return f"Relancer les situations inactives sur {city}."
    if _safe_int(snapshot.get("overloaded_intervenants")) > 0:
        return f"Redistribuer la charge intervenants sur {city}."
    return f"Maintenir la couverture operationnelle sur {city}."


def _canonical_snapshot(city: str, lat: float | None = None, lng: float | None = None) -> dict[str, object]:
    return {
        "city": city,
        "lat": lat,
        "lng": lng,
        "active_requests": 0,
        "critical_requests": 0,
        "unassigned_requests": 0,
        "stale_requests": 0,
        "available_intervenants": 0,
        "overloaded_intervenants": 0,
        "partner_coverage": 0,
        "last_activity": None,
        "risk_level": "low",
        "status_label": "Couverture visible",
        "recommended_action": "",
    }


def load_professional_map_rows(
    *,
    structure_id: int | None = None,
    include_inactive: bool = False,
) -> list[dict[str, object]]:
    """
    Canonical coverage rows for Pilotage maps.

    This helper intentionally mirrors the current professionals map semantics:
    exact coordinates are preferred, then city fallback coordinates are used.
    """

    from ..routes.admin import (
        _assignment_workload_subquery,
        _intervenant_availability,
        _intervenant_city,
        _intervenant_profession,
        _resolve_intervenant_coordinates,
    )

    workload_sq = _assignment_workload_subquery()
    query = (
        db.session.query(
            Intervenant,
            func.coalesce(workload_sq.c.workload, 0).label("workload"),
        )
        .outerjoin(workload_sq, workload_sq.c.intervenant_id == Intervenant.id)
    )
    if not include_inactive:
        query = query.filter(Intervenant.is_active.is_(True))
    if structure_id is not None:
        query = query.filter(Intervenant.structure_id == int(structure_id))

    rows = []
    for intervenant, workload in query.order_by(Intervenant.name.asc(), Intervenant.id.asc()).all():
        lat, lng, has_exact_coordinates = _resolve_intervenant_coordinates(intervenant)
        rows.append(
            {
                "id": int(intervenant.id),
                "city": _intervenant_city(intervenant) or "Paris",
                "latitude": lat,
                "longitude": lng,
                "availability": _intervenant_availability(intervenant),
                "profession": _intervenant_profession(intervenant),
                "actor_type": str(getattr(intervenant, "actor_type", None) or ""),
                "workload": _safe_int(workload),
                "has_exact_coordinates": bool(has_exact_coordinates),
                "is_active": bool(getattr(intervenant, "is_active", False)),
            }
        )
    return rows


def build_territorial_snapshots(
    *,
    risk_items: list[dict[str, object]] | None = None,
    professional_items: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """
    Canonical territorial model for Pilotage maps.

    Operational semantics:
    - a territory is a city-centered operational reading layer
    - requests/cases contribute workload, urgency, stale state, assignment gaps
    - intervenants contribute visible coverage and overload pressure

    Risk semantics:
    - `high` if at least one critical request is visible
    - `medium` if there is stale or unassigned workload
    - `low` otherwise

    Coverage semantics:
    - `available_intervenants` counts only visible, non-saturated actors
    - `overloaded_intervenants` is based on explicit availability saturation or high workload
    - `partner_coverage` is derived only from observed partner/association actors
    """

    buckets: dict[str, dict[str, object]] = {}
    coordinate_accumulator: dict[str, dict[str, float]] = defaultdict(
        lambda: {"lat": 0.0, "lng": 0.0, "count": 0.0}
    )

    for item in risk_items or []:
        city = str(item.get("city") or "").strip()
        if not city:
            continue
        key = _normalize_city(city)
        bucket = buckets.setdefault(key, _canonical_snapshot(city=city))
        lat = _safe_float(item.get("latitude"))
        lng = _safe_float(item.get("longitude"))
        if lat is not None and lng is not None:
            coordinate_accumulator[key]["lat"] += lat
            coordinate_accumulator[key]["lng"] += lng
            coordinate_accumulator[key]["count"] += 1.0
        bucket["active_requests"] = _safe_int(bucket["active_requests"]) + 1
        if _risk_rank(str(item.get("risk_level") or "")) >= 3:
            bucket["critical_requests"] = _safe_int(bucket["critical_requests"]) + 1
        if not bool(item.get("has_assignment")):
            bucket["unassigned_requests"] = _safe_int(bucket["unassigned_requests"]) + 1
        if bool(item.get("is_stale")):
            bucket["stale_requests"] = _safe_int(bucket["stale_requests"]) + 1
        item_activity = _parse_dt(item.get("last_activity") or item.get("updated_at"))
        current_activity = _parse_dt(bucket.get("last_activity"))
        if item_activity and (current_activity is None or item_activity > current_activity):
            bucket["last_activity"] = _safe_iso(item_activity)

    for item in professional_items or []:
        city = str(item.get("city") or "").strip()
        if not city:
            continue
        key = _normalize_city(city)
        bucket = buckets.setdefault(key, _canonical_snapshot(city=city))
        lat = _safe_float(item.get("latitude"))
        lng = _safe_float(item.get("longitude"))
        if lat is not None and lng is not None:
            coordinate_accumulator[key]["lat"] += lat
            coordinate_accumulator[key]["lng"] += lng
            coordinate_accumulator[key]["count"] += 1.0
        if _is_available_professional(item):
            bucket["available_intervenants"] = _safe_int(bucket["available_intervenants"]) + 1
        if _is_overloaded_professional(item):
            bucket["overloaded_intervenants"] = _safe_int(bucket["overloaded_intervenants"]) + 1
        actor_type = str(item.get("actor_type") or item.get("profession") or "").strip().lower()
        if "partenaire" in actor_type or "association" in actor_type or actor_type == "partner":
            bucket["partner_coverage"] = _safe_int(bucket["partner_coverage"]) + 1

    rows: list[dict[str, object]] = []
    for key, bucket in buckets.items():
        coords = coordinate_accumulator.get(key) or {}
        if (coords.get("count") or 0) > 0:
            bucket["lat"] = coords["lat"] / coords["count"]
            bucket["lng"] = coords["lng"] / coords["count"]
        bucket["risk_level"] = _territorial_risk_level(bucket)
        bucket["status_label"] = _territorial_status_label(bucket)
        bucket["recommended_action"] = _territorial_recommended_action(bucket)
        rows.append(bucket)

    rows.sort(
        key=lambda row: (
            _risk_rank(str(row.get("risk_level") or "")),
            _safe_int(row.get("critical_requests")),
            _safe_int(row.get("active_requests")),
            str(row.get("city") or ""),
        ),
        reverse=True,
    )
    return rows
