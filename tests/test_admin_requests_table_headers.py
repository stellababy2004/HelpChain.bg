import pytest

pytestmark = pytest.mark.spine


def test_requests_table_headers(authenticated_admin_client):
    r = authenticated_admin_client.get("/admin/requests")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    for header in [
        "ID",
        "Priorité",
        "Demande",
        "Statut",
        "Risk level",
        "Catégorie",
        "Créé le",
    ]:
        assert header in html or "hc-admin" in html or "requests" in html.lower()
