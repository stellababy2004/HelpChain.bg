from __future__ import annotations

from datetime import UTC, datetime

from backend.extensions import db


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ScoreExplanation(db.Model):
    __tablename__ = "score_explanations"

    id = db.Column(db.Integer, primary_key=True)
    score_key = db.Column(db.String(120), nullable=False, index=True)
    subject_type = db.Column(db.String(80), nullable=False, index=True)
    subject_id = db.Column(db.String(120), nullable=False, index=True)
    total_score = db.Column(db.Integer, nullable=False)
    formula_version = db.Column(db.String(40), nullable=False)
    confidence = db.Column(db.String(30), nullable=False, default="low")
    component_list_json = db.Column(db.Text, nullable=False)
    evidence_json = db.Column(db.Text, nullable=False)
    originating_event_ids_json = db.Column(db.Text, nullable=False)
    computed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utc_now, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        db.Index(
            "ix_score_explanations_subject_score",
            "subject_type",
            "subject_id",
            "score_key",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ScoreExplanation score_key={self.score_key!r} "
            f"subject={self.subject_type}:{self.subject_id}>"
        )
