from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.spine


def _login_admin(client, admin_user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["admin_id"] = admin_user.id
        sess["admin_user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True


def _make_structure(session, *, name: str, slug: str):
    from backend.models import Structure

    row = Structure(name=name, slug=slug)
    session.add(row)
    session.commit()
    return row


def _make_user(session, *, username: str, email: str, structure_id: int):
    from backend.models import User

    row = User(
        username=username,
        email=email,
        password_hash="x",
        role="requester",
        structure_id=structure_id,
        is_active=True,
    )
    session.add(row)
    session.commit()
    return row


def _make_admin(session, *, username: str, email: str, role: str, structure_id=None):
    from backend.models import AdminUser

    row = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        is_active=True,
        structure_id=structure_id,
    )
    session.add(row)
    session.commit()
    return row


def _make_request(session, *, title: str, user_id: int, structure_id: int, status="open"):
    from backend.models import Request

    now = datetime.now(UTC) - timedelta(hours=1)
    row = Request(
        title=title,
        description=f"Description for {title}",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def _make_case(session, *, request_id: int, structure_id: int, status="new"):
    from backend.helpchain_backend.src.models import Case

    now = datetime.now(UTC) - timedelta(hours=1)
    row = Case(
        request_id=request_id,
        structure_id=structure_id,
        status=status,
        priority="normal",
        risk_score=0,
        opened_at=now,
        last_activity_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def test_cases_require_authentication(client):
    resp = client.get("/admin/cases")
    assert resp.status_code in {302, 303, 403, 404}


def test_readonly_can_view_but_cannot_mutate_case(app, session):
    structure = _make_structure(session, name="Read Only Scope", slug="readonly-scope")
    user = _make_user(
        session,
        username="readonly_requester",
        email="readonly_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="readonly_cases",
        email="readonly_cases@test.local",
        role="readonly",
        structure_id=structure.id,
    )
    req = _make_request(session, title="readonly case", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    client = app.test_client()
    _login_admin(client, admin)

    assert client.get(f"/admin/cases/{case_row.id}").status_code == 200
    resp = client.post(f"/admin/cases/{case_row.id}/status", data={"status": "triaged"})

    session.refresh(case_row)
    assert resp.status_code == 403
    assert case_row.status == "new"


def test_admin_ops_and_superadmin_can_update_case_status(app, session):
    from backend.helpchain_backend.src.models import CaseEvent

    structure = _make_structure(session, name="Allowed Scope", slug="allowed-scope")
    user = _make_user(
        session,
        username="allowed_requester",
        email="allowed_requester@test.local",
        structure_id=structure.id,
    )

    for role in ("ops", "admin", "superadmin"):
        admin = _make_admin(
            session,
            username=f"{role}_cases_allowed",
            email=f"{role}_cases_allowed@test.local",
            role=role,
            structure_id=(None if role == "superadmin" else structure.id),
        )
        req = _make_request(
            session,
            title=f"{role} case",
            user_id=user.id,
            structure_id=structure.id,
        )
        case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
        client = app.test_client()
        _login_admin(client, admin)

        resp = client.post(
            f"/admin/cases/{case_row.id}/status",
            data={"status": "triaged"},
            follow_redirects=False,
        )

        session.refresh(case_row)
        assert resp.status_code == 303
        assert case_row.status == "triaged"
        assert (
            CaseEvent.query.filter_by(case_id=case_row.id, event_type="status_changed").count()
            == 1
        )


def test_structure_scoped_admin_cannot_access_other_structure_case(app, session):
    structure_a = _make_structure(session, name="Scope A", slug="scope-a")
    structure_b = _make_structure(session, name="Scope B", slug="scope-b")
    user_b = _make_user(
        session,
        username="tenant_b_requester",
        email="tenant_b_requester@test.local",
        structure_id=structure_b.id,
    )
    admin_a = _make_admin(
        session,
        username="tenant_a_admin",
        email="tenant_a_admin@test.local",
        role="admin",
        structure_id=structure_a.id,
    )
    req_b = _make_request(session, title="tenant b case", user_id=user_b.id, structure_id=structure_b.id)
    case_b = _make_case(session, request_id=req_b.id, structure_id=structure_b.id)
    client = app.test_client()
    _login_admin(client, admin_a)

    assert client.get(f"/admin/cases/{case_b.id}").status_code == 404
    assert client.post(f"/admin/cases/{case_b.id}/status", data={"status": "triaged"}).status_code == 404


def test_invalid_status_transition_is_rejected(app, session):
    from backend.helpchain_backend.src.models import CaseEvent

    structure = _make_structure(session, name="Transitions", slug="transitions")
    user = _make_user(
        session,
        username="transition_requester",
        email="transition_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="transition_ops",
        email="transition_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    req = _make_request(session, title="closed case", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id, status="closed")
    client = app.test_client()
    _login_admin(client, admin)

    resp = client.post(f"/admin/cases/{case_row.id}/status", data={"status": "in_progress"})

    session.refresh(case_row)
    assert resp.status_code == 303
    assert case_row.status == "closed"
    assert CaseEvent.query.filter_by(case_id=case_row.id, event_type="status_changed").count() == 0


def test_request_to_case_conversion_creates_case_and_timeline(app, session):
    from backend.helpchain_backend.src.models import Case, CaseEvent

    structure = _make_structure(session, name="Conversion", slug="conversion")
    user = _make_user(
        session,
        username="conversion_requester",
        email="conversion_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="conversion_ops",
        email="conversion_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    req = _make_request(session, title="conversion request", user_id=user.id, structure_id=structure.id)
    client = app.test_client()
    _login_admin(client, admin)

    resp = client.post(f"/admin/requests/{req.id}/open-case", follow_redirects=False)

    case_row = Case.query.filter_by(request_id=req.id).one()
    assert resp.status_code == 303
    assert case_row.structure_id == structure.id
    assert CaseEvent.query.filter_by(case_id=case_row.id, event_type="case_created").count() == 1
    assert CaseEvent.query.filter_by(case_id=case_row.id, event_type="triage_scored").count() == 1
