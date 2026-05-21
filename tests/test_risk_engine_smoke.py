from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sqlalchemy import inspect, text

from backend.models import Request, Structure, User

pytestmark = pytest.mark.mixed_transition


def _make_user(session) -> User:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"risk_user_{suffix}",
        email=f"risk_{suffix}@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _make_request(session, **overrides) -> Request:
    structure = session.query(Structure).filter_by(slug="default").first()
    user = _make_user(session)
    payload = {
        "title": "Need urgent medical support",
        "description": "Urgence medicale and unsafe situation",
        "user_id": user.id,
        "status": "pending",
        "category": "general",
        "structure_id": getattr(structure, "id", None),
    }
    payload.update(overrides)
    req = Request(**payload)
    session.add(req)
    session.commit()
    return req


def test_create_request_populates_risk_fields(session):
    req = _make_request(session)

    assert req.risk_score is not None
    assert 0 <= int(req.risk_score) <= 100
    assert req.risk_level in {"standard", "attention", "critical"}
    assert req.risk_last_updated is not None


def test_update_request_recomputes_risk(session):
    req = _make_request(
        session,
        description="Need food support only",
        title="Food support",
    )
    first_score = int(req.risk_score or 0)
    first_signals = req.risk_signals or "[]"

    req.description = "Urgence medicale violence danger immediate"
    session.add(req)
    session.commit()
    session.refresh(req)

    assert int(req.risk_score or 0) >= first_score
    assert req.risk_signals != first_signals
    assert req.risk_last_updated is not None


def test_risk_signals_is_valid_json(session):
    req = _make_request(
        session,
        description="Urgence medicale violence logement faim",
    )

    parsed = json.loads(req.risk_signals or "[]")
    assert isinstance(parsed, list)
    assert len(parsed) > 0


def test_request_details_handles_null_signals(authenticated_admin_client, session):
    req = _make_request(session)
    session.execute(
        text("UPDATE requests SET risk_signals = NULL WHERE id = :rid"),
        {"rid": req.id},
    )
    session.commit()

    resp = authenticated_admin_client.get(f"/admin/requests/{req.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Risque:" in html
    assert "traceback" not in html.lower()


def test_admin_requests_list_handles_legacy_like_rows(
    authenticated_admin_client, session
):
    req = _make_request(
        session,
        description="Simple request",
        title="Legacy-like request",
    )
    req.risk_signals = None
    session.add(req)
    session.commit()

    resp = authenticated_admin_client.get("/admin/requests")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Legacy-like request" in html
    assert "traceback" not in html.lower()
    assert f">{req.id}<" in html


def test_admin_requests_risk_filter_and_sort_routes_are_stable(
    authenticated_admin_client, session
):
    _make_request(session, created_at=datetime.now(UTC), description="normal request")

    for query in (
        "/admin/requests?risk=stale",
        "/admin/requests?risk=notseen48",
        "/admin/requests?risk=unknown",
        "/admin/requests?risk_level=critical",
        "/admin/requests?risk_level=attention",
        "/admin/requests?no_owner=1",
        "/admin/requests?not_seen_72h=1",
        "/admin/requests?sort=risk_asc",
        "/admin/requests?sort=created_desc",
        "/admin/sla?sort=created",
        "/admin/sla?sort=overdue",
    ):
        resp = authenticated_admin_client.get(query)
        assert resp.status_code != 500
        assert resp.status_code in (200, 302, 401, 403)


def test_migration_columns_exist_and_legacy_insert_works(
    app, authenticated_admin_client, session
):
    inspector = inspect(session.get_bind())
    cols = {c["name"] for c in inspector.get_columns("requests")}
    assert "risk_score" in cols
    assert "risk_level" in cols
    assert "risk_signals" in cols
    assert "risk_last_updated" in cols

    user = _make_user(session)
    session.execute(
        text(
            """
            INSERT INTO requests (title, category, user_id, status, created_at)
            VALUES (:title, :category, :user_id, :status, :created_at)
            """
        ),
        {
            "title": "Legacy SQL insert request",
            "category": "general",
            "user_id": user.id,
            "status": "pending",
            "created_at": datetime.now(UTC),
        },
    )
    session.commit()

    legacy = (
        session.query(Request)
        .filter(Request.title == "Legacy SQL insert request")
        .order_by(Request.id.desc())
        .first()
    )
    assert legacy is not None
    assert legacy.risk_score is not None
    assert legacy.risk_level in {"standard", "attention", "critical"}

    resp = authenticated_admin_client.get("/admin/requests")
    assert resp.status_code == 200
