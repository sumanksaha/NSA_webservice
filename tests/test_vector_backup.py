"""Tests for the vector-store backup/restore (app/rag/backup.py).

Round-trips a fake in-memory Qdrant store through :func:`backup_collection` /
:func:`restore_collection`, verifying payload + dense + BM25 sparse vectors
survive the JSON archive exactly, plus archive-integrity and dense-only paths.
No Qdrant server or fastembed required — the store is injected.
"""

from __future__ import annotations

import json

import pytest

from app.rag.backup import backup_collection, load_archive, restore_collection
from app.rag.qdrant_client import Point


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def delete_collection(self, name):
        self._store.deleted_collections.append(name)


class _FakeStore:
    """In-memory QdrantStore double with scroll(with_vectors) support."""

    def __init__(self, points=None, sparse=True, collection_name="test_coll", vector_size=768):
        self.points = points or []  # [{"id", "vector", "payload"}]
        self.sparse = sparse
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.upserted: list[Point] = []
        self.ensure_calls: list[dict] = []
        self.deleted_collections: list[str] = []
        self._client = _FakeClient(self)

    def _get_client(self):
        return self._client

    def has_sparse_vectors(self):
        return self.sparse

    def scroll_all(self, with_vectors=False, batch_size=1000, filters=None):
        out = []
        for p in self.points:
            item = {"id": p["id"], "payload": dict(p["payload"])}
            if with_vectors:
                item["vector"] = p["vector"]
            out.append(item)
        return out

    def ensure_collection(self, create_payload_indexes=True, sparse_enabled=False):
        self.ensure_calls.append(
            {"create_payload_indexes": create_payload_indexes, "sparse_enabled": sparse_enabled}
        )
        return True

    def upsert_points(self, points):
        self.upserted.extend(points)
        return len(points)


def _hybrid_points():
    return [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "vector": {
                "dense": [0.1] * 768,
                "text_sparse": {"indices": [1, 5], "values": [0.9, 0.4]},
            },
            "payload": {"document_id": "d1", "chunk_text": "Section 55 penalties.", "section_number": "55"},
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "vector": {
                "dense": [0.2] * 768,
                "text_sparse": {"indices": [2, 9], "values": [0.7, 0.3]},
            },
            "payload": {"document_id": "d2", "chunk_text": "Standards for contaminants.", "section_number": "16"},
        },
    ]


class TestBackupCollection:
    def test_exports_all_points_with_vectors_and_manifest(self, tmp_path):
        store = _FakeStore(points=_hybrid_points(), sparse=True)
        out = tmp_path / "b.json"
        summary = backup_collection(store, str(out))
        assert summary["point_count"] == 2
        assert summary["has_sparse"] is True
        assert summary["vector_size"] == 768
        assert summary["path"] == str(out)

        archive = json.loads(out.read_text(encoding="utf-8"))
        assert archive["collection"] == "test_coll"
        assert archive["sha256"]
        assert len(archive["points"]) == 2
        assert archive["points"][0]["vector"]["text_sparse"] == {"indices": [1, 5], "values": [0.9, 0.4]}

    def test_dense_only_collection_flat_vectors(self, tmp_path):
        store = _FakeStore(
            points=[{"id": "33333333-3333-3333-3333-333333333333", "vector": [0.3] * 768, "payload": {"document_id": "d3"}}],
            sparse=False,
        )
        out = tmp_path / "dense.json"
        summary = backup_collection(store, str(out))
        assert summary["has_sparse"] is False
        archive = json.loads(out.read_text(encoding="utf-8"))
        assert archive["points"][0]["vector"] == [0.3] * 768

    def test_refuses_empty_collection(self, tmp_path):
        store = _FakeStore(points=[])
        with pytest.raises(RuntimeError, match="no points"):
            backup_collection(store, str(tmp_path / "empty.json"))

    def test_raises_when_qdrant_unavailable(self, tmp_path):
        store = _FakeStore(points=_hybrid_points())
        store._get_client = lambda: None
        with pytest.raises(RuntimeError, match="cannot back up"):
            backup_collection(store, str(tmp_path / "b.json"))


class TestRestoreCollection:
    def test_restore_roundtrip_hybrid(self, tmp_path):
        src = _FakeStore(points=_hybrid_points(), sparse=True)
        archive = tmp_path / "b.json"
        backup_collection(src, str(archive))

        dst = _FakeStore(sparse=True)
        summary = restore_collection(dst, str(archive))
        assert summary["points_restored"] == 2
        assert summary["archive_point_count"] == 2
        assert summary["errors"] == []
        # Collection recreated with the archive's sparse layout.
        assert dst.ensure_calls[-1]["sparse_enabled"] is True
        # Points round-trip exactly: id, dense, sparse, payload.
        by_id = {p.id: p for p in dst.upserted}
        assert set(by_id) == {"11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"}
        p = by_id["11111111-1111-1111-1111-111111111111"]
        assert p.vector == [0.1] * 768
        assert p.sparse_vector == {"indices": [1, 5], "values": [0.9, 0.4]}
        assert p.payload["section_number"] == "55"

    def test_restore_roundtrip_dense_only(self, tmp_path):
        src = _FakeStore(
            points=[{"id": "33333333-3333-3333-3333-333333333333", "vector": [0.3] * 768, "payload": {"document_id": "d3"}}],
            sparse=False,
        )
        archive = tmp_path / "dense.json"
        backup_collection(src, str(archive))

        dst = _FakeStore(sparse=False)
        restore_collection(dst, str(archive))
        assert dst.ensure_calls[-1]["sparse_enabled"] is False
        assert dst.upserted[0].vector == [0.3] * 768
        assert dst.upserted[0].sparse_vector is None

    def test_drop_existing_deletes_target_collection(self, tmp_path):
        src = _FakeStore(points=_hybrid_points(), sparse=True)
        archive = tmp_path / "b.json"
        backup_collection(src, str(archive))

        dst = _FakeStore(sparse=True)
        restore_collection(dst, str(archive), drop_existing=True)
        assert dst.deleted_collections == ["test_coll"]

    def test_restore_skips_malformed_named_vector_dict(self, tmp_path):
        """A named-vector dict without 'dense' is skipped with a clean error."""
        archive = {
            "collection": "c",
            "has_sparse": False,
            "vector_size": 768,
            "point_count": 1,
            "points": [
                {"id": "44444444-4444-4444-4444-444444444444", "vector": {"other": [0.1]}, "payload": {}}
            ],
        }
        # Integrity hash not required by restore when sha256 is absent.
        out = tmp_path / "bad.json"
        out.write_text(json.dumps(archive), encoding="utf-8")
        dst = _FakeStore(sparse=False)
        summary = restore_collection(dst, str(out))
        assert summary["points_restored"] == 0
        assert any("without a 'dense'" in e for e in summary["errors"])

    def test_integrity_check_catches_corruption(self, tmp_path):
        src = _FakeStore(points=_hybrid_points(), sparse=True)
        archive = tmp_path / "b.json"
        backup_collection(src, str(archive))

        data = json.loads(archive.read_text(encoding="utf-8"))
        data["points"][0]["payload"]["chunk_text"] = "tampered"
        tampered = tmp_path / "tampered.json"
        tampered.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="integrity"):
            restore_collection(_FakeStore(), str(tampered))


class TestLoadArchive:
    def test_empty_points_rejected(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"points": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="no points"):
            load_archive(str(bad))

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_archive(str(tmp_path / "nope.json"))


# End of test_vector_backup.py
