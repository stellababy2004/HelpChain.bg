from __future__ import annotations

from datetime import UTC, datetime
from secrets import token_urlsafe

from flask import current_app, request, session
from sqlalchemy import inspect as sa_inspect

from backend.extensions import db
from .telemetry_policy import (
    canonical_public_commercial_path,
    classify_public_telemetry_request,
    get_client_ip,
)

try:
    from backend.models_with_analytics import AnalyticsEvent, UserBehavior, utc_now
except Exception:  # pragma: no cover - analytics module is optional in some envs
    AnalyticsEvent = None
    UserBehavior = None

    def utc_now():
        return datetime.now(UTC).replace(tzinfo=None)


HIGH_INTENT_AUDIENCE_PATHS = {
    "/offre",
    "/deploiement",
    "/professionnels",
    "/professionnels/pilote",
    "/demander-acces",
    "/contact",
    "/demo",
    "/securite",
    "/confidentialite",
    "/architecture",
    "/cas-usage",
    "/pilotage-indicateurs",
    "/pour-les-structures",
}


def should_track_audience_page_view(path: str | None, method: str | None, status_code: int) -> bool:
    if method != "GET":
        return False
    if status_code >= 400:
        return False
    decision = classify_public_telemetry_request(
        path,
        user_agent=request.headers.get("User-Agent"),
        client_ip=get_client_ip(),
    )
    return decision.should_persist


def _analytics_tables_available() -> bool:
    if AnalyticsEvent is None or UserBehavior is None:
        return False
    try:
        inspector = sa_inspect(db.session.get_bind())
        return bool(
            inspector.has_table("analytics_events")
            and inspector.has_table("user_behaviors")
        )
    except Exception:
        return False


def _audience_session_id() -> str:
    sid = (session.get("hc_audience_sid") or "").strip()
    if not sid:
        sid = f"aud_{token_urlsafe(18)}"
        session["hc_audience_sid"] = sid
    return sid


def _device_type(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    if any(token in ua for token in ("mobile", "iphone", "android")):
        return "mobile"
    if any(token in ua for token in ("ipad", "tablet")):
        return "tablet"
    if ua:
        return "desktop"
    return "unknown"


def track_audience_page_view() -> bool:
    """Store a minimal page_view event for the founder audience map.

    Telemetry must never break the page response. This function therefore
    returns False on any unavailable table or write failure.
    """
    path = (request.path or "").rstrip("/") or "/"
    decision = classify_public_telemetry_request(
        path,
        user_agent=request.headers.get("User-Agent"),
        client_ip=get_client_ip(),
    )
    if not decision.should_persist:
        return False
    if not _analytics_tables_available():
        return False

    now = utc_now()
    session_id = _audience_session_id()
    referrer = (request.referrer or "").strip() or None
    user_agent = (request.headers.get("User-Agent") or "").strip()[:500] or None
    ip_address = (request.remote_addr or "").strip()[:45] or None
    device_type = _device_type(user_agent)
    canonical_path = canonical_public_commercial_path(path) or path

    try:
        event = AnalyticsEvent(
            event_type="page_view",
            event_category="audience",
            event_action="page_view",
            event_label="high_intent" if canonical_path in HIGH_INTENT_AUDIENCE_PATHS else "public",
            user_session=session_id,
            user_type="guest",
            user_ip=ip_address,
            user_agent=user_agent,
            page_url=canonical_path,
            referrer=referrer,
            device_type=device_type,
            created_at=now,
            updated_at=now,
        )
        db.session.add(event)

        behavior = UserBehavior.query.filter_by(session_id=session_id).first()
        if behavior is None:
            behavior = UserBehavior(
                session_id=session_id,
                user_type="guest",
                ip_address=ip_address,
                user_agent=user_agent,
                device_info=device_type,
                entry_page=canonical_path,
                session_start=now,
                last_activity=now,
                pages_visited=0,
            )
            db.session.add(behavior)
        behavior.pages_visited = (behavior.pages_visited or 0) + 1
        behavior.last_activity = now
        behavior.exit_page = canonical_path
        if behavior.pages_visited > 1:
            behavior.bounce_rate = False

        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        try:
            current_app.logger.info("Audience page_view tracking skipped: %s", exc)
        except Exception:
            pass
        return False
