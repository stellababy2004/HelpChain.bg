from datetime import UTC, datetime, timedelta

import pytest

from backend.extensions import db
from backend.models import AdminUser, Request, Structure
from backend.helpchain_backend.src.services.weekly_operations_report import (
    enqueue_weekly_operations_report,
)

pytestmark = pytest.mark.spine


def test_enqueue_weekly_operations_report_creates_notification_job(app, db_schema):
    now = datetime.now(UTC)

    with app.app_context():
        structure = Structure(
            name="Test Structure",
            slug="test-structure",
        )
        db.session.add(structure)
        db.session.flush()

        admin = AdminUser(
            username="weekly_admin",
            email="weekly-admin@example.test",
            password_hash="test-password-hash",
            role="admin",
            structure_id=structure.id,
            is_active=True,
        )
        db.session.add(admin)

        request_row = Request(
            title="Weekly report request",
            status="new",
            structure_id=structure.id,
            created_at=now - timedelta(days=1),
        )
        db.session.add(request_row)
        db.session.commit()

        result = enqueue_weekly_operations_report(
            structure_id=structure.id,
            days=7,
            base_url="https://helpchain.live",
            send_now=False,
        )

        assert result["ok"] is True
        assert result["queued"] == 1
        assert result["recipients"] == ["weekly-admin@example.test"]
