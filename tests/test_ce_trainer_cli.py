"""Regression test: the CE trainers' monitoring flags must actually print.

`train_legal_ce_v2.py` advertises in its docstring:

    A pollable ``training_status.json`` is written every 5 steps; monitor it
    from another terminal with ``--status`` / ``--watch``.

Both flags used to exit 0 while printing **nothing** — ruff's ``T201`` bans
``print``, so the output was deleted rather than exempted, leaving a discarded
``json.loads(...)`` and a bare parenthesized f-string. Silent-but-successful is
the worst failure mode for a monitoring flag: the operator waits on an answer
that never arrives and sees no error.

These tests are hermetic: they write a status file into ``tmp_path`` rather
than reading the gitignored checkpoint tree, so they pass on a fresh checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from evaluation import train_legal_ce_v2, train_legal_ce_v3

# Mirrors the real training_status.json field set written by write_training_status().
STATUS = {
    "status": "done",
    "phase": "complete",
    "global_step": 5,
    "total_steps": 10,
    "percent": 50.0,
    "epoch": 1,
    "epochs": 2,
}


def _write_status(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "training_status.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_v2_print_status_emits_json(tmp_path, capsys) -> None:
    path = _write_status(tmp_path, STATUS)
    assert train_legal_ce_v2._print_status(path) == 0
    out = capsys.readouterr().out
    assert out.strip(), "--status printed nothing; monitoring is impossible"
    assert json.loads(out)["status"] == "done"


def test_v2_print_status_missing_file_returns_1(tmp_path, capsys) -> None:
    missing = tmp_path / "nope.json"
    assert train_legal_ce_v2._print_status(missing) == 1
    assert capsys.readouterr().out == ""


def test_v2_watch_status_streams_progress(tmp_path, capsys) -> None:
    path = _write_status(tmp_path, STATUS)
    assert train_legal_ce_v2._watch_status(path, interval=0.01) == 0
    out = capsys.readouterr().out
    assert out.strip(), "--watch printed nothing; progress cannot be monitored"
    assert "step 5/10" in out
    assert "phase=complete" in out


def test_v3_status_and_watch_stay_wired(tmp_path, capsys) -> None:
    """The v3 CLI inherited the same shape — keep it honest too."""
    path = _write_status(tmp_path, STATUS)
    assert train_legal_ce_v3._print_status(path) == 0
    assert json.loads(capsys.readouterr().out)["global_step"] == 5
    assert train_legal_ce_v3._watch_status(path, interval=0.01) == 0
    assert "step 5/10" in capsys.readouterr().out
