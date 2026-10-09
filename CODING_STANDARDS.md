# Coding Standards

Rules applied when **reviewing** work in this repo. Mechanical rules are already
enforced by tooling — see [Tooling](#tooling) — so this file holds only the
**judgement calls** a linter cannot make.

Read at review time, not while implementing. If a rule here can be turned into a
deterministic check, prefer building the check and deleting the rule.

---

## How review works

Implementation carries the context pressure: exploring, writing, debugging.
Review receives a diff and needs none. So the reviewer owns standards — the
implementer should not stop mid-task to re-litigate style.

A finding must name a rule or a concrete consequence. "Doesn't feel right" is
not a finding.

## Tooling

The mechanical layer, and where it lives:

| Check | Source of truth |
| --- | --- |
| Lint + format | `pyproject.toml` (`[tool.ruff]`, `[tool.ruff.format])` — not restated here |
| Types | `pyproject.toml` `[tool.mypy]`; `app/`, non-blocking |
| Pre-commit | `.pre-commit-config.yaml` (ruff, mypy, fast pytest, `ce-v2-gate`, ESLint, Prettier) |
| CI | `.github/workflows/lint.yml`, `validation.yml` — ruff runs **repo-wide** |
| Tests | `tests/`, `pyproject.toml` `[tool.pytest.ini_options]` |

If a lint failure keeps coming back, the cause is usually a missing
per-file-ignore for a legitimate pattern (see below), not a discipline problem.

---

## Judgement calls

### Steering files are pointers, not documents

`AGENTS.md` and `CLAUDE.md` are pushed into every agent's context, so every
line spends tokens on every turn. Each section earns its place by naming a
target **and** the condition for reaching it; the detail lives in the target.

The companion test, `tests/test_steering_docs.py`, asserts every backticked path
and every `docs/INDEX.md` link resolves. Adding a pointer to a file that does
not exist yet fails the build — create the target first.

A pointer's wording decides when the material is reached. Front-load the
trigger, one trigger per branch, and cut identity the target already carries.

### Evaluation CLIs print to stdout

For `evaluation/*.py` harnesses, **stdout is the interface**: a report the
operator reads, a status dump, progress streamed from another terminal. These
files carry a `T201` entry in `pyproject.toml` `per-file-ignores` saying so.

The failure mode to watch for in review: when a new evaluation script needs
output and has no `T201` entry, the print gets **deleted instead of exempted**,
leaving `json.loads(...)` with the result discarded or a bare parenthesized
f-string. The flag then exits `0` and prints nothing — silent success on a
monitoring path is worse than a crash. `tests/test_ce_trainer_cli.py` guards the
two trainer CLIs against exactly this.

An application module in `app/` has no such exemption: route handlers log, they
do not print.

### What "frozen" covers

Frozen means *the artifact under comparison must not change*:

- **Benchmark v1.0** — the 150 questions and gold provisions. Any edit mints
  v1.1+; never silently alter a question.
- **`legal_ce_v1`** — the control model. Never retrained.
- **`legal_ce_v2`** — the baseline a candidate is scored against.

"Frozen" describes the **artifact**, not the surrounding plumbing. Fixing a
trainer's `--status` output does not touch the control's weights, and should not
be blocked by the word. It does mean: confirm before retraining anything, and
record which artifact a number came from.

### Split discipline

Training pairs are split **by `question_id`**, never by pair — all pairs from
one question stay in one split. Pair-level splitting leaks question text across
train and val and quietly inflates every metric downstream.

Validation stays on the full frozen val split even when training data is
reweighted, so `val_loss` remains comparable across runs.

### One-off scripts belong in `scripts/`

`scripts/` shares a single ignore rule. Each script placed at the repo root
instead buys its own permanent `per-file-ignores` entry in `pyproject.toml`.
`tests/test_repo_root_layout.py` freezes the current root inventory: adding a
root-level `*.py` requires extending the allowlist in that test, which makes the
cost visible in review.

### Book effort against the right failure bucket

Before recommending work, attribute the failure. This repo already measured the
split (`evaluation/out/ceiling_v5/failure_attribution.json`):
`retrieval_rank` (gold in pool, below the window) is addressable by ranking
training; `generation` and `never_retrieved` are not.

Booking a generation-side failure against a ranker retrain — or claiming a
retrieval gain as an answer-quality gain — is the standard way effort gets spent
on the wrong bucket. State which bucket a claim is about, and cite the artifact
that measured it.

The same discipline applies to evidence: a completed run is proven by
`ce_train_summary_*.json`, not by a `training_status.json` that happens to read
`done` with null losses.

---

## Where to look

| Need | File |
| --- | --- |
| Domain vocabulary | `CONTEXT.md` |
| Past decisions (don't re-litigate) | `docs/adr/` |
| Tracker, labels, domain-doc rules | `docs/agents/` |
| Training/eval cache schemas | `evaluation/README.md` |
| Everything else | `docs/INDEX.md` |

Paths under `evaluation/out/` are gitignored and the file tools refuse them —
read those via the terminal.
