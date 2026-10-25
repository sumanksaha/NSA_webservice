"""Tests for the Airtable sync service.

Covers the pyairtable 1.x/2.x vs 3.x API differences (paged ``iterate()``,
list-vs-dict ``create()`` result), credential-placeholder handling, table
auto-creation and the R2 CSV export.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


class FakeTable:
    """Minimal stand-in for ``pyairtable.Table``."""

    def __init__(self, pages=None, field_names=None, create_result=None, create_errors=None):
        self.pages = pages or []
        self.field_names = field_names or []
        self.create_result = create_result if create_result is not None else {"id": "rec123"}
        self.create_errors = list(create_errors or [])
        self.create_calls = []

    def iterate(self, **kwargs):
        yield from self.pages

    def schema(self, force=False):
        return SimpleNamespace(fields=[{"name": n} for n in self.field_names])

    def create(self, fields):
        self.create_calls.append(dict(fields))
        if self.create_errors:
            raise self.create_errors.pop(0)
        return self.create_result


class FakeClient:
    """Minimal stand-in for ``pyairtable.Api``."""

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.requested = []

    def table(self, base_id, table_name):
        self.requested.append((base_id, table_name))
        return self.tables.get(table_name)


@pytest.fixture
def airtable_app_ctx():
    """App context with Airtable enabled and non-placeholder credentials."""
    from app import app

    with app.app_context():
        from flask import current_app

        from app.services import airtable_sync

        # Drop cached clients/tables so tests never share fakes.
        airtable_sync._thread_local.__dict__.clear()

        current_app.config["ENABLE_AIRTABLE_SYNC"] = True
        current_app.config["AIRTABLE_API_KEY"] = "patTestKey"
        current_app.config["AIRTABLE_BASE_ID"] = "appTestBase"
        yield current_app

    airtable_sync._thread_local.__dict__.clear()


class TestCredentialHandling:
    """Placeholder / missing credentials must not trigger live API calls."""

    def test_placeholder_api_key_is_treated_as_unset(self, airtable_app_ctx):
        from app.services import airtable_sync

        airtable_app_ctx.config["AIRTABLE_API_KEY"] = "your-airtable-personal-access-token-here"
        assert airtable_sync._api_key() is None
        assert airtable_sync.is_configured() is False

    def test_placeholder_base_id_is_treated_as_unset(self, airtable_app_ctx):
        from app.services import airtable_sync

        airtable_app_ctx.config["AIRTABLE_BASE_ID"] = "your-airtable-base-id-here"
        assert airtable_sync._primary_base_id() is None
        assert airtable_sync.is_configured() is False

    def test_missing_credentials_disable_client(self, airtable_app_ctx):
        from app.services import airtable_sync

        airtable_app_ctx.config["AIRTABLE_API_KEY"] = None
        airtable_app_ctx.config["AIRTABLE_BASE_ID"] = None
        assert airtable_sync._get_client() is None
        assert airtable_sync.sync_to_airtable("sample_repo", {"id": 1}) is False


class TestPyAirtableCompatibility:
    """pyairtable 3.x returns pages from iterate() and a dict from create()."""

    def test_iter_records_flattens_pages(self):
        from app.services.airtable_sync import _iter_records

        table = FakeTable(pages=[[{"id": "a"}, {"id": "b"}], [{"id": "c"}]])
        assert [r["id"] for r in _iter_records(table)] == ["a", "b", "c"]

    def test_iter_records_handles_single_dicts(self):
        from app.services.airtable_sync import _iter_records

        table = FakeTable(pages=[{"id": "a"}])
        assert [r["id"] for r in _iter_records(table)] == ["a"]

    def test_record_id_from_dict(self):
        from app.services.airtable_sync import _record_id

        assert _record_id({"id": "rec1"}) == "rec1"

    def test_record_id_from_list(self):
        from app.services.airtable_sync import _record_id

        assert _record_id([{"id": "rec2"}]) == "rec2"
        assert _record_id([]) is None


class TestSyncToAirtable:
    """Row sync behaviour."""

    def test_sync_disabled_by_feature_flag(self, airtable_app_ctx):
        from app.services import airtable_sync

        airtable_app_ctx.config["ENABLE_AIRTABLE_SYNC"] = False
        assert airtable_sync.sync_to_airtable("sample_repo", {"id": 1}) is False

    def test_sync_creates_record_with_mapped_columns(self, airtable_app_ctx):
        from app.services import airtable_sync

        table = FakeTable(field_names=["id", "sample_code"])
        client = FakeClient({"Sample_Repository": table})

        with (
            patch.object(airtable_sync, "_get_client", return_value=client),
            patch.object(airtable_sync, "_get_base_id", return_value="appTestBase"),
            patch.object(airtable_sync, "_ensure_table", return_value=table),
            patch.object(airtable_sync, "_track_airtable_sync") as track,
        ):
            ok = airtable_sync.sync_to_airtable("sample_repo", {"id": 5, "sample_code": "NS-1"}, db_record_id=5)

        assert ok is True
        assert table.create_calls == [{"id": "5", "sample_code": "NS-1"}]
        track.assert_called_once_with(5, "sample_repo", "rec123", "appTestBase")

    def test_sync_tracks_mapping_with_list_result(self, airtable_app_ctx):
        from app.services import airtable_sync

        table = FakeTable(create_result=[{"id": "recLegacy"}])
        client = FakeClient({"Sample_Repository": table})

        with (
            patch.object(airtable_sync, "_get_client", return_value=client),
            patch.object(airtable_sync, "_get_base_id", return_value="appTestBase"),
            patch.object(airtable_sync, "_ensure_table", return_value=table),
            patch.object(airtable_sync, "_track_airtable_sync") as track,
        ):
            ok = airtable_sync.sync_to_airtable("sample_repo", {"id": 5}, db_record_id=5)

        assert ok is True
        track.assert_called_once_with(5, "sample_repo", "recLegacy", "appTestBase")

    def test_sync_drops_fields_missing_from_schema(self, airtable_app_ctx):
        from app.services import airtable_sync

        table = FakeTable(
            field_names=["id"],
            create_errors=[Exception("UNKNOWN_FIELD_NAME: unknown field names: sample_code")],
        )
        client = FakeClient({"Sample_Repository": table})

        with (
            patch.object(airtable_sync, "_get_client", return_value=client),
            patch.object(airtable_sync, "_get_base_id", return_value="appTestBase"),
            patch.object(airtable_sync, "_ensure_table", return_value=table),
            patch.object(airtable_sync, "_track_airtable_sync"),
        ):
            ok = airtable_sync.sync_to_airtable("sample_repo", {"id": 5, "sample_code": "NS-1"}, db_record_id=5)

        assert ok is True
        assert table.create_calls == [{"id": "5", "sample_code": "NS-1"}, {"id": "5"}]

    def test_sync_returns_false_on_api_error(self, airtable_app_ctx):
        from app.services import airtable_sync

        table = FakeTable(create_errors=[RuntimeError("network down")])
        client = FakeClient({"Sample_Repository": table})

        with (
            patch.object(airtable_sync, "_get_client", return_value=client),
            patch.object(airtable_sync, "_get_base_id", return_value="appTestBase"),
            patch.object(airtable_sync, "_ensure_table", return_value=table),
        ):
            ok = airtable_sync.sync_to_airtable("sample_repo", {"id": 5}, db_record_id=5)

        assert ok is False


class TestBaseManagement:
    """Capacity detection, field specs and table auto-creation."""

    def test_capacity_check_counts_records_across_pages(self):
        from app.services.airtable_sync import BASE_ROTATION_THRESHOLD, _base_near_capacity

        pages = [[{"id": str(i)} for i in range(100)] for _ in range(12)]
        client = FakeClient({"Sample_Repository": FakeTable(pages=pages)})
        assert _base_near_capacity(client, "appTestBase", "sample_repo") is True

        small = FakeClient({"Sample_Repository": FakeTable(pages=[[{"id": "1"}]])})
        assert _base_near_capacity(small, "appTestBase", "sample_repo") is False
        assert BASE_ROTATION_THRESHOLD == 1100

    def test_build_table_fields_uses_text_types(self):
        from app.services.airtable_sync import _build_table_fields

        fields = _build_table_fields("sample_repo")
        assert fields[0] == {"name": "id", "type": "text"}
        assert all(f["type"] == "text" for f in fields)

    def test_ensure_table_creates_missing_table(self, airtable_app_ctx):
        from app.services import airtable_sync

        class FakeBase:
            def create_table(self, name, fields):
                self.created = (name, fields)
                return FakeTable(field_names=[f["name"] for f in fields])

        class ClientWithBase(FakeClient):
            def __init__(self):
                super().__init__()
                self.base_obj = FakeBase()

            def base(self, base_id):
                return self.base_obj

        client = ClientWithBase()

        with patch.object(airtable_sync, "_create_table", side_effect=airtable_sync._create_table):
            with patch.object(FakeTable, "schema", side_effect=Exception("TableNotFound")):
                table = airtable_sync._ensure_table(client, "appTestBase", "sample_repo")

        assert table is not None
        assert client.base_obj.created[0] == "Sample_Repository"


class TestExport:
    """R2 CSV export must include every record in every page."""

    def test_export_collects_all_paged_records(self, airtable_app_ctx, monkeypatch):
        from app.services import airtable_sync

        table = FakeTable(pages=[[{"fields": {"id": "1"}}, {"fields": {"id": "2"}}], [{"fields": {"id": "3"}}]])
        client = FakeClient({"Sample_Repository": table})

        captured = {}

        class FakeBucket:
            pass

        class FakeR2:
            def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3 casing
                captured["key"] = Key
                captured["body"] = Body

        monkeypatch.setattr("app.utils.storage._get_client", lambda: FakeR2())
        monkeypatch.setattr("app.utils.storage._get_bucket", lambda: FakeBucket())

        with (
            patch.object(airtable_sync, "_get_client", return_value=client),
            patch.object(airtable_sync, "_known_base_ids", return_value=["appTestBase"]),
        ):
            key = airtable_sync.export_airtable_all_bases_to_r2()

        assert key is not None
        assert "Sample_Repository" in captured["body"]
        assert captured["body"].count("\n") == 4  # header + 3 records
