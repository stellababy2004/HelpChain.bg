import pytest

pytestmark = pytest.mark.spine


@pytest.fixture
def admin_login(authenticated_admin_client):
    return authenticated_admin_client


def test_admin_requests_never_returns_500(client, admin_login):
    r = admin_login.get("/admin/requests")

    # Production contract: this page should never crash with 500.
    assert r.status_code != 500
    assert r.status_code in (200, 302)

    if r.status_code == 200:
        html = r.get_data(as_text=True)
        assert ("Requests" in html) or ("Demandes" in html)
        assert 'id="hcBulkAction"' in html
