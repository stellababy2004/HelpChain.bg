from __future__ import annotations

from datetime import timedelta

from backend.helpchain_backend.src.admin_actor import AdminActor
from backend.helpchain_backend.src.admin_policies import can_view_global_analytics
from backend.models import AdminUser, Structure, utc_now


def _login_admin(client, app, admin_user: AdminUser) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["user_id"] = admin_user.id
        sess["admin_id"] = admin_user.id
        sess["admin_user_id"] = admin_user.id
        sess["role"] = admin_user.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["mfa_required"] = True
        sess[app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()
        sess["admin_mfa_last_verified"] = 4102444800
        sess["admin_mfa_user_id"] = admin_user.id


def _make_structure(session, *, name: str, slug: str) -> Structure:
    row = Structure(name=name, slug=slug)
    session.add(row)
    session.flush()
    return row


def _make_admin(
    session,
    *,
    username: str,
    email: str,
    role: str,
    structure_id: int | None,
) -> AdminUser:
    row = AdminUser(
        username=username,
        email=email,
        password_hash="x",
        role=role,
        structure_id=structure_id,
        is_active=True,
        mfa_enabled=True,
        totp_secret="global-analytics-test-secret",
    )
    session.add(row)
    session.flush()
    return row


def test_can_view_global_analytics_allows_founder_global_superadmin():
    actor = AdminActor(
        admin_id=1,
        role="superadmin",
        structure_id=None,
        is_authenticated=True,
        is_platform_global=True,
        auth_source="session",
        raw_admin=None,
    )

    assert can_view_global_analytics(actor) is True


def test_can_view_global_analytics_denies_structure_scoped_admin():
    actor = AdminActor(
        admin_id=2,
        role="admin",
        structure_id=9,
        is_authenticated=True,
        is_platform_global=False,
        auth_source="session",
        raw_admin=None,
    )

    assert can_view_global_analytics(actor) is False


def test_can_view_global_analytics_preserves_structure_attached_superadmin_access():
    actor = AdminActor(
        admin_id=3,
        role="superadmin",
        structure_id=11,
        is_authenticated=True,
        is_platform_global=False,
        auth_source="session",
        raw_admin=None,
    )

    assert can_view_global_analytics(actor) is True


def test_global_superadmin_can_access_global_analytics_surfaces_and_nav(app, session):
    structure = _make_structure(
        session,
        name="Global Analytics Alpha",
        slug="global-analytics-alpha",
    )
    global_admin = _make_admin(
        session,
        username="global_analytics_superadmin",
        email="global-analytics-superadmin@test.local",
        role="superadmin",
        structure_id=None,
    )
    session.commit()

    client = app.test_client()
    _login_admin(client, app, global_admin)

    requests_page = client.get("/admin/requests")
    requests_html = requests_page.get_data(as_text=True)
    revenue = client.get("/admin/revenue")
    audience = client.get("/admin/audience-map")
    high_intent = client.get("/admin/api/high-intent-sessions")

    assert structure.id is not None
    assert requests_page.status_code == 200
    assert 'href="/admin/revenue"' in requests_html
    assert 'href="/admin/audience-map"' in requests_html
    assert revenue.status_code == 200
    assert "Revenue Control Center" in revenue.get_data(as_text=True)
    assert audience.status_code == 200
    assert "Radar des signaux" in audience.get_data(as_text=True)
    assert high_intent.status_code == 200
    assert high_intent.is_json is True
    assert "sessions" in high_intent.get_json()


def test_structure_admin_cannot_access_global_analytics_surfaces_and_nav_is_hidden(
    app, session
):
    structure = _make_structure(
        session,
        name="Scoped Analytics Beta",
        slug="scoped-analytics-beta",
    )
    structure_admin = _make_admin(
        session,
        username="scoped_analytics_admin",
        email="scoped-analytics-admin@test.local",
        role="admin",
        structure_id=structure.id,
    )
    session.commit()

    client = app.test_client()
    _login_admin(client, app, structure_admin)

    requests_page = client.get("/admin/requests")
    requests_html = requests_page.get_data(as_text=True)
    revenue = client.get("/admin/revenue", follow_redirects=False)
    audience = client.get("/admin/audience-map", follow_redirects=False)
    high_intent = client.get("/admin/api/high-intent-sessions", follow_redirects=False)

    assert requests_page.status_code == 200
    assert 'href="/admin/revenue"' not in requests_html
    assert 'href="/admin/audience-map"' not in requests_html
    assert revenue.status_code == 403
    assert audience.status_code == 403
    assert high_intent.status_code == 403
