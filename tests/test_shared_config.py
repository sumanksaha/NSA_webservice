"""Tests for the shared configuration seam (app/shared/config.py).

The interface is the test surface: named accessors, generic getters, the
Pattern A resolution rule, per-flag boolean conventions, and the docs-parity
meta-test against ``.env.example``.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from flask import Flask

from app.shared.config import Setting, cfg, seed_config_from_env


@pytest.fixture()
def app_ctx():
    """A bare Flask app + context (config readable via current_app)."""
    app = Flask(__name__)
    with app.app_context():
        yield app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove every declared key from the env so tests start from defaults."""
    for setting in cfg.table():
        monkeypatch.delenv(setting.key, raising=False)


# ---------------------------------------------------------------------------
# Defaults through named accessors
# ---------------------------------------------------------------------------


def test_defaults_outside_app_context():
    assert cfg.rag_enabled is True
    assert cfg.evidence_selector is False
    assert cfg.kg_fusion is False
    assert cfg.kg_expansion is False
    assert cfg.ensemble_ce_head == 30  # unified with create_app (was 20 in tasks.py)
    assert cfg.ensemble_ce_weight == 0.5
    assert cfg.agent_checkpointer == "memory"
    assert cfg.qdrant_collection == "fssai_legal_768"
    assert cfg.vector_size == 768
    assert cfg.torch_threads == 4
    assert cfg.reranker_timeout == 5.0


def test_unknown_accessor_raises_helpfully():
    with pytest.raises(AttributeError, match="no setting 'nope'"):
        _ = cfg.nope


# ---------------------------------------------------------------------------
# Boolean conventions (preserved per-flag, declared in the table)
# ---------------------------------------------------------------------------


def test_opt_in_convention(monkeypatch):
    # ENABLE_EVIDENCE_SELECTOR is opt-in: only "true" enables.
    for raw, expected in [("true", True), ("TRUE", True), ("1", False), ("yes", False), ("false", False)]:
        monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", raw)
        assert cfg.evidence_selector is expected, raw


def test_opt_out_convention(monkeypatch):
    # RAG_IDENTIFIER_ROUTE is opt-out: anything but "false" enables.
    for raw, expected in [("true", True), ("1", True), ("yes", True), ("garbage", True), ("false", False)]:
        monkeypatch.setenv("RAG_IDENTIFIER_ROUTE", raw)
        assert cfg.identifier_route is expected, raw


def test_string_false_in_config_is_false__regression(app_ctx):
    """Regression: ``bool("false")`` used to be True in the old resolvers."""
    app_ctx.config["RAG_KG_EXPANSION"] = "false"
    assert cfg.kg_expansion is False
    app_ctx.config["RAG_KG_EXPANSION"] = "true"
    assert cfg.kg_expansion is True
    app_ctx.config["RAG_KG_EXPANSION"] = False
    assert cfg.kg_expansion is False


# ---------------------------------------------------------------------------
# Pattern A resolution
# ---------------------------------------------------------------------------


def test_config_wins_inside_app_context(app_ctx, monkeypatch):
    monkeypatch.setenv("RAG_KG_MAX_PROVISIONS", "9")
    app_ctx.config["RAG_KG_MAX_PROVISIONS"] = 3
    assert cfg.kg_max_provisions == 3


def test_env_ignored_inside_app_context_when_key_unset(app_ctx, monkeypatch):
    """Pattern A: in-context, an unset config key falls to the declared
    default — env is only consulted outside an app context."""
    monkeypatch.setenv("RAG_KG_MAX_PROVISIONS", "9")
    assert cfg.kg_max_provisions == 5


def test_env_used_outside_app_context(monkeypatch):
    monkeypatch.setenv("RAG_KG_MAX_PROVISIONS", "9")
    assert cfg.kg_max_provisions == 9


# ---------------------------------------------------------------------------
# Typed parsing
# ---------------------------------------------------------------------------


def test_int_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RAG_ENSEMBLE_CE_HEAD", "not-a-number")
    assert cfg.ensemble_ce_head == 30


def test_float_parsing(monkeypatch):
    monkeypatch.setenv("RAG_ENSEMBLE_CE_WEIGHT", "0.8")
    assert cfg.ensemble_ce_weight == 0.8


def test_str_parsing_outside_app_context(monkeypatch):
    monkeypatch.setenv("RAG_AGENT_CHECKPOINTER", "postgres")
    assert cfg.agent_checkpointer == "postgres"


# ---------------------------------------------------------------------------
# Generic accessors (dynamic keys)
# ---------------------------------------------------------------------------


def test_get_bool_dynamic_key(monkeypatch):
    monkeypatch.setenv("DYNAMIC_FLAG", "true")
    assert cfg.get_bool("DYNAMIC_FLAG") is True
    monkeypatch.setenv("DYNAMIC_FLAG", "1")
    assert cfg.get_bool("DYNAMIC_FLAG") is False  # opt-in default convention
    assert cfg.get_bool("MISSING_FLAG", default=True) is True


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seed_config_from_env_seeds_and_skips(monkeypatch):
    app = Flask(__name__)
    app.config["RAG_ENABLED"] = False  # pre-existing — must be left alone
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_KG_FUSION", "true")
    monkeypatch.setenv("RAG_ENSEMBLE_CE_HEAD", "25")
    seeded = seed_config_from_env(app)
    assert app.config["RAG_ENABLED"] is False  # untouched
    assert app.config["RAG_KG_FUSION"] is True  # parsed per convention
    assert app.config["RAG_ENSEMBLE_CE_HEAD"] == 25  # typed
    assert seeded >= 2


def test_seed_sets_defaults_when_env_unset():
    """Every declared key lands in config even without env — soft readers
    (current_app.config.get) inside an app context must see the same value
    an out-of-context caller resolves under Pattern A (env-or-default)."""
    app = Flask(__name__)
    seed_config_from_env(app)
    for setting in cfg.table():
        assert setting.key in app.config, setting.key
        assert app.config[setting.key] == setting.default


# ---------------------------------------------------------------------------
# Docs-parity meta-test: every documented RAG_/ENABLE_ env var is declared.
# (Keys read outside the seam are allowlisted explicitly.)
# ---------------------------------------------------------------------------

_NOT_SEAM_SETTINGS = {
    "RAG_LLM_MODEL",  # read by GroundedLLMClient config directly
    "RAG_CORPUS_DIR",  # ingestion scripts
    "RAG_ENABLE_INGESTION_SCHEDULE",  # QStash schedule wiring
    "RAG_INGESTION_CRON",
}


def test_env_example_keys_are_declared():
    env_example = pathlib.Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    documented = set(re.findall(r"^(RAG_[A-Z0-9_]+|ENABLE_[A-Z0-9_]+)=", text, re.MULTILINE))
    declared = {s.key for s in cfg.table()}
    undocumented_by_seam = documented - declared - _NOT_SEAM_SETTINGS
    assert not undocumented_by_seam, (
        f"keys documented in .env.example but not declared in the config seam: {sorted(undocumented_by_seam)}"
    )


def test_table_attrs_are_unique_and_valid():
    attrs = [s.attr for s in cfg.table()]
    keys = [s.key for s in cfg.table()]
    assert len(attrs) == len(set(attrs)), "duplicate accessor names"
    assert len(keys) == len(set(keys)), "duplicate keys"
    for s in cfg.table():
        assert s.type in (bool, int, float, str)
        assert isinstance(s, Setting)
