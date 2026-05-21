from datetime import datetime, timedelta, UTC
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

pytestmark = pytest.mark.spine


def _satisfy_privileged_mfa(client, session, admin_user):
    admin_user.mfa_enabled = True
    admin_user.totp_secret = "test-mfa-secret"
    session.commit()
    with client.session_transaction() as sess:
        sess[client.application.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_required"] = True
        sess["mfa_ok_until"] = (
            datetime.now(UTC) + timedelta(minutes=30)
        ).isoformat()


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


def _make_structure(session, *, structure_id: int, name: str, slug: str):
    from backend.models import Structure

    structure = session.get(Structure, structure_id)
    if structure is None:
        structure = Structure(id=structure_id, name=name, slug=slug)
        session.add(structure)
        session.commit()
    return structure


def _make_user(session, *, username: str, email: str):
    from backend.models import User

    user = User(username=username, email=email, password_hash="x", role="requester")
    session.add(user)
    session.commit()
    return user


def _make_admin(session, *, username: str, email: str, role: str, structure_id=None):
    from backend.models import AdminUser

    admin = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        is_active=True,
        structure_id=structure_id,
    )
    session.add(admin)
    session.commit()
    return admin


def _make_request(
    session,
    *,
    title: str,
    user_id: int,
    structure_id: int,
    status=None,
    owner_id=None,
    priority=None,
    created_at=None,
    updated_at=None,
):
    from backend.models import Request

    req = Request(
        title=title,
        description=f"Description for {title}",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status=status,
        owner_id=owner_id,
        priority=priority,
        created_at=created_at,
        updated_at=updated_at,
    )
    session.add(req)
    session.commit()
    return req


def _make_case(
    session,
    *,
    request_id: int,
    structure_id: int,
    status="new",
    owner_user_id=None,
    priority="normal",
    risk_score=0,
    last_activity_at=None,
    created_at=None,
):
    from backend.helpchain_backend.src.models import Case

    case = Case(
        request_id=request_id,
        structure_id=structure_id,
        status=status,
        owner_user_id=owner_user_id,
        priority=priority,
        risk_score=risk_score,
        last_activity_at=last_activity_at,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(case)
    session.commit()
    return case


def _workspace_kpi_value(html: str, label: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.select(".hc-ops-summary__item"):
        label_el = item.select_one(".hc-ops-summary__label")
        value_el = item.select_one(".hc-ops-summary__value")
        if label_el and value_el and label_el.get_text(" ", strip=True) == label:
            return int(value_el.get_text(strip=True))
    raise AssertionError(f"KPI label not found: {label}")



def _workspace_kpi_value_fuzzy(html: str, label_pattern: str) -> int:
    pattern = re.compile(label_pattern)
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.select(".hc-ops-summary__item"):
        label_el = item.select_one(".hc-ops-summary__label")
        value_el = item.select_one(".hc-ops-summary__value")
        if (
            label_el
            and value_el
            and pattern.search(label_el.get_text(" ", strip=True))
        ):
            return int(value_el.get_text(strip=True))
    raise AssertionError(f"KPI label pattern not found: {label_pattern}")


def _case_row_count(html: str) -> int:
    return html.count('class="hc-case-row')


def _case_overview_value(html: str, label: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.select(".hc-risk-overview__item"):
        label_el = item.select_one(".hc-risk-overview__label")
        value_el = item.select_one(".hc-risk-overview__value")
        if label_el and value_el and label_el.get_text(" ", strip=True) == label:
            return int(value_el.get_text(strip=True))
    raise AssertionError(f"Case overview label not found: {label}")


def _request_summary_cards(html: str) -> dict[str, dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    cards = {}
    for card in soup.select("#hcAdminKpis .hc-request-summary-card"):
        label_el = card.select_one(".hc-request-summary-card__label")
        value_el = card.select_one("[data-kpi-target]")
        hint_el = card.select_one(".hc-request-summary-card__hint")
        cta_el = card.select_one(".hc-request-summary-card__cta")
        assert label_el is not None
        assert value_el is not None
        assert hint_el is not None
        assert cta_el is not None
        cards[label_el.get_text(strip=True)] = {
            "count": int(value_el.get_text(strip=True)),
            "href": card.get("href"),
            "hint": hint_el.get_text(strip=True),
            "cta": cta_el.get_text(strip=True),
        }
    return cards


def _admin_requests_visible_row_count(html: str, href: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    params = parse_qs(urlparse(href).query)
    tab = (params.get("tab", ["ALL"])[0] or "ALL").upper()
    action_only = params.get("action", [""])[0] == "1"

    def is_urgent(row) -> bool:
        status = (row.get("data-hc-status-row") or "").strip().upper()
        priority = (row.get("data-priority") or "").strip().upper()
        return status not in {"CLOSED", "COMPLETED"} and priority in {
            "URGENT",
            "CRITICAL",
            "HIGH",
        }

    def passes_tab(row) -> bool:
        status = (row.get("data-hc-status-row") or "").strip().upper()
        if tab == "URGENT":
            return is_urgent(row)
        return tab == "ALL" or status == tab

    def is_actionable(row) -> bool:
        status = (row.get("data-hc-status-row") or "").strip().upper()
        assigned = (row.get("data-assigned-volunteer-id") or "").strip()
        can_help = int(row.get("data-sig-can-help") or 0)
        unassigned_open = not assigned and status not in {"CLOSED", "COMPLETED"}
        return unassigned_open or status == "IN_PROGRESS" or can_help > 0

    rows = soup.select("tr[data-hc-status-row]")
    should_apply_action = action_only and tab != "COMPLETED"
    return sum(
        1
        for row in rows
        if passes_tab(row) and (not should_apply_action or is_actionable(row))
    )


def _admin_requests_rendered_titles(html: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    titles: set[str] = set()
    for row in soup.select("tr[data-hc-status-row]"):
        title = row.select_one(".hc-req__title")
        if title:
            titles.add(title.get_text(" ", strip=True))
    return titles


def _admin_requests_row_for_title(html: str, title_text: str):
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.select("tr[data-request-id]"):
        title = row.select_one(".hc-req__title")
        if title and title.get_text(" ", strip=True) == title_text:
            return row
    return None


def test_admin_requests_summary_counts_match_global_queues(
    authenticated_admin_client, session
):
    from backend.helpchain_backend.src.models import Volunteer

    structure = _make_structure(
        session, structure_id=43, name="Global Summary", slug="global-summary"
    )
    user = _make_user(
        session, username="global_summary_user", email="global_summary@test.local"
    )
    volunteer = Volunteer(email="global_summary_volunteer@test.local", name="Global Volunteer")
    if hasattr(volunteer, "structure_id"):
        volunteer.structure_id = structure.id
    session.add(volunteer)
    session.commit()

    def request_row(title, *, status, priority="LOW", category="orientation", assigned=False):
        req = _make_request(
            session,
            title=title,
            user_id=user.id,
            structure_id=structure.id,
            status=status,
            priority=priority,
        )
        req.category = category
        if assigned:
            req.assigned_volunteer_id = volunteer.id
        session.add(req)
        session.commit()
        return req

    request_row("global-new-orientation", status="pending")
    request_row("global-new-food", status="pending", category="food")
    request_row("global-new-assigned-hidden", status="pending", category="food", assigned=True)
    request_row("global-in-progress-food", status="in_progress", category="food", assigned=True)
    request_row("global-urgent-food", status="pending", priority="HIGH", category="food")
    request_row("global-completed-food", status="done", category="food", assigned=True)
    request_row("global-rejected-urgent-hidden", status="rejected", priority="URGENT", category="food")

    page = authenticated_admin_client.get("/admin/requests")
    assert page.status_code == 200
    cards = _request_summary_cards(page.get_data(as_text=True))

    expected = {
        "Nouvelles demandes": "À qualifier ou orienter",
        "En traitement": "Suivi actif en cours",
        "Action requise": "File opérateur à traiter",
        "Clôturées": "Demandes terminées",
    }
    assert set(cards) >= set(expected)
    assert "Nouvelles orientations" not in cards

    filtered_titles = {}
    for label, hint in expected.items():
        assert cards[label]["hint"] == hint
        assert cards[label]["cta"] == "Voir"
        params = parse_qs(urlparse(cards[label]["href"]).query)
        assert "category" not in params
        filtered = authenticated_admin_client.get(cards[label]["href"])
        assert filtered.status_code == 200
        filtered_html = filtered.get_data(as_text=True)
        assert cards[label]["count"] == _admin_requests_visible_row_count(
            filtered_html, cards[label]["href"]
        )
        filtered_titles[label] = _admin_requests_rendered_titles(filtered_html)

    assert filtered_titles["Nouvelles demandes"] == {
        "global-new-orientation",
        "global-new-food",
        "global-urgent-food",
    }
    assert "global-new-assigned-hidden" not in filtered_titles["Nouvelles demandes"]

    assert filtered_titles["En traitement"] == {"global-in-progress-food"}
    assert "global-new-food" not in filtered_titles["En traitement"]
    assert "global-completed-food" not in filtered_titles["En traitement"]

    assert filtered_titles["Action requise"] == {"global-urgent-food"}
    assert "global-new-food" not in filtered_titles["Action requise"]
    assert "global-rejected-urgent-hidden" not in filtered_titles["Action requise"]

    assert filtered_titles["Clôturées"] == {"global-completed-food"}
    assert "global-new-food" not in filtered_titles["Clôturées"]
    assert "global-in-progress-food" not in filtered_titles["Clôturées"]


def test_admin_request_assign_redirect_refreshes_no_owner_queue(
    authenticated_admin_client, session
):
    structure = _make_structure(
        session, structure_id=45, name="Assign Summary", slug="assign-summary"
    )
    user = _make_user(
        session,
        username="assign_summary_user",
        email="assign_summary@test.local",
    )
    req = _make_request(
        session,
        title="assign-summary-no-owner",
        user_id=user.id,
        structure_id=structure.id,
        status="pending",
        priority="LOW",
    )
    req.category = "food"
    session.add(req)
    session.commit()

    no_owner_url = "/admin/requests?no_owner=1"
    before = authenticated_admin_client.get(no_owner_url)
    assert before.status_code == 200
    before_html = before.get_data(as_text=True)
    assert "assign-summary-no-owner" in _admin_requests_rendered_titles(before_html)
    before_count = _case_overview_value(before_html, "Cas sans responsable")

    response = authenticated_admin_client.post(
        f"/admin/requests/{req.id}/assign",
        data={"next": no_owner_url},
        follow_redirects=True,
    )
    assert response.status_code == 200
    after_html = response.get_data(as_text=True)
    assert _case_overview_value(after_html, "Cas sans responsable") == before_count - 1
    assert "assign-summary-no-owner" not in _admin_requests_rendered_titles(after_html)

    normal_page = authenticated_admin_client.get("/admin/requests")
    assert normal_page.status_code == 200
    row = _admin_requests_row_for_title(
        normal_page.get_data(as_text=True),
        "assign-summary-no-owner",
    )
    assert row is not None
    assert row.get("data-owner-missing") == "0"
    assert row.get("data-owner-id")


def test_admin_request_completion_updates_summary_queues(
    authenticated_admin_client, session
):
    structure = _make_structure(
        session, structure_id=44, name="Completion Summary", slug="completion-summary"
    )
    user = _make_user(
        session,
        username="completion_summary_user",
        email="completion_summary@test.local",
    )
    req = _make_request(
        session,
        title="completion-summary-active",
        user_id=user.id,
        structure_id=structure.id,
        status="pending",
        priority="LOW",
    )
    req.category = "food"
    session.add(req)
    session.commit()

    before_page = authenticated_admin_client.get("/admin/requests")
    assert before_page.status_code == 200
    before_cards = _request_summary_cards(before_page.get_data(as_text=True))
    before_new = before_cards["Nouvelles demandes"]["count"]
    before_completed = before_cards["Clôturées"]["count"]
    before_no_owner = _case_overview_value(
        before_page.get_data(as_text=True),
        "Cas sans responsable",
    )

    response = authenticated_admin_client.post(
        f"/admin/requests/{req.id}/status",
        data={"status": "done", "next": "/admin/requests"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    session.expire_all()
    refreshed = session.get(type(req), req.id)
    assert refreshed.status == "done"
    assert refreshed.completed_at is not None

    after_html = response.get_data(as_text=True)
    after_cards = _request_summary_cards(after_html)
    assert after_cards["Nouvelles demandes"]["count"] == before_new - 1
    assert after_cards["Clôturées"]["count"] == before_completed + 1
    assert _case_overview_value(after_html, "Cas sans responsable") == before_no_owner - 1

    new_queue = authenticated_admin_client.get("/admin/requests?tab=NEW&action=1")
    assert new_queue.status_code == 200
    assert "completion-summary-active" not in _admin_requests_rendered_titles(
        new_queue.get_data(as_text=True)
    )

    in_progress_queue = authenticated_admin_client.get(
        "/admin/requests?tab=IN_PROGRESS&action=1"
    )
    assert in_progress_queue.status_code == 200
    assert "completion-summary-active" not in _admin_requests_rendered_titles(
        in_progress_queue.get_data(as_text=True)
    )

    completed_queue = authenticated_admin_client.get(
        "/admin/requests?tab=COMPLETED&action=1"
    )
    assert completed_queue.status_code == 200
    completed_html = completed_queue.get_data(as_text=True)
    assert "completion-summary-active" in _admin_requests_rendered_titles(
        completed_html
    )
    assert after_cards["Clôturées"]["count"] == _admin_requests_visible_row_count(
        completed_html,
        after_cards["Clôturées"]["href"],
    )

    rejected_req = _make_request(
        session,
        title="completion-summary-rejected-not-completed",
        user_id=user.id,
        structure_id=structure.id,
        status="pending",
        priority="LOW",
    )
    rejected_req.category = "food"
    session.add(rejected_req)
    session.commit()

    before_reject = authenticated_admin_client.get("/admin/requests")
    assert before_reject.status_code == 200
    before_reject_cards = _request_summary_cards(before_reject.get_data(as_text=True))

    reject_response = authenticated_admin_client.post(
        f"/admin/requests/{rejected_req.id}/status",
        data={"status": "rejected", "next": "/admin/requests"},
        follow_redirects=True,
    )
    assert reject_response.status_code == 200
    reject_cards = _request_summary_cards(reject_response.get_data(as_text=True))
    assert reject_cards["Clôturées"]["count"] == before_reject_cards["Clôturées"]["count"]

    rejected_completed_queue = authenticated_admin_client.get(
        "/admin/requests?tab=COMPLETED&action=1"
    )
    assert rejected_completed_queue.status_code == 200
    assert (
        "completion-summary-rejected-not-completed"
        not in _admin_requests_rendered_titles(rejected_completed_queue.get_data(as_text=True))
    )


def test_admin_requests_shows_seeded_requests_even_with_null_and_new_status(client, session):
    _make_structure(session, structure_id=2, name="Structure 2", slug="structure-2")
    _make_structure(session, structure_id=3, name="Structure 3", slug="structure-3")
    user = _make_user(session, username="seed_user", email="seed_user@test.local")
    admin = _make_admin(
        session,
        username="global_admin_visibility",
        email="global_admin_visibility@test.local",
        role="superadmin",
        structure_id=None,
    )
    _login_admin(client, admin)
    _satisfy_privileged_mfa(client, session, admin)

    _make_request(session, title="seed-null-status", user_id=user.id, structure_id=2, status=None)
    _make_request(session, title="seed-new-status", user_id=user.id, structure_id=2, status="new")
    _make_request(session, title="seed-pending-status", user_id=user.id, structure_id=3, status="pending")

    resp = client.get("/admin/requests", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "seed-null-status" in html
    assert "seed-new-status" in html
    assert "seed-pending-status" in html


def test_admin_requests_summary_counts_match_filtered_orientation_queues(
    authenticated_admin_client, session
):
    from backend.helpchain_backend.src.models import Volunteer, VolunteerAction

    structure = _make_structure(
        session, structure_id=42, name="Summary Structure", slug="summary-structure"
    )
    user = _make_user(
        session, username="summary_user", email="summary_user@test.local"
    )
    volunteer = Volunteer(email="summary_volunteer@test.local", name="Summary Volunteer")
    if hasattr(volunteer, "structure_id"):
        volunteer.structure_id = structure.id
    session.add(volunteer)
    session.commit()

    def request_row(title, *, status, priority="LOW", category="orientation", assigned=False):
        req = _make_request(
            session,
            title=title,
            user_id=user.id,
            structure_id=structure.id,
            status=status,
            priority=priority,
        )
        req.category = category
        if assigned:
            req.assigned_volunteer_id = volunteer.id
        session.add(req)
        session.commit()
        return req

    request_row("summary-new-one", status="pending")
    request_row("summary-new-two", status="new")
    request_row("summary-open-not-new", status="open")
    request_row("summary-new-assigned-hidden", status="pending", assigned=True)
    request_row("summary-in-progress", status="in_progress", assigned=True)
    request_row("summary-urgent-high", status="pending", priority="HIGH")
    request_row("summary-urgent-critical", status="in_progress", priority="CRITICAL", assigned=True)
    completed_can_help = request_row("summary-completed-can-help", status="done", assigned=True)
    completed_urgent = request_row("summary-completed-urgent-hidden", status="done", priority="HIGH", assigned=True)
    request_row("summary-completed-hidden", status="done")
    request_row("summary-rejected-hidden", status="rejected")
    request_row("summary-rejected-urgent-hidden", status="rejected", priority="URGENT")
    request_row("summary-category-decoy", status="pending", category="food")

    session.add(
        VolunteerAction(
            request_id=completed_can_help.id,
            volunteer_id=volunteer.id,
            action="CAN_HELP",
        )
    )
    session.add(
        VolunteerAction(
            request_id=completed_urgent.id,
            volunteer_id=volunteer.id,
            action="CAN_HELP",
        )
    )
    session.commit()

    page = authenticated_admin_client.get("/admin/requests?category=orientation")
    assert page.status_code == 200
    cards = _request_summary_cards(page.get_data(as_text=True))

    expected = {
        "Nouvelles orientations": "À qualifier ou orienter",
        "En traitement": "Suivi actif en cours",
        "Action requise": "File opérateur à traiter",
        "Clôturées": "Demandes terminées",
    }
    assert set(cards) >= set(expected)

    filtered_titles = {}
    for label, hint in expected.items():
        assert cards[label]["hint"] == hint
        assert cards[label]["cta"] == "Voir"
        params = parse_qs(urlparse(cards[label]["href"]).query)
        assert params.get("category") == ["orientation"]
        filtered = authenticated_admin_client.get(cards[label]["href"])
        assert filtered.status_code == 200
        filtered_html = filtered.get_data(as_text=True)
        assert cards[label]["count"] == _admin_requests_visible_row_count(
            filtered_html, cards[label]["href"]
        )
        filtered_titles[label] = _admin_requests_rendered_titles(filtered_html)

    assert filtered_titles["Nouvelles orientations"] == {
        "summary-new-one",
        "summary-new-two",
        "summary-urgent-high",
    }
    assert "summary-open-not-new" not in filtered_titles["Nouvelles orientations"]
    assert "summary-new-assigned-hidden" not in filtered_titles["Nouvelles orientations"]
    assert "summary-rejected-hidden" not in filtered_titles["Nouvelles orientations"]
    assert "summary-completed-can-help" not in filtered_titles["Nouvelles orientations"]
    assert "summary-category-decoy" not in filtered_titles["Nouvelles orientations"]

    assert filtered_titles["En traitement"] == {
        "summary-in-progress",
        "summary-urgent-critical",
    }
    assert "summary-new-one" not in filtered_titles["En traitement"]
    assert "summary-rejected-hidden" not in filtered_titles["En traitement"]
    assert "summary-completed-can-help" not in filtered_titles["En traitement"]
    assert "summary-category-decoy" not in filtered_titles["En traitement"]

    assert filtered_titles["Action requise"] == {
        "summary-urgent-high",
        "summary-urgent-critical",
    }
    assert "summary-new-one" not in filtered_titles["Action requise"]
    assert "summary-new-two" not in filtered_titles["Action requise"]
    assert "summary-completed-urgent-hidden" not in filtered_titles["Action requise"]
    assert "summary-rejected-urgent-hidden" not in filtered_titles["Action requise"]
    assert "summary-category-decoy" not in filtered_titles["Action requise"]

    assert filtered_titles["Clôturées"] == {
        "summary-completed-can-help",
        "summary-completed-hidden",
        "summary-completed-urgent-hidden",
    }
    assert "summary-new-one" not in filtered_titles["Clôturées"]
    assert "summary-in-progress" not in filtered_titles["Clôturées"]
    assert "summary-rejected-hidden" not in filtered_titles["Clôturées"]
    assert "summary-rejected-urgent-hidden" not in filtered_titles["Clôturées"]
    assert "summary-category-decoy" not in filtered_titles["Clôturées"]


def test_operator_dashboard_only_shows_actionable_scoped_requests(client, session):
    structure = _make_structure(session, structure_id=2, name="Structure 2", slug="structure-2")
    _make_structure(session, structure_id=3, name="Structure 3", slug="structure-3")
    user = _make_user(session, username="ops_seed_user", email="ops_seed_user@test.local")
    operator = _make_admin(
        session,
        username="ops_visibility",
        email="ops_visibility@test.local",
        role="ops",
        structure_id=structure.id,
    )
    _login_admin(client, operator)

    now = datetime.now(UTC).replace(tzinfo=None)

    visible_null = _make_request(
        session,
        title="ops-visible-null-status",
        user_id=user.id,
        structure_id=structure.id,
        status=None,
        owner_id=None,
        created_at=now - timedelta(hours=1),
    )
    visible_new = _make_request(
        session,
        title="ops-visible-new-status",
        user_id=user.id,
        structure_id=structure.id,
        status="new",
        owner_id=None,
        created_at=now - timedelta(hours=2),
    )
    hidden_closed = _make_request(
        session,
        title="ops-hidden-closed",
        user_id=user.id,
        structure_id=structure.id,
        status="done",
        owner_id=None,
        created_at=now - timedelta(days=1),
    )
    hidden_other_structure = _make_request(
        session,
        title="ops-hidden-other-structure",
        user_id=user.id,
        structure_id=3,
        status="pending",
        owner_id=None,
        created_at=now - timedelta(hours=3),
    )
    hidden_non_queue = _make_request(
        session,
        title="ops-hidden-owned-normal",
        user_id=user.id,
        structure_id=structure.id,
        status="pending",
        owner_id=operator.id,
        priority="low",
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=10),
    )

    resp = client.get("/ops/workspace", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "ops-visible-null-status" in html
    assert "ops-visible-new-status" in html
    assert "ops-hidden-closed" not in html
    assert "ops-hidden-other-structure" not in html
    assert "ops-hidden-owned-normal" not in html


def test_ops_workspace_kpis_match_quick_action_case_filters(
    authenticated_admin_client, session
):
    from backend.models import NotificationJob, Structure

    structure = session.query(Structure).filter_by(slug="default").first()
    user = _make_user(
        session,
        username="ops_kpi_user",
        email="ops_kpi_user@test.local",
    )
    operator = _make_admin(
        session,
        username="ops_kpi_owner",
        email="ops_kpi_owner@test.local",
        role="ops",
        structure_id=structure.id,
    )

    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    stale_time = now - timedelta(hours=80)

    critical_req = _make_request(
        session,
        title="ops-kpi-critical",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=operator.id,
        created_at=now,
        updated_at=now,
    )
    _make_case(
        session,
        request_id=critical_req.id,
        structure_id=structure.id,
        owner_user_id=operator.id,
        priority="critical",
        risk_score=90,
        created_at=now,
    )

    unassigned_req = _make_request(
        session,
        title="ops-kpi-unassigned",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=None,
        created_at=now,
        updated_at=now,
    )
    _make_case(
        session,
        request_id=unassigned_req.id,
        structure_id=structure.id,
        owner_user_id=None,
        created_at=now,
    )

    stale_req = _make_request(
        session,
        title="ops-kpi-stale",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=operator.id,
        created_at=stale_time,
        updated_at=stale_time,
    )
    _make_case(
        session,
        request_id=stale_req.id,
        structure_id=structure.id,
        owner_user_id=operator.id,
        last_activity_at=stale_time,
        created_at=stale_time,
    )

    resolved_req = _make_request(
        session,
        title="ops-kpi-resolved-decoy",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=None,
        created_at=stale_time,
        updated_at=stale_time,
    )
    _make_case(
        session,
        request_id=resolved_req.id,
        structure_id=structure.id,
        status="resolved",
        owner_user_id=None,
        priority="critical",
        risk_score=95,
        last_activity_at=stale_time,
        created_at=stale_time,
    )

    session.add(
        NotificationJob(
            channel="email",
            event_type="ops_test",
            recipient="ops-test@example.invalid",
            status="failed",
            structure_id=structure.id,
        )
    )
    session.commit()

    workspace = authenticated_admin_client.get("/ops/workspace", follow_redirects=False)
    assert workspace.status_code == 200
    workspace_html = workspace.get_data(as_text=True)

    critical = authenticated_admin_client.get("/ops/cases?risk=critical")
    unassigned = authenticated_admin_client.get("/ops/cases?owner=none")
    stale = authenticated_admin_client.get("/ops/cases?stale_72h=1")
    failed_notifications = authenticated_admin_client.get("/ops/notifications?status=failed")

    assert critical.status_code == 200
    assert unassigned.status_code == 200
    assert stale.status_code == 200
    assert failed_notifications.status_code == 200

    critical_html = critical.get_data(as_text=True)
    unassigned_html = unassigned.get_data(as_text=True)
    stale_html = stale.get_data(as_text=True)

    assert _workspace_kpi_value(workspace_html, "Situations critiques") == _case_row_count(
        critical_html
    )
    assert _workspace_kpi_value(workspace_html, "Demandes non assignées") == _case_row_count(
        unassigned_html
    )
    assert _workspace_kpi_value_fuzzy(workspace_html, r"Sans activit.") == _case_row_count(
        stale_html
    )
    assert "ops-kpi-resolved-decoy" not in critical_html
    assert "ops-kpi-resolved-decoy" not in unassigned_html
    assert "ops-kpi-resolved-decoy" not in stale_html
    assert "Filtres actifs" in critical_html
    assert "Risque: Critique" in critical_html
    assert "Responsable: non attribue" in unassigned_html
    assert "Cas sans action 72h" in stale_html
    assert workspace_html.count('/ops/cases?risk=critical') >= 2


def test_admin_cases_owner_missing_count_matches_filtered_queue(
    authenticated_admin_client, session
):
    from backend.models import Structure

    structure = session.query(Structure).filter_by(slug="default").first()
    user = _make_user(
        session,
        username="admin_cases_owner_user",
        email="admin_cases_owner_user@test.local",
    )
    operator = _make_admin(
        session,
        username="admin_cases_owner_operator",
        email="admin_cases_owner_operator@test.local",
        role="ops",
        structure_id=structure.id,
    )
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)

    critical_no_owner_req = _make_request(
        session,
        title="admin-cases-critical-no-owner",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=None,
        created_at=now,
        updated_at=now,
    )
    _make_case(
        session,
        request_id=critical_no_owner_req.id,
        structure_id=structure.id,
        owner_user_id=None,
        priority="critical",
        risk_score=95,
        created_at=now,
    )

    normal_no_owner_req = _make_request(
        session,
        title="admin-cases-normal-no-owner",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=None,
        created_at=now,
        updated_at=now,
    )
    _make_case(
        session,
        request_id=normal_no_owner_req.id,
        structure_id=structure.id,
        owner_user_id=None,
        risk_score=0,
        created_at=now,
    )

    critical_owned_req = _make_request(
        session,
        title="admin-cases-critical-owned",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=operator.id,
        created_at=now,
        updated_at=now,
    )
    _make_case(
        session,
        request_id=critical_owned_req.id,
        structure_id=structure.id,
        owner_user_id=operator.id,
        priority="critical",
        risk_score=95,
        created_at=now,
    )

    resolved_no_owner_req = _make_request(
        session,
        title="admin-cases-resolved-no-owner",
        user_id=user.id,
        structure_id=structure.id,
        status="open",
        owner_id=None,
        created_at=now,
        updated_at=now,
    )
    _make_case(
        session,
        request_id=resolved_no_owner_req.id,
        structure_id=structure.id,
        status="resolved",
        owner_user_id=None,
        priority="critical",
        risk_score=95,
        created_at=now,
    )

    critical_page = authenticated_admin_client.get(
        "/admin/cases?risk=critical",
        follow_redirects=False,
    )
    filtered_page = authenticated_admin_client.get(
        "/admin/cases?risk=critical&owner=none",
        follow_redirects=False,
    )

    assert critical_page.status_code == 200
    assert filtered_page.status_code == 200
    critical_html = critical_page.get_data(as_text=True)
    filtered_html = filtered_page.get_data(as_text=True)

    owner_missing_badge_count = _case_overview_value(
        critical_html,
        "Cas sans responsable",
    )
    assert owner_missing_badge_count == _case_row_count(filtered_html)
    assert _case_overview_value(filtered_html, "Cas sans responsable") == _case_row_count(
        filtered_html
    )
    assert "admin-cases-critical-no-owner" in filtered_html
    assert "admin-cases-normal-no-owner" not in filtered_html
    assert "admin-cases-critical-owned" not in filtered_html
    assert "admin-cases-resolved-no-owner" not in filtered_html


def test_structure_scoped_admin_requests_excludes_other_structures(client, session):
    structure = _make_structure(session, structure_id=2, name="Structure 2", slug="structure-2")
    _make_structure(session, structure_id=3, name="Structure 3", slug="structure-3")
    user = _make_user(session, username="scoped_seed_user", email="scoped_seed_user@test.local")
    scoped_admin = _make_admin(
        session,
        username="scoped_admin_visibility",
        email="scoped_admin_visibility@test.local",
        role="admin",
        structure_id=structure.id,
    )
    _login_admin(client, scoped_admin)
    _satisfy_privileged_mfa(client, session, scoped_admin)

    in_scope = _make_request(
        session,
        title="scoped-visible-request",
        user_id=user.id,
        structure_id=structure.id,
        status="pending",
    )
    out_of_scope = _make_request(
        session,
        title="scoped-hidden-request",
        user_id=user.id,
        structure_id=3,
        status="pending",
    )

    resp = client.get("/admin/requests", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "scoped-visible-request" in html
    assert "scoped-hidden-request" not in html
