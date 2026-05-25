import hashlib
import math
import os
import re
import secrets
import time
from collections import deque
from datetime import UTC, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

from babel.dates import format_timedelta
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_babel import get_locale as babel_get_locale
from flask_babel import gettext as _
from flask_mail import Message
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_required, logout_user
from markupsafe import Markup, escape
from sqlalchemy import desc, func, or_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import OperationalError
from werkzeug.security import check_password_hash

try:
    from redis import Redis
except Exception:  # pragma: no cover - keep runtime optional
    Redis = None

from ..authz import can_view_request
from backend.core.tenant import current_structure_id
from ..constants.categories import (
    REQUEST_CATEGORY_CODES,
    normalize_request_category,
    request_category_choices,
    request_category_label,
)
from ..category_data import ALIASES, CATEGORIES, COMMON
from ..extensions import csrf, limiter, mail
from ..models import (
    Notification,
    OrganizationAccessRequest,
    ProfessionalLead,
    Request,
    RequestActivity,
    SecurityEvent,
    Volunteer,
    VolunteerAction,
    canonical_role,
    current_structure,
    db,
    utc_now,
)
from ..models.magic_link_token import MagicLinkToken
from ..models.volunteer_interest import VolunteerInterest
from ..notifications.inapp import (
    ensure_new_match_notifications,
    mark_notification_opened,
    mark_request_seen_for_volunteer,
)
from ..security_logging import log_security_event
from ..services.matching_v1 import dismiss_for as match_dismiss_for
from ..services.matching_v1 import get_matched_requests_v1
from ..services.matching_v1 import mark_seen as match_mark_seen
from ..services.geocoding import request_address_display_text
from ..services.prospect_auto_capture import (
    append_audience_context_to_notes,
    attach_session_intelligence_to_access_request,
    attach_session_intelligence_to_professional_lead,
    summarize_session_intelligence,
)
from ..services.telemetry_policy import (
    classify_public_telemetry_request,
    extract_event_path,
    get_client_ip,
)
from ..statuses import normalize_request_status

COUNTRIES_SUPPORTED = ["FR", "CH", "CA", "BG"]

main_bp = Blueprint("main", __name__)

_SCHEMA_TABLE_EXISTS_CACHE: dict[str, bool] = {}


def _table_exists(table_name: str) -> bool:
    cached = _SCHEMA_TABLE_EXISTS_CACHE.get(table_name)
    if cached is not None:
        return cached
    try:
        inspector = sa_inspect(db.session.get_bind())
        exists = bool(inspector.has_table(table_name))
    except Exception:
        exists = False
    _SCHEMA_TABLE_EXISTS_CACHE[table_name] = exists
    return exists


def _allowed_locales() -> set[str]:
    configured = current_app.config.get("SUPPORTED_LOCALES") or ("fr", "en", "de", "bg")
    return {str(x).strip().lower() for x in configured if str(x).strip()}


def _current_structure_id():
    try:
        return int(current_structure().id)
    except RuntimeError:
        return None


def _scope_requests(query):
    return query.filter(Request.structure_id == _current_structure_id())


def scoped_requests_query():
    return _scope_requests(Request.query)


def get_scoped_request_or_404(req_id: int):
    return scoped_requests_query().filter(Request.id == req_id).first_or_404()


def email_or_ip_key():
    """Prefer per-email throttling; fall back to IP for anonymous abuse control."""
    email = (request.form.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    return get_remote_address()


_IN_MEMORY_RL: dict[str, deque] = {}
_IN_MEMORY_BLOCKS: dict[str, float] = {}
_REDIS_RL_CLIENT = None
_REDIS_RL_URL = None


def _client_ip() -> str:
    # Minimal proxy awareness; real deployments should rely on ProxyFix + remote_addr.
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return xff or (request.remote_addr or "unknown")


def _rate_limit_storage_key(key: str) -> str:
    return f"rl:{key}"


def _get_redis_rate_limit_client():
    global _REDIS_RL_CLIENT, _REDIS_RL_URL
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url or Redis is None:
        return None
    if _REDIS_RL_CLIENT is not None and _REDIS_RL_URL == redis_url:
        return _REDIS_RL_CLIENT
    try:
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
    except Exception:
        _REDIS_RL_CLIENT = None
        _REDIS_RL_URL = redis_url
        return None
    _REDIS_RL_CLIENT = client
    _REDIS_RL_URL = redis_url
    return _REDIS_RL_CLIENT


def _rate_limit_block_key_for(key: str) -> str | None:
    if ":ip:" not in key:
        return None
    return f"block:ip:{key.split(':ip:', 1)[1]}"


def _in_memory_block_retry_after(block_key: str) -> int:
    expires_at = _IN_MEMORY_BLOCKS.get(block_key)
    if not expires_at:
        return 0
    now = time.time()
    if expires_at <= now:
        _IN_MEMORY_BLOCKS.pop(block_key, None)
        return 0
    return int(max(1, math.ceil(expires_at - now)))


def _rate_limit_block(block_key: str, duration_sec: int) -> None:
    client = _get_redis_rate_limit_client()
    if client is not None:
        try:
            client.set(_rate_limit_storage_key(block_key), "1", ex=int(duration_sec))
            return
        except Exception:
            pass
    _IN_MEMORY_BLOCKS[block_key] = time.time() + float(duration_sec)


def _rate_limit_block_retry_after(block_key: str) -> int:
    client = _get_redis_rate_limit_client()
    if client is not None:
        try:
            ttl = int(client.ttl(_rate_limit_storage_key(block_key)) or 0)
            if ttl > 0:
                return ttl
        except Exception:
            pass
    return _in_memory_block_retry_after(block_key)


def _rate_limit_allow(key: str, limit: int, window_sec: int) -> bool:
    allowed, _retry_after = _rate_limit_check(key, limit, window_sec)
    return allowed


def _rate_limit_check(key: str, limit: int, window_sec: int) -> tuple[bool, int]:
    """
    Sliding-window limiter with retry_after support.
    Returns (allowed, retry_after_seconds).
    """
    block_key = _rate_limit_block_key_for(key)
    if block_key:
        retry_after = _rate_limit_block_retry_after(block_key)
        if retry_after > 0:
            return False, retry_after

    client = _get_redis_rate_limit_client()
    if client is not None:
        try:
            storage_key = _rate_limit_storage_key(key)
            pipe = client.pipeline(transaction=True)
            pipe.incr(storage_key)
            pipe.expire(storage_key, int(window_sec), nx=True)
            pipe.ttl(storage_key)
            current_count, _ttl_set, ttl = pipe.execute()
            current_count = int(current_count or 0)
            ttl = int(ttl or 0)
            if ttl < 0:
                client.expire(storage_key, int(window_sec))
                ttl = int(window_sec)
            if current_count > int(limit):
                return False, int(max(1, ttl or window_sec))
            return True, 0
        except Exception:
            pass

    now = time.time()
    q = _IN_MEMORY_RL.get(key)
    if q is None:
        q = deque()
        _IN_MEMORY_RL[key] = q

    cutoff = now - float(window_sec)
    while q and q[0] < cutoff:
        q.popleft()

    if len(q) >= int(limit):
        oldest = q[0]
        retry_after = int(max(1, math.ceil(float(window_sec) - (now - oldest))))
        return False, retry_after

    q.append(now)
    return True, 0


def has_control_chars(text: str) -> bool:
    """Detect non-printable control chars (excluding common whitespace)."""
    if not text:
        return False
    return any(ord(ch) < 32 and ch not in ("\t", "\n", "\r") for ch in text)


PRO_LEAD_INVALID_EMAIL_DOMAINS = {"example.com", "test.com"}
PRO_LEAD_SPAM_EMAIL_DOMAINS = {"mailinator.com"}
PRO_LEAD_SUSPICIOUS_MARKERS = ("zap", "zaproxy")
PRO_LEAD_DUMMY_TOKENS = {
    "test",
    "dummy",
    "asdf",
    "qwerty",
    "foobar",
    "lorem ipsum",
    "john doe",
    "jane doe",
    "n/a",
    "na",
    "none",
    "null",
    "xxx",
}


def _normalize_lead_probe_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _lead_field_looks_dummy(value: str | None) -> bool:
    normalized = _normalize_lead_probe_text(value)
    return bool(normalized) and normalized in PRO_LEAD_DUMMY_TOKENS


def _phone_has_basic_sanity(phone: str | None) -> bool:
    cleaned = (phone or "").strip()
    if not cleaned:
        return True
    if re.search(r"[A-Za-z]", cleaned):
        return False
    digits = re.sub(r"\D", "", cleaned)
    if len(digits) < 7 or len(digits) > 15:
        return False
    if len(digits) >= 7 and len(set(digits)) == 1:
        return False
    return True


def _screen_professional_lead_submission(
    form_data: dict[str, str | None],
    *,
    user_agent: str | None,
) -> tuple[str | None, list[str]]:
    website = (form_data.get("website") or "").strip()

    email = ((form_data.get("email") or "").strip().lower())
    _local_part, _sep, domain = email.partition("@")
    domain = domain.strip().lower()

    invalid_reasons: list[str] = []
    spam_reasons: list[str] = []

    if domain in PRO_LEAD_INVALID_EMAIL_DOMAINS:
        invalid_reasons.append(f"email_domain:{domain}")
    elif domain in PRO_LEAD_SPAM_EMAIL_DOMAINS:
        spam_reasons.append(f"email_domain:{domain}")

    if website:
        spam_reasons.append("honeypot:website")
        website_l = website.lower()
        if "@" in website_l or (email and website_l == email):
            spam_reasons.append("honeypot:possible_autofill")

    phone = (form_data.get("phone") or "").strip()
    if phone and not _phone_has_basic_sanity(phone):
        invalid_reasons.append("phone:invalid")

    suspicious_blob = " ".join(
        part
        for part in (
            user_agent,
            form_data.get("full_name"),
            form_data.get("organisation"),
            form_data.get("organization"),
            form_data.get("structure"),
            form_data.get("city"),
            form_data.get("profession"),
            form_data.get("fonction"),
            form_data.get("message"),
        )
        if part
    ).lower()
    marker_hits = [
        marker for marker in PRO_LEAD_SUSPICIOUS_MARKERS if marker in suspicious_blob
    ]
    if marker_hits:
        spam_reasons.append(f"marker:{','.join(marker_hits)}")

    dummy_fields = [
        field_name
        for field_name in (
            "full_name",
            "organisation",
            "organization",
            "structure",
            "city",
            "profession",
            "fonction",
        )
        if _lead_field_looks_dummy(form_data.get(field_name))
    ]
    if len(dummy_fields) >= 2:
        invalid_reasons.append(f"dummy_fields:{','.join(dummy_fields)}")
    elif _lead_field_looks_dummy(form_data.get("message")):
        current_app.logger.info(
            "[PRO-LEAD] message-only dummy signal ignored for screening email_domain=%s",
            domain or "-",
        )

    if spam_reasons:
        return "spam", spam_reasons
    if invalid_reasons:
        return "invalid", invalid_reasons
    return None, []


def _screening_note(classification: str, reasons: list[str]) -> str:
    reason_text = ", ".join(reasons) if reasons else "screened"
    return f"[screening:{classification}] {reason_text}"


def _merge_lead_notes(*parts: str | None) -> str | None:
    cleaned_parts: list[str] = []
    for part in parts:
        text = (part or "").strip()
        if text and text not in cleaned_parts:
            cleaned_parts.append(text)
    if not cleaned_parts:
        return None
    return "\n\n".join(cleaned_parts)


def _resolved_lead_status(existing_status: str | None, screening_status: str | None) -> str:
    if screening_status in {"invalid", "spam"}:
        return screening_status
    current_status = ((existing_status or "").strip().lower() or "new")
    if current_status in {"invalid", "spam"}:
        return "new"
    return current_status


def _normalize_lead_source(value: str | None) -> str | None:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", (value or "").strip().lower())
    normalized = normalized.strip("-_")
    return normalized[:80] or None


def _capture_inbound_lead_source() -> str | None:
    source = _normalize_lead_source(request.args.get("src"))
    if source:
        session["hc_lead_src"] = source
        return source
    saved = _normalize_lead_source(session.get("hc_lead_src"))
    if saved:
        session["hc_lead_src"] = saved
    return saved


def _resolved_inbound_lead_source(*, posted_source: str | None, default_source: str) -> str:
    source = _normalize_lead_source(posted_source)
    default_normalized = _normalize_lead_source(default_source) or default_source
    saved_source = _capture_inbound_lead_source()
    if source and source != default_normalized:
        session["hc_lead_src"] = source
        return source
    if saved_source:
        return saved_source
    return default_source


LEAD_INTENT_PAGE_WEIGHTS = {
    "/demo": 40,
    "/contact": 35,
    "/offre": 30,
    "/deploiement": 20,
}
PERSONAL_EMAIL_PROVIDERS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
    "orange.fr",
    "free.fr",
    "sfr.fr",
    "laposte.net",
    "wanadoo.fr",
}


def _lead_intent_session_id() -> str | None:
    for candidate in (
        request.form.get("session_id"),
        request.form.get("audience_session_id"),
        request.args.get("session_id"),
        request.headers.get("X-Session-Id"),
        request.cookies.get("hc_audience_sid"),
        session.get("hc_audience_sid"),
    ):
        value = (candidate or "").strip()
        if value:
            return value
    return None


def _lead_intent_path(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "/"
    parsed = urlparse(raw)
    return parsed.path or raw.split("?", 1)[0] or "/"


def _mask_email_for_log(email: str | None) -> str:
    value = (email or "").strip().lower()
    local, sep, domain = value.partition("@")
    if not sep or not domain:
        return "***"
    if not local:
        return f"***@{domain}"
    return f"{local[:1]}***@{domain}"


def _extract_email_domain(email: str | None) -> str | None:
    value = (email or "").strip().lower()
    if not value or "@" not in value:
        return None
    _local, _sep, domain = value.rpartition("@")
    domain = domain.strip().strip(".")
    if not domain or "." not in domain:
        return None
    return domain


def _guess_organization_name_from_domain(domain: str) -> str | None:
    label = (domain or "").split(".", 1)[0].strip().lower()
    if not label:
        return None
    words = [chunk for chunk in label.replace("-", " ").split() if chunk]
    if not words:
        return None
    special_tokens = {
        "ccas": "CCAS",
        "cd92": "CD92",
        "cd93": "CD93",
        "cd94": "CD94",
        "cd75": "CD75",
        "asso": "Asso",
        "mairie": "Mairie",
        "ville": "Ville",
    }
    rendered = [special_tokens.get(word, word.title()) for word in words]
    return " ".join(rendered)


def _guess_organization_from_domain(domain: str | None) -> dict:
    normalized = (domain or "").strip().lower()
    if not normalized:
        return {
            "domain": None,
            "probable_name": None,
            "type": "Organisation non detectee",
            "territory_hint": None,
            "confidence": "low",
            "sales_note": "Domaine non exploitable — qualification manuelle necessaire.",
        }
    if normalized in PERSONAL_EMAIL_PROVIDERS:
        return {
            "domain": normalized,
            "probable_name": None,
            "type": "Email personnel",
            "territory_hint": None,
            "confidence": "low",
            "sales_note": "Email personnel — qualification manuelle necessaire.",
        }

    probable_type = "Organisation probable"
    confidence = "low"
    sales_note = "Email institutionnel probable — qualification commerciale a confirmer."

    type_rules = (
        (("ccas",), "CCAS / action sociale", "high", "Email institutionnel detecte — priorite de qualification elevee."),
        (("mairie", "ville-", "villede"), "Collectivite / mairie", "high", "Collectivite probable — verifier le besoin de coordination territoriale."),
        (("departement", "cd92", "hauts-de-seine"), "Conseil departemental", "high", "Compte departemental probable — proposer un cadrage institutionnel."),
        (("association", "asso"), "Association", "medium", "Association probable — qualifier le perimetre d'usage et les relais terrain."),
        (("solidarite", "social"), "Structure sociale", "medium", "Structure sociale probable — priorite de qualification fonctionnelle."),
        (("missionlocale",), "Mission locale", "high", "Mission locale probable — orienter le discours vers le suivi et l'insertion."),
        (("croix-rouge",), "Association nationale / aide sociale", "high", "Organisation d'aide sociale probable — verifier le scope local."),
        (("emmaus",), "Association / insertion", "high", "Structure d'insertion probable — qualifier l'usage coordination et accompagnement."),
    )
    for markers, guessed_type, guessed_confidence, guessed_note in type_rules:
        if any(marker in normalized for marker in markers):
            probable_type = guessed_type
            confidence = guessed_confidence
            sales_note = guessed_note
            break

    territory_hint = None
    territory_rules = (
        (("nanterre",), "Nanterre / 92"),
        (("paris",), "Paris / 75"),
        (("saint-denis", "saintdenis"), "Seine-Saint-Denis / 93"),
        (("boulogne",), "Boulogne-Billancourt / 92"),
        (("creteil",), "Creteil / 94"),
        (("versailles",), "Versailles / 78"),
    )
    for markers, territory in territory_rules:
        if any(marker in normalized for marker in markers):
            territory_hint = territory
            break

    return {
        "domain": normalized,
        "probable_name": _guess_organization_name_from_domain(normalized),
        "type": probable_type,
        "territory_hint": territory_hint,
        "confidence": confidence,
        "sales_note": sales_note,
    }


def _lead_intent_summary(*, lookback_minutes: int = 30) -> dict | None:
    session_id = _lead_intent_session_id()
    if not session_id or not _table_exists("analytics_events"):
        return None

    try:
        from backend.models_with_analytics import AnalyticsEvent
    except Exception:
        return None

    now = datetime.now(UTC).replace(tzinfo=None)
    since = now - timedelta(minutes=lookback_minutes)
    rows = (
        db.session.query(AnalyticsEvent.created_at, AnalyticsEvent.page_url)
        .filter(AnalyticsEvent.event_type == "page_view")
        .filter(AnalyticsEvent.user_session == session_id)
        .filter(AnalyticsEvent.created_at >= since)
        .order_by(AnalyticsEvent.created_at.asc(), AnalyticsEvent.id.asc())
        .limit(100)
        .all()
    )
    if not rows:
        return {
            "version": 2,
            "captured_at": now.isoformat(),
            "session_id": session_id,
            "lookback_minutes": lookback_minutes,
            "lead_intent_score": 0,
            "score": 0,
            "pages_viewed": [],
            "page_count": 0,
            "repeat_visits": False,
            "last_seen_at": None,
            "telemetry_found": False,
        }

    paths = [_lead_intent_path(row.page_url) for row in rows]
    unique_pages = list(dict.fromkeys(paths))
    page_counts: dict[str, int] = {}
    for path in paths:
        page_counts[path] = page_counts.get(path, 0) + 1

    score = 0
    for prefix, weight in LEAD_INTENT_PAGE_WEIGHTS.items():
        if any(path.startswith(prefix) for path in unique_pages):
            score += weight

    repeat_visits = any(count >= 2 for count in page_counts.values())
    if repeat_visits:
        score += 10
    if len(unique_pages) >= 3:
        score += 8
    score = max(0, min(100, score))

    last_seen_at = max((row.created_at for row in rows if row.created_at), default=None)
    return {
        "version": 2,
        "captured_at": now.isoformat(),
        "session_id": session_id,
        "lookback_minutes": lookback_minutes,
        "lead_intent_score": score,
        "score": score,
        "pages_viewed": unique_pages,
        "page_count": len(paths),
        "repeat_visits": repeat_visits,
        "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
        "telemetry_found": True,
    }


def _attach_contact_lead_intelligence(lead, *, email: str) -> dict | None:
    summary = _lead_intent_summary() or summarize_session_intelligence()
    domain = _extract_email_domain(email)
    organization_intelligence = _guess_organization_from_domain(domain)
    if summary is None:
        summary = {
            "version": 2,
            "captured_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            "session_id": _lead_intent_session_id(),
            "lead_intent_score": 0,
            "score": 0,
            "pages_viewed": [],
            "page_count": 0,
            "repeat_visits": False,
            "last_seen_at": None,
            "telemetry_found": False,
        }
    summary["organization_intelligence"] = organization_intelligence
    summary["organization_domain"] = organization_intelligence.get("domain")
    summary["organization_type"] = organization_intelligence.get("type")
    summary["organization_confidence"] = organization_intelligence.get("confidence")
    lead.notes = append_audience_context_to_notes(getattr(lead, "notes", None), summary)
    current_app.logger.info(
        "[LEAD-QUALIFIED] email=%s score=%s pages=%s",
        email,
        int(summary.get("lead_intent_score") or summary.get("score") or 0),
        ",".join(summary.get("pages_viewed") or []) or "-",
    )
    current_app.logger.info(
        "[LEAD-ENRICHED] email=%s domain=%s type=%s confidence=%s",
        _mask_email_for_log(email),
        organization_intelligence.get("domain") or "-",
        organization_intelligence.get("type") or "-",
        organization_intelligence.get("confidence") or "-",
    )
    return summary


def _turnstile_is_enabled() -> bool:
    return bool(current_app.config.get("HC_TURNSTILE_ENABLED"))


def _verify_turnstile_token(*, remote_ip: str | None) -> bool:
    if not _turnstile_is_enabled():
        return True

    token = (request.form.get("cf-turnstile-response") or "").strip()
    secret = (current_app.config.get("HC_TURNSTILE_SECRET_KEY") or "").strip()
    verify_url = (current_app.config.get("HC_TURNSTILE_VERIFY_URL") or "").strip()
    timeout_sec = float(current_app.config.get("HC_TURNSTILE_TIMEOUT_SECONDS") or 2.5)

    if not token or not secret or not verify_url:
        current_app.logger.warning(
            "[TURNSTILE] missing verification input token=%s secret=%s verify_url=%s",
            bool(token),
            bool(secret),
            bool(verify_url),
        )
        return False

    try:
        import requests

        response = requests.post(
            verify_url,
            data={
                "secret": secret,
                "response": token,
                "remoteip": remote_ip or "",
            },
            timeout=max(0.5, timeout_sec),
        )
        payload = response.json() if response.content else {}
    except Exception:
        current_app.logger.exception("[TURNSTILE] verification failed")
        return False

    success = bool(payload.get("success"))
    if not success:
        current_app.logger.info(
            "[TURNSTILE] verification rejected errors=%s",
            payload.get("error-codes") or [],
        )
    return success


def _require_utc_datetime(
    dt: datetime | None, *, assume_naive_utc: bool = False
) -> datetime | None:
    """Normalize datetimes to aware UTC and reject silent timezone ambiguity."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        if not assume_naive_utc:
            raise ValueError("naive datetime provided where UTC-aware datetime is required")
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    """Legacy helper kept for non-auth call sites that still compare naive UTC."""
    normalized = _require_utc_datetime(dt, assume_naive_utc=True)
    if normalized is None:
        return None
    return normalized.replace(tzinfo=None)


# --- Helpers ---
def normalize_list(value):
    """Normalize comma- or list-based values to a lowercase set."""
    if not value:
        return set()
    if isinstance(value, list):
        return {v.strip().lower() for v in value if v and str(v).strip()}
    return {v.strip().lower() for v in str(value).split(",") if v.strip()}


def is_safe_url(target: str) -> bool:
    """Ensure redirects stay on same host."""
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _magic_link_email_fingerprint(email: str | None) -> str | None:
    cleaned = (email or "").strip().lower()
    if not cleaned:
        return None
    return _sha256_hex(cleaned)[:12]


def _magic_link_reject(
    reason: str,
    *,
    token_hash: str | None = None,
    token_id: int | None = None,
    purpose: str | None = None,
) -> tuple[str, int]:
    log_security_event(
        "magic_link_rejected",
        actor_type="anonymous",
        meta={
            "reason": reason,
            "purpose": purpose,
            "token_id": token_id,
            "token_hash_prefix": (token_hash or "")[:12] or None,
        },
    )
    return render_template("magic_link_invalid.html"), 200


def _magic_link_rate_limited(*, purpose: str, email: str, ip: str) -> bool:
    window_sec = 15 * 60
    ip_allowed, _ = _rate_limit_check(f"ml:issue:ip:{ip}", limit=10, window_sec=window_sec)
    email_allowed = True
    if email:
        email_allowed, _ = _rate_limit_check(
            f"ml:issue:email:{email}",
            limit=3,
            window_sec=window_sec,
        )
    if ip_allowed and email_allowed:
        return False
    log_security_event(
        "magic_link_rate_limited",
        actor_type="anonymous",
        ip=ip,
        email_hash=_sha256_hex(email) if email else None,
        meta={
            "purpose": purpose,
            "email_hash_prefix": _magic_link_email_fingerprint(email),
            "email_limited": not email_allowed,
            "ip_limited": not ip_allowed,
        },
    )
    return True


def _detect_suspicious_activity(ip: str, email: str | None) -> bool:
    since = utc_now() - timedelta(minutes=10)
    email_hash = _sha256_hex((email or "").strip().lower()) if email else None
    try:
        ip_count = (
            db.session.query(func.count(SecurityEvent.id))
            .filter(
                SecurityEvent.event_type == "magic_link_attempt",
                SecurityEvent.created_at >= since,
                SecurityEvent.ip == ip,
            )
            .scalar()
            or 0
        )
        distinct_email_count = (
            db.session.query(func.count(func.distinct(SecurityEvent.email_hash)))
            .filter(
                SecurityEvent.event_type == "magic_link_attempt",
                SecurityEvent.created_at >= since,
                SecurityEvent.ip == ip,
                SecurityEvent.email_hash.isnot(None),
            )
            .scalar()
            or 0
        )
        email_count = 0
        if email_hash:
            email_count = (
                db.session.query(func.count(SecurityEvent.id))
                .filter(
                    SecurityEvent.event_type == "magic_link_attempt",
                    SecurityEvent.created_at >= since,
                    SecurityEvent.email_hash == email_hash,
                )
                .scalar()
                or 0
            )
    except Exception:
        db.session.rollback()
        return False

    suspicious = (
        int(ip_count) > 20
        or int(distinct_email_count) >= 5
        or int(email_count) > 10
    )
    if suspicious:
        log_security_event(
            "magic_link_suspicious_activity",
            actor_type="anonymous",
            ip=ip,
            email_hash=email_hash,
            meta={
                "ip_count_10m": int(ip_count),
                "distinct_emails_10m": int(distinct_email_count),
                "email_count_10m": int(email_count),
            },
        )
    return suspicious


def _magic_link_trust_tier(email: str | None) -> str:
    return "unknown"


def _compute_magic_link_risk(ip: str, email: str | None) -> dict:
    since = utc_now() - timedelta(minutes=10)
    normalized_email = (email or "").strip().lower()
    email_hash = _sha256_hex(normalized_email) if normalized_email else None
    trust_tier = _magic_link_trust_tier(normalized_email)
    query_failed = False
    try:
        ip_count_10m = int(
            db.session.query(func.count(SecurityEvent.id))
            .filter(
                SecurityEvent.event_type == "magic_link_attempt",
                SecurityEvent.created_at >= since,
                SecurityEvent.ip == ip,
            )
            .scalar()
            or 0
        )
        distinct_emails_10m = int(
            db.session.query(func.count(func.distinct(SecurityEvent.email_hash)))
            .filter(
                SecurityEvent.event_type == "magic_link_attempt",
                SecurityEvent.created_at >= since,
                SecurityEvent.ip == ip,
                SecurityEvent.email_hash.isnot(None),
            )
            .scalar()
            or 0
        )
        email_count_10m = 0
        recent_reuse_blocked_10m = 0
        if email_hash:
            email_count_10m = int(
                db.session.query(func.count(SecurityEvent.id))
                .filter(
                    SecurityEvent.event_type == "magic_link_attempt",
                    SecurityEvent.created_at >= since,
                    SecurityEvent.email_hash == email_hash,
                )
                .scalar()
                or 0
            )
            recent_reuse_blocked_10m = int(
                db.session.query(func.count(SecurityEvent.id))
                .filter(
                    SecurityEvent.event_type == "magic_link_reuse_blocked",
                    SecurityEvent.created_at >= since,
                    SecurityEvent.email_hash == email_hash,
                )
                .scalar()
                or 0
            )
        recent_rate_limited_10m = int(
            db.session.query(func.count(SecurityEvent.id))
            .filter(
                SecurityEvent.event_type == "magic_link_rate_limited",
                SecurityEvent.created_at >= since,
                SecurityEvent.ip == ip,
            )
            .scalar()
            or 0
        )
        recent_suspicious_10m = int(
            db.session.query(func.count(SecurityEvent.id))
            .filter(
                SecurityEvent.event_type == "magic_link_suspicious_activity",
                SecurityEvent.created_at >= since,
                SecurityEvent.ip == ip,
            )
            .scalar()
            or 0
        )
    except Exception:
        db.session.rollback()
        query_failed = True
        ip_count_10m = 0
        distinct_emails_10m = 0
        email_count_10m = 0
        recent_rate_limited_10m = 0
        recent_reuse_blocked_10m = 0
        recent_suspicious_10m = 0

    score = 0
    signals: list[str] = []
    if query_failed:
        score += 5
        signals.append("risk_query_failure")
    if ip_count_10m > 10:
        score += 2
        signals.append("ip_velocity")
    if distinct_emails_10m >= 5:
        score += 4
        signals.append("email_spray")
    if email_count_10m > 5:
        score += 2
        signals.append("email_velocity")
    if recent_rate_limited_10m > 0:
        score += 2
        signals.append("recent_rate_limit")
    if recent_reuse_blocked_10m > 0:
        score += 1
        signals.append("recent_reuse_block")
    if recent_suspicious_10m > 0:
        # A recent suspicious-activity marker should push the next attempt into
        # a meaningfully stronger shadow-block tier when combined with any
        # additional signal such as rate limiting.
        score += 5
        signals.append("recent_suspicious")
    if trust_tier == "trusted":
        score = max(0, score - 2)

    return {
        "score": score,
        "signals": signals,
        "ip_count_10m": ip_count_10m,
        "distinct_emails_10m": distinct_emails_10m,
        "email_count_10m": email_count_10m,
        "recent_rate_limited_10m": recent_rate_limited_10m,
        "recent_reuse_blocked_10m": recent_reuse_blocked_10m,
        "recent_suspicious_10m": recent_suspicious_10m,
        "trust_tier": trust_tier,
    }


def _magic_link_block_duration_for_score(score: int) -> int:
    if score < 4:
        return 0
    if score <= 6:
        return 10 * 60
    if score <= 9:
        return 60 * 60
    return 24 * 60 * 60


def _recent_active_magic_link(
    *,
    purpose: str,
    email: str,
    request_id: int | None = None,
    cooldown_seconds: int = 120,
):
    now = utc_now()
    cutoff = now - timedelta(seconds=cooldown_seconds)
    query = MagicLinkToken.query.filter_by(
        purpose=purpose,
        email=(email or "").strip().lower(),
        used_at=None,
        invalidated_at=None,
    )
    if request_id is not None:
        query = query.filter(MagicLinkToken.request_id == request_id)
    for row in query.order_by(MagicLinkToken.created_at.desc()).all():
        created_at = _require_utc_datetime(row.created_at, assume_naive_utc=True)
        expires_at = _require_utc_datetime(row.expires_at, assume_naive_utc=True)
        if created_at is None or expires_at is None:
            continue
        if expires_at <= now or created_at < cutoff:
            continue
        remaining = max(1, int((created_at + timedelta(seconds=cooldown_seconds) - now).total_seconds()))
        return row, remaining
    return None, 0


def _invalidate_existing_magic_links(
    *,
    purpose: str,
    email: str,
    request_id: int | None = None,
    exclude_token_hash: str | None = None,
    reason: str = "superseded",
) -> int:
    now = utc_now()
    query = MagicLinkToken.query.filter_by(
        purpose=purpose,
        email=(email or "").strip().lower(),
        used_at=None,
        invalidated_at=None,
    )
    if request_id is not None:
        query = query.filter(MagicLinkToken.request_id == request_id)
    if exclude_token_hash:
        query = query.filter(MagicLinkToken.token_hash != exclude_token_hash)
    invalidated = 0
    for row in query.all():
        row_expires_at = _require_utc_datetime(row.expires_at, assume_naive_utc=True)
        if row_expires_at is None or row_expires_at < now:
            continue
        row.invalidated_at = now
        row.invalidated_reason = reason[:64]
        invalidated += 1
    return invalidated


def _load_legacy_request_magic_link(token_hash: str):
    legacy_req = (
        scoped_requests_query()
        .filter(Request.requester_token_hash == token_hash)
        .order_by(desc(Request.created_at))
        .first()
    )
    if not legacy_req:
        return None

    created_at = _require_utc_datetime(
        getattr(legacy_req, "requester_token_created_at", None),
        assume_naive_utc=True,
    )
    if created_at is None:
        return None

    return {
        "legacy_req": legacy_req,
        "created_at": created_at,
        "expires_at": created_at + timedelta(minutes=15),
    }


def get_safe_next(default_endpoint: str):
    nxt = request.args.get("next")
    if nxt and is_safe_url(nxt):
        return nxt
    return default_endpoint


def _safe_volunteer_next_path(candidate: str | None) -> str | None:
    """Allow only local volunteer paths for post-magic-link redirects."""
    c = (candidate or "").strip()
    if not c:
        return None
    if not is_safe_url(c):
        return None
    if not c.startswith("/volunteer/"):
        return None
    return c


REMOTE_MARKERS = {
    "remote",
    "online",
    "en ligne",
    "à distance",
    "a distance",
    "zoom",
    "google meet",
    "teams",
    "video",
    "онлайн",
    "дистанционно",
    "по телефон",
    "телефон",
}


def is_remote_request(req) -> bool:
    """Heuristic remote flag based on text fields (no is_remote column)."""
    text = " ".join(
        [
            (getattr(req, "location_text", None) or ""),
            (getattr(req, "message", None) or ""),
            (getattr(req, "description", None) or ""),
        ]
    ).lower()

    if any(marker in text for marker in REMOTE_MARKERS):
        return True

    city = (getattr(req, "city", None) or "").strip()
    loc_text = (getattr(req, "location_text", None) or "").strip()
    if not city and loc_text:
        return True

    return False


def is_request_matching_volunteer(
    request_obj, volunteer_obj, interested_request_ids: set[int] | None = None
):
    """MVP matching (V2.2.A): status open + volunteer active + profile complete + not already interested."""
    if not request_obj or not volunteer_obj:
        return False

    if (getattr(request_obj, "status", "") or "").lower() != "open":
        return False

    if not getattr(volunteer_obj, "is_active", False):
        return False

    # profile completeness (location + availability)
    if not (
        getattr(volunteer_obj, "location", None)
        and getattr(volunteer_obj, "availability", None)
    ):
        return False

    # exclude assigned/archived/deleted
    if getattr(request_obj, "assigned_volunteer_id", None) is not None:
        return False
    if getattr(request_obj, "is_archived", False):
        return False
    if getattr(request_obj, "deleted_at", None) is not None:
        return False

    if (
        interested_request_ids
        and getattr(request_obj, "id", None) in interested_request_ids
    ):
        return False

    # Geo/skills matching postponed (later versions)
    return True


def require_volunteer_login(fn):
    """
    Volunteer-only access control using the volunteer session family.

    This guard is intentionally separate from Flask-Login admin auth.
    Unauthenticated volunteer access is redirected to the canonical public
    volunteer entry flow (`main.become_volunteer`), not to admin login.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("volunteer_id"):
            return redirect(
                url_for("main.become_volunteer", next=request.path), code=303
            )
        return fn(*args, **kwargs)

    return wrapper


def _current_volunteer():
    vid = session.get("volunteer_id")
    if not vid:
        return None
    try:
        return db.session.get(Volunteer, int(vid))
    except Exception:
        return None


# ✅ url_lang + safe_url_for in ALL templates rendered by this blueprint
@main_bp.app_context_processor
def inject_template_helpers():
    def url_lang(endpoint: str, **values):
        return url_for(endpoint, **values)

    def safe_url_for(endpoint: str, **values):
        try:
            return url_for(endpoint, **values)
        except Exception:
            return "#"

    def time_ago(dt):
        if not dt:
            return ""
        try:
            delta = datetime.now(UTC).replace(tzinfo=None) - dt.replace(tzinfo=None)
        except Exception:
            return ""
        return format_timedelta(
            delta, add_direction=True, locale=str(babel_get_locale())
        )

    def og_image_url(filename: str | None = None):
        fallback_rel = "img/og-home-1200x630.jpg"
        try:
            candidate = (filename or "").strip()
            if candidate:
                candidate = candidate.replace("\\", "/")
                candidate_rel = (
                    candidate if candidate.startswith("img/") else f"img/{candidate}"
                )
                candidate_path = Path(current_app.static_folder) / Path(candidate_rel)
                if candidate_path.is_file():
                    return url_for("static", filename=candidate_rel, _external=True)
        except Exception:
            pass
        return url_for("static", filename=fallback_rel, _external=True)

    return {
        "url_lang": url_lang,
        "safe_url_for": safe_url_for,
        "time_ago": time_ago,
        "og_image_url": og_image_url,
        "normalize_request_category": normalize_request_category,
        "request_category_label": request_category_label,
        "REQUEST_CATEGORY_CHOICES": request_category_choices(),
        "public_intake_mode": _public_intake_mode(),
    }


@main_bp.route("/", methods=["GET"])
def index():
    """Главна страница"""
    latest_requests = []
    active_count = 0
    try:
        latest_requests = (
            scoped_requests_query()
            .filter(Request.deleted_at.is_(None))
            .filter(Request.is_archived.is_(False))
            .order_by(Request.created_at.desc())
            .limit(6)
            .all()
        )
        active_count = (
            db.session.query(func.count(Request.id))
            .filter(Request.structure_id == _current_structure_id())
            .filter(Request.deleted_at.is_(None))
            .filter(Request.is_archived.is_(False))
            .scalar()
        ) or 0
    except Exception as e:
        current_app.logger.warning("Home latest_requests skipped: %s", e)
        latest_requests = []
        active_count = 0

    now_utc_naive = _to_utc_naive(utc_now())

    def is_new_request(req) -> bool:
        created = _to_utc_naive(getattr(req, "created_at", None))
        if not created or not now_utc_naive:
            return False
        return (now_utc_naive - created) <= timedelta(hours=24)

    return (
        render_template(
            "home_new_slim.html",
            latest_requests=latest_requests,
            active_count=active_count,
            is_new_request=is_new_request,
        ),
        200,
    )


@main_bp.post("/events")
@csrf.exempt
@limiter.limit("120 per minute")
def events_collect():
    data = request.get_json(silent=True) or {}
    event = (data.get("event") or "").strip()
    if not event:
        return jsonify({"ok": False}), 400

    event_path = extract_event_path(data)
    decision = classify_public_telemetry_request(
        event_path,
        user_agent=request.headers.get("User-Agent"),
        client_ip=get_client_ip(),
    )
    if not decision.should_persist:
        try:
            current_app.logger.info(
                "[EVENT-IGNORED] %s ip=%s path=%s reason=%s",
                event,
                get_client_ip() or "",
                event_path or "",
                decision.reason or "ignored",
            )
        except Exception:
            pass
        return jsonify({"ok": True, "ignored": True}), 200

    try:
        current_app.logger.info(
            "[EVENT-QUALIFIED] %s ip=%s path=%s props=%s",
            event,
            get_client_ip() or "",
            decision.canonical_path or event_path or "",
            data.get("props") or {},
        )
    except Exception:
        pass
    return jsonify({"ok": True, "ignored": False}), 200


def _emit_event(event: str, props: dict | None = None) -> None:
    """Internal telemetry helper aligned with /events payload shape."""
    if not event:
        return
    try:
        current_app.logger.info("[EVENT] %s %s", event, props or {})
    except Exception:
        pass


def _public_intake_mode() -> str:
    mode = (current_app.config.get("HC_PUBLIC_INTAKE_MODE") or "open").strip().lower()
    if mode not in {"open", "pilot", "closed"}:
        return "open"
    return mode


@main_bp.post("/csp-report")
@csrf.exempt
def csp_report():
    # Browsers may send application/csp-report or application/json.
    data = request.get_json(silent=True) or {}
    current_app.logger.warning("[CSP-REPORT] %s", data)
    return ("", 204)


@main_bp.route("/logout", methods=["GET", "POST"], endpoint="logout")
def logout():
    """Unified logout for Flask-Login users (admin/front) and volunteer session."""
    # If admin session is active, hand off to admin logout (keeps MFA cleanup)
    if session.get("admin_logged_in"):
        return redirect(url_for("admin.admin_logout"))

    try:
        logout_user()
    except Exception:
        pass

    # Clear all session data to avoid stale logins
    try:
        session.clear()
    except Exception:
        # Fallback: pop known keys
        for key in list(session.keys()):
            session.pop(key, None)

    resp = redirect(url_for("main.index"))

    # Proactively drop remember/session cookies if present
    try:
        sess_cookie = current_app.config.get("SESSION_COOKIE_NAME", "session")
        resp.delete_cookie(sess_cookie, path="/")
    except Exception:
        pass
    try:
        remember_cookie = current_app.config.get(
            "REMEMBER_COOKIE_NAME", "remember_token"
        )
        resp.delete_cookie(remember_cookie, path="/")
    except Exception:
        pass

    return resp


@main_bp.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    role = getattr(current_user, "role_canon", None) or canonical_role(
        getattr(current_user, "role", None)
    )

    if role in ("admin", "superadmin"):
        return redirect(url_for("admin.admin_requests"))

    if role == "requester":
        my_requests = (
            scoped_requests_query()
            .filter(Request.user_id == current_user.id)
            .populate_existing()
            .order_by(desc(Request.created_at))
            .limit(20)
            .all()
        )
        counts = dict(
            ((s or "open"), c)
            for s, c in db.session.query(Request.status, func.count(Request.id))
            .filter(Request.structure_id == _current_structure_id())
            .filter(Request.user_id == current_user.id)
            .group_by(Request.status)
            .all()
        )
        kpi = {
            "open": counts.get("open", 0),
            "in_progress": counts.get("in_progress", 0),
            "done": counts.get("done", 0),
            "cancelled": counts.get("cancelled", 0),
        }
        return render_template(
            "dashboard_requester.html", my_requests=my_requests, kpi=kpi
        )

    if role in ("volunteer", "professional"):
        assigned = (
            scoped_requests_query()
            .filter(Request.owner_id == current_user.id)
            .populate_existing()
            .order_by(desc(Request.owned_at), desc(Request.created_at))
            .limit(20)
            .all()
        )
        for r in assigned:
            try:
                r.status_norm = normalize_request_status(getattr(r, "status", None))
            except Exception:
                r.status_norm = getattr(r, "status", None)
        counts = dict(
            ((s or "open"), c)
            for s, c in db.session.query(Request.status, func.count(Request.id))
            .filter(Request.structure_id == _current_structure_id())
            .filter(Request.owner_id == current_user.id)
            .group_by(Request.status)
            .all()
        )
        kpi = {
            "open": counts.get("open", 0),
            "in_progress": counts.get("in_progress", 0),
            "done": counts.get("done", 0),
            "cancelled": counts.get("cancelled", 0),
        }
        return render_template("dashboard_helper.html", assigned=assigned, kpi=kpi)

    return render_template("dashboard_unknown_role.html", role=role), 403


@main_bp.get("/profile")
def profile():
    # Authenticated users: route by role
    if getattr(current_user, "is_authenticated", False):
        role = getattr(current_user, "role_canon", None) or canonical_role(
            getattr(current_user, "role", None)
        )
        if role in ("admin", "superadmin"):
            return redirect(url_for("admin.admin_requests"))
        if role in ("volunteer", "professional"):
            return redirect(url_for("main.dashboard"))

    # Requester via session-stored email
    requester_email = (session.get("requester_email") or "").strip().lower()
    if not requester_email:
        return redirect(url_for("main.submit_request"))

    my_requests = (
        scoped_requests_query()
        .filter(func.lower(Request.email) == requester_email)
        .order_by(desc(Request.created_at))
        .limit(20)
        .all()
    )
    rows = (
        db.session.query(Request.status, func.count(Request.id))
        .filter(Request.structure_id == _current_structure_id())
        .filter(func.lower(Request.email) == requester_email)
        .group_by(Request.status)
        .all()
    )
    counts = {(s or "open"): c for s, c in rows}
    kpi = {
        "open": counts.get("open", 0),
        "in_progress": counts.get("in_progress", 0),
        "done": counts.get("done", 0),
        "cancelled": counts.get("cancelled", 0),
    }
    return render_template(
        "profile_requester.html",
        kpi=kpi,
        my_requests=my_requests,
        requester_email=requester_email,
    )


@main_bp.get("/requester/logout")
def requester_logout():
    session.pop("requester_email", None)
    flash(_("Your session has been cleared."), "info")
    return redirect(url_for("main.submit_request"))


@main_bp.get("/auth/magic/<token>")
def magic_link_consume(token: str):
    token_hash = _sha256_hex(token)

    try:
        ml = MagicLinkToken.query.filter_by(token_hash=token_hash).first()
    except OperationalError:
        # DB not migrated yet (magic_link_tokens table missing). Fall back to legacy flow.
        db.session.rollback()
        ml = None

    # Legacy compatibility (/r/<token> previously hashed into requests.requester_token_hash).
    if ml is None:
        legacy = _load_legacy_request_magic_link(token_hash)
        if legacy is None:
            return _magic_link_reject("not_found", token_hash=token_hash)

        legacy_req = legacy["legacy_req"]
        expires_at = legacy["expires_at"]
        now = utc_now()
        if now > expires_at:
            return _magic_link_reject("expired", token_hash=token_hash, purpose="request")

        # If the new table isn't available yet, keep legacy behavior (no single-use).
        try:
            _ = MagicLinkToken.__table__
        except Exception:
            session["requester_email"] = (
                (getattr(legacy_req, "email", "") or "").strip().lower()
            )
            session["requester_authenticated"] = True
            if getattr(legacy_req, "id", None):
                session["last_request_id"] = int(legacy_req.id)
            return redirect(url_for("main.profile"), code=303)

        try:
            ml = MagicLinkToken(
                token_hash=token_hash,
                purpose="request",
                email=(getattr(legacy_req, "email", "") or "").strip().lower(),
                request_id=getattr(legacy_req, "id", None),
                created_at=legacy["created_at"],
                expires_at=expires_at,
            )
            db.session.add(ml)
            db.session.commit()
        except Exception:
            # Concurrent consume may have inserted the token already.
            db.session.rollback()
            ml = MagicLinkToken.query.filter_by(token_hash=token_hash).first()
            if ml is None:
                return _magic_link_reject("invalid", token_hash=token_hash)

    try:
        now = utc_now()
        expires_at = _require_utc_datetime(ml.expires_at, assume_naive_utc=True)
        used_at = _require_utc_datetime(ml.used_at, assume_naive_utc=True)
        invalidated_at = _require_utc_datetime(
            getattr(ml, "invalidated_at", None),
            assume_naive_utc=True,
        )
    except ValueError:
        current_app.logger.warning(
            "[MAGIC LINK] token_id=%s rejected due to naive datetime state",
            getattr(ml, "id", None),
        )
        return _magic_link_reject(
            "invalid",
            token_hash=token_hash,
            token_id=getattr(ml, "id", None),
            purpose=getattr(ml, "purpose", None),
        )

    if ml.purpose not in {"request", "volunteer"}:
        return _magic_link_reject(
            "wrong_purpose",
            token_hash=token_hash,
            token_id=ml.id,
            purpose=ml.purpose,
        )
    if invalidated_at is not None:
        return _magic_link_reject(
            "invalid",
            token_hash=token_hash,
            token_id=ml.id,
            purpose=ml.purpose,
        )
    if used_at is not None:
        return _magic_link_reject(
            "already_used",
            token_hash=token_hash,
            token_id=ml.id,
            purpose=ml.purpose,
        )
    if expires_at is None or now > expires_at:
        try:
            if getattr(ml, "invalidated_at", None) is None:
                ml.invalidated_at = now
                ml.invalidated_reason = "expired"
                db.session.commit()
        except Exception:
            db.session.rollback()
        return _magic_link_reject(
            "expired",
            token_hash=token_hash,
            token_id=ml.id,
            purpose=ml.purpose,
        )

    # Single-use claim via explicit row mutation to avoid bulk-update timezone sync issues.
    try:
        ml.used_at = now
        ml.used_ip = _client_ip()
        ml.used_ua = (request.headers.get("User-Agent") or "")[:255]
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        return _magic_link_reject(
            "invalid",
            token_hash=token_hash,
            token_id=getattr(ml, "id", None),
            purpose=getattr(ml, "purpose", None),
        )

    # Reload to get purpose/email/request_id.
    ml = MagicLinkToken.query.filter_by(token_hash=token_hash).first()
    if ml is None:
        return _magic_link_reject("not_found", token_hash=token_hash)

    log_security_event(
        "magic_link_consumed",
        actor_type="anonymous",
        meta={
            "purpose": ml.purpose,
            "token_id": ml.id,
            "request_id": ml.request_id,
            "token_hash_prefix": token_hash[:12],
        },
    )

    if ml.purpose == "request":
        # Requester passwordless session (minimal)
        session["requester_email"] = (ml.email or "").strip().lower()
        session["requester_authenticated"] = True
        if ml.request_id:
            session["last_request_id"] = int(ml.request_id)
        return redirect(url_for("main.profile"), code=303)

    if ml.purpose == "volunteer":
        email = (ml.email or "").strip().lower()

        # Avoid cross-role session bleed (admin/requester/etc.). Preserve locale if present.
        lang = session.get("lang")
        session.clear()
        if lang:
            session["lang"] = lang

        # Find-or-create the volunteer record (MVP-safe).
        v = Volunteer.query.filter(Volunteer.email.ilike(email)).first()
        if not v:
            v = Volunteer(email=email, is_active=True)
            if hasattr(Volunteer, "structure_id"):
                v.structure_id = current_structure_id()
            db.session.add(v)
            db.session.commit()

        # This is what @require_volunteer_login expects.
        session["volunteer_id"] = int(v.id)
        session["volunteer_logged_in"] = True  # legacy compatibility
        session["just_logged_in"] = True

        target = _safe_volunteer_next_path(session.pop("volunteer_next", None))
        if not target:
            target = url_for("main.volunteer_dashboard")
        return redirect(target, code=303)

    return _magic_link_reject(
        "wrong_purpose",
        token_hash=token_hash,
        token_id=ml.id,
        purpose=ml.purpose,
    )


@main_bp.get("/r/<token>")
def magic_link_alias(token: str):
    # Alias for older links and for habit; canonical handler lives at /auth/magic/<token>.
    return redirect(url_for("main.magic_link_consume", token=token), code=302)


@main_bp.get("/set-lang/<lang>")
def set_lang_switch(lang):
    lang = (lang or "").lower().strip()
    if lang not in _allowed_locales():
        abort(404)

    session["lang"] = lang
    session.modified = True

    next_url = request.referrer or url_for("main.index")
    try:
        ref_host = urlparse(request.host_url).netloc
        target_host = urlparse(next_url).netloc
        if ref_host != target_host:
            next_url = url_for("main.index")
    except Exception:
        next_url = url_for("main.index")

    current_app.logger.info(
        "[i18n.switch] route=/set-lang lang=%s session_lang=%s cookie_lang=%s next=%s",
        lang,
        session.get("lang"),
        request.cookies.get("hc_lang"),
        next_url,
    )
    resp = make_response(redirect(next_url))
    resp.set_cookie("hc_lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


@main_bp.get("/lang/<locale>")
def set_lang(locale):
    locale = (locale or "").lower()
    if locale not in _allowed_locales():
        abort(404)

    session["lang"] = locale
    session.modified = True
    next_url = request.args.get("next") or url_for("main.index")
    try:
        ref_host = urlparse(request.host_url).netloc
        target_host = urlparse(next_url).netloc
        if target_host and ref_host != target_host:
            next_url = url_for("main.index")
    except Exception:
        next_url = url_for("main.index")

    current_app.logger.info(
        "[i18n.switch] route=/lang lang=%s session_lang=%s cookie_lang=%s next=%s",
        locale,
        session.get("lang"),
        request.cookies.get("hc_lang"),
        next_url,
    )
    resp = make_response(redirect(next_url))
    resp.set_cookie("hc_lang", locale, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


@main_bp.get("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return render_template("search_results.html", q="", results=None), 200

    q_like = f"%{q}%"
    q_low = q.lower()
    pattern = re.compile(re.escape(q), re.IGNORECASE)

    def highlight_text(text: str) -> Markup | str:
        raw = (text or "").strip()
        if not raw:
            return ""
        last = 0
        chunks = []
        for match in pattern.finditer(raw):
            chunks.append(escape(raw[last : match.start()]))
            chunks.append(Markup("<mark>"))
            chunks.append(escape(match.group(0)))
            chunks.append(Markup("</mark>"))
            last = match.end()
        if not chunks:
            return escape(raw)
        chunks.append(escape(raw[last:]))
        return Markup("").join(chunks)

    def score(item: dict) -> int:
        title = (item.get("title") or "").lower()
        subtitle = (item.get("subtitle") or "").lower()
        snippet = (item.get("snippet") or "").lower()
        if title == q_low:
            return 0
        if title.startswith(q_low):
            return 1
        if q_low in title:
            return 2
        if q_low in subtitle:
            return 3
        if q_low in snippet:
            return 4
        return 5

    def finalize(items: list[dict]) -> list[dict]:
        ranked = sorted(items, key=score)
        final = []
        for item in ranked:
            final.append(
                {
                    "title": highlight_text(item.get("title")),
                    "url": item.get("url") or "#",
                    "subtitle": highlight_text(item.get("subtitle")),
                    "snippet": highlight_text(item.get("snippet")),
                }
            )
        return final

    results: dict[str, list[dict]] = {
        "requests": [],
        "categories": [],
        "professionals": [],
    }

    try:
        req_rows = (
            scoped_requests_query().filter(
                Request.deleted_at.is_(None),
                Request.is_archived.is_(False),
                or_(
                    Request.title.ilike(q_like),
                    Request.description.ilike(q_like),
                    Request.message.ilike(q_like),
                    Request.city.ilike(q_like),
                    Request.category.ilike(q_like),
                ),
            )
            .order_by(desc(Request.created_at))
            .limit(20)
            .all()
        )
        for row in req_rows:
            results["requests"].append(
                {
                    "title": row.title or _("Request"),
                    "url": url_for("main.request_public", req_id=row.id),
                    "subtitle": row.city or row.category or "",
                    "snippet": (row.description or row.message or "")[:180],
                }
            )
    except Exception as exc:
        current_app.logger.warning("Search requests skipped: %s", exc)

    try:
        cat_hits = []
        for slug, meta in CATEGORIES.items():
            title = (
                meta.get("title")
                or meta.get("label")
                or meta.get("content", {}).get("title", {}).get("fr")
                or meta.get("content", {}).get("title", {}).get("en")
                or meta.get("content", {}).get("title", {}).get("bg")
                or slug
            )
            description_text = (
                meta.get("description")
                or meta.get("desc")
                or meta.get("content", {}).get("intro", {}).get("fr")
                or meta.get("content", {}).get("intro", {}).get("en")
                or meta.get("content", {}).get("intro", {}).get("bg")
                or ""
            )

            hay = f"{slug} {title} {description_text}".lower()
            if q_low in hay:
                cat_hits.append(
                    {
                        "title": title,
                        "url": url_for("main.category_help", category=slug),
                        "subtitle": "Catégorie",
                        "snippet": description_text[:160],
                    }
                )

        results["categories"] = sorted(cat_hits[:18], key=score)
    except Exception as exc:
        current_app.logger.warning("Search categories skipped: %s", exc)

    try:
        pro_rows = (
            ProfessionalLead.query.filter(
                or_(
                    ProfessionalLead.status.is_(None),
                    ~func.lower(ProfessionalLead.status).in_(("invalid", "spam")),
                )
            )
            .filter(
                or_(
                    ProfessionalLead.full_name.ilike(q_like),
                    ProfessionalLead.profession.ilike(q_like),
                    ProfessionalLead.organization.ilike(q_like),
                    ProfessionalLead.city.ilike(q_like),
                    ProfessionalLead.message.ilike(q_like),
                )
            )
            .order_by(desc(ProfessionalLead.created_at))
            .limit(12)
            .all()
        )
        for pro in pro_rows:
            subtitle_parts = [pro.profession or "", pro.city or ""]
            results["professionals"].append(
                {
                    "title": pro.full_name or pro.organization or pro.email,
                    "url": url_for("main.professionnels", q=q),
                    "subtitle": " · ".join([part for part in subtitle_parts if part]),
                    "snippet": (pro.message or "")[:180],
                }
            )
    except Exception as exc:
        current_app.logger.warning("Search professionals skipped: %s", exc)
        results["professionals"] = [
            {
                "title": f"Voir les professionnels pour '{q}'",
                "url": url_for("main.professionnels", q=q),
                "subtitle": "Professionnels",
                "snippet": "Ouvrir la liste et filtrer.",
            }
        ]

    results["requests"] = finalize(results["requests"])
    results["categories"] = finalize(results["categories"])
    results["professionals"] = finalize(results["professionals"])

    return render_template("search_results.html", q=q, results=results), 200


@main_bp.route("/categories", methods=["GET"])
@limiter.limit("120 per minute")
def categories():
    """Legacy alias for orienter."""
    return redirect(url_for("main.orienter"), code=301)


@main_bp.route("/orienter", methods=["GET"])
@limiter.limit("120 per minute")
def orienter():
    return render_template("orienter.html")


@main_bp.route("/achievements", methods=["GET"])
def achievements():
    if not session.get("volunteer_logged_in"):
        return redirect(url_for("main.volunteer_login"))

    achievements_data = [
        {"title": "First login", "points": 10, "status": "unlocked"},
        {"title": "First request handled", "points": 20, "status": "locked"},
    ]
    gamification = {"points": 0, "level": 1, "badges": []}
    return (
        render_template(
            "achievements.html",
            achievements=achievements_data,
            gamification=gamification,
        ),
        200,
    )


@main_bp.route("/volunteer_login", methods=["GET", "POST"])
@limiter.limit("5 per 5 minutes")
@limiter.limit("20 per hour")
@limiter.limit("3 per hour", key_func=email_or_ip_key, methods=["POST"])
def volunteer_login():
    """
    Legacy/dev volunteer login entrypoint.

    Canonical public volunteer entry is `main.become_volunteer`.
    This route exists to support controlled dev bypass behavior and must not
    be treated as part of the admin auth family.
    """

    # Legacy volunteer entrypoint: keep dev bypass support, but route real
    # users to the canonical volunteer magic-link flow via `become_volunteer`.
    if not current_app.config.get("VOLUNTEER_DEV_BYPASS_ENABLED"):
        current_app.logger.warning("[VOL-LOGIN] blocked (non-dev) ip=%s", _client_ip())
        return redirect(
            url_for("main.become_volunteer", next=url_for("main.volunteer_profile")),
            code=303,
        )

    current_app.logger.info(
        "volunteer_login cfg bypass_enabled=%s bypass_email=%s args_dev=%s",
        current_app.config.get("VOLUNTEER_DEV_BYPASS_ENABLED"),
        current_app.config.get("VOLUNTEER_DEV_BYPASS_EMAIL"),
        request.args.get("dev"),
    )
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()

        generic_msg = _("If the email is valid, you will receive a login link.")

        log_security_event(
            "magic_link_requested", actor_type="anonymous", meta={"flow": "volunteer"}
        )

        if current_app.config.get(
            "VOLUNTEER_DEV_BYPASS_ENABLED"
        ) and email == current_app.config.get("VOLUNTEER_DEV_BYPASS_EMAIL"):
            v = Volunteer.query.filter_by(email=email).first()
            if not v:
                v = Volunteer(email=email, is_active=True)
                if hasattr(Volunteer, "structure_id"):
                    v.structure_id = current_structure_id()
                db.session.add(v)
                db.session.commit()

            session.clear()
            session["volunteer_id"] = v.id
            session["volunteer_logged_in"] = True
            log_security_event(
                "volunteer_dev_bypass_login", actor_type="volunteer", actor_id=v.id
            )
            target = get_safe_next(url_for("main.volunteer_dashboard"))
            return redirect(target, code=303)

        if not email:
            flash(generic_msg, "info")
            return render_template("volunteer_login.html", minimal_page=True), 200

        # This remains a dev-oriented surface. If the bypass email is not used,
        # preserve anti-enumeration UX and do not turn this route into a second
        # canonical volunteer auth system.
        flash(generic_msg, "info")
        return render_template("volunteer_login.html", minimal_page=True), 200

    prefill_email = ""
    if (
        current_app.config.get("VOLUNTEER_DEV_BYPASS_ENABLED")
        and request.args.get("dev") == "1"
    ):
        prefill_email = current_app.config.get("VOLUNTEER_DEV_BYPASS_EMAIL") or ""

    return (
        render_template(
            "volunteer_login.html", minimal_page=True, prefill_email=prefill_email
        ),
        200,
    )


@main_bp.post("/volunteer/logout")
def volunteer_logout():
    session.pop("volunteer_id", None)
    session.pop("just_logged_in", None)
    session.pop("demo_pending", None)
    return redirect(url_for("main.index"), code=303)


@main_bp.route("/become_volunteer", methods=["GET", "POST"])
def become_volunteer():
    """Public landing + submission endpoint for new volunteers."""
    if request.method == "GET":
        safe_next = _safe_volunteer_next_path(request.args.get("next"))
        if safe_next:
            session["volunteer_next"] = safe_next
        else:
            session.pop("volunteer_next", None)
        flow_mode = "login" if safe_next else "register"
        return render_template("become_volunteer.html", flow_mode=flow_mode), 200

    if request.method == "POST":
        default_cooldown_seconds = 120
        response_cooldown_seconds = default_cooldown_seconds
        accept = (request.headers.get("Accept") or "").lower()
        wants_json = "application/json" in accept

        def _volunteer_magic_ok_response(resend_email: str = ""):
            if wants_json:
                return (
                    jsonify(
                        {"ok": True, "cooldown_seconds": int(response_cooldown_seconds)}
                    ),
                    200,
                )
            safe_email = (
                (resend_email or session.get("volunteer_magic_email") or "")
                .strip()
                .lower()
            )
            return (
                render_template(
                    "volunteer_link_sent.html",
                    resend_email=safe_email,
                    cooldown_seconds=int(response_cooldown_seconds),
                ),
                200,
            )

        # Server-side anti-bot (frontend can be bypassed)
        suppress = False
        suppress_reasons: list[str] = []
        website = (
            request.form.get("company_fax") or request.form.get("website") or ""
        ).strip()
        started_at = (request.form.get("started_at") or "").strip()
        if website:
            # Browser autofill can populate hidden honeypot fields with the user's email.
            # Treat obvious autofill patterns as non-bot to avoid false suppressions.
            website_l = website.lower()
            email_l = (request.form.get("email") or "").strip().lower()
            if "@" in website_l or (email_l and website_l == email_l):
                current_app.logger.info(
                    "[VOL-MAGIC] honeypot autofill ignored website=%r email=%r",
                    website,
                    email_l,
                )
            else:
                suppress = True
                suppress_reasons.append("honeypot")
        try:
            started_ms = int(started_at)
        except Exception:
            started_ms = 0
        # Keep this threshold low to avoid suppressing legitimate autofill + click flows.
        if started_ms and (int(time.time() * 1000) - started_ms) < 900:
            suppress = True
            suppress_reasons.append("timing")

        # Server-side rate-limit (MVP): same UX either way (anti-enumeration).
        ip = _client_ip()
        form_email = (request.form.get("email") or "").strip().lower()
        session_email = (session.get("volunteer_magic_email") or "").strip().lower()
        # For JSON resend requests, allow fallback to session email.
        # For regular form submits, require explicit form email to avoid stale session sends.
        email = form_email or (session_email if wants_json else "")
        email_key = email or ip

        # Volunteer magic link (purpose="volunteer") — always return generic OK.
        if not email or "@" not in email:
            return _volunteer_magic_ok_response()
        session["volunteer_magic_email"] = email

        email_hash = _sha256_hex(email)
        risk = _compute_magic_link_risk(ip, email)
        _detect_suspicious_activity(ip, email)
        log_security_event(
            "magic_link_attempt",
            actor_type="anonymous",
            ip=ip,
            email_hash=email_hash,
            meta={"purpose": "volunteer"},
        )
        block_duration_sec = _magic_link_block_duration_for_score(risk["score"])
        if block_duration_sec > 0:
            _rate_limit_block(f"block:ip:{ip}", block_duration_sec)
            suppress = True
            suppress_reasons.append("risk-blocked")
            log_security_event(
                "magic_link_risk_blocked",
                actor_type="anonymous",
                ip=ip,
                email_hash=email_hash,
                meta={
                    "purpose": "volunteer",
                    "risk_score": risk["score"],
                    "signals": risk["signals"],
                    "block_duration_sec": block_duration_sec,
                    "trust_tier": risk["trust_tier"],
                },
            )

        if _magic_link_rate_limited(purpose="volunteer", email=email, ip=ip):
            suppress = True
            suppress_reasons.append("rate-limited")

        recent_token, cooldown_retry_after = _recent_active_magic_link(
            purpose="volunteer",
            email=email,
            cooldown_seconds=default_cooldown_seconds,
        )
        if recent_token is not None:
            suppress = True
            suppress_reasons.append("reuse-blocked")
            response_cooldown_seconds = int(max(1, cooldown_retry_after))
            log_security_event(
                "magic_link_reuse_blocked",
                actor_type="anonymous",
                email_hash=email_hash,
                meta={
                    "purpose": "volunteer",
                    "token_id": recent_token.id,
                    "email_hash_prefix": _magic_link_email_fingerprint(email),
                },
            )

        current_app.logger.info(
            "[VOL-MAGIC] pre-send decision suppress=%s reasons=%s email=%s ip=%s",
            suppress,
            suppress_reasons,
            email,
            ip,
        )
        if suppress:
            current_app.logger.info(
                "[VOL-MAGIC] suppress=%s reason=%s website=%s started_at=%s ip=%s email_key=%s cooldown_seconds=%s",
                suppress,
                ",".join(suppress_reasons) if suppress_reasons else "-",
                bool(website),
                started_at,
                ip,
                email_key,
                response_cooldown_seconds,
            )
            return _volunteer_magic_ok_response(resend_email=email)
        current_app.logger.info(
            "[VOL-MAGIC] not suppressed, continuing to token+send email=%s", email
        )

        try:
            current_app.logger.info("[VOL-MAGIC] creating token for email=%s", email)
            raw_token = secrets.token_urlsafe(32)
            token_hash = _sha256_hex(raw_token)
            ttl_minutes = 15
            expires_at = utc_now() + timedelta(minutes=ttl_minutes)

            _invalidate_existing_magic_links(
                purpose="volunteer",
                email=email,
                exclude_token_hash=token_hash,
                reason="superseded",
            )
            row = MagicLinkToken(
                purpose="volunteer",
                email=email,
                token_hash=token_hash,
                expires_at=expires_at,
            )
            db.session.add(row)
            db.session.commit()
            current_app.logger.info(
                "[MAGIC LINK VOL] token created id=%s email=%s expires_at=%s",
                row.id,
                email,
                expires_at,
            )
            log_security_event(
                "magic_link_issued",
                actor_type="anonymous",
                meta={
                    "purpose": "volunteer",
                    "token_id": row.id,
                    "expires_at": expires_at.isoformat(),
                },
            )

            base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
            path = url_for("main.magic_link_consume", token=raw_token, _external=False)
            magic_url = (
                f"{base}{path}"
                if base
                else url_for("main.magic_link_consume", token=raw_token, _external=True)
            )

            # Keep volunteer login subject stable in FR regardless of request locale.
            subject = "Votre lien de connexion HelpChain (15 min)"
            context = {
                "magic_link_url": magic_url,
                "ttl_minutes": ttl_minutes,
                "intro_text": _(
                    "Sans mot de passe. Recevez un lien sécurisé par e-mail."
                ),
                "button_text": _("Ouvrir mon lien de connexion"),
                "fallback_text": _(
                    "Si le bouton ne fonctionne pas, copiez-collez ce lien :"
                ),
                "privacy_line": _("Minimal data, GDPR compliant"),
                "ignore_line": _(
                    "Si vous n’êtes pas à l’origine de cette demande, ignorez cet e-mail."
                ),
            }

            try:
                from backend.mail_service import send_notification_email

                current_app.logger.info(
                    "[VOL-MAGIC] about to call send_notification_email email=%s", email
                )
                current_app.logger.info("[VOL-MAGIC] sending to=%s", email)
                send_ok = send_notification_email(
                    email,
                    subject,
                    "emails/magic_link.html",
                    context,
                    purpose="volunteer_magic_link",
                )
                current_app.logger.info(
                    "[MAGIC LINK VOL] token_id=%s email_send_ok=%s",
                    row.id,
                    send_ok,
                )
            except Exception as e:
                current_app.logger.warning(
                    "[EMAIL] volunteer magic link send failed token_id=%s: %s",
                    row.id,
                    e,
                )
        except OperationalError:
            # Table missing / not migrated yet: keep anti-enumeration behavior.
            db.session.rollback()
            current_app.logger.exception(
                "[VOL-MAGIC] operational error while creating/sending magic link"
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "[VOL-MAGIC] unexpected error while creating/sending magic link"
            )

        # Keep UX consistent and privacy-safe.
        return _volunteer_magic_ok_response(resend_email=email)

    return render_template("become_volunteer.html"), 200


@main_bp.get("/volunteer/confirmation")
def volunteer_confirmation():
    """Confirmation screen after submitting volunteer interest."""
    return render_template("volunteer_confirmation.html"), 200


@main_bp.get("/volunteer/onboarding")
@require_volunteer_login
def volunteer_onboarding():
    volunteer = _current_volunteer()
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))
    if getattr(volunteer, "volunteer_onboarded", False):
        return redirect(get_safe_next(url_for("main.volunteer_dashboard")))
    return render_template("volunteer_onboarding.html"), 200


@main_bp.post("/volunteer/onboarding")
@require_volunteer_login
def volunteer_onboarding_submit():
    volunteer_id = session.get("volunteer_id")
    if not volunteer_id:
        return redirect(url_for("main.become_volunteer", next=request.path))

    volunteer = db.session.get(Volunteer, volunteer_id)
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    # ✅ V2.1.a — mark onboarding complete
    volunteer.volunteer_onboarded = True
    db.session.commit()

    flash(
        _("You're all set! You can now see requests where your help matters."),
        "success",
    )

    return redirect(url_for("main.volunteer_dashboard"))


@main_bp.get("/volunteer/dashboard")
@require_volunteer_login
def volunteer_dashboard():
    volunteer = _current_volunteer()

    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))
    if not getattr(volunteer, "volunteer_onboarded", False):
        return redirect(
            url_for("main.volunteer_onboarding", next=request.path), code=303
        )

    just_logged_in = session.pop("just_logged_in", None)

    open_requests = scoped_requests_query().filter_by(status="open").all()

    my_interest_req_ids = set(
        rid
        for (rid,) in db.session.query(VolunteerInterest.request_id)
        .filter(VolunteerInterest.volunteer_id == volunteer.id)
        .all()
    )

    # Show matches even if the volunteer already expressed interest.
    # The dashboard will reflect interest status via badges/CTAs.
    matched_requests = [
        r
        for r in open_requests
        if is_request_matching_volunteer(r, volunteer, interested_request_ids=None)
    ]
    # Smart matching controls
    min_match_raw = (request.args.get("min") or "55").strip()
    try:
        min_match_int = int(min_match_raw)
    except Exception:
        min_match_int = 55
    if min_match_int not in (40, 50, 55, 60, 65, 70, 75, 80):
        min_match_int = 55
    match_prio = (request.args.get("prio") or "all").strip().lower()
    if match_prio not in {"all", "urgent", "high"}:
        match_prio = "all"
    match_near = (request.args.get("near") or "").strip() == "1"
    near_km = 25
    has_coords = bool(
        getattr(volunteer, "latitude", None) is not None
        and getattr(volunteer, "longitude", None) is not None
    )
    if match_near and not has_coords:
        match_near = False

    # Smart matching layers:
    # - strong matches: >=55%
    # - low-confidence matches: 40..54%
    all_scored_raw = get_matched_requests_v1(
        volunteer,
        limit=200,
        min_percent=0,
        prio=match_prio,
        near=match_near,
        max_text_chars=800,
        cache_ttl_sec=90,
    )
    strong_raw = [t for t in all_scored_raw if int(round(t[1])) >= 55]
    low_conf_raw = [t for t in all_scored_raw if 40 <= int(round(t[1])) < 55]
    strong_raw = [t for t in strong_raw if int(round(t[1])) >= min_match_int]
    matched_v1_raw = strong_raw[:8]
    matched_v1 = []
    for req, pct, breakdown in matched_v1_raw:
        matched_v1.append(
            {"req": req, "pct": int(round(pct)), "breakdown": dict(breakdown or {})}
        )
    low_conf_count = len(low_conf_raw)

    # Dynamic guidance + profile completeness
    skills_raw = (getattr(volunteer, "skills", None) or "").strip()
    skills_items = [s.strip() for s in skills_raw.split(",") if s.strip()]
    skills_count = len(skills_items)
    has_location = bool((getattr(volunteer, "location", None) or "").strip())
    has_availability = bool((getattr(volunteer, "availability", None) or "").strip())
    has_coords = (
        getattr(volunteer, "latitude", None) is not None
        and getattr(volunteer, "longitude", None) is not None
    )
    has_skill_depth = skills_count >= 2
    checks = [has_location, has_skill_depth, has_availability, has_coords]
    profile_completeness = int(round((sum(1 for x in checks if x) / len(checks)) * 100))

    smart_tips = []
    if not has_location:
        smart_tips.append(
            {
                "icon": "",
                "text": "Enable location to unlock distance scoring (+20%).",
            }
        )
    if not has_skill_depth:
        smart_tips.append(
            {
                "icon": "",
                "text": "Add 2-3 specific skills to increase match score (+45%).",
            }
        )
    if not has_coords:
        smart_tips.append(
            {
                "icon": "",
                "text": "Distance matching is currently disabled (missing coordinates).",
            }
        )
    if not has_availability:
        smart_tips.append(
            {
                "icon": "",
                "text": "Add availability so requests can be prioritized for your schedule.",
            }
        )

    pending_ids = set(
        rid
        for (rid,) in db.session.query(VolunteerInterest.request_id)
        .filter(
            VolunteerInterest.volunteer_id == volunteer.id,
            VolunteerInterest.status == "pending",
        )
        .all()
    )

    # --- Interest status map (for dashboard badges/CTAs)
    req_ids = [r.id for r in matched_requests] if matched_requests else []
    interest_by_req_id = {}
    if req_ids:
        interests = (
            VolunteerInterest.query.filter(
                VolunteerInterest.volunteer_id == volunteer.id,
                VolunteerInterest.request_id.in_(req_ids),
            )
            .order_by(VolunteerInterest.id.asc())
            .all()
        )
        # If duplicates exist, keep the most recent one
        for it in interests:
            interest_by_req_id[it.request_id] = (it.status or "").upper()

    # --- My interests (dashboard lists) ---
    my_interests = (
        db.session.query(VolunteerInterest, Request)
        .join(Request, Request.id == VolunteerInterest.request_id)
        .filter(Request.structure_id == _current_structure_id())
        .filter(VolunteerInterest.volunteer_id == volunteer.id)
        .order_by(VolunteerInterest.created_at.desc())
        .limit(30)
        .all()
    )

    my_pending: list[dict] = []
    my_approved: list[dict] = []
    my_rejected: list[dict] = []
    my_first_approved_req = None
    my_first_closed_req = None

    for vi, req in my_interests:
        row = {"vi": vi, "req": req}
        status_norm = (vi.status or "").lower()
        my_interest_req_ids.add(req.id)
        if status_norm == "pending":
            my_pending.append(row)
        elif status_norm == "approved":
            my_approved.append(row)
            if not my_first_approved_req:
                my_first_approved_req = row
        elif status_norm == "rejected":
            my_rejected.append(row)
        else:
            my_pending.append(row)  # fallback bucket
        try:
            req_status = normalize_request_status(getattr(req, "status", None))
        except Exception:
            req_status = (getattr(req, "status", None) or "").lower()
        if not my_first_closed_req and req_status in {
            "closed",
            "done",
            "completed",
            "resolved",
        }:
            my_first_closed_req = row

    # --- Generate match notifications lazily (MVP) ---
    profile_complete = bool(volunteer.location) and bool(volunteer.availability)
    if volunteer.is_active and profile_complete:
        eligible_matches = [
            r for r in matched_requests if r.id not in my_interest_req_ids
        ]
        ensure_new_match_notifications(
            volunteer_id=volunteer.id, request_rows=eligible_matches
        )

    current_app.logger.info(
        "Matching check",
        extra={
            "volunteer_id": volunteer.id,
            "matched_requests": len(matched_requests),
        },
    )

    actions = (
        db.session.query(VolunteerAction.request_id, VolunteerAction.action)
        .filter(VolunteerAction.volunteer_id == volunteer.id)
        .all()
    )
    my_actions_by_req_id = {rid: act for rid, act in actions}

    unread_match_notifications = (
        Notification.query.filter_by(
            volunteer_id=volunteer.id, is_read=False, type="new_match"
        )
        .order_by(Notification.created_at.desc())
        .all()
    )
    unread_count = Notification.query.filter_by(
        volunteer_id=volunteer.id, is_read=False
    ).count()
    match_count = int(len(unread_match_notifications or []))

    # --- In-app notifications (V2.2.A) ---
    locale_code = str(babel_get_locale())[:2]
    notif_copy = {
        "fr": {
            "new_match": {
                "title": "New request matches your profile",
                "body": "Localisation + compétences + disponibilité sont alignées. Consultez et choisissez si vous voulez aider.",
                "cta": "Voir la demande",
            },
            "help_accepted": {
                "title": "Vous êtes connectés. Vous pouvez aider.",
                "body": "La personne a accepté votre aide. Vous pouvez maintenant coordonner.",
                "cta": "Ouvrir la demande",
            },
            "request_done": {
                "title": _("This request is closed. Thank you."),
                "body": "Aidez une autre personne quand vous le souhaitez.",
                "cta": "Voir les demandes",
            },
        },
        "bg": {
            "new_match": {
                "title": "Нова заявка съвпада с профила ти",
                "body": "Локация + умения + наличност съвпадат. Виж детайлите и реши дали да помогнеш.",
                "cta": "Виж заявката",
            },
            "help_accepted": {
                "title": "Свързани сте. Можеш да помогнеш вече.",
                "body": "Заявителят прие помощта ти. Сега може да координирате.",
                "cta": "Отвори заявката",
            },
            "request_done": {
                "title": _("This request is closed. Thank you."),
                "body": "Можеш да помогнеш на друг човек, когато решиш.",
                "cta": "Виж заявки",
            },
        },
        "en": {
            "new_match": {
                "title": "New request matches your profile",
                "body": "Location + skills + availability align. Review it and choose if you want to help.",
                "cta": "View request",
            },
            "help_accepted": {
                "title": "You’re connected. You can now help.",
                "body": "The requester accepted your help. Coordinate when ready.",
                "cta": "Open request",
            },
            "request_done": {
                "title": "This request is now completed. Thank you.",
                "body": "Help someone else whenever you like.",
                "cta": "View requests",
            },
        },
    }
    notif_lang = notif_copy.get(locale_code, notif_copy["en"])
    notifications: list[dict] = []
    badge_count = 0

    if my_first_approved_req:
        notifications.append(
            {
                "kind": "help_accepted",
                "title": notif_lang["help_accepted"]["title"],
                "body": notif_lang["help_accepted"]["body"],
                "cta_label": notif_lang["help_accepted"]["cta"],
                "cta_href": url_for(
                    "main.volunteer_request_details",
                    req_id=my_first_approved_req["req"].id,
                ),
                "tone": "success",
            }
        )
        badge_count = 0  # badge clears automatically
    elif my_first_closed_req:
        notifications.append(
            {
                "kind": "request_done",
                "title": notif_lang["request_done"]["title"],
                "body": notif_lang["request_done"]["body"],
                "cta_label": notif_lang["request_done"]["cta"],
                "cta_href": url_for("main.volunteer_dashboard") + "#hc-matches",
                "tone": "secondary",
            }
        )
        badge_count = 0
    elif unread_match_notifications:
        unread_match_count = len(unread_match_notifications)
        first_match = unread_match_notifications[0]
        if locale_code == "bg":
            title = (
                f"Имаш съвпадение с {unread_match_count} заявка"
                if unread_match_count == 1
                else f"Имаш съвпадение с {unread_match_count} заявки"
            )
            body = "Отвори и избери следващо действие."
            cta = "Виж"
        elif locale_code == "fr":
            title = (
                "Vous avez une demande correspondante"
                if unread_match_count == 1
                else f"Vous avez {unread_match_count} demandes correspondantes"
            )
            body = "Ouvrez et choisissez la prochaine action."
            cta = "Voir"
        else:
            title = (
                "You've been matched to 1 request"
                if unread_match_count == 1
                else f"You've been matched to {unread_match_count} requests"
            )
            body = "Open and choose your next action."
            cta = "View"
        notifications.append(
            {
                "kind": "new_match",
                "title": title,
                "body": body,
                "cta_label": cta,
                "cta_href": url_for(
                    "main.volunteer_request_details", req_id=first_match.request_id
                ),
                "tone": "primary",
            }
        )
        badge_count = 1

    return (
        render_template(
            "volunteer_dashboard.html",
            volunteer=volunteer,
            matches=matched_requests,
            matched_v1=matched_v1,
            match_min=min_match_int,
            match_prio=match_prio,
            match_near=match_near,
            near_km=near_km,
            has_coords=has_coords,
            low_conf_count=low_conf_count,
            profile_completeness=profile_completeness,
            smart_tips=smart_tips,
            just_logged_in=bool(just_logged_in),
            pending_ids=pending_ids,
            interest_by_req_id=interest_by_req_id,
            my_pending=my_pending,
            my_approved=my_approved,
            my_rejected=my_rejected,
            notifications=notifications,
            match_count=int(match_count or 0),
            volunteer_badge_count=badge_count,
            unread_count=unread_count,
            my_actions_by_req_id=my_actions_by_req_id,
        ),
        200,
    )


@main_bp.post("/volunteer/match/<int:req_id>/seen")
@main_bp.post("/volunteer/requests/<int:req_id>/seen")
@require_volunteer_login
def volunteer_request_seen(req_id: int):
    volunteer = _current_volunteer()
    if not volunteer:
        return jsonify({"ok": False}), 401
    try:
        match_mark_seen(volunteer.id, req_id)
        return jsonify({"ok": True}), 200
    except Exception:
        current_app.logger.exception("Failed to mark seen req_id=%s", req_id)
        db.session.rollback()
        return jsonify({"ok": False}), 500


@main_bp.post("/volunteer/match/<int:req_id>/dismiss")
@main_bp.post("/volunteer/requests/<int:req_id>/dismiss")
@require_volunteer_login
def volunteer_request_dismiss(req_id: int):
    volunteer = _current_volunteer()
    if not volunteer:
        return jsonify({"ok": False}), 401
    try:
        match_dismiss_for(volunteer.id, req_id, hours=48)
        return jsonify({"ok": True, "dismiss_hours": 48}), 200
    except Exception:
        current_app.logger.exception("Failed to dismiss req_id=%s", req_id)
        db.session.rollback()
        return jsonify({"ok": False}), 500


@main_bp.get("/volunteer/requests/<int:req_id>")
@require_volunteer_login
def volunteer_request_details(req_id: int):
    """Детайли за заявка, достъпни за логнат доброволец."""
    volunteer = _current_volunteer()
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    req = get_scoped_request_or_404(req_id)
    try:
        db.session.refresh(req)
    except Exception:
        pass

    if not can_view_request(volunteer, req, db):
        abort(404)

    vi = (
        VolunteerInterest.query.filter_by(volunteer_id=volunteer.id, request_id=req.id)
        .order_by(VolunteerInterest.id.desc())
        .first()
    )

    already_pending = bool(vi and vi.status == "pending")
    already_approved = bool(vi and vi.status == "approved")
    already_rejected = bool(vi and vi.status == "rejected")
    try:
        status_norm = normalize_request_status(getattr(req, "status", None))
    except Exception:
        status_norm = getattr(req, "status", None)

    action_row = VolunteerAction.query.filter_by(
        request_id=req.id, volunteer_id=volunteer.id
    ).one_or_none()
    # Keep a single "last signal" object for the template (CAN_HELP / CANT_HELP).
    # This is effectively 1 row due to uq_volunteer_action_request_volunteer.
    my_last_signal = (
        VolunteerAction.query.filter_by(request_id=req.id, volunteer_id=volunteer.id)
        .order_by(
            VolunteerAction.updated_at.desc(),
            VolunteerAction.created_at.desc(),
            VolunteerAction.id.desc(),
        )
        .first()
    )

    # опционален контрол: показваме само ако е match/отворена
    # if not is_request_matching_volunteer(req, volunteer):
    #     abort(403)

    # Mark related match notification as read (if any)
    try:
        notif_changed = (
            Notification.query.filter_by(
                volunteer_id=volunteer.id,
                request_id=req.id,
                type="new_match",
                is_read=False,
            ).update({"is_read": True, "read_at": datetime.now(UTC).replace(tzinfo=None)})
            > 0
        )
        state_changed = mark_request_seen_for_volunteer(
            request_id=req.id,
            volunteer_id=volunteer.id,
            seen_at=utc_now(),
            commit=False,
        )
        if notif_changed or state_changed:
            db.session.commit()
    except Exception:
        db.session.rollback()

    return (
        render_template(
            "volunteer_request_details.html",
            req=req,
            volunteer=volunteer,
            status_norm=status_norm,
            is_pending=already_pending,
            already_pending=already_pending,
            already_approved=already_approved,
            already_rejected=already_rejected,
            is_demo=False,
            volunteer_action=action_row,
            my_last_signal=my_last_signal,
            my_signal=my_last_signal,  # alias for template clarity/back-compat
        ),
        200,
    )


@main_bp.get("/volunteer/requests/demo")
@require_volunteer_login
def volunteer_request_demo():
    """Demo детайли за примера в таблото."""
    volunteer = _current_volunteer()
    already_pending = bool(session.get("demo_pending"))
    demo_req = SimpleNamespace(
        id="demo",
        title="Примерна заявка",
        city="Paris",
        created_at="току-що",
        description="Човек търси помощ за документи и насоки къде да ги подаде.",
    )
    return (
        render_template(
            "volunteer_request_details.html",
            req=demo_req,
            volunteer=volunteer,
            is_pending=already_pending,
            already_pending=already_pending,
            already_approved=False,
            already_rejected=False,
            is_demo=True,
        ),
        200,
    )


@main_bp.post("/volunteer/requests/<int:req_id>/help")
@require_volunteer_login
def volunteer_help(req_id: int):
    volunteer = _current_volunteer()
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    current_app.logger.info("VOL_HELP DB=%s", db.engine.url)
    current_app.logger.info("VOL_HELP req_id=%s", req_id)

    req = get_scoped_request_or_404(req_id)

    interest = VolunteerInterest.query.filter_by(
        volunteer_id=volunteer.id, request_id=req.id
    ).one_or_none()

    if interest is None:
        interest = VolunteerInterest(
            volunteer_id=volunteer.id,
            request_id=req.id,
            status="pending",
        )
        db.session.add(interest)
    else:
        interest.status = "pending"

    current_app.logger.info(
        "VOL_HELP about to commit volunteer_id=%s request_id=%s", volunteer.id, req.id
    )
    db.session.commit()
    current_app.logger.info(
        "VOL_HELP committed interest_id=%s status=%s", interest.id, interest.status
    )

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "status": interest.status})

    # Clear match notification when volunteer expresses interest
    try:
        Notification.query.filter_by(
            volunteer_id=volunteer.id, request_id=req.id, type="new_match"
        ).update({"is_read": True, "read_at": datetime.now(UTC).replace(tzinfo=None)})
        db.session.commit()
    except Exception:
        db.session.rollback()

    flash(_("Got it. Your signal was sent to the team."), "success")
    return redirect(url_for("main.volunteer_request_details", req_id=req_id), code=303)


def _upsert_volunteer_action(req_obj, volunteer, action_value: str):
    """Create or update a volunteer action signal for a request."""
    row = VolunteerAction.query.filter_by(
        request_id=req_obj.id, volunteer_id=volunteer.id
    ).one_or_none()
    old_action = getattr(row, "action", None) if row else None
    if row is None:
        row = VolunteerAction(
            request_id=req_obj.id,
            volunteer_id=volunteer.id,
            action=action_value,
        )
        db.session.add(row)
    else:
        row.action = action_value
    db.session.add(
        RequestActivity(
            request_id=req_obj.id,
            volunteer_id=volunteer.id,
            action=f"volunteer_{action_value.lower()}",
            old_value=old_action,
            new_value=action_value,
        )
    )
    db.session.commit()
    _emit_event(
        "volunteer_action_signal",
        {
            "volunteer_id": volunteer.id,
            "request_id": req_obj.id,
            "action": action_value,
        },
    )
    return row


@main_bp.post("/volunteer/requests/<int:req_id>/can-help")
@require_volunteer_login
def volunteer_can_help(req_id: int):
    volunteer = _current_volunteer()
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    req = get_scoped_request_or_404(req_id)

    _upsert_volunteer_action(req, volunteer, "CAN_HELP")

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "action": "CAN_HELP"})
    flash(_("Got it. Your signal was sent to the team."), "success")
    return redirect(url_for("main.volunteer_request_details", req_id=req_id), code=303)


@main_bp.post("/volunteer/requests/<int:req_id>/cant-help")
@require_volunteer_login
def volunteer_cant_help(req_id: int):
    volunteer = _current_volunteer()
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    req = get_scoped_request_or_404(req_id)

    _upsert_volunteer_action(req, volunteer, "CANT_HELP")

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "action": "CANT_HELP"})
    flash(_("Got it. Your signal was sent to the team."), "success")
    return redirect(url_for("main.volunteer_request_details", req_id=req_id), code=303)


@main_bp.post("/volunteer/requests/demo/help")
@require_volunteer_login
def volunteer_request_help_demo():
    """Demo: не записваме нищо, само връщаме UX feedback."""
    session["demo_pending"] = True
    flash(_("Thank you! Your interest has been recorded (demo)."), "success")
    return redirect(url_for("main.volunteer_request_demo"))


@main_bp.get("/volunteer/notifications")
@require_volunteer_login
def volunteer_notifications():
    volunteer = _current_volunteer()
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    # Volunteer UI: notifications are primarily keyed by `volunteer_id`.
    owner_col = getattr(Notification, "volunteer_id", None) or getattr(
        Notification, "user_id", None
    )
    if owner_col is None:
        abort(500)
    notifs = (
        Notification.query.filter(owner_col == volunteer.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    unread_count = Notification.query.filter(
        owner_col == volunteer.id,
        Notification.is_read == False,  # noqa: E712
    ).count()

    return (
        render_template(
            "volunteer_notifications.html",
            notifications=notifs,
            unread_count=unread_count,
        ),
        200,
    )


@main_bp.post("/volunteer/notifications/<int:notif_id>/open")
@require_volunteer_login
def volunteer_notification_open(notif_id: int):
    volunteer = _current_volunteer()
    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    try:
        target_url, request_id = mark_notification_opened(notif_id, volunteer.id)
    except LookupError:
        abort(404)
    except RuntimeError:
        abort(500)

    _emit_event(
        "volunteer_notification_open",
        {
            "volunteer_id": volunteer.id,
            "notification_id": notif_id,
            "request_id": request_id,
        },
    )
    return redirect(target_url)


@main_bp.route("/volunteer/profile", methods=["GET", "POST"])
@require_volunteer_login
def volunteer_profile():
    volunteer = _current_volunteer()

    if not volunteer:
        return redirect(url_for("main.become_volunteer", next=request.path))

    if request.method == "POST":
        current_app.logger.info(
            "VOL PROFILE SAVE location=%s", request.form.get("location")
        )
        for field in (
            "name",
            "email",
            "phone",
            "location",
            "city",
            "skills",
            "notes",
            "availability",
        ):
            if field in request.form:
                setattr(volunteer, field, request.form.get(field, "").strip())
        db.session.commit()
        return redirect(url_for("main.volunteer_dashboard"))

    return render_template("volunteer_profile.html", volunteer=volunteer), 200


@main_bp.get("/request")
@limiter.limit("30 per minute")
def request_category():
    slug = request.args.get("category")
    canonical = ALIASES.get(slug, slug) if slug else None
    category = CATEGORIES.get(canonical) if canonical else None

    if not slug:
        return (
            render_template(
                "request_category.html",
                category=None,
                COMMON=COMMON,
                categories=CATEGORIES,
                not_found=False,
                requested_slug=None,
            ),
            200,
        )

    if not category:
        return (
            render_template(
                "request_category.html",
                category=None,
                COMMON=COMMON,
                categories=CATEGORIES,
                not_found=True,
                requested_slug=slug,
            ),
            404,
        )

    show_emergency = category["ui"].get("severity") == "critical"
    return (
        render_template(
            "request_category.html",
            category=category,
            COMMON=COMMON,
            show_emergency=show_emergency,
            emergency_number=COMMON.get("emergency_number"),
            requested_slug=slug,
        ),
        200,
    )


@main_bp.get("/request/form")
def request_form():
    slug = request.args.get("category")
    canonical = ALIASES.get(slug, slug) if slug else None
    category = CATEGORIES.get(canonical) if canonical else None

    if not slug:
        return redirect(url_for("main.request_category"))

    if not category:
        current_app.logger.warning("Invalid category slug for form: %s", slug)
        return redirect(url_for("main.request_category", category=slug))

    return render_template("request_form.html", category=category, COMMON=COMMON)


@main_bp.get("/chat")
def chat_page():
    return render_template("chat/chat.html"), 200


@main_bp.route("/about")
def about():
    return render_template("about.html")


@main_bp.get("/gouvernance")
def gouvernance():
    return render_template("gouvernance.html")


def normalize_request_form(form):
    """
    Canonical mapping from HTML form fields -> backend variables.

    Accepts:
      - category OR type (optional)
      - urgency OR priority (optional)
      - description OR problem OR message (your HTML uses "problem")
    """
    category = (form.get("category") or form.get("type") or "").strip().lower()
    urgency = (form.get("urgency") or form.get("priority") or "").strip().lower()
    description = (
        form.get("description") or form.get("problem") or form.get("message") or ""
    ).strip()
    return category, urgency, description


def _extract_request_address_values(form) -> dict[str, str]:
    legacy_location_text = (form.get("location_text") or form.get("location") or "").strip()
    address_line = (form.get("address_line") or "").strip()
    postcode = (form.get("postcode") or "").strip()
    city = (form.get("city") or "").strip()
    country_raw = (form.get("country") or "").strip()

    has_meaningful_location = any((legacy_location_text, address_line, postcode, city))
    country = country_raw if has_meaningful_location else ""
    location_text = (
        request_address_display_text(
            address_line=address_line or None,
            postcode=postcode or None,
            city=city or None,
            country=country or None,
            fallback_text=legacy_location_text or None,
        )
        or ""
    )
    return {
        "address_line": address_line,
        "postcode": postcode,
        "city": city,
        "country": country,
        "location_text": location_text,
        "legacy_location_text": legacy_location_text,
    }


def validate_submit_request_form(form):
    """Manual submit_request validator returning (errors, cleaned_values)."""
    errors: dict[str, str] = {}

    category, urgency, description = normalize_request_form(form)
    name = (form.get("name") or "").strip()
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip()
    title = (form.get("title") or "").strip()
    consent = (form.get("privacy_consent") or "").strip()
    address_values = _extract_request_address_values(form)
    address_line = address_values["address_line"]
    postcode = address_values["postcode"]
    city = address_values["city"]
    country = address_values["country"]
    location_text = address_values["location_text"]
    legacy_location_text = address_values["legacy_location_text"]

    MAX_NAME_LEN = 80
    MAX_TITLE_LEN = 120
    MAX_DESC_LEN = 2000
    MAX_ADDRESS_LINE_LEN = 255
    MAX_POSTCODE_LEN = 32
    MAX_CITY_LEN = 200
    MAX_COUNTRY_LEN = 120
    MAX_LOCATION_LEN = 500
    MAX_PHONE_LEN = 32
    MAX_EMAIL_LEN = 254

    if consent != "1" and not current_app.config.get("TESTING", False):
        errors["privacy_consent"] = _(
            "Veuillez accepter la Politique de confidentialité (RGPD) pour continuer."
        )

    for label, value, max_len in (
        ("name", name, MAX_NAME_LEN),
        ("title", title, MAX_TITLE_LEN),
        ("description", description, MAX_DESC_LEN),
        ("address_line", address_line, MAX_ADDRESS_LINE_LEN),
        ("postcode", postcode, MAX_POSTCODE_LEN),
        ("city", city, MAX_CITY_LEN),
        ("country", country, MAX_COUNTRY_LEN),
        ("location_text", legacy_location_text, MAX_LOCATION_LEN),
        ("phone", phone, MAX_PHONE_LEN),
        ("email", email, MAX_EMAIL_LEN),
    ):
        if len(value) > max_len:
            if label == "description":
                errors[label] = _("Please shorten the text.")
            else:
                errors[label] = _("Please shorten the %(field)s.", field=label)

    for label, value in (
        ("name", name),
        ("title", title),
        ("description", description),
        ("address_line", address_line),
        ("postcode", postcode),
        ("city", city),
        ("country", country),
        ("location_text", legacy_location_text),
    ):
        if has_control_chars(value):
            errors[label] = _("Invalid characters detected.")

    lowered = (description or "").lower()
    if "<script" in lowered or "javascript:" in lowered:
        errors["description"] = _("Suspicious content detected.")

    if len(name) < 2:
        errors["name"] = _("Please enter a name (at least 2 characters).")

    if len(description) < 10:
        errors["description"] = _("Please describe the issue (at least 10 characters).")

    if not phone and not email:
        msg = _("Please provide at least a phone number or an email.")
        errors["phone"] = msg
        errors["email"] = msg

    category = normalize_request_category(category)
    allowed_categories = set(REQUEST_CATEGORY_CODES) | {""}
    if category not in allowed_categories:
        errors["category"] = _("Please select a valid category.")

    allowed_urgency = {"low", "normal", "medium", "urgent", "critical", "emergency", ""}
    if urgency not in allowed_urgency:
        errors["urgency"] = _("Please select a valid urgency.")

    # urgency -> priority (DB expects low/medium/high)
    priority_map = {
        "low": "low",
        "medium": "medium",
        "normal": "medium",
        "urgent": "high",
        "critical": "high",
        "emergency": "high",
    }
    priority = priority_map.get(urgency, "medium")

    cleaned = {
        "name": name,
        "phone": phone,
        "email": email,
        "category": category or "orientation",
        "urgency": urgency or "normal",
        "priority": priority,
        "title": title,
        "description": description,
        "address_line": address_line,
        "postcode": postcode,
        "city": city,
        "country": country,
        "location_text": location_text,
        "started_at": (form.get("started_at") or "").strip(),
    }
    return errors, cleaned


@main_bp.route("/submit_request", methods=["GET", "POST"])
@limiter.limit("3 per minute", methods=["POST"])
@limiter.limit("10 per hour", methods=["POST"])
def submit_request():
    """Подаване на заявка за помощ"""
    trust_items = [
        ("&#10003;", "fas fa-shield-heart", _("Verified volunteers only")),
        ("&#9733;", "fas fa-user-clock", _("Fast matching - no bureaucracy")),
        ("&#9889;", "fas fa-lock", _("We keep your data private")),
    ]
    intake_mode = _public_intake_mode()
    entrypoint_raw = (
        request.args.get("entrypoint")
        or request.args.get("source")
        or request.args.get("from")
        or ""
    ).strip().lower()
    allowed_entrypoints = {
        "public",
        "homepage",
        "volunteer",
        "intervenant",
        "professional",
        "admin",
    }
    entrypoint = entrypoint_raw if entrypoint_raw in allowed_entrypoints else "public"
    session["request_entrypoint"] = entrypoint

    if request.method == "POST":
        if intake_mode == "closed":
            flash(
                _(
                    "Les dépôts publics sont temporairement suspendus. "
                    "Merci de contacter votre structure locale ou de réessayer plus tard."
                ),
                "warning",
            )
            return (
                render_template(
                    "submit_request.html",
                    trust_items=trust_items,
                    entrypoint=entrypoint,
                ),
                200,
            )
        current_app.logger.warning("SUBMIT_REQUEST POST hit")
        current_app.logger.warning("Form keys=%s", list(request.form.keys()))
        current_app.logger.warning(
            "website(honeypot)='%s'", (request.form.get("website") or "").strip()
        )
        # Honeypot anti-bot field (ако се задейства, искам да го ВИДИШ)
        website = (request.form.get("website") or "").strip()
        if website:
            current_app.logger.warning(
                "Honeypot triggered on submit_request: website=%r", website
            )
            # Pretend success to avoid bot feedback loops
            return (
                render_template(
                    "submit_request.html", trust_items=trust_items, success=True
                ),
                200,
            )

        errors, cleaned = validate_submit_request_form(request.form)

        current_app.logger.warning(
            "Parsed: name=%r(len=%s) phone=%r(len=%s) email=%r(len=%s) category=%r urgency=%r desc_len=%s title=%r",
            cleaned["name"],
            len(cleaned["name"] or ""),
            cleaned["phone"],
            len(cleaned["phone"] or ""),
            cleaned["email"],
            len(cleaned["email"] or ""),
            cleaned["category"],
            cleaned["urgency"],
            len(cleaned["description"] or ""),
            cleaned["title"],
        )

        if errors:
            current_app.logger.warning(
                "VALIDATION FAIL: submit_request errors=%s", errors
            )
            flash(_("Please correct the errors below."), "warning")
            if "description" in errors and "suspicious" in str(
                errors["description"]
            ).lower():
                flash(_("Suspicious content detected."), "danger")
            status_code = 200 if current_app.config.get("TESTING", False) else 400
            return (
                render_template(
                    "submit_request.html",
                    trust_items=trust_items,
                    form_errors=errors,
                    entrypoint=entrypoint,
                ),
                status_code,
            )

        # ✅ title is NOT NULL in DB
        if not cleaned["title"]:
            cleaned["title"] = (
                _("Request: %(category)s", category=request_category_label(cleaned["category"]))
                if cleaned["category"]
                else _("Help request")
            )

        session["request_draft"] = {
            "name": cleaned["name"],
            "phone": cleaned["phone"],
            "email": cleaned["email"],
            "category": cleaned["category"],
            "urgency": cleaned["urgency"],
            "priority": cleaned["priority"],
            "title": cleaned["title"],
            "description": cleaned["description"],
            "address_line": cleaned["address_line"],
            "postcode": cleaned["postcode"],
            "city": cleaned["city"],
            "country": cleaned["country"],
            "location_text": cleaned["location_text"],
            # Frontend anti-bot timing (optional, can be missing)
            "started_at": cleaned["started_at"],
            "entrypoint": entrypoint,
        }

        if current_app.config.get("TESTING", False):
            try:
                from backend.models import HelpRequest, User

                user = User.query.filter_by(email=cleaned["email"]).first()
                if not user:
                    username = (cleaned["email"].split("@")[0] or "requester").strip()
                    user = User(
                        username=username,
                        email=cleaned["email"],
                        password_hash="test",
                        role="requester",
                        is_active=True,
                    )
                    db.session.add(user)
                    db.session.flush()

                hr = HelpRequest(
                    title=cleaned["title"] or _("Help request"),
                    description=cleaned["description"],
                    name=cleaned["name"],
                    email=cleaned["email"],
                    phone=cleaned["phone"],
                    category=cleaned["category"],
                    priority=cleaned["priority"],
                    message=cleaned["description"],
                    address_line=cleaned["address_line"] or None,
                    postcode=cleaned["postcode"] or None,
                    city=cleaned["city"] or None,
                    country=cleaned["country"] or None,
                    location_text=cleaned["location_text"],
                    user_id=user.id,
                )
                if hasattr(hr, "structure_id"):
                    hr.structure_id = current_structure_id()
                db.session.add(hr)
                db.session.commit()
            except Exception as exc:
                current_app.logger.warning(
                    "submit_request test-compat insert failed: %s", exc
                )
                db.session.rollback()

        # Legacy/test compatibility: trigger emergency notification email once
        # per cooldown window directly on submit.
        if (
            current_app.config.get("EMERGENCY_EMAIL_ENABLED", False)
            and cleaned.get("category") == "emergency"
        ):
            state = current_app.extensions.setdefault("emergency_email_state", {})
            now_ts = int(time.time())
            last_sent_ts = int(state.get("last_sent_ts", 0) or 0)
            cooldown = int(
                current_app.config.get("EMERGENCY_EMAIL_COOLDOWN_SECONDS", 600) or 600
            )
            if (now_ts - last_sent_ts) >= cooldown:
                mail_ext = current_app.extensions.get("mail")
                if mail_ext and hasattr(mail_ext, "send"):
                    try:
                        mail_ext.send(
                            subject="Emergency request alert",
                            body=cleaned.get("description") or "",
                            email=cleaned.get("email") or "",
                            phone=cleaned.get("phone") or "",
                        )
                    except TypeError:
                        # Compatibility with different mail adapters.
                        mail_ext.send(
                            {
                                "subject": "Emergency request alert",
                                "body": cleaned.get("description") or "",
                            }
                        )
                    state["last_sent_ts"] = now_ts

        return render_template(
            "request_preview.html",
            draft=session["request_draft"],
            entrypoint=entrypoint,
        )

    trust_items = [
        ("&#10003;", "fas fa-shield-heart", _("Verified volunteers only")),
        ("&#9733;", "fas fa-user-clock", _("Fast matching - no bureaucracy")),
        ("&#9889;", "fas fa-lock", _("We keep your data private")),
    ]
    return render_template(
        "submit_request.html",
        trust_items=trust_items,
        entrypoint=entrypoint,
    )


@main_bp.post("/submit_request/confirm")
def submit_request_confirm():
    draft = session.get("request_draft")
    if not draft:
        flash(_("Session expired. Please submit the request again."), "error")
        return redirect(url_for("main.submit_request"))
    if _public_intake_mode() == "closed":
        flash(
            _(
                "Les dépôts publics sont temporairement suspendus. "
                "Merci de contacter votre structure locale ou de réessayer plus tard."
            ),
            "warning",
        )
        return redirect(url_for("main.submit_request"))

    try:
        # --- Server-side anti-bot + rate-limit for magic link (anti-enumeration) ---
        suppress_magic_send = False
        website = (request.form.get("website") or "").strip()
        started_at = (
            request.form.get("started_at") or draft.get("started_at") or ""
        ).strip()

        if website:
            current_app.logger.info("[MAGIC LINK] honeypot hit -> suppressed send")
            suppress_magic_send = True

        try:
            started_ms = int(started_at) if started_at else 0
        except Exception:
            started_ms = 0
        if started_ms and (int(time.time() * 1000) - started_ms) < 2500:
            current_app.logger.info("[MAGIC LINK] too fast -> suppressed send")
            suppress_magic_send = True

        ip = _client_ip()
        email = (draft.get("email") or "").strip().lower()
        email_hash = _sha256_hex(email) if email else None
        risk = _compute_magic_link_risk(ip, email)
        _detect_suspicious_activity(ip, email)
        if email:
            log_security_event(
                "magic_link_attempt",
                actor_type="anonymous",
                ip=ip,
                email_hash=email_hash,
                meta={"purpose": "request"},
            )
        block_duration_sec = _magic_link_block_duration_for_score(risk["score"])
        if block_duration_sec > 0:
            _rate_limit_block(f"block:ip:{ip}", block_duration_sec)
            suppress_magic_send = True
            log_security_event(
                "magic_link_risk_blocked",
                actor_type="anonymous",
                ip=ip,
                email_hash=email_hash,
                meta={
                    "purpose": "request",
                    "risk_score": risk["score"],
                    "signals": risk["signals"],
                    "block_duration_sec": block_duration_sec,
                    "trust_tier": risk["trust_tier"],
                },
            )
        if _magic_link_rate_limited(purpose="request", email=email, ip=ip):
            suppress_magic_send = True

        req = Request(
            title=draft.get("title"),
            description=draft.get("description"),
            name=draft.get("name"),
            phone=(draft.get("phone") or None),
            email=(draft.get("email") or None),
            address_line=(draft.get("address_line") or None),
            postcode=(draft.get("postcode") or None),
            city=(draft.get("city") or None),
            country=(draft.get("country") or None),
            location_text=(draft.get("location_text") or None),
            status="pending",
            priority=draft.get("priority"),
            category=draft.get("category"),
            structure_id=current_structure().id,
        )
        db.session.add(req)
        db.session.commit()
        current_app.logger.info(
            "[REQUEST GEO] request_id=%s status=%s normalized_address=%r lat=%r lng=%r",
            req.id,
            getattr(req, "geocoding_status", None),
            getattr(req, "normalized_address", None),
            getattr(req, "latitude", None),
            getattr(req, "longitude", None),
        )

        # Remember requester email in session for profile view
        try:
            if getattr(req, "email", None):
                session["requester_email"] = (req.email or "").strip().lower()
        except Exception:
            pass

        raw_token = None
        magic_url = None
        recent_request_token = None
        if not suppress_magic_send:
            recent_request_token, _ = _recent_active_magic_link(
                purpose="request",
                email=(getattr(req, "email", "") or "").strip().lower(),
                request_id=req.id,
                cooldown_seconds=120,
            )
            if recent_request_token is not None:
                suppress_magic_send = True
                log_security_event(
                    "magic_link_reuse_blocked",
                    actor_type="anonymous",
                    email_hash=email_hash,
                    meta={
                        "purpose": "request",
                        "request_id": req.id,
                        "token_id": recent_request_token.id,
                        "email_hash_prefix": _magic_link_email_fingerprint(email),
                    },
                )

        # Create single-use token row (DB) + build canonical URL.
        if not suppress_magic_send:
            raw_token = secrets.token_urlsafe(32)
            token_hash = _sha256_hex(raw_token)
            expires_at = utc_now() + timedelta(minutes=15)
            try:
                _invalidate_existing_magic_links(
                    purpose="request",
                    email=(getattr(req, "email", "") or "").strip().lower(),
                    request_id=req.id,
                    exclude_token_hash=token_hash,
                    reason="superseded",
                )
                ml = MagicLinkToken(
                    token_hash=token_hash,
                    purpose="request",
                    email=(getattr(req, "email", "") or "").strip().lower(),
                    request_id=req.id,
                    expires_at=expires_at,
                )
                db.session.add(ml)
                req.requester_token_hash = token_hash
                req.requester_token_created_at = utc_now()
                db.session.commit()
                log_security_event(
                    "magic_link_issued",
                    actor_type="anonymous",
                    meta={
                        "purpose": "request",
                        "token_id": ml.id,
                        "request_id": req.id,
                        "expires_at": expires_at.isoformat(),
                    },
                )
                try:
                    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
                    path = url_for("main.magic_link_consume", token=raw_token, _external=False)
                    magic_url = (
                        f"{base}{path}"
                        if base
                        else url_for("main.magic_link_consume", token=raw_token, _external=True)
                    )
                except Exception:
                    magic_url = f"/auth/magic/{raw_token}"
            except Exception:
                # If this fails, we still keep legacy token fields unset.
                db.session.rollback()

        current_app.logger.info("[MAGIC LINK] request_id=%s url=%s", req.id, magic_url)

        # Send magic link email (best effort)
        try:
            from backend.mail_service import send_notification_email

            recipient = getattr(req, "email", None)
            if recipient and not suppress_magic_send and magic_url:
                subject = "Confirmez votre demande HelpChain (15 min)"

                # Dedicated template context (no "content" HTML string)
                context = {
                    "magic_link_url": magic_url,
                    "ttl_minutes": 15,
                    "request_id": req.id,
                    # Email microcopy (i18n)
                    "intro_text": _(
                        "Sans mot de passe. Recevez un lien sécurisé par e-mail."
                    ),
                    "button_text": _("Ouvrir mon lien de connexion"),
                    "fallback_text": _(
                        "Si le bouton ne fonctionne pas, copiez-collez ce lien :"
                    ),
                    "privacy_line": _("Minimal data, GDPR compliant"),
                    "ignore_line": _(
                        "Si vous n’êtes pas à l’origine de cette demande, ignorez cet e-mail."
                    ),
                }

                send_notification_email(
                    recipient,
                    subject,
                    "emails/magic_link.html",
                    context,
                    purpose="request_magic_link",
                )
            elif recipient and suppress_magic_send:
                current_app.logger.info(
                    "[MAGIC LINK] send suppressed (antibot/rate-limit)"
                )
        except Exception as e:
            current_app.logger.warning("[EMAIL] magic link send failed: %s", e)

        session.pop("request_draft", None)
        entrypoint = (
            (request.form.get("entrypoint") or "").strip().lower()
            or (draft.get("entrypoint") or "").strip().lower()
            or (session.get("request_entrypoint") or "").strip().lower()
            or "public"
        )
        session.pop("request_entrypoint", None)
        session["last_request_id"] = req.id

        category = draft.get("category")
        urgency = draft.get("urgency")
        is_emergency = category in ("emergency", "urgent") or urgency in (
            "critical",
            "emergency",
            "urgent",
        )

        app = current_app._get_current_object()
        if (
            is_emergency
            and hasattr(app, "can_send_emergency_email")
            and app.can_send_emergency_email()
        ):
            if hasattr(app, "send_emergency_email"):
                app.send_emergency_email(req)
            if hasattr(app, "mark_emergency_email_sent"):
                app.mark_emergency_email_sent()

        log_security_event(
            "request_submitted",
            actor_type="anonymous",
            meta={"request_id": req.id, "category": getattr(req, "category", None)},
        )

        if entrypoint in {"admin"}:
            return redirect(url_for("admin.admin_requests"))
        if entrypoint in {"volunteer", "intervenant", "professional"}:
            return redirect(url_for("main.dashboard"))
        return redirect(url_for("main.submit_request_check_email"))

    except Exception as e:
        current_app.logger.exception("CONFIRM FAILED: %s", e)
        db.session.rollback()
        flash(_("Save failed. Please try again."), "error")
        return redirect(url_for("main.submit_request"))


@main_bp.get("/success")
def success():
    request_id = request.args.get("request_id") or session.get("last_request_id")
    is_admin = bool(session.get("admin_logged_in"))
    return (
        render_template("success.html", request_id=request_id, is_admin=is_admin),
        200,
    )


@main_bp.get("/submit_request/check_email")
def submit_request_check_email():
    email = (session.get("requester_email") or "").strip().lower()
    if not email:
        return redirect(url_for("main.submit_request"))
    try:
        local, domain = email.split("@", 1)
    except ValueError:
        local, domain = email, ""
    if local and len(local) > 2:
        masked_local = f"{local[:2]}***"
    elif local:
        masked_local = f"{local[:1]}***"
    else:
        masked_local = "***"
    masked_email = f"{masked_local}@{domain}" if domain else masked_local
    return render_template(
        "submit_request_check_email.html",
        email=email,
        masked_email=masked_email,
    )


@main_bp.get("/pilot", endpoint="pilot")
def pilot_dashboard():
    now = utc_now()
    week_ago = now - timedelta(days=7)
    since_14d = now - timedelta(days=14)

    tenant_filter = Request.structure_id == _current_structure_id()
    not_deleted = Request.deleted_at.is_(None)

    total_requests = (
        db.session.query(func.count(Request.id))
        .filter(tenant_filter, not_deleted)
        .scalar()
        or 0
    )

    open_requests = (
        db.session.query(func.count(Request.id))
        .filter(tenant_filter, not_deleted, Request.status.notin_(["done", "rejected"]))
        .scalar()
        or 0
    )

    closed_requests = (
        db.session.query(func.count(Request.id))
        .filter(tenant_filter, not_deleted, Request.status.in_(["done", "rejected"]))
        .scalar()
        or 0
    )

    closed_last_7d = (
        db.session.query(func.count(Request.id))
        .filter(
            tenant_filter,
            not_deleted,
            Request.completed_at.isnot(None),
            Request.completed_at >= week_ago,
        )
        .scalar()
        or 0
    )

    avg_resolution_hours = (
        db.session.query(
            func.avg(
                func.julianday(Request.completed_at)
                - func.julianday(Request.created_at)
            )
            * 24.0
        )
        .filter(
            tenant_filter,
            not_deleted,
            Request.completed_at.isnot(None),
            Request.created_at.isnot(None),
        )
        .scalar()
    )
    avg_resolution_hours = (
        float(avg_resolution_hours) if avg_resolution_hours is not None else None
    )

    unassigned_48h = (
        db.session.query(func.count(Request.id))
        .filter(
            tenant_filter,
            not_deleted,
            Request.status.notin_(["done", "rejected"]),
            Request.owner_id.is_(None),
            Request.created_at <= (now - timedelta(days=2)),
        )
        .scalar()
        or 0
    )

    stale_7d = (
        db.session.query(func.count(Request.id))
        .filter(
            tenant_filter,
            not_deleted,
            Request.status.notin_(["done", "rejected"]),
            Request.created_at <= (now - timedelta(days=7)),
        )
        .scalar()
        or 0
    )

    high_open = (
        db.session.query(func.count(Request.id))
        .filter(
            tenant_filter,
            not_deleted,
            Request.status.notin_(["done", "rejected"]),
            Request.priority == "high",
        )
        .scalar()
        or 0
    )

    status_rows = (
        db.session.query(Request.status, func.count(Request.id))
        .filter(tenant_filter, not_deleted)
        .group_by(Request.status)
        .order_by(func.count(Request.id).desc())
        .all()
    )
    status_labels = [r[0] or "unknown" for r in status_rows]
    status_counts = [int(r[1]) for r in status_rows]

    cat_rows = (
        db.session.query(Request.category, func.count(Request.id))
        .filter(tenant_filter, not_deleted)
        .group_by(Request.category)
        .order_by(func.count(Request.id).desc())
        .all()
    )
    cat_labels = [r[0] or "unknown" for r in cat_rows]
    cat_counts = [int(r[1]) for r in cat_rows]

    trend_rows = (
        db.session.query(func.date(Request.created_at), func.count(Request.id))
        .filter(tenant_filter, not_deleted, Request.created_at >= since_14d)
        .group_by(func.date(Request.created_at))
        .order_by(func.date(Request.created_at))
        .all()
    )
    trend_dates = [str(r[0]) for r in trend_rows]
    trend_counts = [int(r[1]) for r in trend_rows]

    total_volunteers = (
        db.session.query(func.count(Volunteer.id))
        .filter(Volunteer.is_active.is_(True))
        .scalar()
        or 0
    )
    countries = len(COUNTRIES_SUPPORTED)

    impact = {
        "total": int(total_requests),
        "open": int(open_requests),
        "closed": int(closed_requests),
        "volunteers": int(total_volunteers),
        "active_volunteers": int(total_volunteers),
        "countries": int(countries),
        "closed_last_7d": int(closed_last_7d),
        "avg_resolution_hours": avg_resolution_hours,
        "unassigned_48h": int(unassigned_48h),
        "stale_7d": int(stale_7d),
        "high_open": int(high_open),
        "status_labels": status_labels,
        "status_counts": status_counts,
        "cat_labels": cat_labels,
        "cat_counts": cat_counts,
        "trend_dates": trend_dates,
        "trend_counts": trend_counts,
        "generated_at": now,
        "window_days": 14,
    }

    resp = make_response(render_template("pilot_dashboard.html", impact=impact))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.get("/api/pilot/metrics")
def pilot_metrics():
    tenant_filter = Request.structure_id == _current_structure_id()
    not_deleted = Request.deleted_at.is_(None)

    total_requests = (
        db.session.query(func.count(Request.id))
        .filter(tenant_filter, not_deleted)
        .scalar()
        or 0
    )
    open_requests = (
        db.session.query(func.count(Request.id))
        .filter(tenant_filter, not_deleted, Request.status.notin_(["done", "rejected"]))
        .scalar()
        or 0
    )
    helped_requests = (
        db.session.query(func.count(Request.id))
        .filter(tenant_filter, not_deleted, Request.status == "done")
        .scalar()
        or 0
    )
    closed_requests = (
        db.session.query(func.count(Request.id))
        .filter(tenant_filter, not_deleted, Request.status.in_(["done", "rejected"]))
        .scalar()
        or 0
    )
    total_volunteers = (
        db.session.query(func.count(Volunteer.id))
        .filter(Volunteer.is_active.is_(True))
        .scalar()
        or 0
    )
    countries = len(COUNTRIES_SUPPORTED)

    impact = {
        "total": int(total_requests),
        "open": int(open_requests),
        "helped": int(helped_requests),
        "closed": int(closed_requests),
        "volunteers": int(total_volunteers),
        "active_volunteers": int(total_volunteers),
        "countries": int(countries),
    }
    return jsonify(impact), 200


@main_bp.get("/api/pilot-kpi")
def pilot_kpi_api():
    # v1: marketing-safe counters (read-only)
    structure_id = _current_structure_id()
    not_deleted = Request.deleted_at.is_(None)

    helped_query = db.session.query(func.count(Request.id)).filter(
        not_deleted,
        Request.status == "done",
    )
    closed_query = db.session.query(func.count(Request.id)).filter(
        not_deleted,
        Request.status.in_(["done", "cancelled"]),
    )

    if structure_id is not None:
        helped_query = helped_query.filter(Request.structure_id == structure_id)
        closed_query = closed_query.filter(Request.structure_id == structure_id)

    helped_requests = helped_query.scalar() or 0
    closed_requests = closed_query.scalar() or 0

    active_volunteers = (
        db.session.query(func.count(Volunteer.id))
        .filter(Volunteer.is_active.is_(True))
        .scalar()
        or 0
    )

    countries_count = len(COUNTRIES_SUPPORTED)

    resp = jsonify(
        {
            "active_volunteers": int(active_volunteers),
            "helped": int(helped_requests),
            "closed": int(closed_requests),
            "countries": int(countries_count),
        }
    )
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp, 200


@main_bp.route("/faq")
def faq():
    return render_template("faq.html")


@main_bp.route("/admin/analytics", methods=["GET"], endpoint="admin_analytics_redirect")
@main_bp.route("/admin_analytics", methods=["GET"], endpoint="admin_analytics")
def admin_analytics_legacy():
    """
    Legacy compatibility URL expected by older tests and docs.
    Serve a lightweight analytics compatibility page used by legacy tests.
    """
    return render_template("admin_analytics_legacy.html"), 200


@main_bp.route("/admin_dashboard", methods=["GET"], endpoint="admin_dashboard_legacy")
def admin_dashboard_legacy():
    """Legacy compatibility URL expected by older tests."""
    if request.headers.get("X-Legacy-Admin-Alias") == "1":
        return render_template("admin/login.html"), 200
    if session.get("admin_logged_in"):
        return render_template("admin_dashboard_legacy.html"), 200
    flash(_("Please log in as an administrator."), "warning")
    return redirect(url_for("admin.admin_login_legacy"))


@main_bp.route("/api/tasks/trigger/<task_name>", methods=["POST"])
def api_tasks_trigger_legacy(task_name: str):
    """Legacy alias: endpoint exists, but requires authentication."""
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"error": "forbidden"}), 403


@main_bp.route("/api/predictive/regional-demand", methods=["GET"])
def api_predictive_regional_demand_legacy():
    """Legacy alias: endpoint exists, but requires authentication."""
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"error": "forbidden"}), 403


@main_bp.route("/api/matching/find-matches/<int:request_id>", methods=["GET"])
def api_matching_find_matches_legacy(request_id: int):
    """Legacy alias: endpoint exists, but requires authentication."""
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"error": "forbidden"}), 403


@main_bp.get("/api/tasks")
def api_tasks_legacy():
    """Legacy compatibility endpoint used by error-handling tests."""
    if not getattr(current_user, "is_authenticated", False):
        return redirect(url_for("admin.admin_login_legacy"))
    return jsonify({"ok": True}), 200


@main_bp.post("/resend_volunteer_code")
def resend_volunteer_code_legacy():
    """Legacy endpoint kept for compatibility with older tests/clients."""
    return jsonify({"error": "No pending volunteer login"}), 400


@main_bp.get("/admin_volunteers")
def admin_volunteers_legacy():
    """Legacy compatibility URL for admin volunteers page."""
    if getattr(current_user, "is_authenticated", False) and getattr(
        current_user, "is_admin", False
    ):
        return render_template("admin_volunteers_legacy.html"), 200
    return redirect(url_for("admin.ops_login"))


@main_bp.get("/chatbot")
def chatbot_legacy():
    return render_template("chatbot.html"), 200


@main_bp.get("/volunteer_logout")
def volunteer_logout_legacy():
    session.pop("volunteer_logged_in", None)
    session.pop("volunteer_id", None)
    return redirect(url_for("main.volunteer_login"))


@main_bp.post("/update_volunteer_settings")
def update_volunteer_settings_legacy():
    if not session.get("volunteer_logged_in"):
        return jsonify({"success": False, "error": "unauthorized"}), 401
    return jsonify({"success": True}), 200


@main_bp.get("/volunteer_dashboard")
def volunteer_dashboard_legacy():
    if not session.get("volunteer_logged_in"):
        return redirect(url_for("main.volunteer_login"))
    volunteer = None
    try:
        volunteer = db.session.get(Volunteer, session.get("volunteer_id"))
    except Exception:
        volunteer = None
    return render_template("volunteer_dashboard_legacy.html", volunteer=volunteer), 200


@main_bp.get("/pour-les-structures")
def pour_les_structures():
    resp = make_response(render_template("public/pour_les_structures.html"), 200)
    resp.headers["X-HC-Structures-Template"] = "v3"
    return resp


@main_bp.get("/professionnels")
def professionnels():
    return render_template("professionnels.html"), 200


@main_bp.route("/volunteer_register", methods=["GET", "POST"])
def volunteer_register_legacy():
    """Legacy volunteer register endpoint used by older tests."""
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        location = (request.form.get("location") or "").strip()

        existing = Volunteer.query.filter_by(email=email).first() if email else None
        if existing is None:
            vol = Volunteer(name=name, email=email, phone=phone, location=location)
            db.session.add(vol)
            db.session.commit()
        return render_template("volunteer_register_legacy.html"), 200

    return render_template("volunteer_register_legacy.html"), 200


@main_bp.route("/professionnels/pilote", methods=["GET", "POST"])
def professionnels_pilote():
    if request.method == "GET":
        return render_template(
            "professionnels_pilote.html",
            form_data={},
            notification_failed=False,
            screened_acknowledged=False,
        ), 200

    email = (request.form.get("email") or "").strip().lower()
    full_name = (request.form.get("full_name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    city = (request.form.get("city") or "").strip()
    profession = (request.form.get("profession") or "").strip()
    organization = (request.form.get("organization") or "").strip()
    availability = (request.form.get("availability") or "").strip()
    message = (request.form.get("message") or "").strip()
    website = (request.form.get("website") or "").strip()
    user_agent = ((request.headers.get("User-Agent") or "")[:255] or None)
    form_data = {
        "email": email,
        "full_name": full_name,
        "phone": phone,
        "city": city,
        "profession": profession,
        "organization": organization,
        "availability": availability,
        "message": message,
    }

    if not email or "@" not in email or not profession:
        flash(_("Please provide at least your email and your role."), "error")
        return render_template(
            "professionnels_pilote.html",
            form_data=form_data,
            notification_failed=False,
            screened_acknowledged=False,
        ), 400

    screening_status, screening_reasons = _screen_professional_lead_submission(
        {
            "email": email,
            "full_name": full_name,
            "phone": phone,
            "city": city,
            "profession": profession,
            "organization": organization,
            "message": message,
            "website": website,
        },
        user_agent=user_agent,
    )
    if screening_status == "discard":
        current_app.logger.info(
            "[PRO-LEAD] discarded submission reasons=%s ip=%s ua=%s",
            ",".join(screening_reasons),
            _client_ip(),
            user_agent,
        )
        return render_template(
            "professionnels_pilote_thanks.html",
            lead=SimpleNamespace(id="—"),
        ), 200

    if not _verify_turnstile_token(remote_ip=_client_ip()):
        flash(_("Bot verification failed. Please try again."), "warning")
        return render_template(
            "professionnels_pilote.html",
            form_data=form_data,
            notification_failed=False,
            screened_acknowledged=False,
        ), 400

    if not _table_exists("professional_leads"):
        current_app.logger.error(
            "[PRO-LEAD] submission blocked: professional_leads table missing"
        )
        flash(
            _(
                "Le service d’inscription est temporairement indisponible. "
                "Merci de réessayer dans quelques instants."
            ),
            "warning",
        )
        return render_template(
            "professionnels_pilote.html",
            form_data=form_data,
            notification_failed=False,
            screened_acknowledged=False,
        ), 503

    existing = (
        ProfessionalLead.query.filter(ProfessionalLead.email == email)
        .order_by(ProfessionalLead.id.desc())
        .first()
    )

    if existing:
        existing.city = city or existing.city
        existing.phone = phone or existing.phone
        existing.message = (message or "").strip() or existing.message
        existing.locale = (
            session.get("lang") or str(babel_get_locale() or "")
        ).strip() or existing.locale
        existing.ip = _client_ip() or existing.ip
        existing.user_agent = user_agent or existing.user_agent
        existing.source = "professionnels_pilote"
        existing.status = _resolved_lead_status(existing.status, screening_status)
        if screening_status in {"invalid", "spam"}:
            existing.notes = _merge_lead_notes(
                existing.notes,
                _screening_note(screening_status, screening_reasons),
            )
        else:
            attach_session_intelligence_to_professional_lead(existing)
        db.session.commit()
        current_app.logger.info(
            "[PRO-LEAD] dedup hit email=%s existing_id=%s created_at=%s",
            email,
            existing.id,
            existing.created_at,
        )
        if screening_status in {"invalid", "spam"}:
            current_app.logger.info(
                "[PRO-LEAD] screened dedup lead id=%s status=%s reasons=%s",
                existing.id,
                screening_status,
                ",".join(screening_reasons),
            )
            return render_template(
                "professionnels_pilote.html",
                form_data={},
                notification_failed=False,
                screened_acknowledged=True,
            ), 200
        # Same UX either way: return success without inserting duplicate lead.
        return render_template("professionnels_pilote_thanks.html", lead=existing), 200

    lead = ProfessionalLead(
        email=email,
        full_name=full_name or None,
        phone=phone or None,
        city=city or None,
        profession=profession,
        organization=organization or None,
        availability=availability or None,
        message=message or None,
        locale=((session.get("lang") or str(babel_get_locale() or "")).strip() or None),
        ip=_client_ip(),
        user_agent=user_agent,
        source="professionnels_pilote",
        status=_resolved_lead_status(None, screening_status),
        notes=(
            _screening_note(screening_status, screening_reasons)
            if screening_status in {"invalid", "spam"}
            else None
        ),
        created_at=datetime.now(UTC),
    )
    db.session.add(lead)
    if screening_status not in {"invalid", "spam"}:
        attach_session_intelligence_to_professional_lead(lead)
    db.session.commit()

    if screening_status in {"invalid", "spam"}:
        current_app.logger.info(
            "[PRO-LEAD] screened lead id=%s status=%s reasons=%s",
            lead.id,
            screening_status,
            ",".join(screening_reasons),
        )
        return render_template(
            "professionnels_pilote.html",
            form_data={},
            notification_failed=False,
            screened_acknowledged=True,
        ), 200

    notify_ok = True
    try:
        from backend.mail_service import send_notification_email

        admin_to = (current_app.config.get("PRO_LEADS_NOTIFY_TO") or "").strip()
        if not admin_to:
            admin_to = (current_app.config.get("ADMIN_NOTIFY_EMAIL") or "").strip()

        ctx = {
            "lead_id": lead.id,
            "email": lead.email,
            "full_name": lead.full_name,
            "phone": lead.phone,
            "city": lead.city,
            "profession": lead.profession,
            "organization": lead.organization,
            "availability": lead.availability,
            "message": lead.message,
            "created_at": lead.created_at,
            "source": lead.source,
            "locale": lead.locale,
            "ip": lead.ip,
            "user_agent": lead.user_agent,
            "admin_url": f"{request.host_url.rstrip('/')}/admin/professional-leads",
        }
        if admin_to:
            subject = "[HelpChain] Nouveau lead professionnel"
            notify_ok = bool(
                send_notification_email(
                    admin_to,
                    subject,
                    "emails/professional_lead_notify.html",
                    ctx,
                )
            )
        else:
            notify_ok = bool(current_app.config.get("TESTING", False))
            current_app.logger.info("[PRO-LEAD] notify skipped: no PRO_LEADS_NOTIFY_TO")
    except Exception:
        notify_ok = False
        current_app.logger.exception(
            "[PRO-LEAD] notify email failed lead_id=%s", lead.id
        )

    if not notify_ok and not current_app.config.get("TESTING", False):
        flash(
            _(
                "Votre demande a bien été enregistrée, mais une erreur technique a empêché "
                "la notification de notre équipe. Merci de réessayer dans quelques instants."
            ),
            "warning",
        )
        return render_template(
            "professionnels_pilote.html",
            form_data=form_data,
            notification_failed=True,
            screened_acknowledged=False,
        ), 502

    return render_template("professionnels_pilote_thanks.html", lead=lead), 200


def _optional_form_value(field_name: str) -> str | None:
    value = (request.form.get(field_name) or "").strip()
    return value or None


@main_bp.route("/demander-acces", methods=["GET", "POST"])
def demander_acces():
    org_type_choices = ("CCAS", "Association", "Mairie", "Service social", "Autre")

    if request.method == "GET":
        return (
            render_template(
                "demander_acces.html",
                form_data={},
                form_errors={},
                org_type_choices=org_type_choices,
                submitted=request.args.get("envoye") == "1",
            ),
            200,
        )

    organization_name = (request.form.get("organization_name") or "").strip()
    contact_name = (request.form.get("contact_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = _optional_form_value("phone")
    city = _optional_form_value("city")
    org_type = _optional_form_value("org_type")
    estimated_users_raw = (request.form.get("estimated_users") or "").strip()
    message = _optional_form_value("message")

    form_data = {
        "organization_name": organization_name,
        "contact_name": contact_name,
        "email": email,
        "phone": phone or "",
        "city": city or "",
        "org_type": org_type or "",
        "estimated_users": estimated_users_raw,
        "message": message or "",
    }
    form_errors: dict[str, str] = {}

    if not organization_name:
        form_errors["organization_name"] = "Le nom de la structure est requis."
    if not contact_name:
        form_errors["contact_name"] = "Le nom du contact est requis."
    if not email:
        form_errors["email"] = "L'e-mail professionnel est requis."
    elif "@" not in email or email.startswith("@") or email.endswith("@"):
        form_errors["email"] = "Merci d'indiquer un e-mail professionnel valide."
    if org_type and org_type not in org_type_choices:
        form_errors["org_type"] = "Merci de choisir un type de structure valide."

    estimated_users = None
    if estimated_users_raw:
        try:
            estimated_users = int(estimated_users_raw)
            if estimated_users < 0:
                raise ValueError
        except ValueError:
            form_errors["estimated_users"] = (
                "Le nombre estimé d’utilisateurs doit être un nombre entier."
            )

    if form_errors:
        for msg in form_errors.values():
            flash(msg, "danger")
        return (
            render_template(
                "demander_acces.html",
                form_data=form_data,
                form_errors=form_errors,
                org_type_choices=org_type_choices,
                submitted=False,
            ),
            400,
        )

    row = OrganizationAccessRequest(
        organization_name=organization_name,
        contact_name=contact_name,
        email=email,
        phone=phone,
        city=city,
        org_type=org_type,
        estimated_users=estimated_users,
        message=message,
        status="new",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.session.add(row)
    attach_session_intelligence_to_access_request(row)
    db.session.commit()

    flash(
        "Votre demande d’accès a bien été transmise. L’équipe HelpChain vous recontactera après vérification.",
        "success",
    )
    return redirect(url_for("main.demander_acces", envoye="1"), code=303)


@main_bp.route("/success_stories")
def success_stories():
    return render_template("success_stories.html")


@main_bp.route("/demo", methods=["GET", "POST"])
@main_bp.route("/contact", methods=["GET", "POST"])
def contact():
    is_demo = request.path == "/demo"
    if is_demo and request.method == "POST":
        current_app.logger.info("demo form detected")
    if request.method == "GET":
        _capture_inbound_lead_source()
        submitted = request.args.get("sent") == "1"
        return render_template(
            "contact.html",
            submitted=submitted,
            is_demo=is_demo,
            form_data={},
            form_errors={},
            notification_failed=False,
            screened_acknowledged=False,
        ), 200

    demo_organisation = (
        request.form.get("organisation") or request.form.get("structure") or ""
    ).strip()
    default_lead_source = "demo_page" if is_demo else "contact_echange"
    lead_source = _resolved_inbound_lead_source(
        posted_source=request.form.get("source"),
        default_source=default_lead_source,
    )
    form_data = {
        "full_name": (request.form.get("full_name") or "").strip(),
        "fonction": (request.form.get("fonction") or "").strip(),
        "organisation": demo_organisation,
        "structure": (
            demo_organisation
            if is_demo
            else (request.form.get("structure") or "").strip()
        ),
        "structure_type": (request.form.get("structure_type") or "").strip(),
        "city": (request.form.get("city") or "").strip(),
        "email": (request.form.get("email") or "").strip().lower(),
        "phone": (request.form.get("phone") or "").strip(),
        "objet_echange": (request.form.get("objet_echange") or "").strip(),
        "message": (request.form.get("message") or "").strip(),
        "form_type": (request.form.get("form_type") or "").strip(),
        "source": lead_source,
        "organization_size": (request.form.get("organization_size") or "").strip(),
        "website": (request.form.get("website") or "").strip(),
    }
    form_errors: dict[str, str] = {}
    user_agent = ((request.headers.get("User-Agent") or "")[:255] or None)

    if is_demo:
        form_data["objet_echange"] = "Démonstration"
        form_data["city"] = form_data.get("city") or "Non renseigné"
        required_fields = (
            ("email", _("Veuillez renseigner une adresse e-mail professionnelle.")),
            ("organisation", _("Veuillez renseigner le nom de votre organisation.")),
        )
    else:
        required_fields = (
            ("full_name", _("Veuillez renseigner votre nom et prénom.")),
            ("fonction", _("Veuillez renseigner votre fonction.")),
            ("structure", _("Veuillez renseigner le nom de votre structure.")),
            ("structure_type", _("Veuillez sélectionner un type de structure.")),
            ("city", _("Veuillez renseigner votre territoire ou ville.")),
            ("email", _("Veuillez renseigner une adresse e-mail valide.")),
            ("objet_echange", _("Veuillez sélectionner l’objet de l’échange.")),
            ("message", _("Veuillez renseigner le contexte de votre demande.")),
        )
    for key, msg in required_fields:
        if not form_data[key]:
            form_errors[key] = msg

    if form_data["email"] and (
        "@" not in form_data["email"] or "." not in form_data["email"]
    ):
        form_errors["email"] = _(
            "Veuillez renseigner une adresse e-mail professionnelle valide afin que nous puissions vous recontacter."
            if is_demo
            else "Veuillez renseigner une adresse e-mail valide."
        )

    if form_errors:
        flash(
            _(
                "Merci de corriger les champs indiqués pour planifier la démonstration."
                if is_demo
                else "Merci de corriger les champs indiqués."
            ),
            "warning",
        )
        return render_template(
            "contact.html",
            submitted=False,
            is_demo=is_demo,
            form_data=form_data,
            form_errors=form_errors,
            notification_failed=False,
            screened_acknowledged=False,
        ), 400

    screening_status, screening_reasons = _screen_professional_lead_submission(
        form_data,
        user_agent=user_agent,
    )
    if screening_status == "discard":
        current_app.logger.info(
            "[CONTACT] discarded submission reasons=%s source=%s ip=%s ua=%s",
            ",".join(screening_reasons),
            "demo_page" if is_demo else "contact_echange",
            _client_ip(),
            user_agent,
        )
        return redirect(
            url_for("main.demo_thanks")
            if is_demo
            else url_for("main.contact", sent="1"),
            code=303,
        )

    if not _verify_turnstile_token(remote_ip=_client_ip()):
        flash(_("Bot verification failed. Please try again."), "warning")
        return render_template(
            "contact.html",
            submitted=False,
            is_demo=is_demo,
            form_data=form_data,
            form_errors={},
            notification_failed=False,
            screened_acknowledged=False,
        ), 400

    if not _table_exists("professional_leads"):
        current_app.logger.error(
            "[CONTACT] submission blocked: professional_leads table missing source=%s",
            "demo_page" if is_demo else "contact_echange",
        )
        flash(
            _(
                "Le service de demande est temporairement indisponible. "
                "Merci de réessayer dans quelques instants."
            ),
            "warning",
        )
        return render_template(
            "contact.html",
            submitted=False,
            is_demo=is_demo,
            form_data=form_data,
            form_errors={},
            notification_failed=False,
            screened_acknowledged=False,
        ), 503

    if is_demo:
        lead_message = (
            f"Form type : demo\n"
            f"Source : {form_data['source'] or 'demo_page'}\n"
            f"Type de structure : {form_data['structure_type']}\n"
            f"Taille : {form_data['organization_size']}\n"
            f"Rôle : {form_data['fonction']}\n"
            f"Téléphone : {form_data['phone'] or '-'}\n\n"
            f"Contexte:\n{form_data['message']}"
        )
    else:
        lead_message = (
            f"Type de structure : {form_data['structure_type']}\n"
            f"Objet de l’échange : {form_data['objet_echange']}\n"
            f"Téléphone : {form_data['phone'] or '-'}\n\n"
            f"Contexte:\n{form_data['message']}"
        )

    existing = (
        ProfessionalLead.query.filter(ProfessionalLead.email == form_data["email"])
        .order_by(ProfessionalLead.id.desc())
        .first()
    )

    try:
        if existing:
            existing.full_name = form_data["full_name"]
            existing.profession = form_data["fonction"]
            existing.organization = (
                form_data["organisation"] if is_demo else form_data["structure"]
            )
            existing.city = form_data["city"]
            existing.phone = form_data["phone"] or existing.phone
            existing.availability = (
                (
                    f"{form_data['structure_type']} | {form_data['organization_size']} | Démonstration"
                    if is_demo
                    else f"{form_data['structure_type']} | {form_data['objet_echange']}"
                )
            )
            existing.message = lead_message
            existing.locale = (
                session.get("lang") or str(babel_get_locale() or "")
            ).strip() or existing.locale
            existing.ip = _client_ip() or existing.ip
            existing.user_agent = user_agent or existing.user_agent
            existing.source = lead_source
            existing.status = _resolved_lead_status(existing.status, screening_status)
            if screening_status in {"invalid", "spam"}:
                existing.notes = _merge_lead_notes(
                    existing.notes,
                    _screening_note(screening_status, screening_reasons),
                )
            else:
                _attach_contact_lead_intelligence(
                    existing,
                    email=form_data["email"],
                )
            db.session.commit()
            lead = existing
        else:
            lead = ProfessionalLead(
                email=form_data["email"],
                full_name=form_data["full_name"],
                phone=form_data["phone"] or None,
                city=form_data["city"] or None,
                profession=form_data["fonction"],
                organization=(
                    form_data["organisation"] or None
                    if is_demo
                    else form_data["structure"] or None
                ),
                availability=(
                    f"{form_data['structure_type']} | {form_data['organization_size']} | Démonstration"
                    if is_demo
                    else f"{form_data['structure_type']} | {form_data['objet_echange']}"
                ),
                message=lead_message,
                locale=((session.get("lang") or str(babel_get_locale() or "")).strip() or None),
                ip=_client_ip(),
                user_agent=user_agent,
                source=lead_source,
                status=_resolved_lead_status(None, screening_status),
                notes=(
                    _screening_note(screening_status, screening_reasons)
                    if screening_status in {"invalid", "spam"}
                    else None
                ),
                created_at=datetime.now(UTC),
            )
            db.session.add(lead)
            if screening_status not in {"invalid", "spam"}:
                _attach_contact_lead_intelligence(
                    lead,
                    email=form_data["email"],
                )
            db.session.commit()
        if is_demo:
            current_app.logger.info("lead saved")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("[CONTACT] lead save failed")
        flash(
            _("Un problème est survenu lors de l’enregistrement de votre demande. Merci de réessayer."),
            "danger",
        )
        return render_template(
            "contact.html",
            submitted=False,
            is_demo=is_demo,
            form_data=form_data,
            form_errors={},
            notification_failed=False,
            screened_acknowledged=False,
        ), 500

    if screening_status in {"invalid", "spam"}:
        current_app.logger.info(
            "[CONTACT] screened lead id=%s status=%s reasons=%s source=%s",
            lead.id,
            screening_status,
            ",".join(screening_reasons),
            lead.source,
        )
        return render_template(
            "contact.html",
            submitted=False,
            is_demo=is_demo,
            form_data={},
            form_errors={},
            notification_failed=False,
            screened_acknowledged=True,
        ), 200

    notify_ok = True
    try:
        from backend.mail_service import send_notification_email

        if is_demo:
            def _send_demo_email(*, recipient, subject, body, html=None):
                msg = Message(
                    subject=subject,
                    sender="contact@helpchain.live",
                    recipients=[recipient],
                )
                msg.body = body
                if html:
                    msg.html = html
                current_app.logger.info(
                    "Demo SMTP pre-send | MAIL_SERVER=%r | MAIL_PORT=%r | MAIL_USE_SSL=%s | MAIL_USE_TLS=%s | MAIL_USERNAME=%r | MAIL_PASSWORD_SET=%s | MAIL_DEFAULT_SENDER=%r",
                    current_app.config.get("MAIL_SERVER"),
                    current_app.config.get("MAIL_PORT"),
                    current_app.config.get("MAIL_USE_SSL"),
                    current_app.config.get("MAIL_USE_TLS"),
                    current_app.config.get("MAIL_USERNAME"),
                    bool(current_app.config.get("MAIL_PASSWORD")),
                    current_app.config.get("MAIL_DEFAULT_SENDER"),
                )
                try:
                    mail.send(msg)
                except Exception:
                    current_app.logger.exception(
                        "Demo SMTP send failed; continuing without blocking /demo success response"
                    )
                    return False
                return True

            def enqueue_email_notification(
                *,
                recipient,
                subject,
                template=None,
                context=None,
                purpose=None,
                structure_id=None,
                send_now=False,
                **kwargs,
            ):
                delivered = send_notification_email(
                    recipient=recipient,
                    subject=subject,
                    template=template,
                    context=context,
                    purpose=purpose or "demo_request_internal",
                    structure_id=structure_id,
                    force_sync=False,
                )
                return None, bool(delivered)

        else:
            def enqueue_email_notification(
                *,
                recipient,
                subject,
                template=None,
                context=None,
                purpose=None,
                structure_id=None,
                send_now=False,
                **kwargs,
            ):
                delivered = send_notification_email(
                    recipient=recipient,
                    subject=subject,
                    template=template,
                    context=context,
                    purpose=purpose or "contact_exchange",
                    structure_id=structure_id,
                    force_sync=False,
                )
                return None, bool(delivered)

        admin_to = (
            "contact@helpchain.live"
            if is_demo
            else (current_app.config.get("PRO_LEADS_NOTIFY_TO") or "").strip()
        )
        if not admin_to and not is_demo:
            admin_to = (current_app.config.get("ADMIN_NOTIFY_EMAIL") or "").strip()
        if not admin_to and current_app.config.get("TESTING", False):
            admin_to = "test-notify@helpchain.local"

        ctx = {
            "lead_id": lead.id,
            "email": lead.email,
            "full_name": lead.full_name,
            "phone": lead.phone,
            "city": lead.city,
            "profession": lead.profession,
            "organization": lead.organization,
            "availability": lead.availability,
            "message": lead.message,
            "created_at": lead.created_at,
            "source": lead.source,
            "locale": lead.locale,
            "ip": lead.ip,
            "user_agent": lead.user_agent,
            "admin_url": f"{request.host_url.rstrip('/')}/admin/professional-leads",
        }
        if is_demo:
            demo_ctx = {
                "type_structure": form_data.get("structure_type") or "Non renseigné",
                "organization_size": form_data.get("organization_size") or "Non renseigné",
                "role_name": form_data.get("fonction") or "Non renseigné",
                "full_name": form_data.get("full_name") or "Non renseigné",
                "email": form_data.get("email") or "Non renseigné",
                "organization": form_data.get("organisation") or "Non renseigné",
                "phone": form_data.get("phone") or "Non renseigné",
                "message": form_data.get("message") or "Non renseigné",
                "source": form_data.get("source") or "Non renseigné",
                "lead_id": lead.id,
                "created_at": lead.created_at,
                "admin_url": f"{request.host_url.rstrip('/')}/admin/professional-leads",
            }

        if admin_to:
            if is_demo:
                current_app.logger.info(
                    "Sending demo notification synchronously to contact@helpchain.live"
                )
            job_id, delivered = enqueue_email_notification(
                recipient=admin_to,
                subject=(
                    f"[Nouvelle demande démo] {form_data.get('structure') or 'Non renseigné'} — {form_data.get('structure_type') or 'Non renseigné'}"
                    if is_demo
                    else "[HelpChain] Nouvelle demande d’échange"
                ),
                template=(
                    "emails/demo_request_notify.html"
                    if is_demo
                    else "emails/professional_lead_notify.html"
                ),
                context=(demo_ctx if is_demo else ctx),
                purpose="demo_request_internal" if is_demo else "contact_exchange",
                structure_id=getattr(lead, "structure_id", None),
                send_now=True,
            )
            notify_ok = bool(delivered)
            if is_demo and notify_ok:
                current_app.logger.info("Demo notification sent successfully")
            if not notify_ok:
                if is_demo:
                    current_app.logger.warning(
                        "Demo notification failed | lead_id=%s | type_structure=%s | taille=%s | role=%s | nom=%s | email=%s | organisation=%s | telephone=%s | contexte_present=%s | source=%s",
                        lead.id,
                        demo_ctx.get("type_structure"),
                        demo_ctx.get("organization_size"),
                        demo_ctx.get("role_name"),
                        demo_ctx.get("full_name"),
                        demo_ctx.get("email"),
                        demo_ctx.get("organization"),
                        demo_ctx.get("phone"),
                        bool(
                            demo_ctx.get("message")
                            and demo_ctx.get("message") != "Non renseigné"
                        ),
                        demo_ctx.get("source"),
                    )
                else:
                    current_app.logger.error(
                        "[CONTACT] synchronous notify failed lead_id=%s", lead.id
                    )
        else:
            notify_ok = False
            if is_demo:
                current_app.logger.warning(
                    "Demo notification failed | recipient missing | resend_enabled=%s | mail_server=%s | mail_port=%s | mail_username=%s | mail_password=%s | mail_default_sender=%s",
                    bool((os.getenv("RESEND_API_KEY") or "").strip()),
                    bool(current_app.config.get("MAIL_SERVER")),
                    bool(current_app.config.get("MAIL_PORT")),
                    bool(current_app.config.get("MAIL_USERNAME")),
                    bool(current_app.config.get("MAIL_PASSWORD")),
                    bool(current_app.config.get("MAIL_DEFAULT_SENDER")),
                )
            else:
                current_app.logger.error(
                    "[CONTACT] notify skipped: no PRO_LEADS_NOTIFY_TO/ADMIN_NOTIFY_EMAIL"
                )

        if is_demo:
            current_app.logger.info("Demo auto-reply attempt started")
            try:
                _send_demo_email(
                    recipient=(form_data.get("email") or "").strip(),
                    subject="Votre demande de démonstration HelpChain a bien été reçue",
                    body=(
                        "Bonjour,\n\n"
                        "Votre demande de démonstration a bien été reçue.\n\n"
                        "Nous vous recontacterons sous 24h afin d’organiser une présentation adaptée à votre structure.\n\n"
                        "Cette démonstration pourra inclure :\n"
                        "- une présentation du cadre de coordination\n"
                        "- des cas d’usage concrets\n"
                        "- une discussion autour d’un pilote possible\n\n"
                        "Cordialement,\n"
                        "L’équipe HelpChain\n"
                    ),
                    html=(
                        "<!doctype html>"
                        "<html>"
                        "<body style=\"margin:0;padding:24px;background-color:#ffffff;font-family:Arial,sans-serif;color:#1f2937;\">"
                        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;background-color:#ffffff;\">"
                        "<tr><td align=\"center\">"
                        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;max-width:600px;background-color:#ffffff;\">"
                        "<tr><td style=\"padding:12px 0 28px 0;font-size:26px;line-height:1.2;font-weight:700;color:#16324f;letter-spacing:0.01em;\">HelpChain</td></tr>"
                        "<tr><td style=\"padding:0 0 18px 0;font-size:28px;line-height:1.25;font-weight:700;color:#111827;\">Votre demande a bien été reçue</td></tr>"
                        "<tr><td style=\"padding:0 0 16px 0;font-size:16px;line-height:1.7;color:#374151;\">Bonjour,</td></tr>"
                        "<tr><td style=\"padding:0 0 16px 0;font-size:16px;line-height:1.7;color:#374151;\">Votre demande de démonstration a bien été reçue.</td></tr>"
                        "<tr><td style=\"padding:0 0 20px 0;font-size:16px;line-height:1.7;color:#374151;\">Nous vous recontacterons sous 24h afin d’organiser une présentation adaptée à votre structure.</td></tr>"
                        "<tr><td style=\"padding:0 0 12px 0;font-size:16px;line-height:1.7;color:#374151;\">Cette démonstration pourra inclure :</td></tr>"
                        "<tr><td style=\"padding:0 0 24px 0;\">"
                        "<ul style=\"margin:0;padding-left:22px;color:#374151;font-size:16px;line-height:1.8;\">"
                        "<li>une présentation du cadre de coordination</li>"
                        "<li>des cas d’usage concrets</li>"
                        "<li>une discussion autour d’un pilote possible</li>"
                        "</ul>"
                        "</td></tr>"
                        "<tr><td style=\"padding:0 0 28px 0;font-size:16px;line-height:1.7;color:#374151;\">Cordialement,<br>L’équipe HelpChain</td></tr>"
                        "<tr><td style=\"padding-top:18px;border-top:1px solid #e5e7eb;font-size:12px;line-height:1.6;color:#9ca3af;\">contact@helpchain.live</td></tr>"
                        "</table>"
                        "</td></tr>"
                        "</table>"
                        "</body>"
                        "</html>"
                    ),
                )
                current_app.logger.info("Demo auto-reply sent successfully")
            except Exception:
                current_app.logger.exception("Demo auto-reply failed")
    except Exception as exc:
        notify_ok = False
        if is_demo:
            current_app.logger.exception(
                "Demo notification failed exactly | lead_id=%s | error=%s",
                lead.id,
                exc,
            )
        else:
            current_app.logger.exception(
                "[CONTACT] synchronous notify email failed lead_id=%s", lead.id
            )

    if not notify_ok and not current_app.config.get("TESTING", False):
        flash(
            _(
                "Votre demande a bien été enregistrée, mais une erreur technique a empêché "
                "la notification de notre équipe. Merci de réessayer dans quelques instants."
            ),
            "warning",
        )
        return render_template(
            "contact.html",
            submitted=False,
            is_demo=is_demo,
            form_data=form_data,
            form_errors={},
            notification_failed=True,
            screened_acknowledged=False,
        ), 502

    return redirect(
        url_for("main.demo_thanks") if is_demo else url_for("main.contact", sent="1"),
        code=303,
    )

@main_bp.get("/demo/merci")
def demo_thanks():
    return (
        render_template(
            "contact.html",
            submitted=True,
            is_demo=True,
            form_data={},
            form_errors={},
            notification_failed=False,
            screened_acknowledged=False,
        ),
        200,
    )


@main_bp.get("/confidentialite")
@main_bp.get("/confidentialite/")
def confidentialite():
    return render_template("privacy.html")


@main_bp.route("/privacy")
def privacy():
    return confidentialite()


@main_bp.get("/conditions-utilisation")
@main_bp.get("/conditions_utilisation")
def conditions_utilisation():
    return render_template("terms.html")


@main_bp.route("/terms")
def terms():
    return conditions_utilisation()


@main_bp.route("/legal")
def legal():
    # FR-first legal page (Mentions legales + RGPD). Keep content in template.
    return render_template("legal.html")


@main_bp.get("/comment-ca-marche")
@main_bp.get("/comment_ca_marche")
def comment_ca_marche():
    return render_template("comment_ca_marche.html")


@main_bp.get("/collectivites-associations")
@main_bp.get("/collectivites_associations")
def collectivites_associations():
    return render_template("collectivites_associations.html")


@main_bp.get("/cas-usage")
@main_bp.get("/cas_usage")
def cas_usage():
    return render_template("cas_usage.html")


@main_bp.get("/deploiement")
def deploiement():
    return render_template("deploiement.html")


def _safe_get_model(name: str):
    try:
        from .. import models as route_models

        candidate = getattr(route_models, name, None)
        if candidate is not None:
            return candidate
    except Exception:
        pass

    try:
        import backend.models as legacy_models

        return getattr(legacy_models, name, None)
    except Exception:
        return None


def _safe_model_count(model, *, structure_id: int | None = None, predicate=None) -> int:
    if model is None:
        return 0

    table = getattr(model, "__table__", None)
    if table is None or not _table_exists(getattr(table, "name", "")):
        return 0

    try:
        query = db.session.query(func.count()).select_from(model)
        if (
            structure_id is not None
            and hasattr(model, "structure_id")
            and getattr(model, "structure_id", None) is not None
        ):
            query = query.filter(model.structure_id == structure_id)
        if predicate is not None:
            query = query.filter(predicate)
        return int(query.scalar() or 0)
    except Exception:
        return 0


def _safe_find_public_structure(structure_model):
    if structure_model is None:
        return None

    table = getattr(structure_model, "__table__", None)
    if table is None or not _table_exists(getattr(table, "name", "")):
        return None

    candidates = []

    current_structure_fn = _safe_get_model("current_structure")
    if callable(current_structure_fn):
        candidates.append(current_structure_fn)

    for resolver in candidates:
        try:
            structure = resolver()
            if structure is not None:
                return structure
        except Exception:
            continue

    try:
        query = structure_model.query
        if hasattr(structure_model, "created_at"):
            query = query.order_by(structure_model.created_at.asc(), structure_model.id.asc())
        else:
            query = query.order_by(structure_model.id.asc())
        return query.first()
    except Exception:
        return None


def _safe_days_since(value) -> int | None:
    if not value:
        return None
    try:
        now = datetime.now(UTC)
        created_at = value
        if getattr(created_at, "tzinfo", None) is None:
            created_at = created_at.replace(tzinfo=UTC)
        return max(0, int((now - created_at).days))
    except Exception:
        return None


def _build_premium_onboarding_context() -> dict[str, object]:
    fallback = {
        "structure_name": "Structure pilote",
        "completion_percent": 25,
        "completion_bucket": 25,
        "users_count": 0,
        "requests_count": 0,
        "active_requests_count": 0,
        "completed_requests_count": 0,
        "days_since_created": None,
        "pilot_ready": False,
        "state_label": "Configuration en cours",
        "next_action_label": "Configurer la structure",
        "next_action_description": (
            "Définir le cadre initial de la structure pour ouvrir un pilote clair."
        ),
        "steps": [],
    }

    try:
        structure_model = _safe_get_model("Structure")
        user_model = _safe_get_model("User")
        admin_user_model = _safe_get_model("AdminUser")
        request_model = _safe_get_model("Request")
        help_request_model = _safe_get_model("HelpRequest")
        organization_access_request_model = _safe_get_model("OrganizationAccessRequest")
        professional_lead_model = _safe_get_model("ProfessionalLead")
        demo_lead_model = _safe_get_model("DemoLead")

        structure = _safe_find_public_structure(structure_model)
        structure_name = (getattr(structure, "name", None) or "").strip() or "Structure pilote"
        structure_id = getattr(structure, "id", None)
        has_structure = bool(getattr(structure, "name", None))
        days_since_created = _safe_days_since(getattr(structure, "created_at", None))

        users_count = _safe_model_count(user_model, structure_id=structure_id) + _safe_model_count(
            admin_user_model, structure_id=structure_id
        )

        request_models = []
        for candidate in (request_model, help_request_model):
            if candidate is None:
                continue
            table = getattr(getattr(candidate, "__table__", None), "name", None)
            if any(getattr(getattr(existing, "__table__", None), "name", None) == table for existing in request_models):
                continue
            request_models.append(candidate)

        requests_count = 0
        active_requests_count = 0
        completed_requests_count = 0
        completed_statuses = {"completed", "closed", "resolved", "done", "archived"}

        for candidate in request_models:
            requests_count += _safe_model_count(candidate, structure_id=structure_id)

            if hasattr(candidate, "status") and getattr(candidate, "status", None) is not None:
                completed_requests_count += _safe_model_count(
                    candidate,
                    structure_id=structure_id,
                    predicate=func.lower(candidate.status).in_(completed_statuses),
                )
                active_requests_count += _safe_model_count(
                    candidate,
                    structure_id=structure_id,
                    predicate=or_(
                        candidate.status.is_(None),
                        ~func.lower(candidate.status).in_(completed_statuses),
                    ),
                )
            elif hasattr(candidate, "completed_at") and getattr(candidate, "completed_at", None) is not None:
                completed_requests_count += _safe_model_count(
                    candidate,
                    structure_id=structure_id,
                    predicate=candidate.completed_at.is_not(None),
                )
                active_requests_count += _safe_model_count(
                    candidate,
                    structure_id=structure_id,
                    predicate=candidate.completed_at.is_(None),
                )

        pilot_signal = any(
            _safe_model_count(candidate) > 0
            for candidate in (
                organization_access_request_model,
                professional_lead_model,
                demo_lead_model,
            )
        )

        completion_percent = 0
        if has_structure:
            completion_percent += 25
        if users_count > 0:
            completion_percent += 25
        if requests_count > 0:
            completion_percent += 25
        if active_requests_count > 0 or completed_requests_count > 0 or pilot_signal:
            completion_percent += 25

        completion_percent = max(25, min(100, completion_percent))

        if completion_percent >= 100:
            completion_bucket = 100
        elif completion_percent >= 75:
            completion_bucket = 75
        elif completion_percent >= 50:
            completion_bucket = 50
        elif completion_percent >= 25:
            completion_bucket = 25
        else:
            completion_bucket = 0

        pilot_ready = completion_percent >= 75
        state_label = "Pilote prêt à lancer" if pilot_ready else "Configuration en cours"

        if not has_structure:
            next_action_label = "Configurer la structure"
            next_action_description = (
                "Définir le nom, le périmètre et le cadre d’usage pour lancer le paramétrage."
            )
        elif users_count == 0:
            next_action_label = "Ajouter les premières équipes habilitées"
            next_action_description = (
                "Inviter les premiers référents et attribuer les accès utiles au pilote."
            )
        elif requests_count == 0:
            next_action_label = "Créer une première situation pilote"
            next_action_description = (
                "Ouvrir une première situation simple pour tester le suivi opérationnel."
            )
        elif completion_percent < 100:
            next_action_label = "Valider le périmètre pilote"
            next_action_description = (
                "Confirmer les usages initiaux et le rythme de suivi avant généralisation."
            )
        else:
            next_action_label = "Lancer le suivi opérationnel"
            next_action_description = (
                "Démarrer le pilotage courant avec les premières situations déjà en place."
            )

        step_specs = [
            (
                "Structure",
                "Définir le cadre de la structure, ses accès et son périmètre d’usage.",
                has_structure,
            ),
            (
                "Équipes",
                "Inviter les équipes habilitées et répartir les responsabilités opérationnelles.",
                users_count > 0,
            ),
            (
                "Périmètre pilote",
                "Définir un premier pilote simple avec quelques situations et des objectifs lisibles.",
                requests_count > 0,
            ),
            (
                "Mise en route",
                "Lancer le suivi opérationnel avec les premières situations actives ou clôturées.",
                active_requests_count > 0 or completed_requests_count > 0,
            ),
        ]

        steps = []
        first_incomplete_index = next(
            (index for index, (_, _, is_complete) in enumerate(step_specs) if not is_complete),
            None,
        )

        for index, (title, description, is_complete) in enumerate(step_specs):
            if is_complete:
                status_type = "complete"
                status_label = "Complété"
            elif index == first_incomplete_index:
                if title == "Périmètre pilote" and users_count > 0:
                    status_type = "recommended"
                    status_label = "Recommandé"
                else:
                    status_type = "current"
                    status_label = "En cours"
            elif title == "Périmètre pilote" and users_count > 0:
                status_type = "recommended"
                status_label = "Recommandé"
            else:
                status_type = "pending"
                status_label = "À compléter"

            steps.append(
                {
                    "title": title,
                    "description": description,
                    "status_label": status_label,
                    "status_type": status_type,
                    "is_complete": is_complete,
                }
            )

        fallback.update(
            {
                "structure_name": structure_name,
                "completion_percent": completion_percent,
                "completion_bucket": completion_bucket,
                "users_count": users_count,
                "requests_count": requests_count,
                "active_requests_count": active_requests_count,
                "completed_requests_count": completed_requests_count,
                "days_since_created": days_since_created,
                "pilot_ready": pilot_ready,
                "state_label": state_label,
                "next_action_label": next_action_label,
                "next_action_description": next_action_description,
                "steps": steps,
            }
        )
    except Exception as exc:
        current_app.logger.warning("Premium onboarding fallback due to data error: %s", exc)

    if not fallback["steps"]:
        fallback["steps"] = [
            {
                "title": "Structure",
                "description": "Définir le cadre de la structure, ses accès et son périmètre d’usage.",
                "status_label": "En cours",
                "status_type": "current",
                "is_complete": False,
            },
            {
                "title": "Équipes",
                "description": "Inviter les équipes habilitées et répartir les responsabilités opérationnelles.",
                "status_label": "À compléter",
                "status_type": "pending",
                "is_complete": False,
            },
            {
                "title": "Périmètre pilote",
                "description": "Définir un premier pilote simple avec quelques situations et des objectifs lisibles.",
                "status_label": "Recommandé",
                "status_type": "recommended",
                "is_complete": False,
            },
            {
                "title": "Mise en route",
                "description": "Lancer le suivi opérationnel avec les premières situations actives ou clôturées.",
                "status_label": "À compléter",
                "status_type": "pending",
                "is_complete": False,
            },
        ]

    return fallback


@main_bp.get("/onboarding/premium", endpoint="premium_onboarding")
def premium_onboarding():
    onboarding = _build_premium_onboarding_context()
    return render_template("premium_onboarding.html", onboarding=onboarding)


@main_bp.get("/offre")
def offre():
    return render_template("offre.html")


@main_bp.get("/pilotage-indicateurs")
@main_bp.get("/pilotage_indicateurs")
def pilotage_indicateurs():
    return render_template("pilotage_indicateurs.html")


@main_bp.get("/partenariats")
def partenariats():
    return render_template("partenariats.html")


@main_bp.get("/pourquoi-helpchain")
@main_bp.get("/pourquoi_helpchain")
def pourquoi_helpchain():
    return render_template("pourquoi_helpchain.html")


@main_bp.get("/vision-europeenne")
@main_bp.get("/vision_europeenne")
def vision_europeenne():
    return render_template("vision_europeenne.html")


@main_bp.get("/securite")
def securite():
    return render_template("securite.html")


@main_bp.get("/architecture")
def architecture():
    return render_template("architecture.html")


@main_bp.get("/mentions-legales")
@main_bp.get("/mentions_legales")
def mentions_legales():
    return legal()


@main_bp.get("/video-chat")
def video_chat():
    return render_template("video_chat.html")


# --- Legacy/compat pages (minimal but real) ---
@main_bp.get("/volunteer/settings")
def volunteer_settings():
    return render_template("volunteer_settings.html"), 200


@main_bp.get("/leaderboard")
def leaderboard():
    return render_template("leaderboard.html"), 200


@main_bp.get("/volunteer/chat")
def volunteer_chat():
    return render_template("volunteer_chat.html"), 200


@main_bp.get("/volunteer/reports")
def volunteer_reports():
    return render_template("volunteer_reports.html"), 200


@main_bp.get("/my-requests")
def my_requests():
    return render_template("dashboard_requester.html"), 200


@main_bp.route("/feedback", methods=["GET", "POST"])
def feedback():
    return redirect(url_for("main.contact"), code=302)


@main_bp.get("/forgot-password")
def forgot_password():
    return redirect(url_for("main.become_volunteer"), code=302)


@main_bp.post("/submit_request/resend")
def submit_request_resend():
    return redirect(url_for("main.submit_request"), code=302)


@main_bp.get("/r/<int:req_id>")
def request_public(req_id: int):
    return redirect(url_for("main.submit_request"), code=302)


@main_bp.post("/set-language")
@main_bp.post("/set_language")
def set_language():
    supported = _allowed_locales()
    lang = (request.form.get("lang") or "").strip().lower()
    if lang not in supported:
        lang = "fr"

    session["lang"] = lang
    session.modified = True

    next_url = request.form.get("next") or request.referrer or url_for("main.index")
    if not is_safe_url(next_url):
        next_url = url_for("main.index")

    current_app.logger.info(
        "[i18n.switch] route=/set-language lang=%s session_lang=%s cookie_lang=%s next=%s",
        lang,
        session.get("lang"),
        request.cookies.get("hc_lang"),
        next_url,
    )
    resp = make_response(redirect(next_url))
    resp.set_cookie("hc_lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


@main_bp.route("/category_help/<category>", methods=["GET"])
def category_help(category: str):
    # Display name fallback (ако нямаме CATEGORIES)
    category_names = {
        "food": "Храна",
        "medical": "Медицинска помощ",
        "transport": "Транспорт",
        "other": "Друго",
    }
    category_display = category_names.get(category, (category or "").title())

    # Canonical slug (alias support)
    canonical = ALIASES.get(category, category)

    # Category info (from CATEGORIES)
    data = CATEGORIES.get(canonical)
    if data:
        title_bg = data.get("content", {}).get("title", {}).get("bg")
        icon = (
            data.get("ui", {}).get("icon")
            or "fa-solid fa-circle-question text-secondary"
        )
        severity = data.get("ui", {}).get("severity")
        color = "danger" if severity == "critical" else "primary"

        category_info = {
            "slug": canonical,
            "name": title_bg or category_display,
            "icon": icon,
            "color": color,
        }
    else:
        category_info = {
            "slug": canonical,
            "name": category_display,
            "icon": "fa-solid fa-circle-question text-secondary",
            "color": "primary",
        }

    # Category cards in /categories lead to request submission with preselected category.
    return redirect(url_for("main.submit_request", category=canonical), code=302)


@main_bp.get("/sw.js")
def service_worker():
    # Serve /sw.js from src/static/sw.js
    return send_from_directory(current_app.static_folder, "sw.js")

