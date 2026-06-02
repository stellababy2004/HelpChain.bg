from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect as sa_inspect

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


def _make_professional_lead(session, *, full_name: str, email: str):
    from backend.helpchain_backend.src.models import ProfessionalLead

    row = ProfessionalLead(
        full_name=full_name,
        email=email,
        profession="Coordination",
        city="Paris",
        source="test",
        status="new",
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


def test_assigning_professional_upserts_canonical_case_participant(app, session):
    from backend.helpchain_backend.src.models import CaseParticipant

    structure = _make_structure(session, name="Professional Scope", slug="professional-scope")
    user = _make_user(
        session,
        username="professional_requester",
        email="professional_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="professional_ops",
        email="professional_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Assigned Lead",
        email="assigned-lead@test.local",
    )
    req = _make_request(session, title="professional assignment", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    legacy_row = CaseParticipant(
        case_id=case_row.id,
        participant_type="admin_user",
        professional_lead_id=lead.id,
        role="observer",
        status="inactive",
        added_at=datetime.now(UTC),
    )
    session.add(legacy_row)
    session.commit()

    client = app.test_client()
    _login_admin(client, admin)

    response = client.post(
        f"/admin/cases/{case_row.id}/assign-professional",
        data={"assigned_professional_lead_id": str(lead.id)},
        follow_redirects=False,
    )

    session.refresh(case_row)
    participant_rows = CaseParticipant.query.filter_by(case_id=case_row.id).all()

    assert response.status_code == 303
    assert case_row.assigned_professional_lead_id == lead.id
    assert len(participant_rows) == 1
    assert participant_rows[0].participant_type == "professional_lead"
    assert participant_rows[0].professional_lead_id == lead.id
    assert participant_rows[0].role == "primary_professional"
    assert participant_rows[0].status == "active"


def test_case_detail_renders_professional_participant_name_not_unknown(app, session):
    structure = _make_structure(session, name="Participant Render Scope", slug="participant-render-scope")
    user = _make_user(
        session,
        username="participant_render_requester",
        email="participant_render_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="participant_render_ops",
        email="participant_render_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Rendered Lead",
        email="rendered-lead@test.local",
    )
    req = _make_request(session, title="participant render", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id, status="assigned")
    client = app.test_client()
    _login_admin(client, admin)

    assign_response = client.post(
        f"/admin/cases/{case_row.id}/assign-professional",
        data={"assigned_professional_lead_id": str(lead.id)},
        follow_redirects=False,
    )
    detail_response = client.get(f"/admin/cases/{case_row.id}")
    html = detail_response.get_data(as_text=True)

    assert assign_response.status_code == 303
    assert detail_response.status_code == 200
    assert "Participants suppl" in html
    assert "Rendered Lead" in html
    assert "Professional" in html
    assert "Unknown" not in html
    assert f"#{lead.id} - Rendered Lead" in html


def test_add_participant_normalizes_wrong_type_when_professional_is_selected(app, session):
    from backend.helpchain_backend.src.models import CaseParticipant

    structure = _make_structure(session, name="Normalization Scope", slug="normalization-scope")
    user = _make_user(
        session,
        username="normalize_requester",
        email="normalize_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="normalize_ops",
        email="normalize_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Normalized Lead",
        email="normalized-lead@test.local",
    )
    req = _make_request(session, title="normalize participant", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    client = app.test_client()
    _login_admin(client, admin)

    response = client.post(
        f"/admin/cases/{case_row.id}/participants",
        data={
            "participant_type": "admin_user",
            "professional_lead_id": str(lead.id),
            "role": "observer",
            "status": "active",
        },
        follow_redirects=False,
    )

    participant = CaseParticipant.query.filter_by(case_id=case_row.id).one()

    assert response.status_code == 303
    assert participant.participant_type == "professional_lead"
    assert participant.professional_lead_id == lead.id
    assert participant.user_id is None
    assert participant.admin_user_id is None


def test_case_detail_professional_dropdown_lists_professional_leads(app, session):
    structure = _make_structure(session, name="Dropdown Scope", slug="dropdown-scope")
    user = _make_user(
        session,
        username="dropdown_requester",
        email="dropdown_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="dropdown_ops",
        email="dropdown_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Dropdown Lead",
        email="dropdown-lead@test.local",
    )
    req = _make_request(session, title="dropdown case", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    client = app.test_client()
    _login_admin(client, admin)

    response = client.get(f"/admin/cases/{case_row.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="professional_lead_id"' in html
    assert f'<option value="{lead.id}">#{lead.id} - Dropdown Lead</option>' in html


def test_case_detail_matching_and_dropdown_share_canonical_professional_source(app, session):
    structure = _make_structure(session, name="Shared Source Scope", slug="shared-source-scope")
    user = _make_user(
        session,
        username="shared_source_requester",
        email="shared_source_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="shared_source_ops",
        email="shared_source_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Shared Source Lead",
        email="shared-source-lead@test.local",
    )
    req = _make_request(session, title="shared source case", user_id=user.id, structure_id=structure.id)
    req.category = "orientation"
    req.city = "Paris"
    session.commit()
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    client = app.test_client()
    _login_admin(client, admin)

    response = client.get(f"/admin/cases/{case_row.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Shared Source Lead" in html
    assert f'<input type="hidden" name="assigned_professional_lead_id" value="{lead.id}">' in html
    assert f'<option value="{lead.id}">#{lead.id} - Shared Source Lead</option>' in html


def test_add_participant_form_with_selected_professional_creates_professional_participant(app, session):
    from backend.helpchain_backend.src.models import CaseParticipant

    structure = _make_structure(session, name="Form Professional Scope", slug="form-professional-scope")
    user = _make_user(
        session,
        username="form_prof_requester",
        email="form_prof_requester@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="form_prof_ops",
        email="form_prof_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Form Lead",
        email="form-lead@test.local",
    )
    req = _make_request(session, title="form professional", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    client = app.test_client()
    _login_admin(client, admin)

    response = client.post(
        f"/admin/cases/{case_row.id}/participants",
        data={
            "participant_type": "professional_lead",
            "professional_lead_id": str(lead.id),
            "role": "observer",
            "status": "active",
        },
        follow_redirects=False,
    )

    participant = CaseParticipant.query.filter_by(case_id=case_row.id).one()

    assert response.status_code == 303
    assert participant.participant_type == "professional_lead"
    assert participant.professional_lead_id == lead.id
    assert participant.user_id is None


def test_professional_participant_submit_without_selected_professional_does_not_fallback_to_user(app, session):
    from backend.helpchain_backend.src.models import CaseParticipant

    structure = _make_structure(session, name="No Fallback Scope", slug="no-fallback-scope")
    user = _make_user(
        session,
        username="nofallback_requester",
        email="nofallback_requester@test.local",
        structure_id=structure.id,
    )
    user_participant = _make_user(
        session,
        username="nofallback_candidate",
        email="nofallback_candidate@test.local",
        structure_id=structure.id,
    )
    admin = _make_admin(
        session,
        username="nofallback_ops",
        email="nofallback_ops@test.local",
        role="ops",
        structure_id=structure.id,
    )
    req = _make_request(session, title="no fallback", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    client = app.test_client()
    _login_admin(client, admin)

    response = client.post(
        f"/admin/cases/{case_row.id}/participants",
        data={
            "participant_type": "professional_lead",
            "user_id": str(user_participant.id),
            "role": "observer",
            "status": "active",
        },
        follow_redirects=False,
    )

    participants = CaseParticipant.query.filter_by(case_id=case_row.id).all()

    assert response.status_code == 303
    assert participants == []


def test_resolve_professional_participant_display_name_prefers_real_full_name(app, session):
    from backend.helpchain_backend.src.models import CaseParticipant
    from backend.helpchain_backend.src.routes.admin_cases import (
        _load_case_detail_participants,
        resolve_professional_participant_display_name,
    )

    structure = _make_structure(session, name="Hydration Scope", slug="hydration-scope")
    user = _make_user(
        session,
        username="hydration_requester",
        email="hydration_requester@test.local",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Hydrated Lead",
        email="hydrated-lead@test.local",
    )
    req = _make_request(session, title="hydrated participant", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    participant = CaseParticipant(
        case_id=case_row.id,
        participant_type="professional_lead",
        professional_lead_id=lead.id,
        role="observer",
        status="active",
        added_at=datetime.now(UTC),
    )
    session.add(participant)
    session.commit()

    loaded = _load_case_detail_participants(case_row.id)

    assert len(loaded) == 1
    assert resolve_professional_participant_display_name(loaded[0]) == "Hydrated Lead"


def test_case_detail_participants_eager_load_professional_relationship(app, session):
    from backend.helpchain_backend.src.models import CaseParticipant
    from backend.helpchain_backend.src.routes.admin_cases import _load_case_detail_participants

    structure = _make_structure(session, name="Eager Scope", slug="eager-scope")
    user = _make_user(
        session,
        username="eager_requester",
        email="eager_requester@test.local",
        structure_id=structure.id,
    )
    lead = _make_professional_lead(
        session,
        full_name="Eager Lead",
        email="eager-lead@test.local",
    )
    req = _make_request(session, title="eager participant", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    session.add(
        CaseParticipant(
            case_id=case_row.id,
            participant_type="professional_lead",
            professional_lead_id=lead.id,
            role="observer",
            status="active",
            added_at=datetime.now(UTC),
        )
    )
    session.commit()

    participant = _load_case_detail_participants(case_row.id)[0]
    state = sa_inspect(participant)

    assert "professional_lead" not in state.unloaded
    assert participant.professional_lead is not None
    assert participant.professional_lead.full_name == "Eager Lead"


def test_resolve_professional_participant_display_name_falls_back_safely_for_broken_rows(app, session):
    from backend.helpchain_backend.src.models import CaseParticipant
    from backend.helpchain_backend.src.routes.admin_cases import resolve_professional_participant_display_name

    structure = _make_structure(session, name="Broken Scope", slug="broken-scope")
    user = _make_user(
        session,
        username="broken_requester",
        email="broken_requester@test.local",
        structure_id=structure.id,
    )
    req = _make_request(session, title="broken participant", user_id=user.id, structure_id=structure.id)
    case_row = _make_case(session, request_id=req.id, structure_id=structure.id)
    participant = CaseParticipant(
        case_id=case_row.id,
        participant_type="professional_lead",
        professional_lead_id=999999,
        role="observer",
        status="active",
        added_at=datetime.now(UTC),
    )
    session.add(participant)
    session.commit()

    assert resolve_professional_participant_display_name(participant) == "Intervenant #999999"
