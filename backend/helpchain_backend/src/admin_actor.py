from __future__ import annotations

from dataclasses import dataclass

import jwt
from flask import g, has_request_context, request, session
from flask_login import current_user

from .extensions import db
from .jwt_utils import decode_token
from .models import AdminUser, canonical_role


ADMIN_ACTOR_ROLES = {"ops", "admin", "superadmin"}


class BearerActorResolutionError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = int(status_code)


@dataclass(frozen=True)
class AdminActor:
    admin_id: int | None
    role: str | None
    structure_id: int | None
    is_authenticated: bool
    is_platform_global: bool
    auth_source: str | None
    raw_admin: AdminUser | None

    @property
    def is_admin(self) -> bool:
        return self.is_authenticated and self.role in ADMIN_ACTOR_ROLES

    @property
    def is_structure_attached(self) -> bool:
        return self.structure_id is not None

    @property
    def is_ops(self) -> bool:
        return self.role == "ops"

    @property
    def has_founder_global_access(self) -> bool:
        return self.is_authenticated and self.role == "superadmin"

    @property
    def tenant_scope_id(self) -> int | None:
        if self.is_platform_global:
            return None
        return self.structure_id


def _anonymous_actor(auth_source: str | None) -> AdminActor:
    return AdminActor(
        admin_id=None,
        role=None,
        structure_id=None,
        is_authenticated=False,
        is_platform_global=False,
        auth_source=auth_source,
        raw_admin=None,
    )


def _normalize_int(raw_value: object) -> int | None:
    if raw_value in (None, ""):
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _load_admin_user(admin_id: object) -> AdminUser | None:
    normalized_id = _normalize_int(admin_id)
    if normalized_id is None:
        return None
    return db.session.get(AdminUser, normalized_id)


def _build_actor(admin_user: AdminUser | None, *, auth_source: str) -> AdminActor:
    if admin_user is None or not bool(getattr(admin_user, "is_active", False)):
        return _anonymous_actor(auth_source)

    role = canonical_role(getattr(admin_user, "role", None))
    structure_id = _normalize_int(getattr(admin_user, "structure_id", None))
    return AdminActor(
        admin_id=_normalize_int(getattr(admin_user, "id", None)),
        role=role,
        structure_id=structure_id,
        is_authenticated=True,
        is_platform_global=role == "superadmin" and structure_id is None,
        auth_source=auth_source,
        raw_admin=admin_user,
    )


def resolve_session_admin_actor() -> AdminActor:
    cached = getattr(g, "_session_admin_actor", None)
    if cached is not None:
        return cached

    if not session.get("admin_logged_in"):
        actor = _anonymous_actor("session")
        g._session_admin_actor = actor
        return actor

    admin_user = _load_admin_user(
        session.get("admin_user_id")
        or session.get("admin_id")
        or session.get("user_id")
        or getattr(current_user, "id", None)
    )
    if admin_user is None and isinstance(current_user, AdminUser):
        admin_user = current_user

    actor = _build_actor(admin_user, auth_source="session")
    g._session_admin_actor = actor
    return actor


def _decode_bearer_claims() -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise BearerActorResolutionError("Missing Bearer token", 401)

    token = auth.split(" ", 1)[1].strip()
    try:
        return decode_token(token, "access")
    except jwt.ExpiredSignatureError as exc:
        raise BearerActorResolutionError("Token expired", 401) from exc
    except jwt.InvalidTokenError as exc:
        raise BearerActorResolutionError("Invalid token", 401) from exc


def resolve_bearer_admin_actor() -> AdminActor:
    cached = getattr(g, "_bearer_admin_actor", None)
    if cached is not None:
        return cached

    claims = getattr(g, "api_claims", None)
    if claims is None:
        claims = _decode_bearer_claims()
        g.api_claims = claims

    g.api_user_id = claims.get("sub")
    actor = _build_actor(_load_admin_user(claims.get("sub")), auth_source="bearer")
    g._bearer_admin_actor = actor
    return actor


def resolve_current_admin_actor() -> AdminActor:
    if not has_request_context():
        return AdminActor(
            admin_id=None,
            role=None,
            structure_id=None,
            is_authenticated=False,
            is_platform_global=False,
            auth_source="none",
            raw_admin=None,
        )

    cached = getattr(g, "_current_admin_actor", None)
    if cached is not None:
        return cached

    auth = ""
    auth = request.headers.get("Authorization", "")
    actor = resolve_bearer_admin_actor() if auth.startswith("Bearer ") else resolve_session_admin_actor()
    g._current_admin_actor = actor
    return actor
