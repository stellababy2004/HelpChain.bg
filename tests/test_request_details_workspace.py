from __future__ import annotations

from datetime import UTC, datetime

from bs4 import BeautifulSoup

from backend.models import Request, Structure, User


def _make_user(session, suffix: str) -> User:
    user = User(
        username=f"workspace_user_{suffix}",
        email=f"workspace_{suffix}@test.local",
        password_hash="x",
        role="requester",
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def test_request_details_workspace_renders_sections(authenticated_admin_client, session):
    suffix = str(int(datetime.now(UTC).timestamp()))
    structure = session.query(Structure).filter_by(slug="default").first()
    user = _make_user(session, suffix)
    req = Request(
        title="Situation de coordination territoriale",
        description="Demande de suivi social sans informations complètes.",
        user_id=user.id,
        status="pending",
        category="general",
        structure_id=getattr(structure, "id", None),
        risk_level=None,
        risk_signals=None,
        city=None,
        email=None,
        phone=None,
        created_at=datetime.now(UTC),
    )
    session.add(req)
    session.commit()

    resp = authenticated_admin_client.get(f"/admin/requests/{req.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Résumé de la situation" in html
    assert "Informations opérationnelles" in html
    assert "Actions bénévoles" in html
    assert "Historique de la situation" in html


def test_ops_dropdown_exposes_canonical_and_legacy_request_links(
    authenticated_admin_client,
    session,
):
    suffix = f"nav_{int(datetime.now(UTC).timestamp())}"
    structure = session.query(Structure).filter_by(slug="default").first()
    user = _make_user(session, suffix)
    req = Request(
        title="Navigation contract request",
        description="Support de validation de navigation.",
        user_id=user.id,
        status="pending",
        category="general",
        structure_id=getattr(structure, "id", None),
        created_at=datetime.now(UTC),
    )
    session.add(req)
    session.commit()

    resp = authenticated_admin_client.get(f"/admin/requests/{req.id}")
    assert resp.status_code == 200

    soup = BeautifulSoup(resp.data, "html.parser")
    links = {
        " ".join(link.get_text(" ", strip=True).split()): link.get("href", "")
        for link in soup.select("#opsConsoleDropdown + ul a")
    }

    assert links.get("Demandes") == "/admin/requests"
    assert links.get("Accès historique demandes") == "/requests"
