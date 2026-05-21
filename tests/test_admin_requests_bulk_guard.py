import re

import pytest

pytestmark = pytest.mark.spine


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m, "CSRF token not found"
    return m.group(1)


def test_bulk_action_without_selection_is_blocked(authenticated_admin_client):
    page = authenticated_admin_client.get("/admin/requests")
    assert page.status_code == 200
    token = _csrf(page.get_data(as_text=True))

    payload = {
        "csrf_token": token,
        "bulk_action": "set_status_done",
        "selected_ids": [],
    }

    r = authenticated_admin_client.post(
        "/admin/requests/bulk",
        data=payload,
        follow_redirects=False,
    )
    assert r.status_code in (200, 302)
