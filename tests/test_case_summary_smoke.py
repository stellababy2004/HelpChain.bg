from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from backend.helpchain_backend.src.services.case_summary import build_case_summary
from backend.models import Request, Structure, User

pytestmark = pytest.mark.spine


def _make_user(session, suffix: str) -> User:
    user = User(
        username=f"summary_user_{suffix}",
        email=f"summary_{suffix}@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def test_case_summary_helper_returns_string():
    req = SimpleNamespace(
        title="Situation logement",
        description="Besoin urgent d'hÃ©bergement",
        risk_level="critical",
        risk_signals='["logement","no_owner"]',
        owner_id=None,
    )
    out = build_case_summary(req, {"recommended_action": "assign_immediately"})
    assert isinstance(out, str)
    assert out


def test_case_summary_detail_page_renders_block(authenticated_admin_client, session):
    ts = str(int(datetime.now(UTC).timestamp()))
    structure = session.query(Structure).filter_by(slug="default").first()
    user = _make_user(session, ts)
    req = Request(
        title="Cas de suivi social",
        description="Demande d'accompagnement",
        user_id=user.id,
        status="pending",
        category="general",
        structure_id=getattr(structure, "id", None),
        risk_level="attention",
        risk_signals='["not_seen_72h"]',
        created_at=datetime.now(UTC),
    )
    session.add(req)
    session.commit()

    resp = authenticated_admin_client.get(f"/admin/requests/{req.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "situation" in html.lower()


def test_case_summary_empty_signals_safe_fallback():
    req = SimpleNamespace(
        title="",
        description="",
        risk_level=None,
        risk_signals=None,
        owner_id=None,
    )
    out = build_case_summary(req, None)
    assert "Situation nécessitant un suivi social." in out
