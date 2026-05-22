from __future__ import annotations

import os
from datetime import datetime

from flask import current_app, g, has_app_context
from flask_login import current_user
from sqlalchemy.exc import OperationalError, ProgrammingError

from backend.extensions import db
from backend.models import Structure

_DEFAULT_STRUCTURE_ID: int | None = None
TENANT_DEFAULT_SLUG = os.getenv("HC_DEFAULT_STRUCTURE_SLUG", "default")


def _is_test_env() -> bool:
    return (
        os.getenv("HC_ENV") == "test"
        or os.getenv("HELPCHAIN_TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
    )


def _load_default_structure_id() -> int:
    global _DEFAULT_STRUCTURE_ID
    if _DEFAULT_STRUCTURE_ID is None:
        # Legacy compatibility gate:
        # - tests may bootstrap before schema creation
        # - local/dev startup may warm tenant state before explicit scoping exists
        #
        # New request/admin/ops write flows should not depend on this branch long
        # term. They should resolve a tenant explicitly and eventually fail closed
        # before reaching the default-tenant fallback.
        allow_bootstrap = False
        if _is_test_env():
            allow_bootstrap = True
        elif has_app_context():
            allow_bootstrap = bool(
                current_app.config.get("ALLOW_DEFAULT_TENANT_FALLBACK", False)
            )
        try:
            default = (
                db.session.query(Structure)
                .filter(Structure.slug == TENANT_DEFAULT_SLUG)
                .first()
            )
        except (OperationalError, ProgrammingError):
            if allow_bootstrap:
                # Runtime schema creation is forbidden here (drift risk).
                # Use Alembic migrations or explicit manual bootstrap scripts.
                if _is_test_env():
                    # Legacy tests may call app.test_client() before DB bootstrap.
                    _DEFAULT_STRUCTURE_ID = 1
                    return _DEFAULT_STRUCTURE_ID
                if has_app_context():
                    current_app.logger.warning(
                        "Tenant bootstrap blocked: schema/table missing. "
                        "Run Alembic migrations or manual bootstrap script."
                    )
            raise
        if not default:
            if allow_bootstrap:
                try:
                    default = Structure(
                        name="Default",
                        slug=TENANT_DEFAULT_SLUG,
                        created_at=datetime.utcnow(),
                    )
                    db.session.add(default)
                    db.session.commit()
                    _DEFAULT_STRUCTURE_ID = int(default.id)
                    return _DEFAULT_STRUCTURE_ID
                except Exception:
                    db.session.rollback()
            raise RuntimeError("Default structure not found in DB.")
        _DEFAULT_STRUCTURE_ID = int(default.id)
    return _DEFAULT_STRUCTURE_ID


def current_structure_id() -> int:
    """Resolve the active tenant for the current runtime context.

    Resolution order is part of the current compatibility contract:
    1. ``g.structure_id`` when a caller has already bound an explicit tenant.
    2. ``current_user.structure_id`` for authenticated request/admin flows.
    3. ``g.user.structure_id`` for older request bootstrap paths.
    4. ``_load_default_structure_id()`` as a documented legacy fallback.

    The first three layers are the preferred runtime sources. Layer 4 exists to
    preserve legacy tests, bootstrap/startup helpers, and older flows that still
    assume an ambient default tenant. Future write paths and background entry
    points should pass an explicit structure id or fail closed before relying on
    the default tenant fallback.
    """
    if not has_app_context():
        return _load_default_structure_id()

    if hasattr(g, "structure_id"):
        return int(g.structure_id)

    if (
        getattr(current_user, "is_authenticated", False)
        and getattr(current_user, "structure_id", None)
    ):
        g.structure_id = int(current_user.structure_id)
        return int(g.structure_id)

    if hasattr(g, "user") and getattr(g.user, "structure_id", None):
        g.structure_id = int(g.user.structure_id)
        return int(g.structure_id)

    g.structure_id = _load_default_structure_id()
    return int(g.structure_id)
