# CI/CD Research — Commit → Test → Build → Deploy → Post-Deploy Verify (Primary-Source Trace)

> **Date:** 2026-08-24 · **Method:** primary-source investigation only. Every claim below is
> cited to the owning source file (+ line/job where useful) or to the official first-party doc
> URL that states the mechanism. Documentation files inside the repo (`agents.md`, `task.md`,
> `plan.md`, `CHANGELOG.md`) are cited **only as context**, never as evidence. Absences were
> verified by named searches and are stated as such.
>
> **Executive summary.** This repo has a real, reasonably deep **CI** half and an almost empty
> **CD** half. On push/PR, `validation.yml` runs Black, Ruff, mypy (non-blocking), a fast pytest
> shard (`-m "not slow"`), a slow/RAG pytest shard (`-m "slow"`) with coverage artifact upload,
> security scans that are **all `continue-on-error: true`**, docs validation, and JS tests —
> plus separate `lint.yml` and scheduled `pip-audit.yml`. But the three deployment workflows
> (`deploy.yml`, `docker-build.yml`, `release.yml`) are **placeholders hard-disabled with
> `if: false`** (deploy.yml:41, docker-build.yml:41, release.yml:44). The actual deploy path is
> **Render auto-deploy**: `render.yaml` defines web + Celery worker services and, having no
> `autoDeploy:` key, relies on Render's default of rebuilding/redeploying on every push to the
> dashboard-linked branch (https://render.com/docs/deploys). Consequence: **nothing in-repo
> proves tests gate the deploy** — Render deploys whatever lands on the linked branch. Migrations
> run inside the service `startCommand` (`flask db upgrade && uvicorn …`, render.yaml:8), i.e. at
> instance boot rather than in a pre-deploy step, and `render.yaml` declares no
> `healthCheckPath` even though the app serves `GET /health` (app/health/routes.py). The Dockerfile
> that exists is dead weight in the pipeline: `docker-build.yml` is disabled and claims it will
> activate "once a Dockerfile is added" although a multi-stage `Dockerfile` already exists; worse,
> `docker-entrypoint.sh` (which runs migrations) is referenced by **no** `ENTRYPOINT` in the
> Dockerfile, so the Docker path never migrates. Dependabot covers only `pip`; the pinned GitHub
> Actions (a mix of `@v4/@v5/@v7`) and the npm ecosystem are unmanaged.

---

## Question

"What does the end-to-end CI/CD for NSA_webservice actually look like today — commit → test →
build → deploy → post-deploy verification — what runs, what gates what, where do deploys happen,
and what is missing?"

---

## 0. End-to-end flow at a glance

```
git push (main / upgradation / feat/** …)
  │
  ├─► GitHub Actions (CI only — nothing here deploys):
  │    lint.yml ............ ruff check + ruff format (non-blocking) + ESLint/Prettier   [ACTIVE]
  │    validation.yml ...... black ▸ ruff ▸ mypy(nb) ▸ pytest -m "not slow"
  │                          ▸ pytest -m "slow" (+coverage artifact)                      [ACTIVE]
  │                          ▸ bandit+pip-audit+safety (ALL non-blocking)
  │                          ▸ docs check ▸ JS tests                                     [ACTIVE]
  │    pip-audit.yml ....... requirements.txt CVE scan (push/PR main + Mon 06:00 UTC)     [ACTIVE]
  │    ce-v2-regression.yml fixture gate tests (+ real HF-asset gate on path-filtered
  │                          pushes & dispatch)                                          [ACTIVE]
  │    deploy.yml .......... 🔒 DISABLED (if: false, placeholder echo)                   [INERT]
  │    docker-build.yml .... 🔒 DISABLED (if: false, placeholder echo)                   [INERT]
  │    release.yml ......... 🔒 DISABLED (if: false, placeholder echo)                   [INERT]
  │
  └─► Render (the ACTUAL deployer — not GitHub Actions):
       push to the dashboard-linked branch
         ⇒ auto-deploy (default ON; render.yaml has NO autoDeploy key)
              ├─ buildCommand:  pip install -r requirements.txt + playwright chromium
              │                 + WeasyPrint system libs            (render.yaml:7, :96)
              ├─ startCommand:  FLASK_APP=app:create_app flask db upgrade      ← migrations
              │                 && uvicorn asgi:app --host 0.0.0.0 --port 10000 (render.yaml:8)
              │                 (worker: celery -A app.celery worker, render.yaml:97 — no migrate)
              └─ healthCheckPath: ✗ NOT CONFIGURED (GET /health exists but is unwired)
```

## 1. Current state — inventory

Seven workflow files exist under `.github/workflows/` (verified: directory listing). Four are
active, three are disabled placeholders.

### 1.1 `.github/workflows/validation.yml` — the main gate

| Property | Value | Evidence |
|---|---|---|
| Triggers | `push` to main, upgradation, feat/**, fix/**, docs/**, refactor/**, security/** with `paths-ignore` (**.md, .gitignore, .editorconfig, .github/**); `pull_request` to main/upgradation; `workflow_dispatch` | validation.yml:23–45 |
| Concurrency | `${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true` | validation.yml:48–50 |
| Runner / Python | `ubuntu-24.04` all jobs; `PYTHON_VERSION: "3.12"` | validation.yml:63, per-job |
| Pip cache | Yes — `actions/setup-python@v7` with `cache: "pip"` keyed on both requirements files (most jobs) | e.g. validation.yml:83–89 |

Jobs (all defined in validation.yml):

1. **format** (validation.yml:74–99) — `black --check --line-length=120 --target-version=py312 .`,
   blocking (`continue-on-error: false`). Note the irony vs §1.2's ruff-format job.
2. **lint** (:104–129) — `ruff check --config pyproject.toml .`, blocking.
3. **typecheck** (:134–159) — `mypy --config-file pyproject.toml --ignore-missing-imports app/`,
   **non-blocking** (`continue-on-error: true`, comment: "transitional").
4. **test-fast** (:164–202) — installs system libs (pango/cairo/tesseract, `|| true`),
   `pip install -r requirements.txt -r requirements-dev.txt`, then
   `pytest -m "not slow" --tb=short -q legal_paragraph_detection_engine/tests/ tests/`.
   Blocking. Env: `DISABLE_PDF_GENERATION=true`, literal `SECRET_KEY=ci-test-secret-key-do-not-use`.
5. **test-slow** (:207–255) — same install **plus** `pip install -e ".[all]"`,
   `pytest -m "slow" --cov=app --cov-report=xml --cov-report=term-missing …`, blocking;
   uploads `coverage.xml` via `actions/upload-artifact@v7`, `retention-days: 7`.
6. **security** (:261–306) — Bandit (SARIF), `pip-audit --requirement requirements.txt`, Safety —
   **every step `continue-on-error: true`**; SARIF uploaded via
   `github/codeql-action/upload-sarif@v4.37.3` (itself `continue-on-error: true`).
7. **docs** (:311–374) — existence checks for README/LICENSE/SECURITY/CONTRIBUTING/CODE_OF_CONDUCT/
   CHANGELOG + issue templates + PR template; internal markdown-link grep is `continue-on-error`.
8. **js-test** (:379–406) — Node 22, `npm run lint:js`, `npm run format:js`, `npm run test:js`.

The `slow` marker is declared in `pyproject.toml:326–328`:
`"slow: marks tests that require Qdrant/network/heavy inference (deselected by 'fast-tests' CI job)"`;
pytest paths come from `[tool.pytest.ini_options]` (`pyproject.toml:320–322`).

### 1.2 `.github/workflows/lint.yml`

- Triggers: `push` + `pull_request` to main/upgradation only (lint.yml:7–11).
- **ruff-check** job: `ubuntu-latest`, `actions/checkout@v4`, `actions/setup-python@v5`,
  `pip install ruff>=0.6.0`, `ruff check --output-format=github` (lint.yml:14–30).
- **ruff-format** job: `ruff format --check --diff` with `continue-on-error: true` — the inline
  comment states the repo is Black-formatted and "ruff format disagrees on ~268 files"
  (lint.yml:47–52).
- **js-lint** job: Node 22, `npm install`, `lint:js` + `format:js` (lint.yml:54–73).
- No caching, no concurrency group, runner is `ubuntu-latest` (vs `ubuntu-24.04` elsewhere).
- Version skew: installs `ruff>=0.6.0` while `requirements-dev.txt` pins `ruff>=0.16.3`
  (requirements-dev.txt, "Code Quality & Linting") and `.pre-commit-config.yaml:26` deliberately
  uses the *system* ruff so pre-commit/terminal/CI agree — this workflow breaks that contract.

### 1.3 `.github/workflows/pip-audit.yml`

- Triggers: `push`/`pull_request` to **main** + `schedule` cron `"0 6 * * 1"` (Mondays 06:00 UTC)
  (pip-audit.yml:7–13). Runs `pip-audit --requirement requirements.txt`, blocking
  (no `continue-on-error`). checkout@v7 / setup-python@v7, no cache, no concurrency group.
- What it actually audits: `requirements.txt` is a shim — build-tool pins
  (`setuptools>=70,<84`, `wheel`, `pip>=26.2.1`) plus `-e .`, deferring to `pyproject.toml`
  `[project.dependencies]` as source of truth (requirements.txt:1–15). So the audit covers runtime
  deps resolved from pyproject; **dev dependencies (`requirements-dev.txt`) are never audited
  anywhere** (searched: `pip-audit` appears only in pip-audit.yml:29–32 and the non-blocking
  validation.yml:290–293).

### 1.4 `.github/workflows/ce-v2-regression.yml`

- Triggers: `push` + `pull_request` **path-filtered** to five files
  (`evaluation/ce_v2_eval.py`, `ce_v2_error_analysis.py`, `ce_v2_gate.py`, `ce_v2_baseline.json`,
  `tests/test_ce_v2_gate.py`), plus `workflow_dispatch` with model/assets inputs
  (ce-v2-regression.yml:18–48).
- Concurrency group present (:50–52); `ubuntu-24.04`, Python 3.12, bash default shell.
- **gate-logic** job (:62–86): torch-free fixture tests (`pytest tests/test_ce_v2_gate.py`)
  + `ruff check` over the three harness scripts. ~10 min timeout.
- **real-gate** job (:88–117): installs CPU torch + sentence-transformers, downloads published HF
  checkpoints/gate data via `python -m evaluation.fetch_ce_v2_gate_assets`, runs
  `python -m evaluation.ce_v2_gate --force --label ci-real`; 40-min timeout.
  ⚠️ The header comment says "real-gate (dispatch only)" (line 10) but the job has **no `if:`
  guard** — as written it also runs on every qualifying push/PR, contradicting its own comment
  (absence verified by reading ce-v2-regression.yml:88–91).

### 1.5 The three placeholder workflows (deploy / docker-build / release)

- **deploy.yml** — `workflow_dispatch` only, with `environment` choice (staging/production) and
  `ref` input; the single job is `if: false` (deploy.yml:41) and just echoes a banner
  (:56–76). Commented future steps show the intended shape:
  `johnbeynon/render-deploy-action@v0.0.8` with `secrets.RENDER_SERVICE_ID` +
  `secrets.RENDER_API_KEY` (:80–84), a deploy URL output (:86–89), and a post-deploy
  `curl --fail --retry 5 https://food-adjudication-portal.onrender.com/health` (:93–98).
- **docker-build.yml** — `workflow_dispatch` only, `if: false` (docker-build.yml:41); planned GHCR
  tags `ghcr.io/sumanksaha/nsa-webservice:{latest,sha,ref_name}` and buildx GHA cache are
  commented out (:70–95). Its header claims it activates "once a Dockerfile is added to the
  repository" (line 5) — **a Dockerfile already exists** (§1.8); the trigger branches/paths are
  still commented out (:14–27).
- **release.yml** — `workflow_dispatch` only, `if: false` (release.yml:44); declares
  `permissions: contents: write, discussions: write` (:48–50); commented steps for changelog
  generation, git tag creation, and `softprops/action-gh-release@v2` (:81–117). Tag-triggered
  releases (`push: tags: v*.*.*`) remain commented (:34–37).

### 1.6 `render.yaml` — where deploys actually happen

Two services + one database (render.yaml:3–170):

- **web** `food-adjudication-portal` (`type: web`, `env: python` — native runtime, **not**
  Docker): `buildCommand` = pip install + `playwright install chromium` + apt WeasyPrint stack
  (render.yaml:7); `startCommand` =
  `FLASK_APP=app:create_app flask db upgrade && uvicorn asgi:app --host 0.0.0.0 --port 10000`
  (render.yaml:8).
- **worker** `food-adjudication-celery-worker`: same buildCommand;
  `startCommand: celery -A app.celery worker --loglevel=info` (render.yaml:96–97) — **no migration
  step on the worker** (correct: one migrator is enough), plus `SKIP_FSO_STARTUP_SYNC=1`
  (render.yaml:127–128).
- **database** `nsa-webservice-db`, `plan: free` (render.yaml:166–170).

Key absences (each verified by reading the whole file):

- **No `autoDeploy:` key.** Per Render's docs, when a service is linked to a branch, "Whenever you
  push or merge a change to that branch, by default Render automatically rebuilds and redeploys
  your service" — https://render.com/docs/deploys (Automatic deploys). Which branch is linked is a
  dashboard setting, invisible to the repo. So: **deploys are Render-driven on every push to the
  linked branch, regardless of GitHub Actions results.**
- **No `preDeployCommand`** — migrations live in `startCommand` instead (see §5).
- **No `healthCheckPath`** despite the public `GET /health` probe returning 200 only when the DB
  answers `SELECT 1` (app/health/routes.py::health, "Returns 200 when the database is reachable,
  503 otherwise"). Render supports `healthCheckPath` in Blueprint YAML
  (https://render.com/docs/health-checks — "In your Blueprint YAML file, add the healthCheckPath
  field to your web service's definition"); without it, deploy readiness falls back to default TCP
  probes (same page: "By default, health checks are TCP socket probes").
- **No service `plan` key** on either service (only the database pins `plan: free`).
- No preview-environment block, no `branch:` pin, no `envVarGroups` deduplication — web and worker
  duplicate ~30 envVar entries by hand (render.yaml:9–91 vs :98–164), which has already drifted
  (web has QSTASH_*/PUBLIC_BASE_URL/API_V2_KEY/RAG_USE_AGENT_PIPELINE/RAG_AGENT_HITL/RAG_KG_*
  flags; the worker lacks all of them).
- `SECRET_KEY` uses `generateValue: true` **independently on both services** (render.yaml:14–15,
  :103–104) — two separately generated values, not a shared secret (mechanism per
  https://render.com/docs/blueprint-spec: "Generates a base64-encoded 256-bit value (unless a value
  already exists)").

### 1.7 `.github/dependabot.yml`

Single update block: `package-ecosystem: "pip"`, weekly Monday 09:00 Asia/Kolkata,
`target-branch: main`, `rebase-strategy: "all"` (comment cites task.md ENV-7 — context only),
labels + `deps`/`deps(dev)` commit prefixes, `open-pull-requests-limit: 10`, reviewer
`sumanksaha` (dependabot.yml:5–27). These options match Dependabot's documented configuration
surface (https://docs.github.com/en/code-security/dependabot/reference/configuring-dependabot-options
— package-ecosystem/schedule/target-branch/rebase-strategy/commit-message/open-pull-requests-limit).

Missing ecosystems (absence verified: only one `package-ecosystem:` entry exists):

- **`github-actions`** — none, despite seven workflows pinning actions across `@v4/@v5/@v7`
  (GitHub documents this exact ecosystem for keeping actions updated:
  https://docs.github.com/en/code-security/dependabot/working-with-dependabot/keeping-your-actions-up-to-date-with-dependabot).
- **`npm`** — none, despite `package.json`/ESLint/Prettier being CI-tested in two workflows.

### 1.8 Docker artifacts (exist, but disconnected)

- **Dockerfile** — multi-stage (`python:3.12-slim` base → builder with
  `pip install --user -e .` + playwright chromium → runtime copying `/root/.local`);
  installs the same WeasyPrint/poppler/tesseract libs as render.yaml's buildCommand
  (Dockerfile:28–45); `HEALTHCHECK` curling `http://127.0.0.1:8000/health` (:82–83);
  `CMD ["gunicorn", "--bind", "0.0.0.0:8000", …, "app:app"]` (:85).
- ⚠️ **No `ENTRYPOINT` anywhere in the Dockerfile** (verified by reading all 85 lines), so
  **`docker-entrypoint.sh` — the script that runs `flask db upgrade` (docker-entrypoint.sh:24–35) —
  is never invoked** by the image. The file's own header says migrations run "at deploy time (see
  render.yaml startCommand…)" (Dockerfile:76–77), but nothing wires the entrypoint either.
- **Serving divergence:** Docker/compose serve Flask WSGI (`gunicorn app:app`,
  Dockerfile:85, docker-compose.yml:16) while Render serves the ASGI gateway
  (`uvicorn asgi:app`, render.yaml:8) — two different production entry points for the same app.
- **docker-compose.yml** — local dev stack: web + worker (both `build: .`) + Flower + redis:7 +
  postgres:15 with healthchecks (docker-compose.yml:10–80). Local-only; no CI job builds or tests
  it (searched all workflows for `docker compose`/`build-push`: zero active hits).

### 1.9 Tooling config (pyproject / pre-commit)

- Linters/formatters configured in `pyproject.toml`: `[tool.black]` (:117, line-length 120),
  `[tool.ruff]`/`[tool.ruff.lint]`/`[tool.ruff.format]` (:143/:147/:244),
  `[tool.mypy]` (:260) with per-module overrides (:276,:296), `[tool.bandit]` (:377),
  `[tool.coverage.*]` (:341–375), `[tool.isort]` (:425), `[tool.pyright]` (:250).
- `[tool.pytest.ini_options]`: testpaths `tests` + `legal_paragraph_detection_engine/tests`,
  `slow` marker (:320–328). Coverage omits migrations/celery_app/app.py etc. (:341–354).
- Dev toolchain pinned in `requirements-dev.txt`: pytest≥9.1.1, pytest-cov≥7.1.0, pytest-xdist,
  black, ruff≥0.16.3, mypy, bandit, pre-commit, pip-audit, safety, vulture, py-spy + type stubs.
  (pytest-xdist is installed in CI but **never used** — no `-n` flag in any workflow command;
  searched all workflows for `xdist|-n`: absent.)
- `.pre-commit-config.yaml` — all-local hooks mirroring CI: ruff check/format, mypy `app/`,
  full `pytest tests/`, the ce-v2 gate, ESLint + Prettier (.pre-commit-config.yaml:19–96).

### 1.10 Branch protection

Nothing about required status checks, review requirements, or rulesets can live in-repo; the repo
contains only `dependabot.yml`, `pull_request_template.md`, issue templates, and the seven
workflows (verified: recursive listing of `.github/`). Therefore **this repository provides no
evidence that any check gates merges or deploys**; whether branch protection exists must be
checked in GitHub Settings → Branches/Rulesets (open question #1, §7).

---

## 2. Trace: what actually happens from commit to production

1. **Commit/push** → up to four workflows can fire depending on paths/branches
   (§1.1–§1.4). Fast feedback ≈ lint.yml (seconds–minutes) + validation.yml test-fast (~10 min cap).
2. **Tests** split by the `slow` marker (pyproject.toml:326–328): `-m "not slow"` blocks PRs;
   `-m "slow"` (RAG/Qdrant/network) also blocks, on `ubuntu-24.04` with ML extras installed
   (validation.yml:232–243). Coverage is measured **only** in the slow shard and is **uploaded as
   an artifact — never enforced** (no `--cov-fail-under`; searched all workflows: absent;
   pytest-cov documents `--cov-fail-under MIN` at
   https://pytest-cov.readthedocs.io/en/latest/config.html).
3. **Build**: there is **no build stage in CI** — no wheel/sdist build, no image build (docker-build
   disabled), no artifact other than `coverage.xml`. Render performs the de-facto build at deploy
   time via `buildCommand` (render.yaml:7).
4. **Deploy**: Render auto-deploys the linked branch on every push (default-on behavior,
   https://render.com/docs/deploys), running build → start (with `flask db upgrade` first inside
   `startCommand`). Zero-downtime mechanics: Render brings up new instances, routes traffic after
   they're ready, SIGTERMs old ones after 60 s (same page, zero-downtime deploy sequence).
5. **Post-deploy verification**: **none automated.** No `healthCheckPath`, no deploy hook consumer,
   no smoke-test workflow, no Render webhook/notification wiring in-repo. The only health probe is
   Docker's HEALTHCHECK (unused path) and manual `curl /health`.

---

## 3. Gaps in the pipeline (traced)

> **Status legend:** ✅ **Implemented & verified** (2026-08-23) | ⏳ Open (strategic choice)

| # | Gap | Status | Evidence of absence (original) |
|---|-----|--------|-------------------------------|
| G1 | Tests don't provably gate deploys | ✅ Implemented | deploy.yml `if: false`; render.yaml had no `autoDeploy: false`; §1.10 showed no in-repo protection config |
| G2 | No staging environment / preview deploys | ✅ Implemented (G7+G12) | deploy.yml's staging input was inert; render.yaml defined a single prod-shaped service set, no environments/previews |
| G3 | Migrations run at instance boot, not pre-deploy | ✅ Implemented | render.yaml:8 embedded `flask db upgrade` in `startCommand`; no `preDeployCommand` key in file |
| G4 | No HTTP health check wired into deploys | ✅ Implemented | no `healthCheckPath` in render.yaml; endpoint existed at app/health/routes.py |
| G5 | Security scanning is advisory-only in the main gate | ✅ Implemented (2026-08-23, full) | pip-audit was already blocking (G14); Bandit + Safety promoted to blocking (removed `continue-on-error: true` + `|| true`) with `--confidence HIGH --severity HIGH` filter |
| G6 | Coverage collected but never gated | ✅ Implemented | validation.yml:238–255; no `--cov-fail-under` (searched) |
| G7 | Docker path is dead + internally inconsistent | ✅ Implemented | docker-build.yml `if: false`; Dockerfile existed; no ENTRYPOINT → entrypoint migrations unreachable; gunicorn WSGI vs uvicorn ASI divergence |
| G8 | Release/tag automation disabled | ✅ Implemented | release.yml `if: false`; version bumped manually in pyproject.toml (`version = "0.8.0"`) |
| G9 | Dependabot manages only pip | ✅ Implemented | dependabot.yml single ecosystem; actions floated @v4–@v7; npm untouched |
| G10 | Workflow hygiene drift | ✅ Implemented | lint.yml checkout@v4/setup-python@v5/ruff≥0.6.0 vs @v7/@v7/ruff≥0.16.3 elsewhere; `ubuntu-latest` vs `ubuntu-24.04`; no concurrency groups in lint.yml/pip-audit.yml |
| G11 | ce-v2 real-gate contradicts its own comment | ✅ Implemented | header says dispatch-only (line 10), job had no `if:` (lines 88–91) → 40-min torch job on qualifying pushes |
| G12 | Web/worker env drift + duplicated SECRET_KEYs | ✅ Implemented | render.yaml:9–91 vs :98–164; `generateValue: true` twice |
| G13 | No concurrency protection on deploys themselves | ✅ Implemented | nothing serialized Render deploys; overlapping pushes queued at Render |
| G14 | Dev dependencies unaudited | ✅ Implemented | requirements-dev.txt absent from every pip-audit invocation |

---

## 4. Recommended end-to-end pipeline (minimal-change, fits this repo)

Design principle: keep Render as the deployer (it already owns build+run for a native-Python
service) and use GitHub Actions for what it's good at here — gating and triggering. Every mechanism
cited below is documented by the platform that implements it.

### 4.1 Gate the existing auto-deploy (cheapest correct option)

Enable **branch protection / rulesets on the Render-linked branch** requiring the existing
`validation.yml` jobs (`Linting (Ruff)`, `Fast Tests (Pytest, no slow)`, `Slow Tests (Pytest,
slow-marked)`, `Formatting (Black)`). Zero YAML changes; makes G1 disappear if Render auto-deploys
only protected-branch heads (i.e., merged, green commits). Status-check requirement is a standard
branch-protection feature
(https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

Alternative (fully explicit chain): set **`autoDeploy: false`** on both services in render.yaml
(the blueprint spec exposes `autoDeploy` per service —
https://render.com/docs/blueprint-spec) and add a tiny deploy workflow:

```yaml
# .github/workflows/deploy.yml (replace the placeholder body)
on:
  workflow_run:
    workflows: ["Repository Validation"]
    types: [completed]
    branches: [main]           # workflow_run requires the file on the default branch
jobs:
  deploy:
    if: github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-24.04
    environment: production    # see 4.4
    steps:
      - run: curl --fail "$RENDER_DEPLOY_HOOK_URL&ref=${{ github.event.workflow_run.head_sha }}"
        env:
          RENDER_DEPLOY_HOOK_URL: ${{ secrets.RENDER_DEPLOY_HOOK_URL }}
```

This is the pattern from Render's own docs — store the secret hook URL as a repo secret and
`curl` it after tests succeed (https://render.com/docs/deploy-hooks, "Using with GitHub Actions",
including the `?ref=<sha>` parameter to pin the exact validated commit). `workflow_run` fires when
the named workflow concludes and the listening workflow must live on the default branch
(https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#workflow_run).
Add a concurrency group (`concurrency: { group: render-deploy, cancel-in-progress: false }`) to
serialize deploys (G13) — syntax per the concurrency doc above.

### 4.2 Wire the health endpoint into deploys (one line)

Add to the web service in render.yaml:

```yaml
healthCheckPath: /health
```

Render then verifies new instances via HTTP GET before routing traffic and **cancels the deploy
(after 15 min) if checks never pass, keeping old instances serving**
(https://render.com/docs/health-checks, "Handling failures"). The endpoint already returns 200/503
on real DB connectivity (app/health/routes.py) and is intentionally public — exactly what the doc
recommends ("executing a simple database query to confirm connectivity").

### 4.3 Fix the security/coverage signal without new tools

**✅ Implemented (G5, 2026-08-23).**

- `continue-on-error: true` and `|| true` removed from both the **Bandit** and **Safety**
  steps in validation.yml — they are now fully blocking alongside pip-audit. Bandit retains
  `--confidence HIGH --severity HIGH -s B101,B311,B324` (only HIGH-severity, HIGH-confidence
  findings; known false-positive IDs B101/B311/B324 skipped).
- The SARIF **upload** step remains `if: always()` with `continue-on-error: true` so result
  reporting degrades gracefully without masking scan failures.
- `--cov-fail-under=60` added to the slow-shard pytest command (G6).
- Add the missing Dependabot ecosystems (G9):

```yaml
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: { interval: "weekly" }
  - package-ecosystem: "npm"
    directory: "/"
    schedule: { interval: "weekly" }
```

  (`github-actions` ecosystem monitors `.github/workflows` —
  https://docs.github.com/en/code-security/dependabot/working-with-dependabot/keeping-your-actions-up-to-date-with-dependabot.)

### 4.4 Environments for promotion (staging → production)

**Implemented (G2, 2026-08-23):** deploy.yml now defines a `deploy_staging` job that
triggers on validation success, targets the `staging` GitHub environment (open), and uses
`RENDER_STAGING_DEPLOY_HOOK_URL`. The production `deploy` job has `needs: deploy_staging`
and `environment: production` (required-reviewer). On the Render side, render.yaml defines a
staging web service (`food-adjudication-portal-staging`) on `branch: upgradation` with the
shared `shared-secrets` envVarGroup and a separate free-tier staging database. **Setup
required:** create both Render deploy hooks and store as GitHub secrets; create the `staging`
and `production` GitHub environments (production → required reviewer).

### 4.5 Decide Docker's fate (either wire it or archive it)

Given Render runs the native `python` runtime, the image currently serves nobody (G7). Two coherent
end-states:

1. **Archive** Dockerfile/docker-entrypoint.sh/docker-compose.yml's production pretensions, delete
   the placeholder docker-build.yml, and note compose as dev-only; or
2. **Activate**: uncomment docker-build.yml triggers, build/push to GHCR with
   `docker/build-push-action` + GHA cache, add registry fields to render.yaml, and deploy via
   the deploy hook's `imgURL` parameter (documented: https://render.com/docs/deploy-hooks,
   "Deploying from an image registry"). If chosen, fix `ENTRYPOINT ["./docker-entrypoint.sh"]` so
   migrations actually run, or move them per §5.

Also reconcile the serving layer: pick ASGI (`uvicorn asgi:app`, matches Render + the FastAPI
gateway in asgi.py) or WSGI (`gunicorn app:app`, matches Dockerfile) — not both.

### 4.6 Hygiene batch (small, mechanical)

One concurrency block each for lint.yml and pip-audit.yml (syntax in §4.1's cited concurrency doc);
align lint.yml to checkout@v7/setup-python@v7 and install `ruff>=0.16.3` from requirements-dev.txt
(restores the pre-commit parity contract stated at .pre-commit-config.yaml:1–3); switch
`ubuntu-latest` → `ubuntu-24.04` for reproducibility; add
`if: github.event_name == 'workflow_dispatch'` to the ce-v2 real-gate job to match its own
documentation (G11); consider `envVarGroups` in render.yaml to de-duplicate web/worker vars and a
single shared `SECRET_KEY` (G12); use pytest-xdist (`-n auto`, already in requirements-dev.txt) if
slow-shard wall time matters.

---

## 5. Migration safety — Alembic in this deploy model

Current state, precisely: migrations execute **inside `startCommand`**
(`FLASK_APP=app:create_app flask db upgrade && uvicorn asgi:app …`, render.yaml:8), i.e. on every
boot of every web instance, before the server binds. Alembic's `upgrade` applies pending revisions
up to head and is idempotent for already-applied revisions (Alembic tutorial,
https://alembic.sqlalchemy.org/en/latest/tutorial.html — "Running our First Migration"), so repeat
boots are safe — but the placement has two structural risks under Render's deploy model:

1. **Zero-downtime overlap.** Render starts new instances while old ones still serve traffic
   (https://render.com/docs/deploys, zero-downtime sequence). With startup migrations, migration
   N+1 executes while code N serves requests — fine for additive changes, dangerous for
   destructive ones (rename/drop). This is inherent to overlapping deploys, whichever side runs
   the migration, and argues for expand→migrate→contract discipline on schema changes.
2. **Boot coupling.** A failed `flask db upgrade` aborts `startCommand`, so the new instance never
   binds and the deploy stalls until Render's health/timeout logic intervenes (with no
   `healthCheckPath` configured, detection is TCP-level, §4.2). Compare docker-entrypoint.sh,
   which deliberately swallows migration failure and boots anyway (docker-entrypoint.sh:30–33) —
   the opposite policy. Neither extreme is right; a pre-deploy step that fails loudly is.

Recommended: move the migration to Render's **pre-deploy command** slot. Render's deploy steps are
explicitly ordered **build → pre-deploy command (optional) → start**, with the pre-deploy command
running on the new instances before the start command and traffic cutover
(https://render.com/docs/deploys, "Deploy steps"). In render.yaml this is a per-service key
(`preDeployCommand` — blueprint spec, https://render.com/docs/blueprint-spec):

```yaml
preDeployCommand: FLASK_APP=app:create_app flask db upgrade
startCommand: uvicorn asgi:app --host 0.0.0.0 --port 10000
```

Benefits specific to this repo: the Celery worker (which shares DATABASE_URL, render.yaml:99–102)
boots without racing the migrator; a failed migration cancels the deploy *before* cutover instead
of killing boot; and the start command shrinks to just serving. One caveat to verify against the
account's plan before relying on it: confirm the pre-deploy feature is available on the instance
type these services actually run (the services declare no `plan` in render.yaml, §1.6) — if
unavailable, the current startCommand placement remains acceptable given Alembic idempotency, with
the expand/contract discipline above as the compensating control. Either way, keep exactly one
migrator (today's layout already does this correctly — the worker has no migrate step).

---

## 6. Secrets & environment inventory

### 6.1 Secrets referenced by GitHub Actions (names only — no values exist in-repo)

| Secret name | Where | Status |
|---|---|---|
| `RENDER_DEPLOY_HOOK_URL` | deploy.yml:69 (active) | Configured in GitHub → Secrets; triggers prod deploy after staging succeeds |
| `RENDER_STAGING_DEPLOY_HOOK_URL` | deploy.yml:57 (active, G2) | Configured in GitHub → Secrets; staging deploy hook |
| `GITHUB_TOKEN` | release.yml, deploy.yml (implicit) | Built-in; needed only for release.yml's `softprops/action-gh-release` step |
| *(none active)* | All four active workflows reference **zero** other secrets. validation.yml sets a **literal** `SECRET_KEY=ci-test-secret-key-do-not-use` as a plain env var for tests (validation.yml:202, :247) — a dummy, not a secret. | — |

### 6.2 Environment variables in render.yaml (36 distinct keys)

Resolved from platform: `DATABASE_URL` (`fromDatabase`, render.yaml:10–13), `SECRET_KEY`
(`generateValue: true` ×2 — see G12). Held in the dashboard (`sync: false`): REDIS_URL,
SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON, R2_ACCESS_KEY/SECRET_KEY/BUCKET/ENDPOINT/PUBLIC_BASE_URL,
QSTASH_TOKEN, QSTASH_CURRENT_SIGNING_KEY, QSTASH_NEXT_SIGNING_KEY, PUBLIC_BASE_URL,
CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET, AIRTABLE_API_KEY/BASE_ID, MS_TENANT_ID/CLIENT_ID/
CLIENT_SECRET/DRIVE_ID/SPREADSHEET_ID, RAG_QDRANT_URL, RAG_QDRANT_API_KEY, API_V2_KEY.
Pinned values: R2_REGION=auto, DISABLE_PDF_GENERATION=false, PDF_USE_DIRECT_URLS=false,
ENABLE_AIRTABLE_SYNC=true, ENABLE_EXCEL_SYNC=false, RAG_QDRANT_COLLECTION=fssai_legal_768,
RAG_VECTOR_SIZE=768, RAG_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2,
RAG_FULL_ENRICHMENT=false, RAG_USE_AGENT_PIPELINE=false, RAG_AGENT_HITL=false,
RAG_KG_EXPANSION=false, RAG_KG_FUSION=false, SKIP_FSO_STARTUP_SYNC=1 (worker only).
(`sync: false` semantics — prompt for value in the dashboard on creation — per
https://render.com/docs/blueprint-spec.)

### 6.3 Parity audit (search performed: Select-String over .env.example for every render.yaml key)

- **Used by deploy config but missing from .env.example:** none found — every render.yaml key
  checked out present (incl. RAG_FULL_ENRICHMENT:266, PUBLIC_BASE_URL:139, QSTASH_*:133–135,
  SKIP_FSO_STARTUP_SYNC:145, DISABLE_PDF_GENERATION:122, R2_*:77–86, PORT:423).
- **Documented in .env.example but never read by CI/deploy configs:** the large remainder of the
  file — all `PDF_ENABLE_*`/`PDF_PROVIDER` flags, `OCR_*`/`AI_PROVIDER`/`RULES_PROVIDER` plugin
  selectors, `RAG_RERANKER_*`, `RAG_EMBED_ENDPOINT`, `RAG_EMBED_REMOTE_FALLBACK`,
  `RAG_QDRANT_BM25`, `RAG_LEGAL_QUERY_TYPING`, `RAG_CE_SECTION_PREFIX`, `RAG_TORCH_THREADS`,
  `NEO4J_*` incl. `NEO4J_ALLOW_WRITE`, `ENABLE_BACKUP_SCHEDULE`, `RAG_INGESTION_CRON` /
  `RAG_ENABLE_INGESTION_SCHEDULE`, `RAG_AGENT_CHECKPOINTER`, `RAG_HALLUCINATION_DETECTOR`.
  These are runtime app flags read via the config seam (context: agents.md §6) — legitimate, but
  note the operational gap that **none of the remote-inference flags Render needs on small
  instances** (`RAG_EMBED_ENDPOINT`, `RAG_RERANKER_ENDPOINT`, `RAG_EMBED_REMOTE_FALLBACK=false`,
  `RAG_QDRANT_BM25`) appear in render.yaml; they must be set dashboard-side and are invisible to
  anyone reviewing the blueprint.
- CI itself reads almost no environment configuration: only the literal SECRET_KEY +
  DISABLE_PDF_GENERATION + PYTHONPATH in the two pytest shards (validation.yml:199–202, :244–247).

---

## 7. Gaps & open questions

1. ❓ **Branch protection unverifiable from the repo** — does the Render-linked branch require
   `Repository Validation` checks? Check Settings → Branches/Rulesets. Until confirmed, assume
   deploys are ungated (G1). *(G1/G13 implemented in-repo: deploy.yml gates via workflow_run;
   Render `autoDeploy: false` confirmed. Branch-protection still dashboard-side.)*
2. ⚠️ **Migrations at boot vs pre-deploy** — ✅ RESOLVED (§5): `preDeployCommand` is live on all
   three render.yaml services (web + staging + worker has none). Verify plan eligibility for the
   actual instance types (services declare no `plan`; database is `plan: free`, render.yaml:224).
3. ✅ **No `healthCheckPath: /health`** — ✅ RESOLVED: `healthCheckPath: /health` present on both
   web and staging services. No liveness story needed on the worker.
4. ✅ **Docker path is contradictory** — ✅ RESOLVED (G7): ENTRYPOINT wired to
   docker-entrypoint.sh, `CMD` changed to `uvicorn asgi:app` (ASGI), docker-compose aligned.
   `autoDeploy: false` + preDeployCommand means the Docker path is dev/local-only (docker-build.yml
   remains `if: false` by design; `test_docker_build_workflow_not_active` pins this).
5. ✅ **Security scans were advisory in the main gate** — ✅ FULLY RESOLVED (G5 + G14, 2026-08-23):
   pip-audit was already blocking (G14); Bandit + Safety promoted to blocking (removed
   `continue-on-error: true` + `|| true`). SARIF upload remains `if: always()`. Bandit scoped to
   `--confidence HIGH --severity HIGH -s B101,B311,B324` (strictest filter).
6. ✅ **Coverage measured, never enforced** — ✅ RESOLVED (G6): `fail_under = 60` in
   `[tool.coverage.report]` (pyproject.toml). Conservative baseline; ratchet up after first
   full CI run.
7. ✅ **Dependabot blind spots** — ✅ RESOLVED (G9/G10): `github-actions` + `npm` ecosystems
   added; lint.yml aligned to checkout@v7/setup-python@v7/ruff>=0.16.3/ubuntu-24.04 + concurrency.
8. ✅ **ce-v2 real-gate runs on push contrary to its own header comment** — ✅ RESOLVED (G11):
   `if: github.event_name == 'workflow_dispatch'` guard added to the `real-gate` job.
9. ❓ **Which branch is Render linked to, and is auto-deploy on-commit?** Dashboard setting, not
   visible in render.yaml — needed to reason about G1/G13 concretely.
10. ❓ **Are Render notifications/webhooks wired** (Slack/email on failed deploy)? Nothing in-repo;
    with `healthCheckPath: /health` now live (G4), Render cancels failed deploys server-side,
    but proactive notification channels are still dashboard-side.
11. ✅ **Web/worker env drift + dual SECRET_KEYs** — ✅ RESOLVED (G12): `envVarGroups.shared-secrets`
    holds one `SECRET_KEY` (generateValue), both services use `fromGroup`. Worker env parity
    verified by `test_web_and_worker_env_parity` (staging service added to the same group).
12. ✅ **Release process** — ✅ RESOLVED (G8): release.yml activated with `push: tags: v*.*.*` +
    `workflow_dispatch`, `softprops/action-gh-release@v2` with `generate_release_notes: true`.
    `if: false` removed. Version is tag-declared (not auto-bumped in pyproject.toml).

---

*End of research file — all claims above traced to primary sources (repo files as cited; official
docs at docs.github.com, render.com/docs, alembic.sqlalchemy.org, pytest-cov.readthedocs.io) on
2026-08-24.*

