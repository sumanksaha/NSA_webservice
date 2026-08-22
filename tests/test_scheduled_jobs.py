"""Tests for the ScheduledJobs registry (app/services/scheduled_jobs.py).

The interface is the test surface: JOBS is enumerable, register_all() gates on
declared cfg flags, and a fake publisher replaces QStash entirely — no factory
import, no network, no import-time side effects.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import pytest

from app.services.scheduled_jobs import JOBS, register_all


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("ENABLE_BACKUP_SCHEDULE", "RAG_ENABLE_INGESTION_SCHEDULE", "RAG_INGESTION_CRON", "RAG_CORPUS_DIR"):
        monkeypatch.delenv(key, raising=False)


def test_jobs_are_enumerable_and_declared():
    """Every schedule flag/cron key is declared in the config seam."""
    from app.shared.config import cfg

    declared = {s.key for s in cfg.table()}
    for job in JOBS:
        assert job.flag_key in declared, job.flag_key
        if job.cron_key:
            assert job.cron_key in declared, job.cron_key


def test_nothing_registered_when_all_flags_off():
    calls = []
    assert register_all(app=None, publisher=lambda *a, **k: calls.append((a, k))) == []
    assert calls == []


def test_backup_schedule_registered_when_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_BACKUP_SCHEDULE", "true")
    captured = {}

    def fake_publisher(task_name, schedule, payload):
        captured["task"] = task_name
        captured["schedule"] = schedule
        return {"mode": "disabled"}  # QStash unconfigured is fine

    results = register_all(app=None, publisher=fake_publisher)
    assert captured == {"task": "backup_redundant_sheets", "schedule": "0 2 * * *"}
    assert results == [{"job": "backup_redundant_sheets", "status": "registered", "result": {"mode": "disabled"}}]


def test_ingestion_schedule_gated_on_corpus_dir(monkeypatch):
    monkeypatch.setenv("RAG_ENABLE_INGESTION_SCHEDULE", "true")
    # No RAG_CORPUS_DIR → skipped entirely.
    assert register_all(app=None, publisher=lambda *a, **k: {}) == []

    monkeypatch.setenv("RAG_CORPUS_DIR", "/tmp/corpus")
    monkeypatch.setenv("RAG_INGESTION_CRON", "30 4 * * 1")
    captured = {}

    def fake_publisher(task_name, schedule, payload):
        captured.update(task=task_name, schedule=schedule, payload=payload)
        return {"mode": "async"}

    results = register_all(app=None, publisher=fake_publisher)
    assert captured == {
        "task": "ingest_corpus",
        "schedule": "30 4 * * 1",
        "payload": {"corpus_dir": "/tmp/corpus"},
    }
    assert results[0]["job"] == "ingest_corpus"


def test_publisher_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("ENABLE_BACKUP_SCHEDULE", "true")

    def boom(*a, **k):
        raise RuntimeError("qstash down")

    results = register_all(app=None, publisher=boom)
    assert results == [{"job": "backup_redundant_sheets", "status": "error", "error": "qstash down"}]
