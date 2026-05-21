from datetime import UTC, datetime, timedelta

from backend.extensions import db
from backend.models import AdminUser, Request, Structure, User


def _login_admin(client, admin_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["_fresh"] = True
        sess["admin_user_id"] = admin_id
        sess["admin_logged_in"] = True
        sess["mfa_ok"] = True


def test_ops_metrics_normalizes_legacy_request_status_reads(client, app):
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)

    with app.app_context():
        structure = Structure(name="Ops Compat", slug="ops-compat", status="active")
        admin = AdminUser(
            username="ops_compat_admin",
            email="ops-compat-admin@test.local",
            password_hash="x",
            role="admin",
            structure_id=None,
            is_active=True,
        )
        user = User(
            username="ops_compat_user",
            email="ops-compat-user@test.local",
            password_hash="x",
            role="requester",
            is_active=True,
        )
        db.session.add_all([structure, admin, user])
        db.session.flush()
        admin_id = admin.id

        db.session.add_all(
            [
                Request(
                    title="pending request",
                    category="general",
                    user_id=user.id,
                    structure_id=structure.id,
                    status="pending",
                    created_at=now - timedelta(days=1),
                ),
                Request(
                    title="active request",
                    category="general",
                    user_id=user.id,
                    structure_id=structure.id,
                    status="active",
                    created_at=now - timedelta(days=1),
                ),
                Request(
                    title="completed today",
                    category="general",
                    user_id=user.id,
                    structure_id=structure.id,
                    status="completed",
                    completed_at=now,
                    created_at=now - timedelta(days=2),
                ),
                Request(
                    title="resolved today",
                    category="general",
                    user_id=user.id,
                    structure_id=structure.id,
                    status="resolved",
                    completed_at=now,
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
        db.session.commit()

    _login_admin(client, admin_id)
    response = client.get("/admin/api/ops-metrics")

    assert response.status_code == 200
    data = response.get_json()
    assert data["active_requests"] == 2
    assert data["resolved_today"] == 2
