from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import g

from backend.core import tenant
from backend.models import Request, Structure, User


def _make_user(session, *, email: str, structure_id: int | None = None) -> User:
    user = User(
        username=email.split("@", 1)[0],
        email=email,
        password_hash="x",
        role="user",
        is_active=True,
        structure_id=structure_id,
    )
    session.add(user)
    session.commit()
    return user


def test_current_structure_id_prefers_explicit_g_structure(app, session, monkeypatch):
    with app.test_request_context("/"):
        g.structure_id = 321
        g.user = SimpleNamespace(structure_id=654)
        monkeypatch.setattr(
            tenant,
            "current_user",
            SimpleNamespace(is_authenticated=True, structure_id=987),
        )

        assert tenant.current_structure_id() == 321


def test_current_structure_id_falls_back_to_current_user(app, session, monkeypatch):
    structure = session.query(Structure).filter_by(slug="default").first()
    assert structure is not None

    with app.test_request_context("/"):
        monkeypatch.setattr(
            tenant,
            "current_user",
            SimpleNamespace(is_authenticated=True, structure_id=structure.id),
        )

        assert tenant.current_structure_id() == structure.id
        assert g.structure_id == structure.id


def test_current_structure_id_falls_back_to_g_user(app, session, monkeypatch):
    structure = session.query(Structure).filter_by(slug="default").first()
    assert structure is not None

    with app.test_request_context("/"):
        g.user = SimpleNamespace(structure_id=structure.id)
        monkeypatch.setattr(
            tenant,
            "current_user",
            SimpleNamespace(is_authenticated=False, structure_id=None),
        )

        assert tenant.current_structure_id() == structure.id
        assert g.structure_id == structure.id


def test_current_structure_id_uses_default_tenant_fallback(app, session, monkeypatch):
    structure = session.query(Structure).filter_by(slug="default").first()
    assert structure is not None
    monkeypatch.setattr(tenant, "_DEFAULT_STRUCTURE_ID", None)

    with app.test_request_context("/"):
        monkeypatch.setattr(
            tenant,
            "current_user",
            SimpleNamespace(is_authenticated=False, structure_id=None),
        )

        assert tenant.current_structure_id() == structure.id
        assert g.structure_id == structure.id


def test_current_structure_id_raises_when_all_resolution_layers_fail(
    app, session, monkeypatch
):
    with app.test_request_context("/"):
        monkeypatch.setattr(
            tenant,
            "current_user",
            SimpleNamespace(is_authenticated=False, structure_id=None),
        )

        def _raise_runtime_error():
            raise RuntimeError("tenant resolution unavailable")

        monkeypatch.setattr(tenant, "_load_default_structure_id", _raise_runtime_error)

        with pytest.raises(RuntimeError, match="tenant resolution unavailable"):
            tenant.current_structure_id()


def test_request_insert_uses_active_tenant_resolution(app, session, monkeypatch):
    default_structure = session.query(Structure).filter_by(slug="default").first()
    assert default_structure is not None

    active_structure = Structure(name="Scoped", slug="scoped")
    session.add(active_structure)
    session.commit()

    user = _make_user(
        session,
        email="tenant-request@test.local",
        structure_id=default_structure.id,
    )

    monkeypatch.setattr(tenant, "current_structure_id", lambda: active_structure.id)

    row = Request(title="Tenant contract", category="general", user_id=user.id)
    session.add(row)
    session.commit()

    assert row.structure_id == active_structure.id


def test_request_insert_falls_back_to_default_structure_when_resolver_fails(
    app, session, monkeypatch
):
    default_structure = session.query(Structure).filter_by(slug="default").first()
    assert default_structure is not None

    user = _make_user(
        session,
        email="tenant-fallback@test.local",
        structure_id=default_structure.id,
    )

    def _raise_runtime_error():
        raise RuntimeError("no tenant context")

    monkeypatch.setattr(tenant, "current_structure_id", _raise_runtime_error)

    row = Request(title="Legacy fallback", category="general", user_id=user.id)
    session.add(row)
    session.commit()

    assert row.structure_id == default_structure.id
