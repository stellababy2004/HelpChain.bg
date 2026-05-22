from __future__ import annotations

from types import SimpleNamespace

from backend.helpchain_backend.src.admin_actor import AdminActor
from backend.helpchain_backend.src.admin_policies import (
    can_access_professional_leads,
    can_export_operational_data,
    can_mutate_request,
    can_view_global_analytics,
)


def _actor(
    *,
    role: str | None,
    structure_id: int | None,
    is_authenticated: bool = True,
    is_platform_global: bool = False,
) -> AdminActor:
    return AdminActor(
        admin_id=1 if is_authenticated else None,
        role=role,
        structure_id=structure_id,
        is_authenticated=is_authenticated,
        is_platform_global=is_platform_global,
        auth_source="test",
        raw_admin=None,
    )


def test_professional_lead_and_global_analytics_helpers_match_founder_access():
    structure_admin = _actor(role="admin", structure_id=12)
    founder_global = _actor(role="superadmin", structure_id=None, is_platform_global=True)
    founder_scoped = _actor(role="superadmin", structure_id=12, is_platform_global=False)

    assert can_access_professional_leads(structure_admin) is False
    assert can_view_global_analytics(structure_admin) is False

    assert can_access_professional_leads(founder_global) is True
    assert can_view_global_analytics(founder_global) is True

    assert can_access_professional_leads(founder_scoped) is True
    assert can_view_global_analytics(founder_scoped) is True


def test_operational_export_helper_respects_admin_scope():
    anonymous = _actor(role=None, structure_id=None, is_authenticated=False)
    structure_admin = _actor(role="admin", structure_id=12)
    founder_global = _actor(role="superadmin", structure_id=None, is_platform_global=True)

    assert can_export_operational_data(anonymous) is False
    assert can_export_operational_data(structure_admin) is True
    assert can_export_operational_data(structure_admin, structure_id=12) is True
    assert can_export_operational_data(structure_admin, structure_id=99) is False
    assert can_export_operational_data(founder_global) is True
    assert can_export_operational_data(founder_global, structure_id=99) is True


def test_request_mutation_helper_respects_request_tenant_scope():
    structure_admin = _actor(role="admin", structure_id=12)
    founder_global = _actor(role="superadmin", structure_id=None, is_platform_global=True)

    visible_request = SimpleNamespace(id=101, structure_id=12)
    hidden_request = SimpleNamespace(id=202, structure_id=99)
    unscoped_request = SimpleNamespace(id=303, structure_id=None)

    assert can_mutate_request(structure_admin, visible_request) is True
    assert can_mutate_request(structure_admin, hidden_request) is False
    assert can_mutate_request(structure_admin, unscoped_request) is False
    assert can_mutate_request(founder_global, visible_request) is True
    assert can_mutate_request(founder_global, hidden_request) is True
