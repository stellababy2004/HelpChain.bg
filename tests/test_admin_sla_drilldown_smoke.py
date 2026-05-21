from datetime import UTC, datetime, timedelta
import re
from uuid import uuid4

import pytest

from backend.models import AdminUser, Request, Structure, User

pytestmark = pytest.mark.spine


def _admin_id_from_client(client) -> int:
    with client.session_transaction() as sess:
        val = (
            sess.get("admin_user_id")
            or sess.get("admin_id")
            or sess.get("user_id")
            or sess.get("_user_id")
        )
    return int(val)


def _set_admin_role(session, client, role: str) -> None:
    admin_id = _admin_id_from_client(client)
    admin = session.get(AdminUser, admin_id)
    admin.role = role
    session.commit()


def _make_request(session, structure_id: int, *, age_hours: int, owner_id):
    suffix = uuid4().hex[:8]
    user = User(
        username=f"sla_user_{suffix}",
        email=f"sla_{suffix}@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
    )
    session.add(user)
    session.flush()

    created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=age_hours)
    req = Request(
        title=f"SLA req {suffix}",
        user_id=user.id,
        status="pending",
        category="general",
        structure_id=structure_id,
        owner_id=owner_id,
        created_at=created_at,
    )
    session.add(req)
    session.commit()
    return req


def test_sla_kpi_drilldown_owner_assignment_returns_exact_matches(
    authenticated_admin_client, session
):
    _set_admin_role(session, authenticated_admin_client, "superadmin")
    structure = session.query(Structure).filter_by(slug="default").first()
    assert structure is not None
    session.query(Request).delete()
    session.commit()

    req_match_1 = _make_request(
        session, structure.id, age_hours=80, owner_id=None
    )  # overdue
    req_match_2 = _make_request(
        session, structure.id, age_hours=96, owner_id=None
    )  # overdue
    req_non_match = _make_request(
        session, structure.id, age_hours=12, owner_id=None
    )  # not overdue

    sla_page = authenticated_admin_client.get("/admin/sla?days=30")
    assert sla_page.status_code == 200
    sla_html = sla_page.get_data(as_text=True)
    assert "queue=sla" in sla_html
    assert "sla_kind=owner_assignment_overdue" in sla_html

    filtered = authenticated_admin_client.get(
        "/admin/requests?queue=sla&sla_kind=owner_assignment_overdue&sla_days=30"
    )
    assert filtered.status_code == 200
    html = filtered.get_data(as_text=True)

    assert f'data-request-id="{req_match_1.id}"' in html
    assert f'data-request-id="{req_match_2.id}"' in html
    assert f'data-request-id="{req_non_match.id}"' not in html
    ids = set(int(x) for x in re.findall(r'data-request-id="(\d+)"', html))
    assert ids == {req_match_1.id, req_match_2.id}
