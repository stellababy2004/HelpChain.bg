from __future__ import annotations
from backend.helpchain_backend.src.services.radar_v2 import compute_radar_v2

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from flask import session
from sqlalchemy import inspect as sa_inspect

from backend.extensions import db
from .institutional_intent import build_intent_summary, normalize_intent_path
from .territorial_intelligence import build_territory_summary, normalize_territory_name

try:
    from backend.models_with_analytics import AnalyticsEvent
except Exception:  # pragma: no cover - analytics is optional in some envs
    AnalyticsEvent = None


AUDIENCE_CONTEXT_START = "[audience_auto_capture]"
AUDIENCE_CONTEXT_END = "[/audience_auto_capture]"
AUDIENCE_SCORE_CAP = 50
AUDIENCE_INTENT_PATHS = (
    "/demander-acces",
    "/professionnels",
    "/offre",
    "/deploiement",
    "/contact",
)
AUDIENCE_PAGE_SCORES = (
    ("/demander-acces", 12),
    ("/contact", 8),
    ("/deploiement", 7),
    ("/offre", 6),
    ("/collectivites", 5),
    ("/professionnels", 4),
    ("/", 1),
)


def get_current_audience_session_id() -> str | None:
    sid = (session.get("hc_audience_sid") or "").strip()
    return sid or None


def _analytics_events_available() -> bool:
    if AnalyticsEvent is None:
        return False
    try:
        return sa_inspect(db.session.get_bind()).has_table("analytics_events")
    except Exception:
        return False


def _path(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "/"
    parsed = urlparse(raw)
    return parsed.path or raw.split("?", 1)[0] or "/"


def _source_label(referrer: str | None) -> str:
    raw = (referrer or "").strip()
    if not raw:
        return "Direct"
    parsed = urlparse(raw)
    domain = (parsed.netloc or raw).strip().lower()
    domain = domain.split("@")[-1].split(":", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or domain in {"direct", "none", "-"}:
        return "Direct"
    if "google." in domain:
        return "Google"
    if domain.endswith("linkedin.com") or "linkedin." in domain:
        return "LinkedIn"
    if domain == "chat.openai.com" or domain.endswith(".openai.com"):
        return "ChatGPT"
    return domain


def _is_external_referrer(referrer: str | None) -> bool:
    label = _source_label(referrer)
    return label != "Direct" and "helpchain" not in label.lower()


def _page_score(path: str | None) -> int:
    normalized = _path(path)
    for prefix, score in AUDIENCE_PAGE_SCORES:
        if prefix == "/" and normalized == "/":
            return score
        if prefix != "/" and normalized.startswith(prefix):
            return score
    return 0


def _temperature(score: int) -> str:
    if score >= 25:
        return "Tres chaud"
    if score >= 16:
        return "Chaud"
    if score >= 8:
        return "Tiede"
    return "Froid"


def _score_session(
    paths: list[str],
    *,
    page_count: int,
    last_seen_at: datetime | None,
    now: datetime,
    has_external_referrer: bool,
    repeated_same_day: bool,
) -> int:
    score = sum(_page_score(path) for path in paths)
    if repeated_same_day:
        score += 6
    if page_count >= 5:
        score += 10
    elif page_count >= 3:
        score += 5
    if last_seen_at and last_seen_at >= now - timedelta(hours=24):
        score += 4
    if has_external_referrer:
        score += 3
    if page_count == 1:
        score -= 5
    return max(0, min(AUDIENCE_SCORE_CAP, score))


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat()


def summarize_session_intelligence(
    session_id: str | None = None,
    *,
    now: datetime | None = None,
    lookback_days: int = 30,
    territory_hint: str | None = None,
    territory_source: str | None = None,
    source_kind: str = "session",
) -> dict | None:
    sid = (session_id or get_current_audience_session_id() or "").strip()

    if not sid:
        try:
            last_event = (
                db.session.query(AnalyticsEvent.user_session)
                .filter(AnalyticsEvent.event_type == "page_view")
                .filter(AnalyticsEvent.user_session.isnot(None))
                .order_by(AnalyticsEvent.created_at.desc(), AnalyticsEvent.id.desc())
                .first()
            )
            if last_event and last_event.user_session:
                sid = str(last_event.user_session).strip()
        except Exception:
            return None

    if not sid:
        return None

    now = now or datetime.now(UTC).replace(tzinfo=None)
    since = now - timedelta(days=lookback_days)
    rows = (
        db.session.query(
            AnalyticsEvent.created_at,
            AnalyticsEvent.page_url,
            AnalyticsEvent.referrer,
        )
        .filter(AnalyticsEvent.event_type == "page_view")
        .filter(AnalyticsEvent.user_session == sid)
        .filter(AnalyticsEvent.created_at >= since)
        .order_by(AnalyticsEvent.created_at.asc(), AnalyticsEvent.id.asc())
        .limit(100)
        .all()
    )
    if not rows:
        return None

    has_submit = bool(
        db.session.query(AnalyticsEvent.id)
        .filter(AnalyticsEvent.user_session == sid)
        .filter(AnalyticsEvent.created_at >= since)
        .filter(AnalyticsEvent.event_type.like("%_form_submit"))
        .limit(1)
        .first()
    )
    paths = [normalize_intent_path(row.page_url) or _path(row.page_url) for row in rows]
    unique_pages = list(dict.fromkeys(paths))
    day_counts = Counter(row.created_at.date() for row in rows if row.created_at)
    source = "Direct"
    for row in rows:
        label = _source_label(row.referrer)
        if label != "Direct":
            source = label
            break
    first_seen_at = min((row.created_at for row in rows if row.created_at), default=None)
    last_seen_at = max((row.created_at for row in rows if row.created_at), default=None)
    score = _score_session(
        paths,
        page_count=len(paths),
        last_seen_at=last_seen_at,
        now=now,
        has_external_referrer=any(_is_external_referrer(row.referrer) for row in rows),
        repeated_same_day=any(count >= 2 for count in day_counts.values()),
    )
    intent_flags = {
        "visited_offre": any(path.startswith("/offre") for path in paths),
        "visited_deploiement": any(path.startswith("/deploiement") for path in paths),
        "visited_professionnels": any(path.startswith("/professionnels") for path in paths),
        "visited_demander_acces": any(path.startswith("/demander-acces") for path in paths),
        "visited_contact": any(path.startswith("/contact") for path in paths),
    }
    intent_summary = build_intent_summary(unique_pages, has_submit=has_submit)
    territory_summary = None
    normalized_territory = normalize_territory_name(territory_hint)
    if normalized_territory:
        territory_summary = build_territory_summary(
            [
                {
                    "territory": normalized_territory,
                    "territory_source": (territory_source or "organization_hint").strip() or "organization_hint",
                    "source_kind": (source_kind or "session").strip() or "session",
                    "session_id": sid,
                    "paths": unique_pages,
                    "intent_tier": intent_summary["tier"],
                    "primary_interest": intent_summary["primary_interest"],
                    "trust_friction_detected": bool(intent_summary["trust_friction_detected"]),
                    "repeat_visit": len(paths) > 1 or len(day_counts) > 1,
                }
            ]
        )
    return {
        "version": 1,
        "captured_at": _iso(now),
        "session_id": sid,
        "score": score,
        "temperature": _temperature(score),
        "first_seen_at": _iso(first_seen_at),
        "last_seen_at": _iso(last_seen_at),
        "page_count": len(paths),
        "pages_viewed": unique_pages,
        "key_pages": [path for path in unique_pages if any(path.startswith(p) for p in AUDIENCE_INTENT_PATHS)],
        "source": source,
        "repeat_visit": len(paths) > 1 or len(day_counts) > 1,
        "repeat_visit_count": max(0, len(paths) - 1),
        "intent_flags": intent_flags,
        "institutional_intent": intent_summary,
        "lead_intent_score": int(intent_summary["score"]),
        "lead_intent_tier": intent_summary["tier"],
        "lead_intent_label": intent_summary["label"],
        "primary_interest": intent_summary["primary_interest"],
        "recommended_action": intent_summary["recommended_action"],
        "trust_friction_detected": bool(intent_summary["trust_friction_detected"]),
        "friction_reason": intent_summary["friction_reason"],
        "territorial_intelligence": territory_summary,
    }


def _strip_existing_audience_context(notes: str | None) -> str:
    text = (notes or "").strip()
    if not text or AUDIENCE_CONTEXT_START not in text:
        return text
    before, _, rest = text.partition(AUDIENCE_CONTEXT_START)
    _, end_found, after = rest.partition(AUDIENCE_CONTEXT_END)
    if not end_found:
        return before.strip()
    return "\n\n".join(part.strip() for part in (before, after) if part.strip())


def format_audience_context_note(summary: dict) -> str:
    payload = json.dumps(summary, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"{AUDIENCE_CONTEXT_START}\n{payload}\n{AUDIENCE_CONTEXT_END}"


def append_audience_context_to_notes(notes: str | None, summary: dict | None) -> str | None:
    if not summary:
        return notes
    base = _strip_existing_audience_context(notes)
    block = format_audience_context_note(summary)
    if base:
        return f"{base}\n\n{block}"
    return block


def notes_without_audience_context(notes: str | None) -> str:
    return _strip_existing_audience_context(notes)


def extract_audience_context(notes: str | None) -> dict | None:
    text = notes or ""
    if AUDIENCE_CONTEXT_START not in text:
        return None
    _, _, rest = text.partition(AUDIENCE_CONTEXT_START)
    payload, end_found, _ = rest.partition(AUDIENCE_CONTEXT_END)
    if not end_found:
        return None
    try:
        parsed = json.loads(payload.strip())
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def attach_session_intelligence_to_access_request(access_request) -> dict | None:
    summary = summarize_session_intelligence(
        territory_hint=getattr(access_request, "city", None),
        territory_source="access_request_city",
        source_kind="access_request",
    )
    if not summary:
        return None
    access_request.internal_notes = append_audience_context_to_notes(
        getattr(access_request, "internal_notes", None),
        summary,
    )
    return summary


def attach_session_intelligence_to_professional_lead(lead) -> dict | None:
    summary = summarize_session_intelligence(
        territory_hint=getattr(lead, "city", None),
        territory_source="professional_lead_city",
        source_kind="professional_lead",
    )
    if not summary:
        return None

    new_notes = append_audience_context_to_notes(getattr(lead, "notes", None), summary)
    lead.notes = new_notes

    try:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(lead, "notes")
    except Exception:
        pass

    return summary


def captured_audience_session_targets() -> dict[str, str]:
    targets: dict[str, str] = {}
    try:
        inspector = sa_inspect(db.session.get_bind())
        if inspector.has_table("organization_access_requests"):
            from ..models import OrganizationAccessRequest

            for row in db.session.query(OrganizationAccessRequest.id, OrganizationAccessRequest.internal_notes).all():
                context = extract_audience_context(row.internal_notes)
                sid = (context or {}).get("session_id")
                if sid:
                    targets[str(sid)] = "Lie a une demande d'acces"
        if inspector.has_table("professional_leads"):
            from ..models import ProfessionalLead

            for row in db.session.query(ProfessionalLead.id, ProfessionalLead.notes).all():
                context = extract_audience_context(row.notes)
                sid = (context or {}).get("session_id")
                if sid and sid not in targets:
                    targets[str(sid)] = "Lie a un lead professionnel"
    except Exception:
        return targets
    return targets




