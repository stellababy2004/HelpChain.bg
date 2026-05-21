from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_

from backend.extensions import db
from backend.helpchain_backend.src.statuses import (
    canonical_request_status,
    request_status_read_values,
    request_terminal_status_read_values,
)
from backend.models import Intervenant, Request, Structure
from backend.helpchain_backend.src.services.sla_alerts import build_sla_alerts


CLOSED_STATUSES = set(request_terminal_status_read_values()) | {"archived"}

RESOLVED_STATUSES = set(request_status_read_values("done"))

PRIORITY_SEVERITY_RANK = {
    "stable": 1,
    "moderate": 2,
    "high": 3,
    "critical": 4,
}

INTERVENANT_AVAILABLE_STATES = {"available"}


@dataclass(frozen=True)
class OperationalReportPeriod:
    start: datetime
    end: datetime


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _period_for_days(days: int, now: datetime | None = None) -> OperationalReportPeriod:
    safe_days = max(1, min(int(days or 7), 366))
    end = _normalize_dt(now) or _utc_now_naive()
    start = end - timedelta(days=safe_days)
    return OperationalReportPeriod(start=start, end=end)


def _status_expr():
    return func.lower(func.coalesce(Request.status, ""))


def _open_filter():
    return or_(Request.status.is_(None), ~_status_expr().in_(tuple(CLOSED_STATUSES)))


def _open_filter_as_of(as_of: datetime):
    return and_(
        Request.created_at.isnot(None),
        Request.created_at <= as_of,
        or_(Request.completed_at.is_(None), Request.completed_at > as_of),
        or_(
            Request.status.is_(None),
            ~_status_expr().in_(tuple(CLOSED_STATUSES)),
            Request.completed_at > as_of,
        ),
    )


def _resolved_filter():
    return and_(
        Request.completed_at.isnot(None),
        or_(Request.status.is_(None), _status_expr().in_(tuple(RESOLVED_STATUSES))),
    )


def _critical_filter():
    return or_(
        func.lower(func.coalesce(Request.priority, "")).in_(("urgent", "critical", "high")),
        func.lower(func.coalesce(Request.risk_level, "")).in_(("critical", "high")),
        func.coalesce(Request.risk_score, 0) >= 85,
    )


def _base_query(structure_id: int | None = None):
    query = Request.query.filter(Request.deleted_at.is_(None))

    if hasattr(Request, "is_archived"):
        query = query.filter(Request.is_archived.is_(False))

    if structure_id is not None and hasattr(Request, "structure_id"):
        query = query.filter(Request.structure_id == int(structure_id))

    return query


def _count(query) -> int:
    return int(query.count() or 0)


def _safe_ratio(numerator: int | float, denominator: int | float, digits: int = 1) -> float:
    denom = float(denominator or 0)
    if denom <= 0:
        return 0.0
    return round(float(numerator or 0) / denom, digits)


def _duration_hours(start: datetime | None, end: datetime | None) -> float | None:
    start = _normalize_dt(start)
    end = _normalize_dt(end)

    if start is None or end is None or end < start:
        return None

    seconds = (end - start).total_seconds()

    if seconds < 0 or seconds > 366 * 24 * 3600:
        return None

    return seconds / 3600.0


def _avg_duration_hours(rows, start_attr: str, end_attr: str) -> float:
    values = []
    for row in rows:
        value = _duration_hours(
            getattr(row, start_attr, None),
            getattr(row, end_attr, None),
        )
        if value is not None:
            values.append(value)

    if not values:
        return 0.0

    return round(sum(values) / len(values), 2)


def _build_sparkline_points(values, width=220, height=52):
    if not values:
        return ""

    max_value = max(values) or 1
    step_x = width / max(len(values) - 1, 1)

    points = []
    for idx, value in enumerate(values):
        x = round(idx * step_x, 2)
        y = round(height - ((value / max_value) * height), 2)
        points.append(f"{x},{y}")

    return " ".join(points)


def _rows_by_label(rows, label_name: str, count_name: str = "count") -> list[dict]:
    return [
        {
            label_name: label or "unknown",
            count_name: int(count or 0),
        }
        for label, count in rows
    ]


def _canonical_status_rows(rows) -> list[dict]:
    counts: OrderedDict[str, int] = OrderedDict()
    unknown_counts: OrderedDict[str, int] = OrderedDict()

    for raw_status, count in rows:
        canonical = canonical_request_status(
            raw_status,
            active_as="open",
            fallback=str(raw_status or "").strip().lower() or "unknown",
        )
        target = counts if canonical in {"open", "in_progress", "done", "cancelled"} else unknown_counts
        target[canonical] = target.get(canonical, 0) + int(count or 0)

    result = [
        {"status": status, "count": counts[status]}
        for status in ("open", "in_progress", "done", "cancelled")
        if counts.get(status)
    ]
    result.extend(
        {"status": status, "count": count}
        for status, count in unknown_counts.items()
    )
    return result


def _trend_semantic(direction: str, positive_when: str = "up") -> str:
    if direction == "stable":
        return "neutral"
    if direction == positive_when:
        return "positive"
    return "negative"


def _build_trend_metric(current_value: int | float, previous_value: int | float) -> dict:
    current = float(current_value or 0)
    previous = float(previous_value or 0)

    if previous == 0:
        if current == 0:
            return {
                "current": current_value,
                "previous": previous_value,
                "delta_percent": 0.0,
                "direction": "stable",
                "label": "Stable",
            }
        return {
            "current": current_value,
            "previous": previous_value,
            "delta_percent": 100.0,
            "direction": "up",
            "label": "+100%",
        }

    delta = ((current - previous) / previous) * 100
    rounded = round(delta, 1)

    if rounded > 0:
        direction = "up"
        label = f"+{rounded}%"
    elif rounded < 0:
        direction = "down"
        label = f"{rounded}%"
    else:
        direction = "stable"
        label = "Stable"

    return {
        "current": current_value,
        "previous": previous_value,
        "delta_percent": rounded,
        "direction": direction,
        "label": label,
    }


def _format_delta_value(current_value: int | float, previous_value: int | float, *, is_hours: bool = False) -> str:
    delta = float(current_value or 0) - float(previous_value or 0)
    if abs(delta) < 0.05:
        return "Stable"

    sign = "+" if delta > 0 else "-"
    value = abs(delta)
    if is_hours:
        return f"{sign}{value:.1f}h"
    if float(value).is_integer():
        return f"{sign}{int(value)}"
    return f"{sign}{value:.1f}"


def _trend_copy(current_value: int | float, previous_value: int | float, *, is_hours: bool = False) -> str:
    direction = "stable"
    if float(current_value or 0) > float(previous_value or 0):
        direction = "up"
    elif float(current_value or 0) < float(previous_value or 0):
        direction = "down"

    arrow = {"up": "↑", "down": "↓", "stable": "→"}[direction]
    return f"{arrow} {_format_delta_value(current_value, previous_value, is_hours=is_hours)} vs periode precedente"


def _priority_weight(value: str) -> int:
    return PRIORITY_SEVERITY_RANK.get(value, 1)


def _severity_sort_key(item: dict) -> tuple[int, int]:
    return (_priority_weight(str(item.get("severity") or "stable")), int(item.get("sort_metric") or 0))


def _kpi_tone(level: str) -> str:
    mapping = {
        "critical": "critical",
        "high": "elevated",
        "moderate": "watch",
        "stable": "calm",
    }
    return mapping.get(level, "calm")


def _severity_label(level: str) -> str:
    mapping = {
        "critical": "Critique",
        "high": "Eleve",
        "moderate": "Modere",
        "stable": "Stable",
    }
    return mapping.get(level, "Stable")


def _stability_label(level: str) -> str:
    mapping = {
        "critical": "Instable",
        "high": "Sous tension",
        "moderate": "Sous surveillance",
        "stable": "Stable",
    }
    return mapping.get(level, "Stable")


def _normalize_city_key(value: str | None) -> str:
    return " ".join((value or "").strip().lower().replace("-", " ").split())


def _intervenant_city_value(intervenant: Intervenant) -> str:
    raw = (getattr(intervenant, "location", None) or "").strip()
    if not raw:
        return ""
    if "||" in raw:
        city, _address = raw.split("||", 1)
        return city.strip()
    return raw


def _count_sla_breaches(rows, as_of: datetime) -> int:
    count = 0
    for row in rows:
        created_at = _normalize_dt(getattr(row, "created_at", None))
        updated_at = _normalize_dt(getattr(row, "updated_at", None)) or created_at
        completed_at = _normalize_dt(getattr(row, "completed_at", None))
        raw_status = getattr(row, "status", None)
        status = canonical_request_status(
            raw_status,
            active_as="open",
            fallback=str(raw_status or "").lower(),
        )
        priority = str(getattr(row, "priority", "") or "").lower()
        owner_id = getattr(row, "owner_id", None)

        if created_at is None or created_at > as_of:
            continue
        if completed_at is not None and completed_at <= as_of:
            continue
        if status in CLOSED_STATUSES and completed_at is None:
            continue

        breached = False
        if not owner_id and created_at <= as_of - timedelta(hours=48):
            breached = True
        if updated_at and updated_at <= as_of - timedelta(hours=72):
            breached = True
        if priority in {"urgent", "critical", "high"} and not owner_id:
            breached = True

        if breached:
            count += 1

    return count


def _build_snapshot_metric(
    *,
    key: str,
    label: str,
    current_value: int | float,
    previous_value: int | float,
    severity: str,
    summary: str,
    unit: str = "",
    is_hours: bool = False,
    precision: int = 0,
) -> dict:
    if is_hours:
        display_value = f"{float(current_value or 0):.{precision}f}h"
    elif unit:
        display_value = f"{float(current_value or 0):.{precision}f}{unit}"
    elif precision > 0:
        display_value = f"{float(current_value or 0):.{precision}f}"
    else:
        display_value = str(int(round(float(current_value or 0))))

    trend = _build_trend_metric(current_value, previous_value)
    return {
        "key": key,
        "label": label,
        "value": display_value,
        "raw_value": current_value,
        "previous": previous_value,
        "trend": trend,
        "delta_text": _trend_copy(current_value, previous_value, is_hours=is_hours),
        "severity": severity,
        "severity_label": _severity_label(severity),
        "tone": _kpi_tone(severity),
        "summary": summary,
    }


def _pressure_label(open_count: int, active_count: int, available_count: int, density: float, critical_count: int, stale_count: int) -> tuple[str, str]:
    if open_count <= 0:
        return "stable", "Zone stable"
    if active_count <= 0:
        return "critical", "Couverture insuffisante"
    if density >= 5 or critical_count >= 2:
        return "critical", "Saturation"
    if density >= 3 or available_count <= 0 or stale_count >= 2:
        return "high", "Couverture faible"
    if density >= 1.5:
        return "moderate", "Vigilance"
    return "stable", "Stable"


def _build_territorial_pressure(open_rows, structure_id: int | None = None) -> dict:
    requests_by_city: dict[str, dict] = defaultdict(lambda: {
        "city": "",
        "open_requests": 0,
        "critical_requests": 0,
        "stale_requests": 0,
        "unassigned_requests": 0,
    })

    for row in open_rows:
        city = (getattr(row, "city", None) or "").strip() or "Non localisee"
        key = _normalize_city_key(city)
        bucket = requests_by_city[key]
        bucket["city"] = city
        bucket["open_requests"] += 1

        priority = str(getattr(row, "priority", "") or "").lower()
        risk_level = str(getattr(row, "risk_level", "") or "").lower()
        risk_score = int(getattr(row, "risk_score", 0) or 0)
        if priority in {"urgent", "critical", "high"} or risk_level in {"critical", "high"} or risk_score >= 85:
            bucket["critical_requests"] += 1

        created_at = _normalize_dt(getattr(row, "created_at", None))
        if created_at is not None and created_at <= _utc_now_naive() - timedelta(hours=72):
            bucket["stale_requests"] += 1

        if getattr(row, "owner_id", None) is None or getattr(row, "owned_at", None) is None:
            bucket["unassigned_requests"] += 1

    professionals_query = Intervenant.query
    if structure_id is not None and hasattr(Intervenant, "structure_id"):
        professionals_query = professionals_query.filter(Intervenant.structure_id == int(structure_id))

    professionals = professionals_query.all()

    by_city_professionals: dict[str, dict] = defaultdict(lambda: {
        "active_intervenants": 0,
        "available_intervenants": 0,
        "availability": 0.0,
    })

    for intervenant in professionals:
        city = _intervenant_city_value(intervenant) or "Non localisee"
        key = _normalize_city_key(city)
        availability = str(getattr(intervenant, "availability", "") or "").strip().lower()
        is_active = bool(getattr(intervenant, "is_active", False))

        if is_active:
            by_city_professionals[key]["active_intervenants"] += 1
        if is_active and availability in INTERVENANT_AVAILABLE_STATES:
            by_city_professionals[key]["available_intervenants"] += 1

    zones = []
    all_keys = set(requests_by_city.keys()) | set(by_city_professionals.keys())
    for key in all_keys:
        request_bucket = requests_by_city.get(key, {})
        professional_bucket = by_city_professionals.get(key, {})
        city = request_bucket.get("city") or next(
            (value for value in [_intervenant_city_value(p) for p in professionals if _normalize_city_key(_intervenant_city_value(p) or "Non localisee") == key] if value),
            "Non localisee",
        )
        open_count = int(request_bucket.get("open_requests", 0) or 0)
        active_count = int(professional_bucket.get("active_intervenants", 0) or 0)
        available_count = int(professional_bucket.get("available_intervenants", 0) or 0)
        critical_count = int(request_bucket.get("critical_requests", 0) or 0)
        stale_count = int(request_bucket.get("stale_requests", 0) or 0)
        unassigned_count = int(request_bucket.get("unassigned_requests", 0) or 0)
        density = _safe_ratio(open_count, active_count, 1) if active_count > 0 else float(open_count)
        availability_rate = round((available_count / active_count) * 100, 1) if active_count > 0 else 0.0
        severity, coverage_label = _pressure_label(
            open_count,
            active_count,
            available_count,
            density,
            critical_count,
            stale_count,
        )
        zones.append({
            "city": city,
            "open_requests": open_count,
            "critical_requests": critical_count,
            "stale_requests": stale_count,
            "unassigned_requests": unassigned_count,
            "active_intervenants": active_count,
            "available_intervenants": available_count,
            "density": density,
            "availability_rate": availability_rate,
            "severity": severity,
            "severity_label": _severity_label(severity),
            "coverage_label": coverage_label,
            "heat": min(5, max(1, int(round(density if density > 0 else open_count or 1)))),
            "alert": (
                f"{city} requiert un renfort de couverture."
                if severity in {"critical", "high"}
                else f"{city} reste dans un regime de suivi maitrise."
            ),
            "sort_metric": (open_count * 10) + (critical_count * 5) + unassigned_count + stale_count,
        })

    zones.sort(key=lambda row: _severity_sort_key(row), reverse=True)
    top_zones = [row for row in zones if row["open_requests"] > 0][:5]

    if top_zones:
        headline = top_zones[0]
        summary = (
            f"{headline['city']} concentre actuellement la pression la plus visible "
            f"avec {headline['open_requests']} situation(s) ouvertes et "
            f"{headline['active_intervenants']} intervenant(s) actif(s)."
        )
    else:
        summary = "Aucune zone sous tension n'est identifiee sur le perimetre visible."

    return {
        "summary": summary,
        "zones": top_zones,
        "stable_zones": len([row for row in top_zones if row["severity"] == "stable"]),
        "watch_zones": len([row for row in top_zones if row["severity"] == "moderate"]),
        "high_pressure_zones": len([row for row in top_zones if row["severity"] in {"high", "critical"}]),
    }


def _build_operational_recommendations(metrics, territorial_pressure: dict | None = None):
    recommendations = []
    territorial_pressure = territorial_pressure or {}
    zones = territorial_pressure.get("zones", []) or []

    open_requests = int(metrics.get("open_requests", 0) or 0)
    unassigned = int(metrics.get("unassigned_requests", 0) or 0)
    stale = int(metrics.get("stale_requests", 0) or 0)
    avg_resolution = float(metrics.get("avg_resolution_hours", 0.0) or 0.0)
    assignment_rate = float(metrics.get("assignment_rate", 0.0) or 0.0)
    sla_breaches = int(metrics.get("sla_breaches", 0) or 0)
    critical_requests = int(metrics.get("critical_requests", 0) or 0)

    if unassigned > 0:
        severity = "critical" if unassigned >= 8 else "high"
        recommendations.append({
            "priority": "Critique" if severity == "critical" else "Eleve",
            "severity": severity,
            "title": "Reconstituer la chaine d'assignation",
            "description": (
                f"Une degradation du pilotage operationnel est observee sur les flux d'assignation. "
                f"{unassigned} situation(s) restent sans referent identifie."
            ),
            "impact": "Risque de rupture de suivi, d'allongement des delais et de perte de tracabilite.",
            "risk": "Pilotage",
            "horizon": "Immediat",
            "sort_metric": unassigned,
        })

    if stale > 0 or sla_breaches > 0:
        severity = "critical" if sla_breaches >= 6 else "high" if stale >= 4 else "moderate"
        recommendations.append({
            "priority": _severity_label(severity),
            "severity": severity,
            "title": "Relancer les dossiers sans activite recente",
            "description": (
                f"Les signaux de suivi indiquent {sla_breaches} situation(s) en depassement SLA "
                f"et {stale} situation(s) ouvertes sans activite recente."
            ),
            "impact": "Risque de glissement des engagements de suivi et d'exposition sur les situations sensibles.",
            "risk": "Delais",
            "horizon": "72h",
            "sort_metric": max(sla_breaches, stale),
        })

    if avg_resolution >= 72 or critical_requests > 0:
        severity = "high" if avg_resolution >= 96 or critical_requests >= 4 else "moderate"
        recommendations.append({
            "priority": _severity_label(severity),
            "severity": severity,
            "title": "Stabiliser les delais sur les situations sensibles",
            "description": (
                f"Le temps moyen de resolution atteint {avg_resolution:.1f}h "
                f"avec {critical_requests} situation(s) critique(s) ouvertes."
            ),
            "impact": "Risque de saturation progressive des files et de sur-exposition sur les cas prioritaires.",
            "risk": "Capacite",
            "horizon": "Semaine",
            "sort_metric": int(avg_resolution),
        })

    top_pressure = next((zone for zone in zones if zone["severity"] in {"critical", "high"}), None)
    if top_pressure is not None:
        recommendations.append({
            "priority": _severity_label(top_pressure["severity"]),
            "severity": top_pressure["severity"],
            "title": f"Renforcer la couverture {top_pressure['city']}",
            "description": (
                f"{top_pressure['city']} presente une pression territoriale de niveau {top_pressure['coverage_label'].lower()} "
                f"avec {top_pressure['open_requests']} situation(s) ouvertes pour "
                f"{top_pressure['active_intervenants']} intervenant(s) actif(s)."
            ),
            "impact": "Risque de concentration territoriale et de couverture insuffisante sur la zone.",
            "risk": "Territorial",
            "horizon": "72h" if top_pressure["severity"] == "high" else "Immediat",
            "sort_metric": int(top_pressure["sort_metric"] or 0),
        })

    if assignment_rate < 70 and open_requests > 0:
        recommendations.append({
            "priority": "Modere",
            "severity": "moderate",
            "title": "Reviser les regles de priorisation et d'orientation",
            "description": (
                f"Le taux d'assignation ressort a {assignment_rate:.1f}%. "
                "Une revue du triage, des disponibilites et des capacites est recommandee."
            ),
            "impact": "Risque d'inefficience du pilotage si la file augmente plus vite que l'affectation.",
            "risk": "Processus",
            "horizon": "Semaine",
            "sort_metric": int(round((70 - assignment_rate) * 10)),
        })

    if not recommendations:
        recommendations.append({
            "priority": "Stable",
            "severity": "stable",
            "title": "Maintenir le rythme de supervision",
            "description": (
                "Les indicateurs restent maitrises. Il convient de maintenir le niveau de tracabilite, "
                "de revue de file et de coordination territoriale."
            ),
            "impact": "Faible risque a court terme sous reserve de conservation des routines de pilotage.",
            "risk": "Supervision",
            "horizon": "Semaine",
            "sort_metric": 0,
        })

    recommendations.sort(key=lambda item: _severity_sort_key(item), reverse=True)
    return recommendations[:4]


def _compute_operational_severity(metrics):
    stale = int(metrics.get("stale_requests", 0) or 0)
    unassigned = int(metrics.get("unassigned_requests", 0) or 0)
    avg_resolution = float(metrics.get("avg_resolution_hours", 0.0) or 0.0)
    sla_breaches = int(metrics.get("sla_breaches", 0) or 0)

    if stale >= 15 or avg_resolution >= 168 or sla_breaches >= 10:
        return {
            "level": "critical",
            "label": "Critique",
            "message": "Des situations necessitent une intervention immediate de pilotage.",
        }

    if stale >= 5 or unassigned >= 5 or avg_resolution >= 72 or sla_breaches >= 4:
        return {
            "level": "warning",
            "label": "Attention requise",
            "message": "Une tension operationnelle est detectee sur les flux de suivi.",
        }

    return {
        "level": "stable",
        "label": "Stable",
        "message": "Les indicateurs operationnels restent maitrises sur la periode.",
    }


def _build_executive_summary(metrics):
    open_requests = int(metrics.get("open_requests", 0) or 0)
    unassigned = int(metrics.get("unassigned_requests", 0) or 0)
    stale = int(metrics.get("stale_requests", 0) or 0)
    avg_resolution = float(metrics.get("avg_resolution_hours", 0.0) or 0.0)
    assignment_rate = float(metrics.get("assignment_rate", 0.0) or 0.0)

    activity_label = (
        "activite soutenue"
        if open_requests >= 20
        else "activite moderee"
        if open_requests >= 10
        else "activite limitee"
    )

    resolution_label = (
        "des delais de resolution eleves"
        if avg_resolution >= 96
        else "des delais de resolution maitrises"
    )

    assignment_label = (
        "Le taux d'assignation reste solide."
        if assignment_rate >= 70
        else "Le taux d'assignation necessite une attention operationnelle."
    )

    return (
        f"Le pilotage montre une {activity_label} avec "
        f"{open_requests} situations ouvertes, dont {unassigned} non assignees. "
        f"La periode presente {resolution_label} (moyenne: {avg_resolution:.1f}h). "
        f"{stale} situations necessitent une relance. {assignment_label}"
    )


def _build_priority_actions(metrics: dict, territorial_pressure: dict, executive_snapshot: list[dict]) -> list[dict]:
    actions = []
    zones = territorial_pressure.get("zones", []) or []
    snapshot_map = {item["key"]: item for item in executive_snapshot}

    unassigned = int(metrics.get("unassigned_requests", 0) or 0)
    stale = int(metrics.get("stale_requests", 0) or 0)
    critical_requests = int(metrics.get("critical_requests", 0) or 0)
    sla_breaches = int(metrics.get("sla_breaches", 0) or 0)

    if unassigned > 0:
        actions.append({
            "title": f"Reassigner {unassigned} situation(s) sans referent",
            "severity": "critical" if unassigned >= 8 else "high",
            "severity_label": "Critique" if unassigned >= 8 else "Eleve",
            "reason": snapshot_map.get("unassigned_requests", {}).get("delta_text", "Pression immediate sur l'assignation."),
            "impact": "Reduire le risque de rupture de suivi et fluidifier les prises en charge.",
            "horizon": "Immediat",
            "sort_metric": unassigned,
        })

    if stale > 0 or sla_breaches > 0:
        actions.append({
            "title": "Relancer les dossiers sans activite > 72h",
            "severity": "critical" if sla_breaches >= 6 else "high" if stale >= 4 else "moderate",
            "severity_label": "Critique" if sla_breaches >= 6 else "Eleve" if stale >= 4 else "Modere",
            "reason": f"{sla_breaches} depassement(s) SLA et {stale} situation(s) a relancer.",
            "impact": "Limiter l'allongement des delais et la perte de maitrise des files.",
            "horizon": "72h",
            "sort_metric": max(sla_breaches, stale),
        })

    if critical_requests > 0:
        actions.append({
            "title": "Verifier la prise en charge des situations critiques",
            "severity": "high" if critical_requests >= 4 else "moderate",
            "severity_label": "Eleve" if critical_requests >= 4 else "Modere",
            "reason": f"{critical_requests} situation(s) critiques restent ouvertes sur le perimetre.",
            "impact": "Eviter un cumul de risque humain et operationnel sur les cas sensibles.",
            "horizon": "Immediat",
            "sort_metric": critical_requests,
        })

    top_zone = next((zone for zone in zones if zone["severity"] in {"critical", "high"}), None)
    if top_zone is not None:
        actions.append({
            "title": f"Renforcer la couverture {top_zone['city']}",
            "severity": top_zone["severity"],
            "severity_label": _severity_label(top_zone["severity"]),
            "reason": (
                f"{top_zone['coverage_label']} avec {top_zone['open_requests']} situation(s) "
                f"pour {top_zone['active_intervenants']} intervenant(s) actif(s)."
            ),
            "impact": "Ameliorer la capacite locale et reduire la concentration territoriale.",
            "horizon": "72h" if top_zone["severity"] == "high" else "Immediat",
            "sort_metric": int(top_zone["sort_metric"] or 0),
        })

    if not actions:
        actions.append({
            "title": "Maintenir la cadence de revue operationnelle",
            "severity": "stable",
            "severity_label": "Stable",
            "reason": "Aucune action urgente supplementaire n'est identifiee.",
            "impact": "Conserver un niveau de supervision adapte.",
            "horizon": "Semaine",
            "sort_metric": 0,
        })

    actions.sort(key=lambda item: _severity_sort_key(item), reverse=True)
    return actions[:4]


def _build_automatic_analysis(metrics: dict, trends: dict, territorial_pressure: dict) -> list[dict]:
    analysis = []

    open_trend = trends.get("open_requests", {})
    unassigned_trend = trends.get("unassigned_requests", {})
    resolution_trend = trends.get("avg_resolution_hours", {})
    critical_trend = trends.get("critical_requests", {})
    top_zone = next((zone for zone in (territorial_pressure.get("zones", []) or []) if zone["severity"] in {"critical", "high"}), None)

    if float(open_trend.get("delta_percent", 0) or 0) > 15:
        analysis.append({
            "title": "Hausse du stock ouvert",
            "severity": "high",
            "risk": "Flux",
            "horizon": "Semaine",
            "text": (
                f"Le volume de situations ouvertes progresse de {open_trend.get('label', '0%')} "
                "par rapport a la periode precedente, signe d'un flux plus rapide que la capacite de sortie."
            ),
        })

    if float(unassigned_trend.get("delta_percent", 0) or 0) > 0:
        analysis.append({
            "title": "Degradation de l'assignation",
            "severity": "critical" if float(unassigned_trend.get("delta_percent", 0) or 0) >= 50 else "high",
            "risk": "Pilotage",
            "horizon": "Immediat",
            "text": (
                f"Les situations non assignees evoluent de {unassigned_trend.get('label', '0%')}. "
                "Une degradation du pilotage operationnel est observee sur les flux d'assignation."
            ),
        })

    if float(resolution_trend.get("delta_percent", 0) or 0) > 0:
        analysis.append({
            "title": "Ralentissement de resolution",
            "severity": "high" if float(metrics.get("avg_resolution_hours", 0) or 0) >= 72 else "moderate",
            "risk": "Delais",
            "horizon": "72h",
            "text": (
                f"Le temps moyen de resolution se degrade de {resolution_trend.get('label', '0%')} "
                "et allonge la duree d'exposition des situations dans la file."
            ),
        })

    if float(critical_trend.get("delta_percent", 0) or 0) > 0:
        analysis.append({
            "title": "Progression des situations critiques",
            "severity": "critical" if float(metrics.get("critical_requests", 0) or 0) >= 4 else "high",
            "risk": "Priorites",
            "horizon": "Immediat",
            "text": (
                f"Le nombre de situations critiques ouvertes evolue de {critical_trend.get('label', '0%')}, "
                "ce qui augmente la pression de coordination et de priorisation."
            ),
        })

    if top_zone is not None:
        analysis.append({
            "title": f"Concentration territoriale sur {top_zone['city']}",
            "severity": top_zone["severity"],
            "risk": "Territorial",
            "horizon": "72h",
            "text": (
                f"{top_zone['city']} concentre la pression principale avec {top_zone['open_requests']} situation(s) "
                f"et une couverture classee {top_zone['coverage_label'].lower()}."
            ),
        })

    if not analysis:
        analysis.append({
            "title": "Cadence operationnelle maitrisee",
            "severity": "stable",
            "risk": "Supervision",
            "horizon": "Semaine",
            "text": "Les signaux exploites par l'assistant de pilotage restent coherents avec une activite stable et un niveau de risque contenu.",
        })

    analysis.sort(key=lambda item: _severity_sort_key(item), reverse=True)
    return analysis[:4]


def _build_operational_conclusion(metrics: dict, recommendations: list[dict], territorial_pressure: dict, operational_severity: dict) -> dict:
    primary_recommendation = recommendations[0]["title"] if recommendations else "Maintenir la supervision courante"
    top_zone = (territorial_pressure.get("zones") or [None])[0]
    risk_labels = []

    if int(metrics.get("unassigned_requests", 0) or 0) > 0:
        risk_labels.append("assignation")
    if int(metrics.get("sla_breaches", 0) or 0) > 0:
        risk_labels.append("delais de suivi")
    if top_zone is not None and top_zone["severity"] in {"critical", "high"}:
        risk_labels.append(f"couverture territoriale ({top_zone['city']})")
    if not risk_labels:
        risk_labels.append("aucun risque majeur identifie")

    stability = operational_severity.get("level", "stable")
    if stability == "warning":
        stability_key = "moderate"
    elif stability == "critical":
        stability_key = "critical"
    else:
        stability_key = "stable"

    open_requests = int(metrics.get("open_requests", 0) or 0)
    avg_resolution = float(metrics.get("avg_resolution_hours", 0.0) or 0.0)
    unassigned = int(metrics.get("unassigned_requests", 0) or 0)

    summary = (
        f"La structure presente actuellement {open_requests} situation(s) ouvertes "
        f"avec un niveau de stabilite {_stability_label(stability_key).lower()}. "
        f"Le delai moyen de resolution s'etablit a {avg_resolution:.1f}h et "
        f"{unassigned} situation(s) demeurent sans assignation."
    )

    return {
        "summary": summary,
        "stability": _stability_label(stability_key),
        "severity": stability_key,
        "main_risks": risk_labels,
        "primary_recommendation": primary_recommendation,
    }


def build_operational_report(
    *,
    structure_id: int | None = None,
    days: int = 7,
    now: datetime | None = None,
) -> dict:
    """
    Build a tenant-safe operational report payload.

    This service deliberately returns data only. Rendering, routes, PDF and CSV
    exports belong to separate layers.
    """
    period = _period_for_days(days, now=now)
    base = _base_query(structure_id=structure_id)

    period_base = base.filter(Request.created_at >= period.start, Request.created_at <= period.end)
    open_base = base.filter(_open_filter())
    resolved_base = base.filter(_resolved_filter())
    current_open_rows = open_base.all()

    stale_threshold = period.end - timedelta(hours=72)

    new_count = _count(period_base)
    resolved_count = _count(
        resolved_base.filter(Request.completed_at >= period.start, Request.completed_at <= period.end)
    )
    open_count = _count(open_base)
    stale_count = _count(
        open_base.filter(Request.created_at.isnot(None)).filter(Request.created_at < stale_threshold)
    )

    unassigned_count = _count(
        open_base.filter(
            or_(
                Request.owner_id.is_(None),
                Request.owned_at.is_(None),
            )
        )
    )

    critical_open_count = _count(open_base.filter(_critical_filter()))

    by_category_rows = (
        period_base.with_entities(Request.category, func.count(Request.id))
        .group_by(Request.category)
        .order_by(func.count(Request.id).desc())
        .limit(10)
        .all()
    )

    status_expr = func.coalesce(Request.status, "unknown").label("status_label")
    raw_status_rows = (
        base.with_entities(status_expr, func.count(Request.id))
        .group_by(status_expr)
        .order_by(func.count(Request.id).desc())
        .all()
    )
    by_status_rows = _canonical_status_rows(raw_status_rows)

    assignment_rows = (
        base.filter(Request.owned_at.isnot(None))
        .filter(Request.created_at.isnot(None))
        .all()
    )
    resolved_rows = (
        resolved_base.filter(Request.completed_at.isnot(None))
        .filter(Request.created_at.isnot(None))
        .all()
    )

    avg_assignment_hours = _avg_duration_hours(assignment_rows, "created_at", "owned_at")
    avg_resolution_hours = _avg_duration_hours(resolved_rows, "created_at", "completed_at")

    assignment_rate = 0.0
    if open_count > 0:
        assignment_rate = round(((open_count - unassigned_count) / open_count) * 100, 1)

    resolved_under_24h_count = 0
    for row in resolved_rows:
        duration = _duration_hours(getattr(row, "created_at", None), getattr(row, "completed_at", None))
        if duration is not None and duration <= 24:
            resolved_under_24h_count += 1

    resolved_under_24h_rate = 0.0
    if resolved_count > 0:
        resolved_under_24h_rate = round((resolved_under_24h_count / resolved_count) * 100, 1)

    previous_start = period.start - timedelta(days=days)
    previous_end = period.start

    previous_new_count = _count(base.filter(Request.created_at >= previous_start, Request.created_at < previous_end))
    previous_resolved_count = _count(
        resolved_base.filter(Request.completed_at >= previous_start, Request.completed_at < previous_end)
    )
    previous_open_base = base.filter(_open_filter_as_of(previous_end))
    previous_open_count = _count(previous_open_base)
    previous_unassigned_count = _count(
        previous_open_base.filter(
            or_(
                Request.owner_id.is_(None),
                Request.owned_at.is_(None),
                Request.owned_at > previous_end,
            )
        )
    )
    previous_stale_count = _count(
        previous_open_base.filter(Request.created_at.isnot(None)).filter(
            Request.created_at < previous_end - timedelta(hours=72)
        )
    )
    previous_critical_open_count = _count(previous_open_base.filter(_critical_filter()))

    previous_resolved_rows = (
        resolved_base.filter(Request.completed_at >= previous_start, Request.completed_at < previous_end)
        .filter(Request.created_at.isnot(None))
        .all()
    )
    previous_avg_resolution_hours = _avg_duration_hours(previous_resolved_rows, "created_at", "completed_at")

    sla_alerts = build_sla_alerts(structure_id=structure_id, now=now)
    current_sla_breach_count = _count_sla_breaches(base.filter(Request.created_at <= period.end).all(), period.end)
    previous_sla_breach_count = _count_sla_breaches(base.filter(Request.created_at <= previous_end).all(), previous_end)

    timeline_map = OrderedDict()
    for offset in range(days):
        current_day = (period.end - timedelta(days=(days - offset - 1))).date()
        timeline_map[str(current_day)] = {"created": 0, "closed": 0}

    created_timeline_rows = (
        period_base.with_entities(func.date(Request.created_at), func.count(Request.id))
        .group_by(func.date(Request.created_at))
        .all()
    )
    for row_date, row_count in created_timeline_rows:
        key = str(row_date)
        if key in timeline_map:
            timeline_map[key]["created"] = int(row_count or 0)

    closed_timeline_rows = (
        resolved_base.with_entities(func.date(Request.completed_at), func.count(Request.id))
        .filter(Request.completed_at >= period.start, Request.completed_at <= period.end)
        .group_by(func.date(Request.completed_at))
        .all()
    )
    for row_date, row_count in closed_timeline_rows:
        key = str(row_date)
        if key in timeline_map:
            timeline_map[key]["closed"] = int(row_count or 0)

    timeline = [{"date": key, "created": values["created"], "closed": values["closed"]} for key, values in timeline_map.items()]
    timeline_created_values = [item["created"] for item in timeline]
    timeline_closed_values = [item["closed"] for item in timeline]

    report_items = [
        {
            "id": row.id,
            "title": getattr(row, "title", None) or f"Demande #{row.id}",
            "city": getattr(row, "city", None) or "",
            "status": getattr(row, "status", None) or "",
            "priority": getattr(row, "priority", None) or "",
            "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else "",
            "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else "",
            "owner_id": getattr(row, "owner_id", None),
        }
        for row in period_base.order_by(Request.created_at.desc()).limit(200).all()
    ]

    structure = None
    if structure_id is not None:
        structure = db.session.get(Structure, int(structure_id))

    active_intervenants_count = 0
    if hasattr(Intervenant, "is_active"):
        professionals_query = Intervenant.query.filter(Intervenant.is_active.is_(True))
        if structure_id is not None and hasattr(Intervenant, "structure_id"):
            professionals_query = professionals_query.filter(Intervenant.structure_id == int(structure_id))
        active_intervenants_count = int(professionals_query.count() or 0)

    operational_load = _safe_ratio(open_count, active_intervenants_count, 1) if active_intervenants_count > 0 else float(open_count)
    previous_operational_load = _safe_ratio(previous_open_count, active_intervenants_count, 1) if active_intervenants_count > 0 else float(previous_open_count)

    insight_metrics = {
        "open_requests": open_count,
        "unassigned_requests": unassigned_count,
        "stale_requests": stale_count,
        "avg_resolution_hours": avg_resolution_hours,
        "assignment_rate": assignment_rate,
        "sla_breaches": current_sla_breach_count,
        "critical_requests": critical_open_count,
        "active_intervenants": active_intervenants_count,
        "operational_load": operational_load,
    }

    executive_summary = _build_executive_summary(insight_metrics)
    operational_severity = _compute_operational_severity(insight_metrics)
    territorial_pressure = _build_territorial_pressure(current_open_rows, structure_id=structure_id)

    recommendations = _build_operational_recommendations(insight_metrics, territorial_pressure)

    trends = {
        "new_requests": {
            **_build_trend_metric(new_count, previous_new_count),
            "semantic": _trend_semantic(_build_trend_metric(new_count, previous_new_count)["direction"], "down"),
        },
        "resolved_requests": {
            **_build_trend_metric(resolved_count, previous_resolved_count),
            "semantic": _trend_semantic(_build_trend_metric(resolved_count, previous_resolved_count)["direction"], "up"),
        },
        "open_requests": {
            **_build_trend_metric(open_count, previous_open_count),
            "semantic": _trend_semantic(_build_trend_metric(open_count, previous_open_count)["direction"], "down"),
        },
        "unassigned_requests": {
            **_build_trend_metric(unassigned_count, previous_unassigned_count),
            "semantic": _trend_semantic(_build_trend_metric(unassigned_count, previous_unassigned_count)["direction"], "down"),
        },
        "sla_breaches": {
            **_build_trend_metric(current_sla_breach_count, previous_sla_breach_count),
            "semantic": _trend_semantic(_build_trend_metric(current_sla_breach_count, previous_sla_breach_count)["direction"], "down"),
        },
        "critical_requests": {
            **_build_trend_metric(critical_open_count, previous_critical_open_count),
            "semantic": _trend_semantic(_build_trend_metric(critical_open_count, previous_critical_open_count)["direction"], "down"),
        },
        "avg_resolution_hours": {
            **_build_trend_metric(avg_resolution_hours, previous_avg_resolution_hours),
            "semantic": _trend_semantic(_build_trend_metric(avg_resolution_hours, previous_avg_resolution_hours)["direction"], "down"),
        },
        "operational_load": {
            **_build_trend_metric(operational_load, previous_operational_load),
            "semantic": _trend_semantic(_build_trend_metric(operational_load, previous_operational_load)["direction"], "down"),
        },
    }

    executive_snapshot = [
        _build_snapshot_metric(
            key="open_requests",
            label="Situations ouvertes",
            current_value=open_count,
            previous_value=previous_open_count,
            severity="critical" if open_count >= 25 else "high" if open_count >= 12 else "moderate" if open_count >= 6 else "stable",
            summary="Volume actuellement en suivi actif.",
        ),
        _build_snapshot_metric(
            key="unassigned_requests",
            label="Non assignees",
            current_value=unassigned_count,
            previous_value=previous_unassigned_count,
            severity="critical" if unassigned_count >= 8 else "high" if unassigned_count >= 4 else "moderate" if unassigned_count >= 1 else "stable",
            summary="Situations sans referent operationnel.",
        ),
        _build_snapshot_metric(
            key="sla_breaches",
            label="SLA depasses",
            current_value=current_sla_breach_count,
            previous_value=previous_sla_breach_count,
            severity="critical" if current_sla_breach_count >= 6 else "high" if current_sla_breach_count >= 3 else "moderate" if current_sla_breach_count >= 1 else "stable",
            summary="Situations en depassement de jalons de suivi.",
        ),
        _build_snapshot_metric(
            key="avg_resolution_hours",
            label="Temps moyen de resolution",
            current_value=avg_resolution_hours,
            previous_value=previous_avg_resolution_hours,
            severity="critical" if avg_resolution_hours >= 96 else "high" if avg_resolution_hours >= 72 else "moderate" if avg_resolution_hours >= 36 else "stable",
            summary="Delai moyen observe sur les sorties de file.",
            is_hours=True,
            precision=1,
        ),
        _build_snapshot_metric(
            key="critical_requests",
            label="Situations critiques",
            current_value=critical_open_count,
            previous_value=previous_critical_open_count,
            severity="critical" if critical_open_count >= 5 else "high" if critical_open_count >= 2 else "stable",
            summary="Cas prioritaires ouverts a securiser.",
        ),
        _build_snapshot_metric(
            key="operational_load",
            label="Charge operationnelle",
            current_value=operational_load,
            previous_value=previous_operational_load,
            severity=(
                "critical"
                if (active_intervenants_count == 0 and open_count > 0) or operational_load >= 6
                else "high"
                if operational_load >= 3
                else "moderate"
                if operational_load >= 1.5
                else "stable"
            ),
            summary="Situations ouvertes par intervenant actif.",
            precision=1,
        ),
    ]

    priority_actions = _build_priority_actions(insight_metrics, territorial_pressure, executive_snapshot)
    automatic_analysis = _build_automatic_analysis(insight_metrics, trends, territorial_pressure)
    operational_conclusion = _build_operational_conclusion(
        insight_metrics,
        recommendations,
        territorial_pressure,
        operational_severity,
    )

    return {
        "generated_at": period.end.isoformat() + "Z",
        "period": {
            "days": max(1, min(int(days or 7), 366)),
            "start": period.start.isoformat() + "Z",
            "end": period.end.isoformat() + "Z",
            "previous_start": previous_start.isoformat() + "Z",
            "previous_end": previous_end.isoformat() + "Z",
        },
        "scope": {
            "structure_id": structure_id,
            "structure_name": getattr(structure, "name", None) if structure else None,
        },
        "requests": {
            "new": new_count,
            "resolved": resolved_count,
            "open": open_count,
            "stale": stale_count,
            "unassigned": unassigned_count,
            "critical": critical_open_count,
            "sla_breaches": current_sla_breach_count,
        },
        "sla": {
            "avg_assignment_hours": avg_assignment_hours,
            "avg_resolution_hours": avg_resolution_hours,
            "assignment_rate": assignment_rate,
            "resolved_under_24h_rate": resolved_under_24h_rate,
            "breach_count": current_sla_breach_count,
        },
        "breakdowns": {
            "by_category": _rows_by_label(by_category_rows, "category"),
            "by_status": by_status_rows,
        },
        "timeline": timeline,
        "items": report_items,
        "timeline_charts": {
            "created": _build_sparkline_points(timeline_created_values),
            "closed": _build_sparkline_points(timeline_closed_values),
        },
        "executive_summary": executive_summary,
        "operational_severity": operational_severity,
        "executive_snapshot": executive_snapshot,
        "priority_actions": priority_actions,
        "recommendations": recommendations,
        "territorial_pressure": territorial_pressure,
        "automatic_analysis": automatic_analysis,
        "operational_conclusion": operational_conclusion,
        "sla_alerts": sla_alerts,
        "trends": trends,
        "definition": {
            "scope": "structure-scoped when structure_id is provided; excludes deleted and archived requests",
            "open": "status is not in canonical closed/resolved/cancelled/archived states",
            "resolved": "completed_at is present and status is done/completed/resolved/closed",
            "stale": "open request created more than 72 hours before report generation",
        },
    }
