from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

import pytest

pytestmark = pytest.mark.spine


def _request_titles(html: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    return {
        node.get_text(" ", strip=True)
        for node in soup.select("tr[data-hc-status-row] .hc-req__title")
    }


def _admin_home_not_seen_counts(html: str) -> list[int]:
    soup = BeautifulSoup(html, "html.parser")
    counts = []
    for link in soup.select('a.hc-command-action[href*="not_seen_72h=1"]'):
        value = link.select_one("strong")
        if value is not None:
            counts.append(int(value.get_text(strip=True)))
    return counts


def test_admin_requests_not_seen_72h_uses_dashboard_activity_source(
    authenticated_admin_client, session
):
    from backend.models import Request, RequestActivity, Structure, User

    structure = session.query(Structure).filter_by(slug="default").first()
    user = User(
        username="inactive_queue_user",
        email="inactive_queue_user@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
    )
    session.add(user)
    session.flush()

    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    stale_time = now - timedelta(hours=80)
    fresh_time = now - timedelta(hours=2)

    stale_without_signal = Request(
        title="inactive-queue-shared-source",
        description="Stale by activity source, no serialized risk signal.",
        user_id=user.id,
        structure_id=getattr(structure, "id", None),
        status="open",
        category="general",
        created_at=stale_time,
        updated_at=stale_time,
        risk_signals=None,
    )
    stale_with_recent_activity = Request(
        title="inactive-queue-recent-activity-hidden",
        description="Old updated_at but recent meaningful activity.",
        user_id=user.id,
        structure_id=getattr(structure, "id", None),
        status="open",
        category="general",
        created_at=stale_time,
        updated_at=stale_time,
        risk_signals=None,
    )
    fresh_with_legacy_signal = Request(
        title="inactive-queue-legacy-signal-hidden",
        description="Legacy not_seen signal should not override fresh activity.",
        user_id=user.id,
        structure_id=getattr(structure, "id", None),
        status="open",
        category="general",
        created_at=fresh_time,
        updated_at=fresh_time,
        risk_signals='["not_seen_72h"]',
    )
    terminal_stale = Request(
        title="inactive-queue-terminal-hidden",
        description="Terminal stale request should not be dashboard actionable.",
        user_id=user.id,
        structure_id=getattr(structure, "id", None),
        status="done",
        category="general",
        created_at=stale_time,
        updated_at=stale_time,
        risk_signals=None,
    )
    archived_stale = Request(
        title="inactive-queue-archived-hidden",
        description="Archived stale request should not match the dashboard count.",
        user_id=user.id,
        structure_id=getattr(structure, "id", None),
        status="open",
        category="general",
        created_at=stale_time,
        updated_at=stale_time,
        is_archived=True,
        risk_signals=None,
    )
    session.add_all(
        [
            stale_without_signal,
            stale_with_recent_activity,
            fresh_with_legacy_signal,
            terminal_stale,
            archived_stale,
        ]
    )
    session.flush()
    session.add(
        RequestActivity(
            request_id=stale_with_recent_activity.id,
            action="status_change",
            created_at=fresh_time,
        )
    )
    session.commit()

    dashboard = authenticated_admin_client.get("/admin/home")
    assert dashboard.status_code == 200
    dashboard_counts = _admin_home_not_seen_counts(dashboard.get_data(as_text=True))
    assert dashboard_counts
    assert set(dashboard_counts) == {1}

    queue = authenticated_admin_client.get("/admin/requests?not_seen_72h=1")
    assert queue.status_code == 200
    titles = _request_titles(queue.get_data(as_text=True))

    assert titles == {"inactive-queue-shared-source"}
    assert len(titles) == dashboard_counts[0]
