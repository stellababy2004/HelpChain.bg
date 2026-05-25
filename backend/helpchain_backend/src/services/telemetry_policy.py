from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from flask import request, session

try:
    from flask_login import current_user
except Exception:  # pragma: no cover - optional in some environments
    current_user = None


PUBLIC_COMMERCIAL_PATH_ALIASES = {
    "/": "/",
    "/offre": "/offre",
    "/deploiement": "/deploiement",
    "/professionnels": "/professionnels",
    "/professionnels/pilote": "/professionnels/pilote",
    "/demander-acces": "/demander-acces",
    "/contact": "/contact",
    "/demo": "/demo",
    "/securite": "/securite",
    "/confidentialite": "/confidentialite",
    "/architecture": "/architecture",
    "/cas-usage": "/cas-usage",
    "/cas_usage": "/cas-usage",
    "/pilotage-indicateurs": "/pilotage-indicateurs",
    "/pilotage_indicateurs": "/pilotage-indicateurs",
    "/pour-les-structures": "/pour-les-structures",
    "/collectivites-associations": "/collectivites-associations",
    "/collectivites_associations": "/collectivites-associations",
    "/pourquoi-helpchain": "/pourquoi-helpchain",
    "/pourquoi_helpchain": "/pourquoi-helpchain",
    "/comment-ca-marche": "/comment-ca-marche",
    "/comment_ca_marche": "/comment-ca-marche",
}

STATIC_PATH_PREFIXES = (
    "/static/",
    "/assets/",
    "/favicon",
    "/sw.js",
    "/manifest",
)
HEALTH_PATH_PREFIXES = (
    "/health",
    "/healthz",
    "/ready",
    "/readyz",
    "/live",
    "/livez",
    "/ping",
)
NON_PUBLIC_PREFIXES = (
    "/admin",
    "/ops",
    "/api",
    "/events",
    "/auth",
    "/login",
    "/logout",
    "/request",
    "/requests",
    "/dashboard",
    "/volunteer",
    "/leaderboard",
    "/my-requests",
)
BOT_TOKENS = (
    "bot",
    "crawler",
    "spider",
    "headlesschrome",
    "uptime",
    "monitor",
    "healthcheck",
    "pingdom",
    "datadog",
    "newrelic",
)
INTERNAL_ROLE_TOKENS = {"admin", "superadmin", "super_admin", "ops", "operator", "staff"}
FOUNDER_IP_PREFIXES = ("176.187.",)


@dataclass(frozen=True)
class TelemetryDecision:
    should_persist: bool
    canonical_path: str | None
    reason: str | None = None


def _normalize_path(path_or_url: str | None) -> str | None:
    raw = (path_or_url or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    path = (parsed.path or raw.split("?", 1)[0] or "").strip()
    if not path:
        path = "/"
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = path.rstrip("/") or "/"
    return normalized.lower()


def canonical_public_commercial_path(path_or_url: str | None) -> str | None:
    normalized = _normalize_path(path_or_url)
    if not normalized:
        return None
    return PUBLIC_COMMERCIAL_PATH_ALIASES.get(normalized)


def extract_event_path(payload: dict[str, Any] | None) -> str | None:
    payload = payload or {}
    props = payload.get("props") or payload.get("properties") or {}
    metadata = payload.get("metadata") or {}
    candidates = (
        payload.get("url"),
        payload.get("page"),
        payload.get("page_url"),
        payload.get("path"),
        props.get("url"),
        props.get("page"),
        props.get("page_url"),
        props.get("path"),
        metadata.get("page"),
        request.referrer,
    )
    for candidate in candidates:
        normalized = _normalize_path(candidate)
        if normalized:
            return normalized
    return None


def get_client_ip() -> str | None:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip
    remote = (request.remote_addr or "").strip()
    return remote or None


def _is_internal_user() -> bool:
    if session.get("admin_logged_in") or session.get("is_admin"):
        return True

    role = (session.get("role") or "").strip().lower()
    if role in INTERNAL_ROLE_TOKENS:
        return True

    try:
        if current_user is None or not getattr(current_user, "is_authenticated", False):
            return False
        if getattr(current_user, "is_admin", False):
            return True
        for attr in ("role_canon", "role"):
            value = (getattr(current_user, attr, None) or "").strip().lower()
            if value in INTERNAL_ROLE_TOKENS:
                return True
    except Exception:
        return False
    return False


def _is_local_or_dev_ip(value: str | None) -> bool:
    ip = (value or "").strip().lower()
    if not ip:
        return False
    return (
        ip == "::1"
        or ip == "localhost"
        or ip.startswith("127.")
        or ip.startswith("192.168.")
    )


def _is_founder_ip(value: str | None) -> bool:
    ip = (value or "").strip()
    return any(ip.startswith(prefix) for prefix in FOUNDER_IP_PREFIXES)


def _is_obvious_bot(user_agent: str | None) -> bool:
    ua = (user_agent or "").strip().lower()
    if not ua:
        return False
    return any(token in ua for token in BOT_TOKENS)


def should_track_public_commercial_path(path_or_url: str | None) -> bool:
    return canonical_public_commercial_path(path_or_url) is not None


def classify_public_telemetry_request(
    path_or_url: str | None,
    *,
    user_agent: str | None = None,
    client_ip: str | None = None,
) -> TelemetryDecision:
    normalized_path = _normalize_path(path_or_url)
    canonical_path = canonical_public_commercial_path(normalized_path)

    if _is_internal_user():
        return TelemetryDecision(False, canonical_path, "internal_user")
    if _is_founder_ip(client_ip):
        return TelemetryDecision(False, canonical_path, "founder_ip")
    if _is_local_or_dev_ip(client_ip):
        return TelemetryDecision(False, canonical_path, "local_ip")
    if _is_obvious_bot(user_agent):
        return TelemetryDecision(False, canonical_path, "bot_user_agent")
    if not normalized_path:
        return TelemetryDecision(False, None, "missing_path")
    if normalized_path.startswith(STATIC_PATH_PREFIXES):
        return TelemetryDecision(False, None, "static_path")
    if normalized_path.startswith(HEALTH_PATH_PREFIXES):
        return TelemetryDecision(False, None, "health_path")
    if normalized_path.startswith(NON_PUBLIC_PREFIXES):
        return TelemetryDecision(False, None, "non_public_path")
    if canonical_path is None:
        return TelemetryDecision(False, None, "non_public_path")
    return TelemetryDecision(True, canonical_path, None)
