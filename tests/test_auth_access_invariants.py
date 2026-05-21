from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pyotp
import pytest

from backend.models import AdminUser, Request, Structure, User, utc_now


TEST_ADMIN_PASSWORD = "TestPassword1"

pytestmark = pytest.mark.shared_platform


def _default_structure(session) -> Structure:
    structure = session.query(Structure).filter_by(slug="default").first()
    assert structure is not None
    return structure


def _make_structure(session, *, structure_id: int, name: str, slug: str) -> Structure:
    structure = session.get(Structure, structure_id)
    if structure is None:
        structure = Structure(id=structure_id, name=name, slug=slug)
        session.add(structure)
        session.commit()
    return structure


def _make_admin(
    session,
    *,
    username: str,
    email: str,
    role: str,
    structure_id: int | None = None,
    password: str = TEST_ADMIN_PASSWORD,
) -> AdminUser:
    admin = session.query(AdminUser).filter_by(username=username).first()
    if admin is None:
        admin = AdminUser(
            username=username,
            email=email,
            role=role,
            is_active=True,
            structure_id=structure_id,
        )
        session.add(admin)
    admin.role = role
    admin.is_active = True
    admin.structure_id = structure_id
    admin.mfa_enabled = False
    admin.totp_secret = None
    admin.set_password(password)
    session.commit()
    return admin


def _make_request(
    session,
    *,
    title: str,
    user_id: int,
    structure_id: int,
    status: str | None = "pending",
) -> Request:
    req = Request(
        title=title,
        description=f"Description for {title}",
        category="general",
        user_id=user_id,
        structure_id=structure_id,
        status=status,
    )
    session.add(req)
    session.commit()
    return req


def _make_requester(session, *, username: str, email: str) -> User:
    user = User(username=username, email=email, password_hash="x", role="requester")
    session.add(user)
    session.commit()
    return user


def _set_mfa_ready_session(client, session, admin: AdminUser) -> None:
    admin.mfa_enabled = True
    admin.totp_secret = "test-mfa-secret"
    session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)
        sess["user_id"] = admin.id
        sess["admin_id"] = admin.id
        sess["admin_user_id"] = admin.id
        sess["role"] = admin.role
        sess["is_authenticated"] = True
        sess["is_admin"] = True
        sess["admin_logged_in"] = True
        sess["admin_last_seen"] = utc_now().isoformat()
        sess["admin_auth_at"] = utc_now().isoformat()
        sess["mfa_required"] = True
        sess[client.application.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
        sess["mfa_ok_until"] = (utc_now() + timedelta(minutes=30)).isoformat()


def _post_admin_login(client, *, username: str, password: str = TEST_ADMIN_PASSWORD):
    client.application.config["EMAIL_2FA_ENABLED"] = False
    return client.post(
        "/admin/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_admin_ops_login_get_redirects_to_canonical_login(client):
    response = client.get("/admin/ops/login", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "/admin/login" in (response.headers.get("Location", "") or "")


def test_admin_ops_login_post_redirects_to_canonical_login(client):
    response = client.post(
        "/admin/ops/login",
        data={"username": "any-user", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/login" in (response.headers.get("Location", "") or "")


def test_plain_admin_cannot_access_superadmin_roles_surface(client, session):
    structure = _default_structure(session)
    admin = _make_admin(
        session,
        username="plain_admin_invariant",
        email="plain_admin_invariant@test.local",
        role="admin",
        structure_id=structure.id,
    )
    _set_mfa_ready_session(client, session, admin)

    response = client.get("/admin/roles", follow_redirects=False)
    assert response.status_code == 403


def test_superadmin_login_without_mfa_redirects_to_setup(client, session):
    admin = _make_admin(
        session,
        username="superadmin_no_mfa",
        email="superadmin_no_mfa@test.local",
        role="superadmin",
        structure_id=None,
    )

    response = _post_admin_login(client, username=admin.username)
    assert response.status_code in (302, 303)
    assert "/admin/mfa/setup" in (response.headers.get("Location", "") or "")


def test_admin_login_without_mfa_redirects_to_setup(client, session):
    structure = _default_structure(session)
    admin = _make_admin(
        session,
        username="structure_admin_no_mfa",
        email="structure_admin_no_mfa@test.local",
        role="admin",
        structure_id=structure.id,
    )

    response = _post_admin_login(client, username=admin.username)
    assert response.status_code in (302, 303)
    assert "/admin/mfa/setup" in (response.headers.get("Location", "") or "")


def test_ops_login_without_mfa_redirects_to_operator_landing(client, session):
    structure = _default_structure(session)
    admin = _make_admin(
        session,
        username="ops_no_mfa",
        email="ops_no_mfa@test.local",
        role="ops",
        structure_id=structure.id,
    )

    response = _post_admin_login(client, username=admin.username)
    assert response.status_code in (302, 303)
    assert "/ops/workspace" in (response.headers.get("Location", "") or "")


def test_admin_login_with_mfa_enabled_continues_through_verify_flow(client, session):
    structure = _default_structure(session)
    secret = pyotp.random_base32()
    admin = _make_admin(
        session,
        username="structure_admin_with_mfa",
        email="structure_admin_with_mfa@test.local",
        role="admin",
        structure_id=structure.id,
    )
    admin.mfa_enabled = True
    admin.totp_secret = secret
    session.commit()

    response = _post_admin_login(client, username=admin.username)
    assert response.status_code in (302, 303)
    verify_location = response.headers.get("Location", "") or ""
    assert "/admin/mfa/verify" in verify_location

    verify_response = client.post(
        "/admin/mfa/verify",
        data={"code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    assert verify_response.status_code in (302, 303)
    assert "/admin/mfa/verify" not in (verify_response.headers.get("Location", "") or "")


def test_structure_level_admin_is_scoped_in_admin_requests(client, session):
    structure = _make_structure(
        session, structure_id=2, name="Invariant Structure 2", slug="invariant-2"
    )
    _make_structure(
        session, structure_id=3, name="Invariant Structure 3", slug="invariant-3"
    )
    requester = _make_requester(
        session,
        username=f"req_{uuid4().hex[:8]}",
        email=f"req_{uuid4().hex[:8]}@test.local",
    )
    admin = _make_admin(
        session,
        username="scoped_admin_invariant",
        email="scoped_admin_invariant@test.local",
        role="admin",
        structure_id=structure.id,
    )
    _set_mfa_ready_session(client, session, admin)

    _make_request(
        session,
        title="in-scope-invariant-request",
        user_id=requester.id,
        structure_id=structure.id,
        status="pending",
    )
    _make_request(
        session,
        title="out-of-scope-invariant-request",
        user_id=requester.id,
        structure_id=3,
        status="pending",
    )

    response = client.get("/admin/requests", follow_redirects=False)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "in-scope-invariant-request" in html
    assert "out-of-scope-invariant-request" not in html
