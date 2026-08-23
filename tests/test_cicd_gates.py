"""Structural regression tests for the CI/CD pipeline gates.

Every claim in ``docs/CI_CD_RESEARCH.md`` §4 is pinned here so the pipeline
invariants cannot silently drift back to the pre-ec45ba5 state. These tests
read config files directly (YAML / TOML / .py) — no Flask app, no DB, no
network. They run in the ``test-fast`` CI job (``-m "not slow"``).

Covers gaps: G1, G2, G3, G4, G5, G6, G7, G8, G9, G10, G11, G12, G13, G14.
"""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict:
    """Load YAML, normalizing the YAML 1.1 ``on`` → ``True`` gotcha."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # PyYAML resolves bare `on:` (used for GitHub Actions triggers) to the
    # boolean True. Restore it to the string key so tests can use `["on"]`.
    if isinstance(data, dict) and True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _render() -> dict:
    return _read_yaml(ROOT / "render.yaml")


def _workflow(name: str) -> dict:
    return _read_yaml(WORKFLOWS / name)


def _pyproject() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def _staging_service() -> dict:
    """Return the staging web service from render.yaml."""
    services = _render()["services"]
    staging = next(s for s in services if "staging" in s.get("name", ""))
    assert staging is not None, "no staging service found in render.yaml"
    return staging


def _web_service() -> dict:
    """Return the production web service (type=web, not staging)."""
    services = _render()["services"]
    return next(s for s in services if s.get("type") == "web" and "staging" not in s.get("name", ""))


def _worker_service() -> dict:
    """Return the Celery worker service (type=worker)."""
    services = _render()["services"]
    return next(s for s in services if s.get("type") == "worker")


# ═══════════════════════════════════════════════════════════════════════════════
# G1 / G13 — Deploy gating: autoDeploy off + workflow_run gate + concurrency
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeployGating:
    def test_render_auto_deploy_disabled_on_all_services(self):
        """No service in render.yaml should auto-deploy — deploys go through
        deploy.yml which gates on green CI."""
        for svc in _render()["services"]:
            assert svc.get("autoDeploy") is False, (
                f"{svc['name']}: autoDeploy is {svc.get('autoDeploy')}, expected false"
            )

    def test_deploy_workflow_triggers_on_validation_success(self):
        """deploy.yml must use workflow_run on 'Repository Validation' completion."""
        wf = _workflow("deploy.yml")
        on = wf["on"]
        assert "workflow_run" in on, "deploy.yml must use workflow_run trigger"
        wr = on["workflow_run"]
        assert "Repository Validation" in wr["workflows"], (
            "workflow_run must gate on 'Repository Validation' (validation.yml name)"
        )
        assert "completed" in wr["types"]
        assert "main" in wr["branches"]

    def test_deploy_workflow_has_concurrency_group(self):
        """Deploys must be serialized to avoid overlapping cutovers (G13)."""
        wf = _workflow("deploy.yml")
        assert "concurrency" in wf, "deploy.yml missing concurrency block"
        assert wf["concurrency"]["cancel-in-progress"] is False, (
            "cancel-in-progress must be false — never interrupt a live deploy"
        )

    def test_deploy_workflow_has_production_environment(self):
        """deploy.yml must use a named environment for reviewer/wait-timer gating."""
        wf = _workflow("deploy.yml")
        job = wf["jobs"]["deploy"]
        env = job.get("environment", {})
        assert env.get("name") == "production"

    def test_production_deploy_not_skipped_when_staging_skipped(self):
        """On workflow_dispatch→production, deploy_staging is intentionally
        skipped. GitHub Actions skips any job whose `needs` didn't succeed,
        so the deploy job's `if:` must include !cancelled() and a
        needs.deploy_staging.result guard or manual production deploys are
        silently unreachable."""
        job = _workflow("deploy.yml")["jobs"]["deploy"]
        cond = str(job.get("if", ""))
        assert "!cancelled()" in cond, "deploy if must use !cancelled()"
        assert "needs.deploy_staging.result != 'failure'" in cond, "deploy if must tolerate a skipped staging leg"


# ═══════════════════════════════════════════════════════════════════════════════
# G3 / G4 — Migrations in preDeployCommand, healthCheckPath wired
# ═══════════════════════════════════════════════════════════════════════════════


class TestRenderHealthAndMigrations:
    def test_web_service_has_health_check_path(self):
        web = _web_service()
        assert web.get("healthCheckPath") == "/health"

    def test_worker_service_has_no_pre_deploy_command(self):
        """Only the web service migrates — the worker should never run flask db upgrade."""
        worker = _worker_service()
        assert "preDeployCommand" not in worker, "worker must not run migrations — one migrator only (web)"

    def test_web_service_uses_pre_deploy_command(self):
        web = _web_service()
        cmd = web.get("preDeployCommand", "")
        assert "flask db upgrade" in cmd, "preDeployCommand must run migrations"

    def test_start_command_has_migration_fallback(self):
        """preDeployCommand needs a paid Render plan; on free tier it is ignored.
        Each web service's startCommand must therefore retain an idempotent
        boot-time `flask db upgrade` fallback so migrations always run."""
        for svc in _render()["services"]:
            if svc.get("type") == "web":
                assert "db upgrade" in svc.get("startCommand", ""), (
                    f"{svc['name']}: startCommand must retain migration fallback"
                )

    def test_worker_never_migrates(self):
        """The Celery worker start command must never run migrations."""
        worker = _worker_service()
        assert "db upgrade" not in worker.get("startCommand", ""), "worker must not run migrations"

    def test_health_endpoint_registered_as_public(self):
        """app/health/routes.py::health must be in public_endpoints so the
        Render healthCheckPath probe works without auth."""
        init_py = _read_text(ROOT / "app" / "__init__.py")
        assert "health.health" in init_py, "health endpoint must be in public_endpoints"
        assert "healthCheckPath" in _read_text(ROOT / "render.yaml")


# ═══════════════════════════════════════════════════════════════════════════════
# G5 — Security scanning: all blocking (pip-audit + bandit + safety)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSecurityGates:
    def test_pip_audit_blocking_in_validation(self):
        """pip-audit in validation.yml must be blocking (continue-on-error: false)."""
        wf = _workflow("validation.yml")
        security_job = wf["jobs"]["security"]
        pip_step = None
        for step in security_job["steps"]:
            if "pip-audit" in step.get("name", ""):
                pip_step = step
                break
        assert pip_step is not None, "pip-audit step not found in security job"
        assert pip_step.get("continue-on-error") is False, "pip-audit must be blocking (continue-on-error: false)"

    def test_pip_audit_covers_dev_deps(self):
        """G14: requirements-dev.txt must be audited alongside requirements.txt."""
        for wf_name in ("validation.yml", "pip-audit.yml"):
            content = _read_text(WORKFLOWS / wf_name)
            assert "requirements.txt" in content, f"{wf_name} must scan requirements.txt"
            assert "requirements-dev.txt" in content, f"{wf_name} must also scan requirements-dev.txt (G14)"

    def test_security_scans_are_blocking(self):
        """G5: Bandit and Safety scan steps must be blocking (continue-on-error
        absent/False) — no longer advisory. The SARIF *upload* step remains
        non-blocking. pip-audit is already blocking (see test above)."""
        wf = _workflow("validation.yml")
        for step in wf["jobs"]["security"]["steps"]:
            name = step.get("name", "")
            if name == "Run Bandit (static security analysis)":
                assert step.get("continue-on-error", False) is False, "Bandit scan must be blocking (G5)"
                assert "|| true" not in step.get("run", ""), "Bandit must not swallow exit codes"
            if name == "Run Safety (additional dependency scan)":
                assert step.get("continue-on-error", False) is False, "Safety scan must be blocking (G5)"
                assert "|| true" not in step.get("run", ""), "Safety must not swallow exit codes"


# ═══════════════════════════════════════════════════════════════════════════════
# G6 — Coverage gate (fail_under)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCoverageGate:
    def test_coverage_fail_under_declared(self):
        """pyproject.toml must declare fail_under under [tool.coverage.report] (G6)."""
        cov = _pyproject().get("tool", {}).get("coverage", {}).get("report", {})
        assert "fail_under" in cov, "coverage report must have fail_under threshold"
        assert cov["fail_under"] >= 50, f"fail_under={cov['fail_under']} is suspiciously low (should be ~60+)"

    def test_slow_tests_enable_coverage(self):
        """validation.yml slow job must use --cov=app."""
        wf = _workflow("validation.yml")
        steps_text = yaml.dump(wf)
        assert "--cov=app" in steps_text


# ═══════════════════════════════════════════════════════════════════════════════
# G7 — Docker path: ENTRYPOINT wired, ASGI serving
# ═════════════════════════════════════════════════════════════════════════════──


class TestDockerConsistency:
    def test_dockerfile_has_entrypoint(self):
        """Dockerfile must declare ENTRYPOINT referencing docker-entrypoint.sh (G7)."""
        df = _read_text(ROOT / "Dockerfile")
        assert "ENTRYPOINT" in df, "Dockerfile missing ENTRYPOINT"
        assert "docker-entrypoint.sh" in df, "ENTRYPOINT must reference docker-entrypoint.sh"

    def test_dockerfile_serves_asgi(self):
        """Dockerfile CMD must use uvicorn asgi:app (matching render.yaml)."""
        df = _read_text(ROOT / "Dockerfile")
        assert "asgi:app" in df, "Dockerfile must serve via ASGI (asgi:app), not WSGI (app:app)"

    def test_docker_compose_uses_asgi(self):
        """docker-compose.yml web service must use uvicorn asgi:app."""
        dc = _read_text(ROOT / "docker-compose.yml")
        assert "asgi:app" in dc, "docker-compose must serve ASGI"

    def test_docker_build_workflow_not_active(self):
        """docker-build.yml must remain if:false until explicitly activated (G7 §4.5)."""
        wf = _workflow("docker-build.yml")
        job = wf["jobs"]["build"]
        # `if: false` in YAML becomes Python boolean False; check identity
        # to distinguish from a missing key (None) which would also pass `not`.
        assert job.get("if") is False, (
            "docker-build.yml must remain disabled (if: false) until the Docker "
            "path is explicitly activated or archived"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# G9 — Dependabot covers pip + github-actions + npm
# ═══════════════════════════════════════════════════════════════════════════════


class TestDependabot:
    def test_dependabot_covers_all_ecosystems(self):
        """G9: Dependabot must cover pip, github-actions, and npm."""
        content = _read_text(ROOT / ".github" / "dependabot.yml")
        ecosystems = re.findall(r'package-ecosystem:\s*"([^"]+)"', content)
        assert "pip" in ecosystems
        assert "github-actions" in ecosystems, "github-actions ecosystem missing"
        assert "npm" in ecosystems, "npm ecosystem missing"


# ═══════════════════════════════════════════════════════════════════════════════
# G10 — Workflow hygiene: action versions + concurrency + runner consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkflowHygiene:
    def test_lint_workflow_aligned_versions(self):
        """lint.yml must use @v7 actions and ruff>=0.16.3 (G10)."""
        content = _read_text(WORKFLOWS / "lint.yml")
        assert "actions/checkout@v7" in content and "actions/setup-python@v7" in content
        assert "ruff>=0.16.3" in content or "ruff>=0.16" in content
        assert "ubuntu-24.04" in content, "lint.yml must use ubuntu-24.04"

    def test_lint_workflow_has_concurrency(self):
        content = _read_text(WORKFLOWS / "lint.yml")
        assert "concurrency:" in content

    def test_pip_audit_workflow_has_concurrency(self):
        content = _read_text(WORKFLOWS / "pip-audit.yml")
        assert "concurrency:" in content

    def test_validation_workflow_names_match(self):
        """deploy.yml workflow_run must reference the exact name in validation.yml."""
        val_wf = _workflow("validation.yml")
        deploy_wf = _workflow("deploy.yml")
        val_name = val_wf["name"]
        wr_workflows = deploy_wf["on"]["workflow_run"]["workflows"]
        assert val_name in wr_workflows, (
            f"deploy.yml workflow_run '{wr_workflows}' must include validation.yml name '{val_name}'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# G11 — ce-v2 real-gate is dispatch-only
# ═══════════════════════════════════════════════════════════════════════════════


class TestCeV2Gate:
    def test_real_gate_job_is_dispatch_only(self):
        """G11: real-gate job must only run on workflow_dispatch (not push/PR)."""
        wf = _workflow("ce-v2-regression.yml")
        real_gate = wf["jobs"]["real-gate"]
        assert "if" in real_gate, "real-gate must have an 'if' guard"
        assert "workflow_dispatch" in real_gate["if"], (
            "real-gate must be restricted to workflow_dispatch to match its header comment"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# G12 — envVarGroup for shared SECRET_KEY + worker env parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnvParity:
    def test_shared_secret_key_via_env_var_group(self):
        """G12: SECRET_KEY must come from an envVarGroup, not generateValue per-service."""
        render = _render()
        assert "envVarGroups" in render, "render.yaml missing envVarGroups"
        groups = {g["name"]: g for g in render["envVarGroups"]}
        assert "shared-secrets" in groups, "shared-secrets envVarGroup missing"
        sk_var = groups["shared-secrets"]["envVars"][0]
        assert sk_var["key"] == "SECRET_KEY"

    def test_no_generate_value_on_service_secret_key(self):
        """Neither web nor worker should have generateValue: true on SECRET_KEY."""
        for svc in _render()["services"]:
            for v in svc["envVars"]:
                if v.get("key") == "SECRET_KEY":
                    assert "generateValue" not in v, f"{svc['name']}: SECRET_KEY must use fromGroup, not generateValue"
                    assert "fromGroup" in v

    def test_web_and_worker_env_parity(self):
        """All RAG/API/QStash keys on the web service must also be on the worker."""
        web = _web_service()
        worker = _worker_service()

        web_keys = {v["key"] for v in web["envVars"]}
        worker_keys = {v["key"] for v in worker["envVars"]}

        # SKIP_FSO_STARTUP_SYNC is intentionally worker-only
        worker_only = worker_keys - web_keys
        assert worker_only == {"SKIP_FSO_STARTUP_SYNC"}, f"Unexpected worker-only keys: {worker_only}"

        # No web-only keys that the worker needs for its tasks
        # (worker doesn't need API_V2_KEY for serving, but it does for RAG agent task dispatch)
        missing = web_keys - worker_keys
        assert not missing, f"Worker missing these web keys: {missing}"


# ═══════════════════════════════════════════════════════════════════════════════
# G2 — Staging environment (Render staging service + deploy.yml staging job)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStagingEnvironment:
    def test_staging_web_service_exists(self):
        """G2: render.yaml must define a staging web service."""
        services = _render()["services"]
        staging = [s for s in services if "staging" in s.get("name", "")]
        assert len(staging) == 1, f"Expected exactly 1 staging service, found {len(staging)}"

    def test_staging_service_auto_deploy_disabled(self):
        """G2: staging service must have autoDeploy: false (deployed via deploy hook)."""
        staging = _staging_service()
        assert staging.get("autoDeploy") is False, "staging service must have autoDeploy: false"

    def test_staging_service_has_health_check(self):
        """G2: staging service must have healthCheckPath: /health (G4 parity)."""
        staging = _staging_service()
        assert staging.get("healthCheckPath") == "/health", "staging service must have healthCheckPath: /health"

    def test_staging_service_has_pre_deploy_command(self):
        """G2: staging service must run migrations in preDeployCommand (G3 parity),
        with the same idempotent boot-time fallback in startCommand."""
        staging = _staging_service()
        cmd = staging.get("preDeployCommand", "")
        assert "flask db upgrade" in cmd, "staging preDeployCommand must run migrations"
        assert "db upgrade" in staging.get("startCommand", ""), "staging startCommand must retain migration fallback"

    def test_staging_service_uses_default_branch(self):
        """G2: staging service tracks main — deploy.yml pins validated main SHAs
        via the deploy hook (?ref=<sha>); an off-branch pin would be rejected."""
        staging = _staging_service()
        assert staging.get("branch") == "main", "staging service must branch: main"

    def test_staging_service_shares_secret_key(self):
        """G2: staging service must use the shared-secrets envVarGroup (G12 parity)."""
        staging = _staging_service()
        sk_vars = [v for v in staging.get("envVars", []) if v.get("key") == "SECRET_KEY"]
        assert len(sk_vars) == 1
        assert sk_vars[0].get("fromGroup") == "shared-secrets", "staging SECRET_KEY must come from shared-secrets group"

    def test_render_has_staging_database(self):
        """G2: render.yaml must declare a staging database resource."""
        dbs = _render()["databases"]
        staging_dbs = [d for d in dbs if "staging" in d.get("name", "")]
        assert len(staging_dbs) >= 1, "staging database not declared in render.yaml"

    def test_deploy_workflow_has_staging_job(self):
        """G2: deploy.yml must define a deploy_staging job."""
        wf = _workflow("deploy.yml")
        assert "deploy_staging" in wf["jobs"], "deploy.yml must have a deploy_staging job"

    def test_deploy_staging_uses_staging_environment(self):
        """G2: deploy_staging job must target the staging GitHub environment."""
        wf = _workflow("deploy.yml")
        job = wf["jobs"]["deploy_staging"]
        env = job.get("environment", {})
        assert env == "staging" or (isinstance(env, dict) and env.get("name") == "staging"), (
            "deploy_staging must use environment: staging"
        )

    def test_deploy_production_depends_on_staging(self):
        """G2: production deploy job must depend on staging success (needs: deploy_staging)."""
        wf = _workflow("deploy.yml")
        prod_job = wf["jobs"]["deploy"]
        needs = prod_job.get("needs", "")
        assert "deploy_staging" in (needs if isinstance(needs, list) else [needs]), (
            "production deploy must depend on staging (needs: deploy_staging)"
        )

    def test_deploy_staging_uses_staging_hook_secret(self):
        """G2: deploy_staging must reference RENDER_STAGING_DEPLOY_HOOK_URL."""
        content = _read_text(WORKFLOWS / "deploy.yml")
        assert "RENDER_STAGING_DEPLOY_HOOK_URL" in content, (
            "deploy.yml must use RENDER_STAGING_DEPLOY_HOOK_URL for staging"
        )
        assert "RENDER_DEPLOY_HOOK_URL" in content, "deploy.yml must still use RENDER_DEPLOY_HOOK_URL for production"


# ═══════════════════════════════════════════════════════════════════════════════
# G8 — Release workflow status
# ═══════════════════════════════════════════════════════════════════════════════


class TestReleaseWorkflow:
    def test_release_workflow_is_activated(self):
        """G8: release.yml must be fully activated — no `if: false` placeholder."""
        wf = _workflow("release.yml")
        job = wf["jobs"]["release"]
        # G8: the job must NOT be disabled with `if: false`
        assert job.get("if") is not False, "release.yml must be activated (remove if: false) — G8"

    def test_release_has_tag_trigger(self):
        """G8: release.yml must trigger on v*.*.* tags (tag-triggered releases)."""
        wf = _workflow("release.yml")
        on = wf["on"]
        assert "push" in on, "Activated release must have push triggers"
        assert on["push"]["tags"] == ["v*.*.*"], "Must trigger on version tags"

    def test_release_uses_gh_release_action(self):
        """G8: release.yml must use softprops/action-gh-release@v2 for release creation."""
        content = _read_text(WORKFLOWS / "release.yml")
        assert "softprops/action-gh-release@v2" in content, "release.yml must use softprops/action-gh-release@v2"

    def test_release_not_placeholder(self):
        """G8: release.yml must not contain placeholder/disabled markers after activation."""
        content = _read_text(WORKFLOWS / "release.yml")
        assert "DISABLED" not in content.upper(), "release.yml must not contain DISABLED after G8 activation"
        assert "Placeholder" not in content, "release.yml must not contain Placeholder after G8 activation"


# ═══════════════════════════════════════════════════════════════════════════════
# Pre-commit hook stack — must not block commits on missing tools
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreCommitConfig:
    """Validate .pre-commit-config.yaml — the stack that developers run locally
    before every commit. These tests pin the structural invariants that keep
    the hook stack from blocking commits:

    - mypy hook must not hard-block (mirrors CI's continue-on-error: true)
    - pytest hook must run only the fast subset (not slow markers)
    - ce-v2-gate hook must be file-scoped (not always_run)
    - minimum_pre_commit_version must be pinned
    """

    def test_pre_commit_config_exists(self):
        """The pre-commit config file must exist."""
        assert (ROOT / ".pre-commit-config.yaml").exists(), ".pre-commit-config.yaml is missing"

    def test_mypy_hook_is_non_blocking(self):
        """mypy hook must not hard-block commits — mirrors CI's
        continue-on-error: true. The entry must have a `|| true` fallback so
        that type errors don't prevent commits (the codebase is transitional)."""
        content = _read_text(ROOT / ".pre-commit-config.yaml")
        # Find the mypy hook section
        mypy_section = content[content.index("id: mypy") :]
        mypy_section = mypy_section[: mypy_section.index("id: pytest")]
        assert "|| true" in mypy_section or "||exit 0" in mypy_section or "exit 0" in mypy_section, (
            "mypy pre-commit hook must be non-blocking (|| true) to mirror CI's continue-on-error: true"
        )

    def test_pytest_hook_runs_fast_subset(self):
        """pytest pre-commit hook must skip slow tests (Qdrant/network/heavy
        inference) to keep commit latency low — the full suite runs in CI."""
        content = _read_text(ROOT / ".pre-commit-config.yaml")
        pytest_section = content[content.index("id: pytest") :]
        pytest_section = pytest_section[: pytest_section.index("id: ce-v2-gate")]
        assert '-m "not slow"' in pytest_section or "-m 'not slow'" in pytest_section, (
            "pytest pre-commit hook must run with -m 'not slow' to avoid CI-only tests"
        )
        # Must NOT run the full suite (no bare pytest without -m filter)
        assert "always_run: true" not in pytest_section or "-m" in pytest_section, (
            "pytest hook must not run always_run without a slow-test filter"
        )

    def test_ce_v2_gate_is_file_scoped(self):
        """ce-v2-gate hook must only run when evaluation/ files change, not
        on every commit (always_run: true would force a ~3-5 min check per commit)."""
        content = _read_text(ROOT / ".pre-commit-config.yaml")
        gate_section = content[content.index("id: ce-v2-gate") :]
        # Ce-v2-gate section ends at the next hook or end of file
        next_hook = gate_section.find("\n      # ───", 10)  # skip the first match
        if next_hook > 0:
            gate_section = gate_section[:next_hook]
        assert "always_run" not in gate_section or "always_run: false" in gate_section, (
            "ce-v2-gate must not have always_run: true — it must be file-scoped"
        )
        assert "files:" in gate_section, "ce-v2-gate must have a files filter so it only runs on relevant changes"

    def test_minimum_pre_commit_version_pinned(self):
        """The config must pin a minimum_pre_commit_version for reproducibility."""
        content = _read_text(ROOT / ".pre-commit-config.yaml")
        assert "minimum_pre_commit_version" in content, (
            ".pre-commit-config.yaml must declare minimum_pre_commit_version"
        )
