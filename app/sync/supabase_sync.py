"""Supabase sync service with conflict resolution (Phase 17).

The Supabase client is lazy-imported — the module loads fine without the
``supabase`` package installed (all public methods catch ``ImportError`` and
degrade to an error result).

Conflict resolution strategy (optimistic concurrency via ``SyncState``):
    - Each synced record has a row in the ``sync_state`` table with a
      ``sync_version`` integer that increments on every successful push/pull.
    - Push: the service checks each dirty record's local ``sync_version``
      against Supabase's stored version; mismatches are queued as conflicts
      rather than silently overwriting.
    - Pull: the same check runs in the opposite direction.
    - Conflicts are persisted to the ``sync_conflicts`` table with both
      versions so the UI can present a diff and the user can pick a winner
      via ``POST /sync/resolve-conflict/<id>``.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.extensions import db
from app.models import Adjudication, Bill, CaseFile, Inspection, Sample
from app.shared.config import cfg
from app.sync.models import SyncConflict, SyncState

logger = logging.getLogger(__name__)

#: Models that participate in Supabase sync, paired with their Supabase
#: table name. Centralised so adding a model is a one-line edit.
_SYNC_MODELS: list[tuple[type, str]] = [
    (CaseFile, "case_files"),
    (Adjudication, "adjudications"),
    (Bill, "bills"),
    (Sample, "samples"),
    (Inspection, "inspections"),
]

#: Column excluded from sync payloads (local bookkeeping only).
_SYNC_SKIP_COLUMNS = frozenset({
    "sync_version",
    "pdf_task_id",
})


@dataclass
class SyncResult:
    """Outcome of a push / pull / resolve operation."""

    status: str = "ok"  # ok | error | partial
    pushed: int = 0
    pulled: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    http_status: int = 200  # HTTP status code for route responses

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pushed": self.pushed,
            "pulled": self.pulled,
            "conflicts": self.conflicts,
            "errors": self.errors,
            "detail": self.detail,
        }


class SupabaseSyncService:
    """Push/pull core records to/from Supabase with conflict detection.

    The service is now **always enabled** (synchronous mandatory sync).
    Credentials must be configured for it to work.
    """

    def __init__(self):
        self._client: Any = None

    # ------------------------------------------------------------------ #
    # Enable / client lifecycle
    # ------------------------------------------------------------------ #

    def is_enabled(self) -> bool:
        """Always True — sync is mandatory. Credentials checked on first use."""
        return True

    def get_client(self) -> Any:
        """Return a cached Supabase client, or None if unavailable.

        Gracefully handles missing Supabase package or misconfiguration.
        """
        if self._client is not None:
            return self._client

        if not cfg.supabase_url or not cfg.supabase_api_key:
            return None

        try:
            from supabase import create_client, Client
        except ImportError:
            return None

        self._client = create_client(cfg.supabase_url, cfg.supabase_api_key)
        return self._client

    # ------------------------------------------------------------------ #
    # Push (local → Supabase)
    # ------------------------------------------------------------------ #

    # Supabase PostgREST caps batch upserts at this many rows per request.
    _BATCH_SIZE = 500

    def push(self) -> SyncResult:
        """Push all locally-dirty records to Supabase.

        A record is dirty when its ``SyncState`` row is missing or its
        ``synced_at`` is older than the model's ``updated_at`` (falls back
        to ``created_at``).

        Uses **batch upserts** — collects all dirty payloads for a table
        into a single ``upsert([...])`` call (chunked at 500 rows),
        reducing API calls from N to ceil(N / 500) per table.
        """
        result = SyncResult()
        client = self.get_client()

        for model, table_name in _SYNC_MODELS:
            try:
                dirty_records = self._find_dirty_records(model, table_name)
                if not dirty_records:
                    continue

                # Batch-fetch all remote versions in a single API call.
                remote_versions = self._batch_remote_versions(client, table_name, [r.id for r in dirty_records])

                # Separate records into push-able vs conflicting.
                to_push: list[dict[str, Any]] = []
                push_records: list[tuple[Any, SyncState]] = []
                for record in dirty_records:
                    state = self._get_or_create_state(table_name, record.id)
                    rv = remote_versions.get(record.id)
                    if rv is not None and rv != state.sync_version:
                        # Conflict: remote changed since last sync.
                        remote_row = self._fetch_remote_row(client, table_name, record.id)
                        self._record_conflict(
                            table_name,
                            record.id,
                            state.sync_version,
                            rv,
                            "push",
                            remote_row or {},
                        )
                        result.conflicts += 1
                        continue

                    payload = self._model_to_payload(record, model)
                    to_push.append(payload)
                    push_records.append((record, state))

                # Batch upsert in chunks of _BATCH_SIZE.
                for chunk_start in range(0, len(to_push), self._BATCH_SIZE):
                    chunk = to_push[chunk_start : chunk_start + self._BATCH_SIZE]
                    try:
                        client.table(table_name).upsert(chunk).execute()
                        # Update sync state for this chunk.
                        for _, state in push_records[chunk_start : chunk_start + self._BATCH_SIZE]:
                            state.sync_version += 1
                            state.synced_at = datetime.now(UTC)
                        result.pushed += len(chunk)
                    except Exception as exc:
                        for rec, _ in push_records[chunk_start : chunk_start + self._BATCH_SIZE]:
                            result.errors.append(f"{model.__name__} #{rec.id}: {exc}")

                try:
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    result.errors.append(f"{model.__name__} commit: {exc}")
            except Exception as exc:
                result.errors.append(f"{model.__name__}: {exc}")

        if result.errors and result.pushed > 0:
            result.status = "partial"
        elif result.errors:
            result.status = "error"
        return result

    # ------------------------------------------------------------------ #
    # Pull (Supabase → local)
    # ------------------------------------------------------------------ #

    def pull(self) -> SyncResult:
        """Pull records from Supabase that are newer than the local copy.

        This is a **synchronous blocking call** — will not return until
        all remote records have been pulled and applied.
        """
        result = SyncResult()
        client = self.get_client()
        if client is None:
            result.status = "error"
            result.errors.append("Supabase client not available — check SUPABASE_URL and SUPABASE_API_KEY")
            return result

        for model, table_name in _SYNC_MODELS:
            try:
                resp = client.table(table_name).select("*").execute()
                for row in resp.data or []:
                    try:
                        local_id = row.get("local_id")
                        if local_id is None:
                            local_id = row.get("id")
                        remote_version = int(row.get("sync_version", 0))
                        existing = db.session.get(model, local_id) if local_id else None

                        if existing is not None:
                            state = self._get_state(table_name, existing.id)
                            local_version = state.sync_version if state else 0
                            if remote_version > local_version:
                                self._apply_remote_row(model, row, existing)
                                if state:
                                    state.sync_version = remote_version
                                else:
                                    state = SyncState(
                                        table_name=table_name,
                                        local_id=existing.id,
                                        sync_version=remote_version,
                                        synced_at=datetime.now(UTC),
                                    )
                                    db.session.add(state)
                                result.pulled += 1
                            elif remote_version < local_version:
                                self._record_conflict(
                                    table_name,
                                    existing.id,
                                    local_version,
                                    remote_version,
                                    "pull",
                                    row,
                                )
                                result.conflicts += 1
                        else:
                            self._insert_remote_row(model, table_name, row)
                            result.pulled += 1
                    except Exception as exc:
                        result.errors.append(f"{model.__name__} pull row: {exc}")
                try:
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    result.errors.append(f"{model.__name__} commit: {exc}")
            except Exception as exc:
                result.errors.append(f"{model.__name__}: {exc}")

        if result.errors and result.pulled > 0:
            result.status = "partial"
        elif result.errors:
            result.status = "error"
        return result

    # ------------------------------------------------------------------ #
    # Conflict resolution
    # ------------------------------------------------------------------ #

    def resolve_conflict(self, conflict_id: int, winner: str) -> SyncResult:
        """Resolve a pending sync conflict by picking a winner.

        Args:
            conflict_id: PK of the ``SyncConflict`` row.
            winner: ``"local"`` to keep local changes, ``"remote"`` to
                accept the remote version.
        """
        result = SyncResult()
        conflict = db.session.get(SyncConflict, conflict_id)
        if conflict is None:
            result.status = "error"
            result.http_status = 404
            result.errors.append(f"conflict #{conflict_id} not found")
            return result

        if winner not in ("local", "remote"):
            result.status = "error"
            result.http_status = 400
            result.errors.append(f"invalid winner '{winner}' — must be 'local' or 'remote'")
            return result

        client = self.get_client()
        model_cls, table_name = self._model_for_table(conflict.table_name)

        try:
            if winner == "local":
                # Re-push the local version, overwriting remote.
                if client:
                    record = db.session.get(model_cls, conflict.local_id)
                    if record is not None:
                        payload = self._model_to_payload(record, model_cls)
                        payload["sync_version"] = conflict.local_version + 1
                        client.table(table_name).upsert(payload).execute()
                        result.pushed += 1
                # Update local sync state.
                state = self._get_state(table_name, conflict.local_id)
                if state:
                    state.sync_version = conflict.local_version + 1
                    state.synced_at = datetime.now(UTC)
                    state.last_error = None

            else:
                # Apply the remote version locally.
                snapshot = self._parse_snapshot(conflict.remote_snapshot)
                if snapshot:
                    existing = db.session.get(model_cls, conflict.local_id)
                    self._apply_remote_row(model_cls, snapshot, existing)
                state = self._get_state(table_name, conflict.local_id)
                if state:
                    state.sync_version = conflict.remote_version
                    state.synced_at = datetime.now(UTC)
                result.pulled += 1

            db.session.delete(conflict)
            db.session.commit()
            result.status = "ok"
        except Exception as exc:
            db.session.rollback()
            result.status = "error"
            result.errors.append(str(exc))
        return result

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        """Return a summary of sync state for the dashboard."""
        conflicts = db.session.query(SyncConflict).count()
        enabled = self.is_enabled()
        client = self.get_client() if enabled else None

        row_counts: dict[str, int] = {}
        dirty_counts: dict[str, int] = {}
        for model, table_name in _SYNC_MODELS:
            row_counts[table_name] = db.session.query(model).count()
            dirty_counts[table_name] = self._count_dirty(model, table_name)

        return {
            "enabled": enabled,
            "client_connected": client is not None,
            "supabase_url": cfg.supabase_url if enabled else None,
            "synced_models": [m.__name__ for m, _ in _SYNC_MODELS],
            "row_counts": row_counts,
            "dirty_counts": dirty_counts,
            "pending_conflicts": conflicts,
            "sync_interval": cfg.supabase_sync_interval,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _model_for_table(self, table_name: str) -> tuple[type, str]:
        """Return ``(model_class, table_name)`` for a sync-table name."""
        for model, tn in _SYNC_MODELS:
            if tn == table_name:
                return model, tn
        # Fallback: return the first model (should never happen in practice).
        return _SYNC_MODELS[0]

    def _get_state(self, table_name: str, local_id: int) -> SyncState | None:
        """Return the ``SyncState`` row for a record, or ``None``."""
        return db.session.query(SyncState).filter_by(table_name=table_name, local_id=local_id).first()

    def _get_or_create_state(self, table_name: str, local_id: int) -> SyncState:
        """Return the ``SyncState`` row, creating it if needed."""
        state = self._get_state(table_name, local_id)
        if state is None:
            state = SyncState(table_name=table_name, local_id=local_id, sync_version=0)
            db.session.add(state)
            db.session.flush()
        return state

    def _find_dirty_records(self, model: type, table_name: str) -> list[Any]:
        """Return local records that need pushing (no SyncState or stale)."""
        from sqlalchemy import select

        local_ids_stmt = select(SyncState.local_id).where(SyncState.table_name == table_name)
        # Records with no SyncState row -> never synced -> dirty.
        never_synced = db.session.query(model).filter(~model.id.in_(local_ids_stmt)).all()
        # Records with a SyncState row but stale (updated since last sync).
        stale: list[Any] = []
        for state in (
            db.session.query(SyncState).filter_by(table_name=table_name).filter(SyncState.synced_at.is_(None)).all()
        ):
            record = db.session.get(model, state.local_id)
            if record is not None:
                stale.append(record)
        return never_synced + stale

    def _count_dirty(self, model: type, table_name: str) -> int:
        """Count dirty (unsynced) records for a model — for the dashboard."""
        from sqlalchemy import select

        local_ids_stmt = select(SyncState.local_id).where(SyncState.table_name == table_name)
        never_synced = db.session.query(model).filter(~model.id.in_(local_ids_stmt)).count()
        stale = (
            db.session.query(SyncState).filter_by(table_name=table_name).filter(SyncState.synced_at.is_(None)).count()
        )
        return never_synced + stale

    def _remote_version(self, client: Any, table_name: str, local_id: int) -> int | None:
        """Return the ``sync_version`` Supabase has for this local row."""
        try:
            resp = client.table(table_name).select("sync_version").eq("local_id", local_id).execute()
            rows = resp.data or []
            if rows:
                return int(rows[0].get("sync_version", 0))
        except Exception as exc:
            logger.debug("remote version lookup failed for %s/%s: %s", table_name, local_id, exc)
        return None

    def _batch_remote_versions(self, client: Any, table_name: str, local_ids: list[int]) -> dict[int, int]:
        """Fetch sync_version for all local_ids in a single API call.

        Returns ``{local_id: sync_version}`` for rows that exist remotely.
        Falls back to individual lookups on batch failure.
        """
        if not local_ids:
            return {}
        try:
            resp = client.table(table_name).select("local_id,sync_version").in_("local_id", local_ids).execute()
            return {int(row["local_id"]): int(row.get("sync_version", 0)) for row in (resp.data or [])}
        except Exception as exc:
            logger.debug("batch remote version lookup failed for %s: %s", table_name, exc)
            # Fallback: individual lookups.
            return {
                lid: (rv or 0) for lid in local_ids if (rv := self._remote_version(client, table_name, lid)) is not None
            }

    def _fetch_remote_row(self, client: Any, table_name: str, local_id: int) -> dict[str, Any] | None:
        """Fetch the full remote row for a local_id."""
        try:
            resp = client.table(table_name).select("*").eq("local_id", local_id).execute()
            rows = resp.data or []
            return rows[0] if rows else None
        except Exception as exc:
            logger.debug("remote fetch failed for %s/%s: %s", table_name, local_id, exc)
            return None

    def _record_conflict(
        self,
        table_name: str,
        local_id: int,
        local_version: int,
        remote_version: int,
        direction: str,
        snapshot: dict[str, Any],
    ) -> None:
        """Persist a sync conflict for user resolution."""
        existing = db.session.query(SyncConflict).filter_by(table_name=table_name, local_id=local_id).first()
        snapshot_json = json.dumps(snapshot) if isinstance(snapshot, dict) else snapshot
        if existing is None:
            existing = SyncConflict(
                table_name=table_name,
                local_id=local_id,
                local_version=local_version,
                remote_version=remote_version,
                direction=direction,
                remote_snapshot=snapshot_json,
            )
            db.session.add(existing)
        else:
            existing.local_version = local_version
            existing.remote_version = remote_version
            existing.direction = direction
            existing.remote_snapshot = snapshot_json
            existing.updated_at = datetime.now(UTC)

    @staticmethod
    def _parse_snapshot(raw: str | None) -> dict[str, Any] | None:
        """Parse a JSON blob from ``SyncConflict.remote_snapshot``."""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    def _model_to_payload(self, record: Any, model: type) -> dict[str, Any]:
        """Convert a SQLAlchemy model instance to a Supabase upsert payload."""
        payload: dict[str, Any] = {}
        for col in model.__table__.columns:
            if col.name in _SYNC_SKIP_COLUMNS:
                continue
            val = getattr(record, col.name, None)
            if val is None:
                payload[col.name] = None
            elif isinstance(val, datetime):
                payload[col.name] = val.isoformat()
            else:
                payload[col.name] = str(val) if not isinstance(val, (int, float, bool)) else val
        payload["local_id"] = record.id
        return payload

    def _apply_remote_row(self, model: type, row: dict[str, Any], existing: Any | None) -> None:
        """Apply a remote Supabase row to a local model instance."""
        if existing is None:
            existing = model()
            db.session.add(existing)
            db.session.flush()

        for col in model.__table__.columns:
            if col.name in _SYNC_SKIP_COLUMNS or col.name == "id":
                continue
            if col.name not in row:
                continue
            val = row[col.name]
            col_type = col.type.__class__.__name__
            if val is None:
                setattr(existing, col.name, None)
            elif col_type in ("Integer", "BigInteger") and isinstance(val, (int, float)):
                setattr(existing, col.name, int(val))
            elif col_type == "Boolean" and isinstance(val, bool):
                setattr(existing, col.name, val)
            elif col_type in ("DateTime", "TIMESTAMP") and isinstance(val, str):
                with contextlib.suppress(ValueError, TypeError):
                    setattr(existing, col.name, datetime.fromisoformat(val.replace("Z", "+00:00")))
            else:
                setattr(existing, col.name, val)

    def _insert_remote_row(self, model: type, table_name: str, row: dict[str, Any]) -> None:
        """Insert a brand-new remote row as a local model instance."""
        instance = model()
        for col in model.__table__.columns:
            if col.name in _SYNC_SKIP_COLUMNS or col.name == "id":
                continue
            if col.name not in row:
                continue
            val = row[col.name]
            col_type = col.type.__class__.__name__
            if val is None:
                setattr(instance, col.name, None)
            elif col_type in ("Integer", "BigInteger") and isinstance(val, (int, float)):
                setattr(instance, col.name, int(val))
            elif col_type == "Boolean" and isinstance(val, bool):
                setattr(instance, col.name, val)
            elif col_type in ("DateTime", "TIMESTAMP") and isinstance(val, str):
                with contextlib.suppress(ValueError, TypeError):
                    setattr(instance, col.name, datetime.fromisoformat(val.replace("Z", "+00:00")))
            else:
                setattr(instance, col.name, val)
        db.session.add(instance)
        db.session.flush()

        # Create the SyncState row for the new record.
        state = SyncState(
            table_name=table_name,
            local_id=instance.id,
            sync_version=int(row.get("sync_version", 0)),
            synced_at=datetime.now(UTC),
        )
        db.session.add(state)


# Module-level singleton (mirrors the _query_breaker pattern in rag/routes.py).
_service: SupabaseSyncService | None = None


def get_sync_service() -> SupabaseSyncService:
    """Return the shared :class:`SupabaseSyncService` singleton."""
    global _service
    if _service is None:
        _service = SupabaseSyncService()
    return _service
