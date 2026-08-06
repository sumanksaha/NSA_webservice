"""Food Cell module models.

Supports the DO (Designated Officer) Intimation workflow:

- ``DoIntimation`` — tracks the per-sample DO intimation record: HTML snapshot
  path, downloadable PDF URL, DO reference number, forwarding timestamp, and
  sync status across the three parallel targets (Sheets, Airtable, Excel).

The ``food_cell_forwarded`` column on ``Sample`` (see ``app/models/billing.py``)
records the wall-clock timestamp at which the intimation was dispatched to the
Food Cell, giving a single indexable column for queries and restore chaining.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from app.extensions import db


class DoIntimation(db.Model):
    """Designated Officer (DO) Intimation record.

    One row per sample that has been forwarded to the Food Cell.  The
    intimation HTML is saved on disk (under the instance path) so it can be
    re-rendered or audited; the PDF is stored via the storage abstraction
    layer (``app/utils/storage.py``) and its public URL is kept in
    ``pdf_url`` for direct browser download.
    """

    __tablename__ = "do_intimation"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    version_id = db.Column(db.Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {
        "version_id_col": version_id,
    }

    sample_id = db.Column(
        db.Integer,
        db.ForeignKey("sample.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    do_reference_no = db.Column(db.String(100), nullable=False, unique=True, index=True)
    html_path = db.Column(db.String(512), nullable=True)
    pdf_url = db.Column(db.String(512), nullable=True)
    food_cell_forwarded = db.Column(db.DateTime, nullable=True)

    # Status tracking
    status = db.Column(
        db.String(32),
        nullable=False,
        default="pending",
    )  # pending | generated | forwarded | acknowledged | failed
    sync_status = db.Column(
        db.Text,
        nullable=True,
        default=None,
    )  # JSON blob: {"sheets": true, "airtable": false, "excel": true}

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    # Relationship back-reference to the Sample
    sample = db.relationship("Sample", backref=db.backref("do_intimation", uselist=False, lazy="selectin"))

    __table_args__ = (
        db.Index("idx_do_intimation_sample_id", "sample_id"),
        db.Index("idx_do_intimation_status", "status"),
        db.Index("idx_do_intimation_forwarded", "food_cell_forwarded"),
    )

    def __repr__(self) -> str:
        return f"<DoIntimation sample={self.sample_id} ref={self.do_reference_no} status={self.status}>"
