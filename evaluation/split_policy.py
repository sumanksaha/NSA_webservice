"""Answer-level split policy — declare and enforce which slice an A/B used.

Why this module exists
----------------------
``benchmark_v1.0.jsonl`` labels every question ``dev`` (100) / ``validation``
(30) / ``holdout`` (20).  Until now **no harness read that label** — the
loader exposed it as ``BenchmarkQuestion.split`` and every answer-level
harness ran all 150, so the 20 holdout questions were in every reported A/B.
A field that looks like a holdout but is evaluated on every iteration is
worse than no field, because it licenses the belief that a holdout exists.

The ranking path has always enforced a split (``pairwise_train_split.json``,
read by ``pairwise_dataset``, ``ce_v2_eval``, ``ranking_loss_trainer``,
``train_legal_ce_v3``).  That asymmetry is why pairwise eval can support a
0.7pp MDE claim while answer-level eval cannot support a 5pp one.

What this module does
---------------------
It does **not** silently shrink any harness's population.  Changing
``load_questions()``'s default from "all 150" to a 30-question slice would
have quietly changed every ``n=`` in every report and invalidated the stored
baselines — a different kind of wrong number, produced by a different route.

Instead it makes the split an explicit, stamped property of every answer-level
report:

* :func:`contamination_report` — what an arbitrary qid set actually contains.
* :func:`stamp_split` — adds the declaration to a report dict.
* :func:`assert_no_holdout` — hard guard for the runs that must not peek.

Policy
------
``dev`` (100) trains and iterates.  ``validation`` (30) is the answer-level
evaluation slice — at n=30 the MDE is worse than at n=150, and that is the
honest price of not training on the thing you measure.  ``holdout`` (20) is
opened for final adjudication only.

At the current sizes no answer-level claim below ~5pp is detectable
regardless (``docs/RAG_EVALUATION_ENLARGEMENT.md``: MDE 5.1pp at n=150 with a
5% flip fraction).  Slicing makes that worse, not better; slicing makes it
*true*.  See ``docs/adr/0011-answer-eval-split-policy.md``.

Records a correction: that document describes the file as "train 95 / val 20 /
test 21".  The file says ``dev`` / ``validation`` / ``holdout`` =
100 / 30 / 20.  Plan against the file, not the document.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Iterable

__all__ = [
    "DEV_SPLIT",
    "EVAL_SPLIT",
    "HOLDOUT_SPLIT",
    "answer_eval_qids",
    "assert_no_holdout",
    "contamination_report",
    "describe_qids",
    "stamp_split",
]

#: Split names as they appear in ``benchmark_v1.0.jsonl``.
DEV_SPLIT = "dev"
EVAL_SPLIT = "validation"
HOLDOUT_SPLIT = "holdout"

#: Known split names, for validating a caller's ``--split``.
KNOWN_SPLITS = (DEV_SPLIT, EVAL_SPLIT, HOLDOUT_SPLIT)

#: Cache for ``_split_of`` — the census is read once per process.
_SPLIT_BY_QID: dict[str, str] | None = None


def split_census() -> dict[str, list[str]]:
    """Benchmark split census: dev/validation/holdout qid lists.

    Single source of truth for ``evaluation/split_policy`` and any test that
    needs the split labels without re-deriving them.  Mirrors the frozen
    benchmark's ``split`` field (100 / 30 / 20).
    """
    from evaluation.benchmark import load_questions

    out: dict[str, list[str]] = {}
    for q in load_questions():
        out.setdefault(q.raw.get("split") or "MISSING", []).append(q.question_id)
    return out


def _split_of() -> dict[str, str]:
    global _SPLIT_BY_QID
    if _SPLIT_BY_QID is None:
        from evaluation.split_policy import split_census as _split_census

        _SPLIT_BY_QID = {qid: name for name, qids in _split_census().items() for qid in qids}
    return _SPLIT_BY_QID


def answer_eval_qids(split: str = EVAL_SPLIT) -> list[str]:
    """Question ids for a split, in benchmark order."""
    return [qid for qid, name in _split_of().items() if name == split]


def describe_qids(qids: Iterable[str]) -> dict[str, Any]:
    """Split breakdown for an arbitrary qid set.

    Args:
        qids: Question ids an experiment actually ran on.

    Returns:
        Counts per split, plus ``unlabelled`` for ids that are not in the
        benchmark at all — a report that silently includes questions outside
        the benchmark is a different contamination mode, and it should not
        hide behind a total.
    """
    lookup = _split_of()
    counts: dict[str, int] = {}
    unlabelled: list[str] = []
    for qid in qids:
        name = lookup.get(str(qid))
        if name is None:
            counts["unlabelled"] = counts.get("unlabelled", 0) + 1
            if len(unlabelled) < 20:
                unlabelled.append(str(qid))
        else:
            counts[name] = counts.get(name, 0) + 1
    return {
        "counts": counts,
        "n": sum(counts.values()),
        "unlabelled_qids": unlabelled,
        "declared_split": None,
        "clean": counts.get(HOLDOUT_SPLIT, 0) == 0 and counts.get("unlabelled", 0) == 0,
    }


def contamination_report(qids: Iterable[str], *, declared_split: str | None = None) -> dict[str, Any]:
    """What this run's population actually is, for stamping into a report.

    Args:
        qids: Question ids the run executed.
        declared_split: The split the caller *claims* to have used, if any.
            A mismatch between the claim and the contents is itself the
            finding, so it is reported rather than reconciled.

    Returns:
        A dict with the observed counts, the declared split, whether the run
        touched the holdout, and whether the declaration is true.  Designed to
        be embedded verbatim in a report's ``limitations`` provenance.
    """
    qids = list(qids)
    observed = describe_qids(qids)
    counts = observed["counts"]
    touches_holdout = counts.get(HOLDOUT_SPLIT, 0) > 0
    touches_dev = counts.get(DEV_SPLIT, 0) > 0

    problems: list[str] = []
    if touches_holdout:
        problems.append(
            f"{counts[HOLDOUT_SPLIT]} holdout question(s) were evaluated; the holdout is not admissible for iteration"
        )
    if touches_dev:
        problems.append(
            f"{counts[DEV_SPLIT]} dev question(s) were evaluated; dev feeds "
            "training, so answer-level metrics on it leak"
        )
    if observed["counts"].get("unlabelled", 0):
        problems.append(f"{observed['counts']['unlabelled']} qid(s) are not in the benchmark")
    if declared_split and observed["counts"].get(declared_split, 0) != observed["n"]:
        problems.append(
            f"declared split {declared_split!r} but the population is "
            f"{counts} — the declaration does not describe this run"
        )

    report = {
        "declared_split": declared_split,
        "observed_counts": counts,
        "n": observed["n"],
        "touches_holdout": touches_holdout,
        "touches_dev": touches_dev,
        "unlabelled_qids": observed["unlabelled_qids"],
        "problems": problems,
    }
    report["statement"] = _statement(report)
    return report


def _statement(report: dict[str, Any]) -> str:
    counts = report["observed_counts"]
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    if not report["problems"]:
        return f"Answer-level split: clean ({parts}). No holdout or dev questions were evaluated."
    return "Answer-level split: CONTAMINATED - " + "; ".join(report["problems"])


def assert_no_holdout(qids: Iterable[str]) -> None:
    """Raise unless the population is *provably* free of holdout questions.

    For runs that claim to be training-clean.  Deliberately a raise, not a
    warning: a holdout that has been read cannot be un-read, so the only
    safe moment to stop is before the run.

    Also refuses qids that are not in the benchmark at all.  An unidentifiable
    qid might be a holdout from another slice or a later revision, and a guard
    that passes when it cannot tell is not a guard — being unable to prove the
    population is clean has to count as not clean.
    """
    described = describe_qids(qids)
    counts = described["counts"]
    if counts.get(HOLDOUT_SPLIT, 0):
        raise ValueError(
            f"{counts[HOLDOUT_SPLIT]} holdout question(s) in this population: "
            "the holdout must be opened once, for final adjudication only"
        )
    if counts.get("unlabelled", 0):
        raise ValueError(
            f"{counts['unlabelled']} qid(s) are not in the benchmark and "
            f"cannot be cleared: {described['unlabelled_qids']}"
        )


def stamp_split(report: dict[str, Any], qids: Iterable[str], *, declared_split: str | None = None) -> dict[str, Any]:
    """Attach split provenance to a report dict, in place, and return it.

    Every answer-level report should carry this.  A report that cannot say
    which slice it ran on cannot be compared with another report, and the
    stored baselines predate this field — so a future comparison has to notice
    the mismatch rather than assume it.
    """
    report["split_provenance"] = contamination_report(qids, declared_split=declared_split)
    return report
