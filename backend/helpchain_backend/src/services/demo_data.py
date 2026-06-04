from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _admin(username: str) -> SimpleNamespace:
    return SimpleNamespace(username=username)


def _request(
    *,
    request_id: int,
    title: str,
    city: str,
    category: str,
    status: str,
    priority: str,
    owner_name: str | None,
    created_delta_hours: int,
    updated_delta_hours: int,
) -> SimpleNamespace:
    now = _now()
    owner = _admin(owner_name) if owner_name else None
    return SimpleNamespace(
        id=request_id,
        title=title,
        city=city,
        category=category,
        status=status,
        priority=priority,
        owner=owner,
        owner_id=(1 if owner_name else None),
        created_at=now - timedelta(hours=created_delta_hours),
        updated_at=now - timedelta(hours=updated_delta_hours),
        risk_score=92 if priority == "high" else 68 if priority == "medium" else 35,
    )


def _case(
    *,
    case_id: int,
    request_row: SimpleNamespace,
    status: str,
    priority: str,
    owner_name: str | None,
    last_activity_hours: int,
    risk_score: int,
) -> SimpleNamespace:
    now = _now()
    owner = _admin(owner_name) if owner_name else None
    return SimpleNamespace(
        id=case_id,
        request_id=request_row.id,
        request=request_row,
        status=status,
        priority=priority,
        owner_user=owner,
        owner_user_id=(case_id if owner_name else None),
        last_activity_at=now - timedelta(hours=last_activity_hours),
        updated_at=now - timedelta(hours=last_activity_hours),
        created_at=request_row.created_at,
        risk_score=risk_score,
        assigned_professional_lead=None,
    )


def _notification(
    *,
    job_id: int,
    status: str,
    event_type: str,
    recipient: str,
    channel: str = "email",
    attempts: int = 0,
    max_attempts: int = 5,
    created_delta_hours: int = 1,
    retry_delta_hours: int | None = None,
    sent_delta_hours: int | None = None,
    last_error: str | None = None,
) -> SimpleNamespace:
    now = _now()
    return SimpleNamespace(
        id=job_id,
        channel=channel,
        event_type=event_type,
        recipient=recipient,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
        created_at=now - timedelta(hours=created_delta_hours),
        next_retry_at=(now + timedelta(hours=retry_delta_hours))
        if retry_delta_hours is not None
        else None,
        sent_at=(now - timedelta(hours=sent_delta_hours))
        if sent_delta_hours is not None
        else None,
        last_error=last_error,
    )


INSTITUTIONAL_UNIVERSES = {
    "ccas_urbain",
    "reseau_associatif",
    "coordination_sante",
}
_FIXTURES_PATH = (
    Path(__file__).resolve().parents[4] / "docs" / "institutional-pilot-fixtures.yaml"
)


def _parse_fixture_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except Exception:
        return None


def _load_institutional_fixtures_raw() -> dict[str, object] | None:
    try:
        if not _FIXTURES_PATH.exists():
            return None
        return json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_universe_name(universe: str | None) -> str:
    normalized = (universe or "").strip().lower()
    if normalized in INSTITUTIONAL_UNIVERSES:
        return normalized
    return "ccas_urbain"


def _owner_namespace(owner_name: object, owner_role: object) -> SimpleNamespace | None:
    name = str(owner_name or "").strip()
    role = str(owner_role or "").strip()
    if not name and not role:
        return None
    username = name.lower().replace(" ", ".") if name else role or "coordination"
    return SimpleNamespace(username=username, name=name or username, role=role or None)


def _notification_bucket_label(bucket: str) -> str:
    return {
        "pending": "en attente",
        "processing": "en cours",
        "retry": "a relancer",
        "failed": "en echec",
        "sent": "envoyee",
    }.get((bucket or "").strip().lower(), "en attente")


def _build_institutional_request(row: dict[str, object]) -> SimpleNamespace:
    request_id_text = str(row.get("id") or "R0").strip()
    digits = "".join(ch for ch in request_id_text if ch.isdigit()) or "0"
    request_id = int(digits)
    created_at = _parse_fixture_dt(row.get("created_at")) or _now()
    updated_at = _parse_fixture_dt(row.get("updated_at")) or created_at
    universe = str(row.get("universe") or "").strip().lower()
    urgency = str(row.get("urgency_level") or "standard").strip().lower()
    priority = {
        "critique": "critical",
        "urgent": "high",
        "sensible": "medium",
    }.get(urgency, "standard")
    owner = _owner_namespace(row.get("owner_name"), row.get("owner_role"))
    return SimpleNamespace(
        id=request_id,
        fixture_id=request_id_text,
        title=str(row.get("title") or f"Demande #{request_id}"),
        name=str(row.get("title") or f"Demande #{request_id}"),
        city=str(row.get("territorial_context") or ""),
        location_text=str(row.get("territorial_context") or ""),
        category=str(row.get("request_type") or "social"),
        request_type=str(row.get("request_type") or "social"),
        status=str(row.get("status") or "open"),
        priority=priority,
        urgency_level=urgency,
        operational_stage=str(row.get("operational_stage") or "receptionnee"),
        blocking_reason=row.get("blocking_reason"),
        handoff_state=str(row.get("handoff_state") or "aucun"),
        owner=owner,
        owner_id=(request_id if owner else None),
        owner_role=str(row.get("owner_role") or ""),
        next_action=str(row.get("next_action") or ""),
        sla_badge=str(row.get("sla_badge") or ""),
        description=str(row.get("description") or ""),
        territorial_context=str(row.get("territorial_context") or ""),
        universe=universe,
        created_at=created_at,
        updated_at=updated_at,
        risk_level="critical" if urgency == "critique" else "attention" if urgency in {"urgent", "sensible"} else "standard",
        risk_score=92 if urgency == "critique" else 81 if urgency == "urgent" else 61 if urgency == "sensible" else 34,
        phone=None,
        email=None,
        service=None,
        service_id=None,
        structure=None,
        structure_id=None,
        assigned_volunteer_id=None,
    )


def _build_institutional_notification(row: dict[str, object]) -> SimpleNamespace:
    status = str(row.get("status") or "pending").strip().lower()
    return SimpleNamespace(
        id=int(row.get("id") or 0),
        channel=str(row.get("channel") or "email"),
        event_type=str(row.get("event_type") or ""),
        recipient=str(row.get("recipient") or ""),
        status=status,
        attempts=int(row.get("attempts") or 0),
        max_attempts=int(row.get("max_attempts") or 0),
        created_at=_parse_fixture_dt(row.get("created_at")) or _now(),
        next_retry_at=_parse_fixture_dt(row.get("next_retry_at")),
        sent_at=_parse_fixture_dt(row.get("sent_at")),
        last_error=row.get("last_error"),
        title=str(row.get("title") or ""),
        body=str(row.get("body") or ""),
        request_id=row.get("request_id"),
        ui_status_bucket=status if status in {"pending", "processing", "retry", "failed", "sent"} else "pending",
        ui_status_label=_notification_bucket_label(status),
    )


def _build_institutional_audit_event(row: dict[str, object], request_lookup: dict[str, SimpleNamespace]) -> SimpleNamespace:
    request_id = str(row.get("request_id") or "").strip()
    request_row = request_lookup.get(request_id)
    payload = dict(row.get("payload") or {})
    if request_row is not None:
        payload.setdefault("request_fixture_id", request_row.fixture_id)
        payload.setdefault("request_title", request_row.title)
    return SimpleNamespace(
        created_at=_parse_fixture_dt(row.get("timestamp")) or _now(),
        action=str(row.get("action") or ""),
        admin_username=str(row.get("admin_username") or "system"),
        admin_user_id=1,
        target_type=str(row.get("target_type") or "Request"),
        target_id=int(row.get("target_id") or 0),
        ip=str(row.get("ip") or "127.0.0.1"),
        payload=payload,
        request_id=request_id or None,
    )


def _tone_to_priority(urgency: str) -> str:
    return {
        "critique": "critique",
        "urgent": "eleve",
        "sensible": "eleve",
    }.get((urgency or "").strip().lower(), "normal")


def _build_cases_from_requests(requests: list[SimpleNamespace]) -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    for idx, request_row in enumerate(requests[:8], start=1):
        rows.append(
            SimpleNamespace(
                id=200 + idx,
                request_id=request_row.id,
                request=request_row,
                status=request_row.status,
                priority=_tone_to_priority(getattr(request_row, "urgency_level", "standard")),
                owner_user=request_row.owner,
                owner_user_id=request_row.owner_id,
                last_activity_at=request_row.updated_at,
                updated_at=request_row.updated_at,
                created_at=request_row.created_at,
                risk_score=request_row.risk_score,
                assigned_professional_lead=None,
            )
        )
    return rows


def _build_priority_items(requests: list[SimpleNamespace]) -> list[dict[str, object]]:
    def _rank(row: SimpleNamespace) -> tuple[int, float]:
        urgency = getattr(row, "urgency_level", "standard")
        rank = 0 if urgency == "critique" else 1 if urgency == "urgent" else 2 if getattr(row, "blocking_reason", None) else 3
        updated_at = getattr(row, "updated_at", None)
        ts = float(updated_at.timestamp()) if isinstance(updated_at, datetime) else 0.0
        return (rank, -ts)

    items: list[dict[str, object]] = []
    for row in sorted(requests, key=_rank)[:6]:
        items.append(
            {
                "id": row.id,
                "fixture_id": row.fixture_id,
                "title": row.title,
                "summary_compact": f"{row.operational_stage} · {row.territorial_context}",
                "risk_level": row.risk_level,
                "indicator_label": row.blocking_reason or row.sla_badge or "Suivi en cours",
                "operational_stage": row.operational_stage,
                "next_action": row.next_action,
            }
        )
    return items


def _build_notification_summary(rows: list[SimpleNamespace]) -> dict[str, int]:
    summary = {
        "pending": 0,
        "processing": 0,
        "done": 0,
        "dead_letter": 0,
        "retry": 0,
        "failed": 0,
        "sent": 0,
    }
    for row in rows:
        status = str(getattr(row, "status", "") or "").strip().lower()
        if status in summary:
            summary[status] += 1
    return summary


def _build_timeline_lookup(rows: list[dict[str, object]]) -> dict[int, list[str]]:
    lookup: dict[int, list[str]] = {}
    for row in rows:
        request_id = str(row.get("request_id") or "").strip()
        digits = "".join(ch for ch in request_id if ch.isdigit()) or "0"
        lookup[int(digits)] = [str(item) for item in row.get("entries") or []]
    return lookup


def validate_institutional_pilot_payload(payload: dict[str, object] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    universe = payload.get("universe")
    requests = payload.get("requests")
    kpis = payload.get("kpis")
    notifications = payload.get("notifications")
    audit_events = payload.get("audit_events")
    if not isinstance(universe, dict):
        return False
    if not str(universe.get("name") or "").strip():
        return False
    return (
        isinstance(requests, list)
        and len(requests) == 30
        and isinstance(kpis, list)
        and len(kpis) == 12
        and isinstance(notifications, list)
        and len(notifications) == 6
        and isinstance(audit_events, list)
        and len(audit_events) == 20
    )


def get_institutional_pilot_payload(universe: str | None = None) -> dict[str, object] | None:
    raw = _load_institutional_fixtures_raw()
    if not raw:
        return None

    normalized_universe = _normalize_universe_name(universe)
    universe_meta = (
        raw.get("universes", {}) or {}
    ).get(normalized_universe)
    if not isinstance(universe_meta, dict):
        return None

    all_request_dicts = [
        row for row in (raw.get("requests") or []) if isinstance(row, dict)
    ]
    all_timeline_dicts = [
        row for row in (raw.get("timelines") or []) if isinstance(row, dict)
    ]
    all_audit_dicts = [
        row for row in (raw.get("audit_events") or []) if isinstance(row, dict)
    ]
    all_kpi_dicts = [
        row for row in (raw.get("kpi_cards") or []) if isinstance(row, dict)
    ]
    all_notification_dicts = [
        row for row in (raw.get("notifications") or []) if isinstance(row, dict)
    ]

    request_rows = [
        _build_institutional_request(row)
        for row in all_request_dicts
        if str(row.get("universe") or "").strip().lower() == normalized_universe
    ]
    if not request_rows:
        return None

    request_lookup_by_fixture_id = {row.fixture_id: row for row in request_rows}
    notification_rows = [
        _build_institutional_notification(row)
        for row in all_notification_dicts
    ]
    audit_rows = [
        _build_institutional_audit_event(row, request_lookup_by_fixture_id)
        for row in all_audit_dicts
        if (
            str(row.get("request_id") or "").strip() in request_lookup_by_fixture_id
            or str(row.get("action") or "").startswith("security.")
        )
    ]
    timeline_rows = [
        row
        for row in all_timeline_dicts
        if str(row.get("request_id") or "").strip() in request_lookup_by_fixture_id
    ]
    timeline_lookup = _build_timeline_lookup(timeline_rows)

    case_rows = _build_cases_from_requests(request_rows)
    case_signals = {
        case_row.id: [
            getattr(case_row.request, "operational_stage", "suivi"),
            getattr(case_row.request, "blocking_reason", None) or "continuite",
        ]
        for case_row in case_rows
    }
    case_priority_levels = {
        case_row.id: _tone_to_priority(getattr(case_row.request, "urgency_level", "standard"))
        for case_row in case_rows
    }
    queue_reasons = {
        row.id: [
            row.operational_stage.replace("_", " "),
            row.blocking_reason.replace("_", " ") if row.blocking_reason else row.sla_badge,
        ]
        for row in request_rows
    }
    priority_levels = {
        row.id: _tone_to_priority(getattr(row, "urgency_level", "standard"))
        for row in request_rows
    }

    kpi_lookup = {
        str(card.get("key") or ""): card
        for card in all_kpi_dicts
    }

    critical_count = sum(1 for row in request_rows if row.urgency_level == "critique")
    attention_count = sum(1 for row in request_rows if row.urgency_level in {"urgent", "sensible"})
    standard_count = max(0, len(request_rows) - critical_count - attention_count)
    no_owner_count = sum(1 for row in request_rows if not row.owner)
    not_seen_72h_count = int(kpi_lookup.get("sans_activite_72h", {}).get("value", 0) or 0)
    critical_without_owner_count = sum(
        1 for row in request_rows if row.urgency_level == "critique" and not row.owner
    )
    assign_immediately_count = sum(
        1 for row in request_rows if row.urgency_level in {"critique", "urgent"} and not row.owner
    )
    manager_review_today_count = sum(
        1 for row in request_rows if row.blocking_reason in {"capacite_saturee", "saturation_partenaire", "dossier_incomplet"}
    )
    rec_counts = {
        "assign_immediately": assign_immediately_count,
        "manager_review_today": manager_review_today_count,
        "route_to_housing_partner": sum(1 for row in request_rows if row.request_type == "hebergement" and row.handoff_state in {"propose", "transmis"}),
        "route_to_food_support": sum(1 for row in request_rows if row.request_type == "aide_alimentaire"),
        "route_to_health_support": sum(1 for row in request_rows if row.universe == "coordination_sante"),
    }

    scenario_label = str(universe_meta.get("label") or normalized_universe.replace("_", " "))
    scenario_description = str(universe_meta.get("short_description") or "")
    narrative_request_id = str(universe_meta.get("narrative_request_id") or "")
    narrative_request = request_lookup_by_fixture_id.get(narrative_request_id)
    narrative_timeline = timeline_lookup.get(narrative_request.id if narrative_request else 0, [])

    sla_rows = [
        {
            "id": row.id,
            "title": row.title,
            "category": row.category,
            "status": row.status,
            "created_at": row.created_at,
            "owner_id": row.owner_id,
            "assigned_volunteer_id": None,
            "overdue_hours": 18.0 if row.blocking_reason else 6.0,
            "breach_type": "owner_assign" if not row.owner else "resolve",
        }
        for row in request_rows
        if row.blocking_reason or not row.owner
    ][:8]

    payload = {
        "universe": {
            "key": normalized_universe,
            "name": scenario_label,
            "short_description": scenario_description,
            "kpi_tone": universe_meta.get("kpi_tone") or "",
            "coordination_flow": universe_meta.get("coordination_flow") or "",
        },
        "requests": all_request_dicts,
        "timelines": all_timeline_dicts,
        "audit_events": all_audit_dicts,
        "kpis": all_kpi_dicts,
        "notifications": all_notification_dicts,
        "universes": raw.get("universes") or {},
        "scenario_name": normalized_universe,
        "scenario_meta": {
            "label": scenario_label,
            "short_description": scenario_description,
        },
        "workspace_kpis": {
            "critical": critical_count,
            "unassigned": no_owner_count,
            "relance": int(kpi_lookup.get("relances_sans_retour", {}).get("value", 0) or 0),
            "notifications_failed": _build_notification_summary(notification_rows)["failed"],
            "updated_today": sum(1 for row in request_rows if row.updated_at.date() == datetime(2026, 6, 4).date()),
            "retry_notifications": _build_notification_summary(notification_rows)["retry"],
        },
        "workspace_rows": request_rows,
        "workspace_queue_reasons": queue_reasons,
        "workspace_priority_levels": priority_levels,
        "cases_kpis": {
            "critical": critical_count,
            "attention": attention_count,
            "no_owner": no_owner_count,
            "stale": not_seen_72h_count,
        },
        "cases_rows": case_rows,
        "cases_signals": case_signals,
        "cases_priority_levels": case_priority_levels,
        "notifications_kpis": _build_notification_summary(notification_rows),
        "notification_rows": notification_rows,
        "notification_channels": sorted({row.channel for row in notification_rows if row.channel}),
        "security_kpis": {
            "success_24h": 9,
            "failed_24h": 3,
            "distinct_failed_ips_24h": 1,
            "distinct_failed_usernames_24h": 1,
            "lockout_buckets_24h": 0,
            "risky_actions_24h": 2,
            "denied_24h": sum(1 for row in audit_rows if row.action.startswith("security.")),
        },
        "security_anomalies": {
            "spike_failed_logins": False,
            "repeated_fails_by_ip": False,
            "repeated_fails_by_username": False,
            "failed_1h": 0,
            "avg_hourly": 0.0,
            "spike_threshold": 10.0,
            "top_ip": "203.0.113.44",
            "top_ip_fails": 1,
            "top_username": "ops.reseau",
            "top_username_fails": 1,
            "denied_spike": False,
            "repeated_denied": False,
            "denied_1h": 0,
            "avg_denied_hourly": 0.0,
            "top_denied_ip": "203.0.113.44",
            "top_denied_ip_count": 1,
            "top_denied_username": "ops.reseau",
            "top_denied_username_count": 1,
        },
        "security_recent_attempts": {
            "recent_logins": [],
            "recent_risky": [],
            "recent_denied": [row for row in audit_rows if row.action.startswith("security.")][:3],
            "recent_sensitive": [row for row in audit_rows if "referral" in row.action][:3],
            "top_ips": [("203.0.113.44", 1)],
            "top_usernames": [("ops.reseau", 1)],
            "top_denied_ips": [("203.0.113.44", 1)],
            "top_denied_usernames": [("ops.reseau", 1)],
            "risky_actions": [
                "request.status_changed",
                "request.owner_assigned",
                "security.denied_cross_structure_access",
            ],
        },
        "audit_rows": audit_rows,
        "audit_filters": {"action": "", "admin": "", "target_type": "", "target_id": "", "days": "7"},
        "audit_actions": sorted({row.action for row in audit_rows}),
        "audit_target_types": sorted({row.target_type for row in audit_rows}),
        "audit_pagination": _pagination(len(audit_rows)),
        "sla_kpis": {
            "breach_label": "SLA continuité de suivi",
            "resolve_count": sum(1 for row in sla_rows if row["breach_type"] == "resolve"),
            "owner_assign_count": sum(1 for row in sla_rows if row["breach_type"] == "owner_assign"),
            "volunteer_assign_count": 0,
            "prediction_counts": {
                "resolution_overdue": sum(1 for row in request_rows if row.blocking_reason),
                "owner_assignment_overdue": no_owner_count,
                "volunteer_assignment_overdue": 0,
            },
        },
        "sla_rows": sla_rows,
        "pilotage": {
            "critical_count": critical_count,
            "attention_count": attention_count,
            "standard_count": standard_count,
            "no_owner_count": no_owner_count,
            "not_seen_72h_count": not_seen_72h_count,
            "critical_without_owner_count": critical_without_owner_count,
            "assign_immediately_count": assign_immediately_count,
            "manager_review_today_count": manager_review_today_count,
            "priority_items": _build_priority_items(request_rows),
            "category_trend_text": "Le volume reste principalement oriente vers les flux courants d aide alimentaire, d acces aux droits et de suivi social.",
            "assignment_delay_text": "Quelques dossiers conservent une latence de reprise, sans rupture generale de coordination.",
            "vigilance_text": "Les points de vigilance se concentrent sur les relances sans retour, les attentes partenaires et les validations de cloture.",
            "rec_counts": rec_counts,
            "received_today": sum(1 for row in request_rows if row.created_at.date() == datetime(2026, 6, 4).date()),
            "taken_today": sum(1 for row in request_rows if row.status == "in_progress" and row.updated_at.date() == datetime(2026, 6, 4).date()),
            "closed_today": sum(1 for row in request_rows if row.status == "done" and row.updated_at.date() == datetime(2026, 6, 4).date()),
        },
        "institutional_kpis": list(raw.get("kpi_cards") or []),
        "institutional_timelines": timeline_lookup,
        "institutional_story": {
            "request_id": narrative_request.id if narrative_request else None,
            "request_fixture_id": narrative_request.fixture_id if narrative_request else None,
            "title": narrative_request.title if narrative_request else "",
            "timeline": narrative_timeline,
            "summary": "Premiere demande, qualification, orientation, relance, supervision, prise en charge et consolidation dans le pilotage.",
        },
        "institutional_universe": {
            "key": normalized_universe,
            "label": scenario_label,
            "short_description": scenario_description,
            "kpi_tone": universe_meta.get("kpi_tone") or "",
            "coordination_flow": universe_meta.get("coordination_flow") or "",
        },
    }
    return payload if validate_institutional_pilot_payload(payload) else None


def get_demo_kpis() -> dict[str, int]:
    return {
        "critical": 2,
        "unassigned": 3,
        "relance": 1,
        "notifications_failed": 1,
        "updated_today": 4,
    }


def get_demo_requests() -> list[SimpleNamespace]:
    return [
        _request(
            request_id=101,
            title="Femme isolée sans ressources",
            city="Paris",
            category="social",
            status="open",
            priority="high",
            owner_name=None,
            created_delta_hours=6,
            updated_delta_hours=2,
        ),
        _request(
            request_id=102,
            title="Demande logement urgente",
            city="Boulogne",
            category="logement",
            status="in_progress",
            priority="high",
            owner_name="Marie Dupont",
            created_delta_hours=26,
            updated_delta_hours=24,
        ),
        _request(
            request_id=103,
            title="Signalement social critique",
            city="Lyon",
            category="social",
            status="open",
            priority="high",
            owner_name=None,
            created_delta_hours=72,
            updated_delta_hours=72,
        ),
        _request(
            request_id=104,
            title="Orientation vers un hébergement temporaire",
            city="Paris",
            category="logement",
            status="in_progress",
            priority="medium",
            owner_name="Nadia Bernard",
            created_delta_hours=18,
            updated_delta_hours=7,
        ),
    ]


def get_demo_queue_reasons() -> dict[int, list[str]]:
    return {
        101: ["Critique", "Sans responsable"],
        102: ["À vérifier"],
        103: ["Critique", "Sans action récente"],
        104: ["Coordination à suivre"],
    }


def get_demo_ops_priority_levels() -> dict[int, str]:
    return {
        101: "critique",
        102: "élevé",
        103: "critique",
        104: "normal",
    }


def get_demo_cases() -> list[SimpleNamespace]:
    requests = {req.id: req for req in get_demo_requests()}
    return [
        _case(
            case_id=201,
            request_row=requests[101],
            status="new",
            priority="critical",
            owner_name=None,
            last_activity_hours=2,
            risk_score=92,
        ),
        _case(
            case_id=202,
            request_row=requests[102],
            status="in_progress",
            priority="high",
            owner_name="Marie Dupont",
            last_activity_hours=24,
            risk_score=84,
        ),
        _case(
            case_id=203,
            request_row=requests[103],
            status="assigned",
            priority="critical",
            owner_name=None,
            last_activity_hours=73,
            risk_score=95,
        ),
    ]


def get_demo_case_signals() -> dict[int, list[str]]:
    return {
        201: ["URGENT", "NON ASSIGNÉ"],
        202: ["À VÉRIFIER"],
        203: ["CRITIQUE", "NOTIF. ÉCHEC"],
    }


def get_demo_notifications() -> list[SimpleNamespace]:
    return [
        _notification(
            job_id=301,
            status="pending",
            event_type="contact_exchange",
            recipient="orientation@ccas-paris.fr",
            attempts=0,
            created_delta_hours=1,
            retry_delta_hours=1,
        ),
        _notification(
            job_id=302,
            status="failed",
            event_type="email_send",
            recipient="pilotage@territoire.fr",
            attempts=3,
            max_attempts=5,
            created_delta_hours=5,
            retry_delta_hours=2,
            last_error="SMTP timeout lors du dernier envoi",
        ),
        _notification(
            job_id=303,
            status="pending",
            event_type="reminder",
            recipient="coordination@boulogne.fr",
            attempts=1,
            created_delta_hours=3,
            retry_delta_hours=1,
        ),
    ]


def get_demo_notification_summary() -> dict[str, int]:
    return {
        "pending": 2,
        "processing": 0,
        "done": 0,
        "dead_letter": 1,
        "retry": 1,
        "failed": 1,
        "sent": 0,
    }


def get_demo_notification_channels() -> list[str]:
    return ["email"]


def get_demo_sla_payload() -> dict[str, object]:
    now = _now()
    rows = [
        {
            "id": 101,
            "title": "Femme isolée sans ressources",
            "category": "social",
            "status": "open",
            "created_at": now - timedelta(days=3, hours=2),
            "owner_id": None,
            "assigned_volunteer_id": None,
            "overdue_hours": 26.0,
            "breach_type": "owner_assign",
        },
        {
            "id": 102,
            "title": "Demande logement urgente",
            "category": "logement",
            "status": "in_progress",
            "created_at": now - timedelta(days=2, hours=5),
            "owner_id": 1,
            "assigned_volunteer_id": None,
            "overdue_hours": 11.5,
            "breach_type": "resolve",
        },
        {
            "id": 103,
            "title": "Signalement social critique",
            "category": "social",
            "status": "open",
            "created_at": now - timedelta(days=4),
            "owner_id": None,
            "assigned_volunteer_id": None,
            "overdue_hours": 38.0,
            "breach_type": "owner_assign",
        },
    ]
    return {
        "breach_label": "SLA assignation owner",
        "resolve_count": 1,
        "owner_assign_count": 2,
        "volunteer_assign_count": 0,
        "prediction_counts": {
            "resolution_overdue": 1,
            "owner_assignment_overdue": 2,
            "volunteer_assignment_overdue": 0,
        },
        "rows": rows,
    }


SCENARIOS = {"pilot_ccas", "crise_sociale", "surcharge_hiver"}


def _login_attempt(*, hours_ago: int, username: str, ip: str, success: bool) -> SimpleNamespace:
    return SimpleNamespace(
        created_at=_now() - timedelta(hours=hours_ago),
        username=username,
        ip=ip,
        success=success,
    )


def _audit_event(
    *,
    hours_ago: int,
    action: str,
    admin_username: str,
    target_type: str,
    target_id: int,
    ip: str,
    payload: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        created_at=_now() - timedelta(hours=hours_ago),
        action=action,
        admin_username=admin_username,
        admin_user_id=1,
        target_type=target_type,
        target_id=target_id,
        ip=ip,
        payload=payload or {},
    )


def _pagination(total: int) -> SimpleNamespace:
    return SimpleNamespace(page=1, pages=1, total=total, has_prev=False, has_next=False)


def _base_actions() -> list[str]:
    return [
        "ROLE_CHANGE",
        "STRUCTURE_CREATED",
        "STRUCTURE_ADMIN_ASSIGNED",
        "STATUS_CHANGE",
        "ASSIGN_OPERATOR",
        "CREATE_REQUEST",
    ]


def get_demo_scenario_name(scenario: str | None) -> str:
    normalized = (scenario or "").strip().lower()
    if normalized in SCENARIOS:
        return normalized
    return "pilot_ccas"


def _build_pilot_ccas_payload() -> dict[str, object]:
    requests = [
        _request(request_id=101, title="Femme isolee sans ressources", city="Paris", category="social", status="open", priority="high", owner_name=None, created_delta_hours=6, updated_delta_hours=2),
        _request(request_id=102, title="Demande logement urgente", city="Boulogne", category="logement", status="in_progress", priority="high", owner_name="Marie Dupont", created_delta_hours=26, updated_delta_hours=24),
        _request(request_id=103, title="Signalement social critique", city="Lyon", category="social", status="open", priority="high", owner_name=None, created_delta_hours=72, updated_delta_hours=72),
        _request(request_id=104, title="Orientation vers un hebergement temporaire", city="Paris", category="logement", status="in_progress", priority="medium", owner_name="Nadia Bernard", created_delta_hours=18, updated_delta_hours=7),
    ]
    request_map = {req.id: req for req in requests}
    cases = [
        _case(case_id=201, request_row=request_map[101], status="new", priority="critical", owner_name=None, last_activity_hours=2, risk_score=92),
        _case(case_id=202, request_row=request_map[102], status="in_progress", priority="high", owner_name="Marie Dupont", last_activity_hours=24, risk_score=84),
        _case(case_id=203, request_row=request_map[103], status="assigned", priority="critical", owner_name=None, last_activity_hours=73, risk_score=95),
    ]
    notifications = [
        _notification(job_id=301, status="pending", event_type="contact_exchange", recipient="orientation@ccas-paris.fr", attempts=0, created_delta_hours=1, retry_delta_hours=1),
        _notification(job_id=302, status="failed", event_type="email_send", recipient="pilotage@territoire.fr", attempts=3, max_attempts=5, created_delta_hours=5, retry_delta_hours=2, last_error="SMTP timeout sur la derniere tentative"),
        _notification(job_id=303, status="pending", event_type="reminder", recipient="coordination@boulogne.fr", attempts=1, created_delta_hours=3, retry_delta_hours=1),
    ]
    audit_rows = [
        _audit_event(
            hours_ago=2,
            action="admin_login_failure",
            admin_username="ops.paris",
            target_type="AdminUser",
            target_id=8,
            ip="203.0.113.18",
            payload={"route": "login", "reason": "invalid_credentials"},
        ),
        _audit_event(
            hours_ago=4,
            action="ASSIGN_OPERATOR",
            admin_username="admin.ccas",
            target_type="Request",
            target_id=102,
            ip="198.51.100.9",
            payload={"old": "none", "new": "Marie Dupont"},
        ),
        _audit_event(
            hours_ago=7,
            action="security.denied_action",
            admin_username="ops.lyon",
            target_type="Request",
            target_id=103,
            ip="203.0.113.27",
            payload={"attempted_action": "POST /admin/requests/103/delete"},
        ),
    ]
    return {
        "scenario_meta": {
            "label": "Pilot CCAS",
            "short_description": "Environnement pilote equilibre avec quelques signaux de vigilance.",
        },
        "workspace_kpis": {
            "critical": 2,
            "unassigned": 3,
            "relance": 1,
            "notifications_failed": 1,
            "updated_today": 4,
            "retry_notifications": 1,
        },
        "workspace_rows": requests,
        "workspace_queue_reasons": {
            101: ["Critique", "Sans responsable"],
            102: ["A verifier"],
            103: ["Critique", "Sans action recente"],
            104: ["Coordination a suivre"],
        },
        "workspace_priority_levels": {
            101: "critique",
            102: "eleve",
            103: "critique",
            104: "normal",
        },
        "cases_kpis": {"critical": 2, "attention": 1, "no_owner": 2, "stale": 1},
        "cases_rows": cases,
        "cases_signals": {
            201: ["URGENT", "NON ASSIGNE"],
            202: ["A VERIFIER"],
            203: ["CRITIQUE", "NOTIF. ECHEC"],
        },
        "cases_priority_levels": {201: "critique", 202: "eleve", 203: "critique"},
        "notifications_kpis": {
            "pending": 2,
            "processing": 0,
            "done": 0,
            "dead_letter": 1,
            "retry": 1,
            "failed": 1,
            "sent": 0,
        },
        "notification_rows": notifications,
        "notification_channels": ["email"],
        "security_kpis": {
            "success_24h": 9,
            "failed_24h": 5,
            "distinct_failed_ips_24h": 2,
            "distinct_failed_usernames_24h": 2,
            "lockout_buckets_24h": 1,
            "risky_actions_24h": 2,
            "denied_24h": 3,
        },
        "security_anomalies": {
            "spike_failed_logins": False,
            "repeated_fails_by_ip": False,
            "repeated_fails_by_username": False,
            "failed_1h": 1,
            "avg_hourly": 0.21,
            "spike_threshold": 10.0,
            "top_ip": "203.0.113.18",
            "top_ip_fails": 3,
            "top_username": "ops.paris",
            "top_username_fails": 2,
            "denied_spike": False,
            "repeated_denied": False,
            "denied_1h": 1,
            "avg_denied_hourly": 0.12,
            "top_denied_ip": "203.0.113.18",
            "top_denied_ip_count": 2,
            "top_denied_username": "ops.paris",
            "top_denied_username_count": 2,
        },
        "security_recent_attempts": {
            "recent_logins": [
                _login_attempt(hours_ago=1, username="ops.paris", ip="203.0.113.18", success=False),
                _login_attempt(hours_ago=2, username="admin.ccas", ip="198.51.100.9", success=True),
                _login_attempt(hours_ago=4, username="ops.lyon", ip="203.0.113.27", success=False),
            ],
            "recent_risky": [
                _audit_event(hours_ago=3, action="ROLE_CHANGE", admin_username="admin.ccas", target_type="AdminUser", target_id=14, ip="198.51.100.9", payload={"old": "readonly", "new": "ops"})
            ],
            "recent_denied": [
                _audit_event(hours_ago=1, action="security.denied_action", admin_username="ops.paris", target_type="Request", target_id=101, ip="203.0.113.18", payload={"attempted_action": "POST /admin/requests/101/assign"})
            ],
            "recent_sensitive": [
                _audit_event(hours_ago=5, action="STRUCTURE_ADMIN_ASSIGNED", admin_username="superadmin", target_type="Structure", target_id=7, ip="198.51.100.4")
            ],
            "top_ips": [("203.0.113.18", 3), ("203.0.113.27", 2)],
            "top_usernames": [("ops.paris", 2), ("ops.lyon", 2)],
            "top_denied_ips": [("203.0.113.18", 2), ("203.0.113.27", 1)],
            "top_denied_usernames": [("ops.paris", 2), ("ops.lyon", 1)],
            "risky_actions": _base_actions(),
        },
        "audit_rows": audit_rows,
        "audit_filters": {"action": "", "admin": "", "target_type": "", "target_id": "", "days": "7"},
        "audit_actions": ["admin_login_failure", "ASSIGN_OPERATOR", "security.denied_action"],
        "audit_target_types": ["AdminUser", "Request"],
        "sla_kpis": {
            "breach_label": "SLA assignation owner",
            "resolve_count": 1,
            "owner_assign_count": 2,
            "volunteer_assign_count": 0,
            "prediction_counts": {
                "resolution_overdue": 1,
                "owner_assignment_overdue": 2,
                "volunteer_assignment_overdue": 0,
            },
        },
        "sla_rows": [
            {"id": 101, "title": "Femme isolee sans ressources", "category": "social", "status": "open", "created_at": _now() - timedelta(days=3, hours=2), "owner_id": None, "assigned_volunteer_id": None, "overdue_hours": 26.0, "breach_type": "owner_assign"},
            {"id": 102, "title": "Demande logement urgente", "category": "logement", "status": "in_progress", "created_at": _now() - timedelta(days=2, hours=5), "owner_id": 1, "assigned_volunteer_id": None, "overdue_hours": 11.5, "breach_type": "resolve"},
        ],
    }


def _build_crise_sociale_payload() -> dict[str, object]:
    now = _now()
    requests = [
        _request(request_id=111, title="Famille sans hebergement immediat", city="Paris", category="logement", status="open", priority="high", owner_name=None, created_delta_hours=4, updated_delta_hours=1),
        _request(request_id=112, title="Sortie d'hospitalisation sans relais", city="Lyon", category="sante", status="open", priority="high", owner_name=None, created_delta_hours=11, updated_delta_hours=9),
        _request(request_id=113, title="Demande alimentaire urgente", city="Paris", category="social", status="in_progress", priority="high", owner_name="Marie Dupont", created_delta_hours=19, updated_delta_hours=17),
        _request(request_id=114, title="Signalement expulsion imminente", city="Boulogne", category="logement", status="open", priority="high", owner_name=None, created_delta_hours=53, updated_delta_hours=53),
        _request(request_id=115, title="Coordination aide sociale de crise", city="Lyon", category="social", status="in_progress", priority="medium", owner_name="Nadia Bernard", created_delta_hours=30, updated_delta_hours=8),
    ]
    request_map = {req.id: req for req in requests}
    return {
        "scenario_meta": {"label": "Crise sociale", "short_description": "Pression elevee sur les files, les notifications et les delais."},
        "workspace_kpis": {"critical": 4, "unassigned": 4, "relance": 3, "notifications_failed": 3, "updated_today": 6, "retry_notifications": 3},
        "workspace_rows": requests,
        "workspace_queue_reasons": {111: ["Critique", "Sans responsable"], 112: ["Critique", "A verifier"], 113: ["Coordination a suivre"], 114: ["Critique", "Sans action recente"], 115: ["Volume eleve"]},
        "workspace_priority_levels": {111: "critique", 112: "critique", 113: "eleve", 114: "critique", 115: "eleve"},
        "cases_kpis": {"critical": 3, "attention": 2, "no_owner": 3, "stale": 2},
        "cases_rows": [
            _case(case_id=201, request_row=request_map[111], status="new", priority="critical", owner_name=None, last_activity_hours=1, risk_score=97),
            _case(case_id=202, request_row=request_map[112], status="new", priority="critical", owner_name=None, last_activity_hours=9, risk_score=91),
            _case(case_id=203, request_row=request_map[114], status="assigned", priority="critical", owner_name=None, last_activity_hours=73, risk_score=98),
        ],
        "cases_signals": {201: ["URGENT", "NON ASSIGNE"], 202: ["CRITIQUE", "A VERIFIER"], 203: ["CRITIQUE", "SANS ACTION 72H"]},
        "cases_priority_levels": {201: "critique", 202: "eleve", 203: "critique"},
        "notifications_kpis": {"pending": 1, "processing": 0, "done": 0, "dead_letter": 2, "retry": 3, "failed": 3, "sent": 1},
        "notification_rows": [
            _notification(job_id=311, status="pending", event_type="contact_exchange", recipient="urgence@paris.fr", attempts=0, created_delta_hours=1, retry_delta_hours=1),
            _notification(job_id=312, status="failed", event_type="email_send", recipient="astreinte@lyon.fr", attempts=4, max_attempts=5, created_delta_hours=6, retry_delta_hours=2, last_error="Timeout SMTP sur la passerelle regionale"),
            _notification(job_id=313, status="failed", event_type="reminder", recipient="orientation@boulogne.fr", attempts=3, max_attempts=5, created_delta_hours=8, retry_delta_hours=1, last_error="Boite distante indisponible"),
            _notification(job_id=314, status="retry", event_type="owner_alert", recipient="pilotage@territoire.fr", attempts=2, max_attempts=5, created_delta_hours=2, retry_delta_hours=1),
        ],
        "notification_channels": ["email"],
        "security_kpis": {"success_24h": 14, "failed_24h": 27, "distinct_failed_ips_24h": 6, "distinct_failed_usernames_24h": 5, "lockout_buckets_24h": 4, "risky_actions_24h": 6, "denied_24h": 11},
        "security_anomalies": {"spike_failed_logins": True, "repeated_fails_by_ip": True, "repeated_fails_by_username": True, "failed_1h": 9, "avg_hourly": 1.12, "spike_threshold": 10.0, "top_ip": "203.0.113.44", "top_ip_fails": 14, "top_username": "ops.crise", "top_username_fails": 9, "denied_spike": True, "repeated_denied": True, "denied_1h": 6, "avg_denied_hourly": 0.46, "top_denied_ip": "203.0.113.44", "top_denied_ip_count": 7, "top_denied_username": "ops.crise", "top_denied_username_count": 6},
        "security_recent_attempts": {
            "recent_logins": [_login_attempt(hours_ago=1, username="ops.crise", ip="203.0.113.44", success=False), _login_attempt(hours_ago=1, username="ops.crise", ip="203.0.113.44", success=False), _login_attempt(hours_ago=2, username="admin.ccas", ip="198.51.100.10", success=True)],
            "recent_risky": [_audit_event(hours_ago=1, action="ROLE_CHANGE", admin_username="superadmin", target_type="AdminUser", target_id=22, ip="198.51.100.4", payload={"old": "ops", "new": "admin"}), _audit_event(hours_ago=3, action="CREATE_REQUEST", admin_username="ops.crise", target_type="Request", target_id=111, ip="203.0.113.44")],
            "recent_denied": [_audit_event(hours_ago=1, action="security.denied_action", admin_username="ops.crise", target_type="Request", target_id=114, ip="203.0.113.44"), _audit_event(hours_ago=2, action="security.denied_action", admin_username="ops.lyon", target_type="Request", target_id=112, ip="203.0.113.52")],
            "recent_sensitive": [_audit_event(hours_ago=5, action="STRUCTURE_CREATED", admin_username="superadmin", target_type="Structure", target_id=12, ip="198.51.100.4")],
            "top_ips": [("203.0.113.44", 14), ("203.0.113.52", 7), ("198.51.100.61", 4)],
            "top_usernames": [("ops.crise", 9), ("ops.lyon", 6), ("admin.demo", 4)],
            "top_denied_ips": [("203.0.113.44", 7), ("203.0.113.52", 3)],
            "top_denied_usernames": [("ops.crise", 6), ("ops.lyon", 3)],
            "risky_actions": _base_actions(),
        },
        "audit_rows": [
            _audit_event(hours_ago=1, action="admin_login_failure", admin_username="ops.crise", target_type="AdminUser", target_id=18, ip="203.0.113.44", payload={"route": "login", "reason": "invalid_credentials"}),
            _audit_event(hours_ago=2, action="security.denied_action", admin_username="ops.lyon", target_type="Request", target_id=112, ip="203.0.113.52", payload={"attempted_action": "POST /admin/requests/112/delete"}),
            _audit_event(hours_ago=4, action="ASSIGN_OPERATOR", admin_username="superadmin", target_type="Request", target_id=113, ip="198.51.100.4", payload={"old": "none", "new": "Marie Dupont"}),
            _audit_event(hours_ago=6, action="CREATE_REQUEST", admin_username="ops.crise", target_type="Request", target_id=111, ip="203.0.113.44"),
        ],
        "audit_filters": {"action": "", "admin": "", "target_type": "", "target_id": "", "days": "7"},
        "audit_actions": ["admin_login_failure", "security.denied_action", "ASSIGN_OPERATOR", "CREATE_REQUEST"],
        "audit_target_types": ["AdminUser", "Request"],
        "sla_kpis": {"breach_label": "SLA crise sociale", "resolve_count": 3, "owner_assign_count": 4, "volunteer_assign_count": 1, "prediction_counts": {"resolution_overdue": 3, "owner_assignment_overdue": 4, "volunteer_assignment_overdue": 1}},
        "sla_rows": [
            {"id": 111, "title": "Famille sans hebergement immediat", "category": "logement", "status": "open", "created_at": now - timedelta(days=4), "owner_id": None, "assigned_volunteer_id": None, "overdue_hours": 39.0, "breach_type": "owner_assign"},
            {"id": 112, "title": "Sortie d'hospitalisation sans relais", "category": "sante", "status": "open", "created_at": now - timedelta(days=3, hours=8), "owner_id": None, "assigned_volunteer_id": None, "overdue_hours": 29.5, "breach_type": "resolve"},
            {"id": 114, "title": "Signalement expulsion imminente", "category": "logement", "status": "open", "created_at": now - timedelta(days=5), "owner_id": None, "assigned_volunteer_id": None, "overdue_hours": 48.0, "breach_type": "owner_assign"},
        ],
    }


def _build_surcharge_hiver_payload() -> dict[str, object]:
    now = _now()
    requests = [
        _request(request_id=121, title="Recherche d'hebergement d'urgence", city="Paris", category="logement", status="open", priority="high", owner_name=None, created_delta_hours=8, updated_delta_hours=3),
        _request(request_id=122, title="Menage en precarite energetique", city="Lyon", category="social", status="in_progress", priority="medium", owner_name="Marie Dupont", created_delta_hours=20, updated_delta_hours=10),
        _request(request_id=123, title="Sortie rue par temps froid", city="Boulogne", category="logement", status="open", priority="high", owner_name=None, created_delta_hours=34, updated_delta_hours=28),
        _request(request_id=124, title="Coordination sante et hebergement", city="Paris", category="sante", status="in_progress", priority="medium", owner_name="Nadia Bernard", created_delta_hours=42, updated_delta_hours=6),
    ]
    request_map = {req.id: req for req in requests}
    return {
        "scenario_meta": {"label": "Surcharge hiver", "short_description": "Hausse saisonniere du volume avec pression logement et hebergement."},
        "workspace_kpis": {"critical": 2, "unassigned": 2, "relance": 2, "notifications_failed": 1, "updated_today": 5, "retry_notifications": 2},
        "workspace_rows": requests,
        "workspace_queue_reasons": {121: ["Critique", "Logement"], 122: ["Coordination a suivre"], 123: ["Sans responsable", "Sans action recente"], 124: ["Sante", "Hiver"]},
        "workspace_priority_levels": {121: "critique", 122: "normal", 123: "eleve", 124: "eleve"},
        "cases_kpis": {"critical": 2, "attention": 2, "no_owner": 2, "stale": 1},
        "cases_rows": [
            _case(case_id=201, request_row=request_map[121], status="new", priority="critical", owner_name=None, last_activity_hours=3, risk_score=90),
            _case(case_id=202, request_row=request_map[122], status="in_progress", priority="high", owner_name="Marie Dupont", last_activity_hours=10, risk_score=76),
            _case(case_id=203, request_row=request_map[123], status="assigned", priority="high", owner_name=None, last_activity_hours=74, risk_score=88),
        ],
        "cases_signals": {201: ["URGENT", "HEBERGEMENT"], 202: ["A VERIFIER"], 203: ["SANS ACTION 72H", "NON ASSIGNE"]},
        "cases_priority_levels": {201: "critique", 202: "eleve", 203: "critique"},
        "notifications_kpis": {"pending": 1, "processing": 0, "done": 0, "dead_letter": 1, "retry": 2, "failed": 1, "sent": 2},
        "notification_rows": [
            _notification(job_id=321, status="pending", event_type="owner_alert", recipient="hebergement@paris.fr", attempts=1, created_delta_hours=2, retry_delta_hours=1),
            _notification(job_id=322, status="retry", event_type="reminder", recipient="astreinte@lyon.fr", attempts=2, max_attempts=5, created_delta_hours=4, retry_delta_hours=1),
            _notification(job_id=323, status="failed", event_type="email_send", recipient="nuit@boulogne.fr", attempts=3, max_attempts=5, created_delta_hours=7, retry_delta_hours=2, last_error="Serveur distant temporairement indisponible"),
        ],
        "notification_channels": ["email"],
        "security_kpis": {"success_24h": 11, "failed_24h": 8, "distinct_failed_ips_24h": 3, "distinct_failed_usernames_24h": 3, "lockout_buckets_24h": 1, "risky_actions_24h": 3, "denied_24h": 4},
        "security_anomalies": {"spike_failed_logins": False, "repeated_fails_by_ip": False, "repeated_fails_by_username": False, "failed_1h": 2, "avg_hourly": 0.33, "spike_threshold": 10.0, "top_ip": "203.0.113.80", "top_ip_fails": 4, "top_username": "ops.hiver", "top_username_fails": 3, "denied_spike": False, "repeated_denied": False, "denied_1h": 1, "avg_denied_hourly": 0.17, "top_denied_ip": "203.0.113.80", "top_denied_ip_count": 2, "top_denied_username": "ops.hiver", "top_denied_username_count": 2},
        "security_recent_attempts": {
            "recent_logins": [_login_attempt(hours_ago=1, username="ops.hiver", ip="203.0.113.80", success=False), _login_attempt(hours_ago=3, username="admin.ccas", ip="198.51.100.11", success=True), _login_attempt(hours_ago=5, username="ops.logement", ip="203.0.113.81", success=True)],
            "recent_risky": [_audit_event(hours_ago=4, action="STATUS_CHANGE", admin_username="ops.logement", target_type="Request", target_id=123, ip="203.0.113.81", payload={"old": "open", "new": "in_progress"})],
            "recent_denied": [_audit_event(hours_ago=2, action="security.denied_action", admin_username="ops.hiver", target_type="Request", target_id=121, ip="203.0.113.80")],
            "recent_sensitive": [_audit_event(hours_ago=6, action="ASSIGN_OPERATOR", admin_username="admin.ccas", target_type="Request", target_id=124, ip="198.51.100.11")],
            "top_ips": [("203.0.113.80", 4), ("203.0.113.81", 2)],
            "top_usernames": [("ops.hiver", 3), ("ops.logement", 2)],
            "top_denied_ips": [("203.0.113.80", 2)],
            "top_denied_usernames": [("ops.hiver", 2)],
            "risky_actions": _base_actions(),
        },
        "audit_rows": [
            _audit_event(hours_ago=2, action="security.denied_action", admin_username="ops.hiver", target_type="Request", target_id=121, ip="203.0.113.80", payload={"attempted_action": "POST /admin/requests/121/assign"}),
            _audit_event(hours_ago=4, action="STATUS_CHANGE", admin_username="ops.logement", target_type="Request", target_id=123, ip="203.0.113.81", payload={"old": "open", "new": "in_progress"}),
            _audit_event(hours_ago=9, action="ASSIGN_OPERATOR", admin_username="admin.ccas", target_type="Request", target_id=124, ip="198.51.100.11", payload={"old": "none", "new": "Nadia Bernard"}),
        ],
        "audit_filters": {"action": "", "admin": "", "target_type": "", "target_id": "", "days": "7"},
        "audit_actions": ["security.denied_action", "STATUS_CHANGE", "ASSIGN_OPERATOR"],
        "audit_target_types": ["Request"],
        "sla_kpis": {"breach_label": "SLA surcharge hiver", "resolve_count": 1, "owner_assign_count": 2, "volunteer_assign_count": 1, "prediction_counts": {"resolution_overdue": 2, "owner_assignment_overdue": 2, "volunteer_assignment_overdue": 1}},
        "sla_rows": [
            {"id": 121, "title": "Recherche d'hebergement d'urgence", "category": "logement", "status": "open", "created_at": now - timedelta(days=3), "owner_id": None, "assigned_volunteer_id": None, "overdue_hours": 18.0, "breach_type": "owner_assign"},
            {"id": 123, "title": "Sortie rue par temps froid", "category": "logement", "status": "open", "created_at": now - timedelta(days=4), "owner_id": None, "assigned_volunteer_id": None, "overdue_hours": 22.0, "breach_type": "resolve"},
        ],
    }


def get_demo_payload(scenario: str | None = None) -> dict[str, object]:
    scenario_name = get_demo_scenario_name(scenario)
    institutional_payload = None
    raw_scenario = (scenario or "").strip().lower()
    if raw_scenario in INSTITUTIONAL_UNIVERSES:
        institutional_payload = get_institutional_pilot_payload(raw_scenario)
    if institutional_payload is not None:
        institutional_payload["scenario_name"] = raw_scenario
        institutional_payload["audit_pagination"] = _pagination(
            len(institutional_payload.get("audit_rows") or [])
        )
        return institutional_payload
    if scenario_name == "crise_sociale":
        payload = _build_crise_sociale_payload()
    elif scenario_name == "surcharge_hiver":
        payload = _build_surcharge_hiver_payload()
    else:
        payload = _build_pilot_ccas_payload()
    payload["scenario_name"] = scenario_name
    payload["audit_pagination"] = _pagination(len(payload["audit_rows"]))
    return payload


def get_demo_kpis(scenario: str | None = None) -> dict[str, int]:
    return get_demo_payload(scenario)["workspace_kpis"]  # type: ignore[return-value]


def get_demo_requests(scenario: str | None = None) -> list[SimpleNamespace]:
    return get_demo_payload(scenario)["workspace_rows"]  # type: ignore[return-value]


def get_demo_queue_reasons(scenario: str | None = None) -> dict[int, list[str]]:
    return get_demo_payload(scenario)["workspace_queue_reasons"]  # type: ignore[return-value]


def get_demo_ops_priority_levels(scenario: str | None = None) -> dict[int, str]:
    return get_demo_payload(scenario)["workspace_priority_levels"]  # type: ignore[return-value]


def get_demo_cases(scenario: str | None = None) -> list[SimpleNamespace]:
    return get_demo_payload(scenario)["cases_rows"]  # type: ignore[return-value]


def get_demo_case_signals(scenario: str | None = None) -> dict[int, list[str]]:
    return get_demo_payload(scenario)["cases_signals"]  # type: ignore[return-value]


def get_demo_notifications(scenario: str | None = None) -> list[SimpleNamespace]:
    return get_demo_payload(scenario)["notification_rows"]  # type: ignore[return-value]


def get_demo_notification_summary(scenario: str | None = None) -> dict[str, int]:
    return get_demo_payload(scenario)["notifications_kpis"]  # type: ignore[return-value]


def get_demo_notification_channels(scenario: str | None = None) -> list[str]:
    return get_demo_payload(scenario)["notification_channels"]  # type: ignore[return-value]


def get_demo_sla_payload(scenario: str | None = None) -> dict[str, object]:
    payload = get_demo_payload(scenario)
    return {**payload["sla_kpis"], "rows": payload["sla_rows"]}  # type: ignore[arg-type]
