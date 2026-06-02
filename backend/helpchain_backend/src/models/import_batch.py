from __future__ import annotations

from datetime import UTC, datetime

from backend.extensions import db


class ImportBatch(db.Model):
    __tablename__ = "import_batches"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    source_type = db.Column(db.String(40), nullable=False, default="csv", index=True)
    target_type = db.Column(
        db.String(80), nullable=False, default="professional_leads", index=True
    )
    status = db.Column(db.String(30), nullable=False, default="preview", index=True)
    created_by_admin_id = db.Column(
        db.Integer, db.ForeignKey("admin_users.id"), nullable=False, index=True
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    created_count = db.Column(db.Integer, nullable=False, default=0)
    updated_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_duplicate_count = db.Column(db.Integer, nullable=False, default=0)
    rejected_count = db.Column(db.Integer, nullable=False, default=0)
    imported_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    error_count = db.Column(db.Integer, nullable=False, default=0)
    mapping_json = db.Column(db.Text, nullable=True)
    errors_json = db.Column(db.Text, nullable=True)

    created_by_admin = db.relationship("AdminUser", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<ImportBatch id={self.id} target={self.target_type} "
            f"status={self.status}>"
        )
