from datetime import UTC, datetime, timedelta

from backend.extensions import db
from backend.models import Request
from backend.helpchain_backend.src.services.sla_alerts import build_sla_alerts
import pytest

pytestmark = pytest.mark.spine


def test_sla_alerts_detect_unassigned_and_stale_requests(app, db_schema):
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    with app.app_context():
        db.session.add(
            Request(
                title="Old unassigned request",
                status="new",
                created_at=now - timedelta(hours=60),
                updated_at=now - timedelta(hours=80),
                priority="normal",
            )
        )
        db.session.add(
            Request(
                title="Urgent unassigned request",
                status="new",
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
                priority="urgent",
            )
        )
        db.session.commit()

        result = build_sla_alerts(now=now)

        assert result["severity"] == "critical"
        assert result["metrics"]["unassigned_48h"] == 1
        assert result["metrics"]["stale_72h"] == 1
        assert result["metrics"]["urgent_unassigned"] == 1

        codes = {alert["code"] for alert in result["alerts"]}
        assert "unassigned_48h" in codes
        assert "stale_72h" in codes
        assert "urgent_unassigned" in codes
