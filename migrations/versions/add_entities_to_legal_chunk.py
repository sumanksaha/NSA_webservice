"""Add entities JSON column to legal_chunk (Agent A §3.4)

Adds the structured entity list ``[{name, type, confidence}]`` column to the
``legal_chunk`` table, mirroring the existing ``citations`` / ``references``
JSON columns.  Populated by the :class:`LegalEntityExtractor`
(``app/rag/entity_extractor.py``) when wired into the ingestion pipeline.

Revision ID: add_entities_to_legal_chunk
Revises: add_legal_document_tables
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa


revision = "add_entities_to_legal_chunk"
down_revision = "add_legal_document_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("legal_chunk", sa.Column("entities", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("legal_chunk", "entities")
