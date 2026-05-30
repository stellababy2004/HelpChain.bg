import re

import pytest

pytestmark = pytest.mark.spine


@pytest.fixture
def admin_login(authenticated_admin_client):
    return authenticated_admin_client


def _extract_bulk_actions(html: str) -> list[str]:
    m = re.search(
        r'<select[^>]+id="hcBulkAction"[^>]*>(.*?)</select>',
        html,
        flags=re.S | re.I,
    )
    assert m, "Bulk action <select id='hcBulkAction'> not found in HTML"
    block = m.group(1)
    return re.findall(r"<option[^>]*>(.*?)</option>", block, flags=re.I)


def test_bulk_actions_dropdown_has_expected_options(admin_login):
    r = admin_login.get("/admin/requests")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="hcBulkAction"' in html

    options = [o.strip() for o in _extract_bulk_actions(html) if o.strip()]

    expected = [
        "Action de coordination",
        "Claim ownership (me)",
        "Open selected",
        "Copier les identifiants",
        "Copier les liens",
        "Nudge selected bénévoles",
        "Set statut : Pending",
        "Set statut : In progress",
        "Set statut : Done",
        "Set statut : Rejected",
    ]

    for label in expected:
        assert label in options or label.encode("utf-8").decode("latin-1") in options, f"Missing option: {label}"

    assert set(options) >= set(expected)


