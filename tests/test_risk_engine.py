from datetime import datetime, timedelta, timezone

import pytest

from backend.extensions import db
from backend.helpchain_backend.src.models import (
    AdminUser,
    Case,
    CaseEvent,
    Request,
    Structure,
    User,
)
from backend.models import Assignment, Intervenant

pytestmark = pytest.mark.spine


def _login_admin_session(client, admin_id: int):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["_fresh"] = True
        sess["admin_logged_in"] = True
        sess["admin_user_id"] = admin_id


def _seed_case(app, status: str):
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with app.app_context():
        structure = Structure.query.filter_by(slug="default").first()
        if not structure:
            structure = Structure(name="Default", slug="default")
            db.session.add(structure)
            db.session.flush()

        admin = AdminUser(
            username=f"risk_admin_{status}",
            email=f"risk_admin_{status}@test.local",
            password_hash="x",
            role="admin",
            is_active=True,
        )
        requester = User(
            username=f"risk_req_{status}",
            email=f"risk_req_{status}@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
        )
        db.session.add_all([admin, requester])
        db.session.flush()

        req = Request(
            title=f"risk req {status}",
            category="general",
            status="open",
            user_id=requester.id,
            structure_id=structure.id,
            created_at=now - timedelta(days=5),
        )
        db.session.add(req)
        db.session.flush()

        case = Case(
            request_id=req.id,
            structure_id=structure.id,
            status=status,
            created_at=req.created_at,
        )
        db.session.add(case)
        db.session.flush()

        db.session.add(
            CaseEvent(
                case_id=case.id,
                event_type="note",
                created_at=now - timedelta(days=4),
            )
        )
        db.session.commit()

        return admin.id, case.id, structure.id, req.id


def test_closed_case_returns_zero_risk(client, app):
    admin_id, case_id, *_ = _seed_case(app, status="closed")
    _login_admin_session(client, admin_id)

    resp = client.get(f"/admin/api/cases/{case_id}/risk")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["case_id"] == case_id
    assert data["risk_score"] == 0.0
    assert data["risk_level"] == "low"


def test_case_without_assignment_increases_score(client, app):
    admin_id, case_id, *_ = _seed_case(app, status="in_progress")
    _login_admin_session(client, admin_id)

    resp = client.get(f"/admin/api/cases/{case_id}/risk")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["factors"]["no_assignment"] is True
    assert data["risk_score"] >= 10.0


def test_case_with_assignment_reduces_no_assignment_factor(client, app):
    admin_id, case_id, structure_id, request_id = _seed_case(app, status="in_progress")

    with app.app_context():
        intervenant = Intervenant(
            structure_id=structure_id,
            name="Risk Intervenant",
            actor_type="professional",
            email="risk_intervenant@test.local",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(intervenant)
        db.session.flush()
        db.session.add(
            Assignment(
                request_id=request_id,
                intervenant_id=intervenant.id,
                structure_id=structure_id,
                assigned_by_admin_id=admin_id,
            )
        )
        db.session.commit()

    _login_admin_session(client, admin_id)
    resp = client.get(f"/admin/api/cases/{case_id}/risk")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["factors"]["no_assignment"] is False


def test_case_risk_endpoint_returns_json(client, app):
    admin_id, case_id, *_ = _seed_case(app, status="new")
    _login_admin_session(client, admin_id)

    resp = client.get(f"/admin/api/cases/{case_id}/risk")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["case_id"] == case_id
    assert "risk_score" in data
    assert "risk_level" in data
    assert "factors" in data
