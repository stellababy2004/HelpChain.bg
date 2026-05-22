from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import false

from .models import Case, NotificationJob, Request

if TYPE_CHECKING:
    from .admin_actor import AdminActor


def can_access_professional_leads(actor: "AdminActor | None") -> bool:
    return bool(actor and actor.has_founder_global_access)


def can_view_global_analytics(actor: "AdminActor | None") -> bool:
    return bool(actor and actor.has_founder_global_access)


def can_export_operational_data(
    actor: "AdminActor | None", structure_id: int | None = None
) -> bool:
    if not actor or not actor.is_authenticated or not actor.is_admin:
        return False
    if actor.is_platform_global:
        return True

    tenant_scope_id = actor.tenant_scope_id
    if tenant_scope_id is None:
        return False
    if structure_id is None:
        return True

    try:
        return int(tenant_scope_id) == int(structure_id)
    except (TypeError, ValueError):
        return False


def can_mutate_request(actor: "AdminActor | None", request) -> bool:
    if not actor or not request or not actor.is_authenticated or not actor.is_admin:
        return False
    if actor.is_platform_global:
        return True

    tenant_scope_id = actor.tenant_scope_id
    request_structure_id = getattr(request, "structure_id", None)
    if tenant_scope_id is None or request_structure_id is None:
        return False

    try:
        return int(tenant_scope_id) == int(request_structure_id)
    except (TypeError, ValueError):
        return False


def _scope_query_to_structure(actor: "AdminActor | None", query, structure_column):
    if not actor or not actor.is_authenticated or not actor.is_admin:
        return query.filter(false())
    if actor.is_platform_global:
        return query

    tenant_scope_id = actor.tenant_scope_id
    if tenant_scope_id is None:
        return query.filter(false())

    try:
        return query.filter(structure_column == int(tenant_scope_id))
    except (TypeError, ValueError):
        return query.filter(false())


def scope_request_query(actor: "AdminActor | None", query):
    return _scope_query_to_structure(actor, query, Request.structure_id)


def scope_notification_query(actor: "AdminActor | None", query):
    return _scope_query_to_structure(actor, query, NotificationJob.structure_id)


def scope_case_query(actor: "AdminActor | None", query):
    return _scope_query_to_structure(actor, query, Case.structure_id)
