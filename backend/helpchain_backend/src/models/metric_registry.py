from __future__ import annotations

from datetime import UTC, datetime

from backend.extensions import db


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MetricDefinition(db.Model):
    __tablename__ = "metric_definitions"

    id = db.Column(db.Integer, primary_key=True)
    metric_key = db.Column(db.String(120), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(180), nullable=False)
    formula_version = db.Column(db.String(40), nullable=False)
    owner = db.Column(db.String(120), nullable=False)
    refresh_interval_seconds = db.Column(db.Integer, nullable=False)
    confidence = db.Column(db.String(30), nullable=False, default="low")
    source_tables_json = db.Column(db.Text, nullable=False)
    query_origin = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    def __repr__(self) -> str:
        return f"<MetricDefinition metric_key={self.metric_key!r}>"


class MetricRun(db.Model):
    __tablename__ = "metric_runs"

    id = db.Column(db.Integer, primary_key=True)
    metric_key = db.Column(db.String(120), nullable=False, index=True)
    formula_version = db.Column(db.String(40), nullable=False)
    value_json = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="unavailable", index=True)
    unavailable_reason = db.Column(db.Text, nullable=True)
    source_tables_json = db.Column(db.Text, nullable=False)
    query_origin = db.Column(db.Text, nullable=False)
    confidence = db.Column(db.String(30), nullable=False, default="low")
    source_row_count = db.Column(db.Integer, nullable=False, default=0)
    period_start = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    period_end = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    computed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utc_now, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utc_now)

    def __repr__(self) -> str:
        return f"<MetricRun metric_key={self.metric_key!r} status={self.status!r}>"
