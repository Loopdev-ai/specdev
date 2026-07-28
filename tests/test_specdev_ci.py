import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RM_PATH = ROOT / "assets" / "specdev" / "tools" / "run_manifest.py"
RUN_SEED = ROOT / "assets" / "specdev" / "run.json"
CI_SEED = ROOT / "assets" / "specdev" / "ci.json"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rm = load_mod(RM_PATH, "run_manifest")


def write_run(root, doc):
    (root / ".specdev").mkdir(parents=True, exist_ok=True)
    (root / ".specdev" / "run.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8")


# ---- schema / validation -------------------------------------------------

def test_seed_run_json_is_valid_and_inert():
    doc = json.loads(RUN_SEED.read_text(encoding="utf-8"))
    assert rm.validate(doc) == []
    assert doc["mode"] == "prod"
    assert doc["feat"] is None


def test_validate_rejects_unknown_mode():
    errs = rm.validate({"schema_version": 1, "feat": None, "mode": "prototype"})
    assert any("mode" in e for e in errs)


def test_validate_poc_requires_poc_environment():
    errs = rm.validate({"schema_version": 1, "feat": "FEAT-001", "mode": "poc"})
    assert any("poc_environment" in e for e in errs)
    ok = rm.validate({"schema_version": 1, "feat": "FEAT-001", "mode": "poc",
                      "poc_environment": "poc"})
    assert ok == []


def test_validate_feat_format():
    assert any("feat" in e for e in
               rm.validate({"schema_version": 1, "feat": "FEATURE-1", "mode": "prod"}))
    assert rm.validate({"schema_version": 1, "feat": "FEAT-042", "mode": "prod"}) == []


# ---- guard / mode --------------------------------------------------------

def test_prod_chain_runs_except_for_poc():
    assert rm.prod_chain_should_run(None) is True
    assert rm.prod_chain_should_run({"mode": "prod"}) is True
    assert rm.prod_chain_should_run({"mode": "poc"}) is False


def test_mode_of_defaults_prod_when_absent(tmp_path):
    assert rm.mode_of(tmp_path) == "prod"
    write_run(tmp_path, {"schema_version": 1, "feat": "FEAT-001", "mode": "poc",
                         "poc_environment": "poc"})
    assert rm.mode_of(tmp_path) == "poc"


# ---- ci.json -------------------------------------------------------------

def test_ci_get_uses_defaults_when_absent(tmp_path):
    assert rm.ci_get("runner", tmp_path) == "ubuntu-latest"
    assert rm.ci_get("max_session_minutes", tmp_path) == 300


def test_seed_ci_json_has_expected_keys():
    cfg = json.loads(CI_SEED.read_text(encoding="utf-8"))
    assert cfg["runner"] == "ubuntu-latest"
    assert isinstance(cfg["max_session_minutes"], int)


# ---- CLI -----------------------------------------------------------------

def run_cli(root, *args):
    return subprocess.run([sys.executable, str(RM_PATH), "--root", str(root), *args],
                          capture_output=True, text=True)


def test_cli_mode_prints_prod_when_absent(tmp_path):
    out = run_cli(tmp_path, "mode")
    assert out.returncode == 0
    assert out.stdout.strip() == "prod"


def test_cli_init_then_validate_roundtrip(tmp_path):
    (tmp_path / ".specdev").mkdir()
    assert run_cli(tmp_path, "init", "--feat", "FEAT-007", "--mode", "poc").returncode == 0
    doc = json.loads((tmp_path / ".specdev" / "run.json").read_text(encoding="utf-8"))
    assert doc == {"schema_version": 1, "feat": "FEAT-007", "mode": "poc",
                   "poc_environment": "poc"}
    assert run_cli(tmp_path, "validate").returncode == 0
    assert run_cli(tmp_path, "mode").stdout.strip() == "poc"


# ---- detect_deploy poc environment --------------------------------------

DETECT_PATH = ROOT / "assets" / "specdev" / "tools" / "detect_deploy.py"


def test_detect_deploy_seeds_poc_environment(tmp_path):
    # Empty repo -> target 'manual', but the environments must include poc.
    out = subprocess.run([sys.executable, str(DETECT_PATH), "--root", str(tmp_path)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    profile = json.loads((tmp_path / ".specdev" / "deploy.profile.json").read_text(encoding="utf-8"))
    assert "poc" in profile["environments"]
    assert profile["environments"]["poc"]["url"].startswith("https://")
    # staging/production still present and untouched.
    assert set(profile["environments"]) >= {"staging", "production", "poc"}


# ---- workflows -----------------------------------------------------------

WF_DIR = ROOT / "assets" / "workflows"


def wf_text(name):
    return (WF_DIR / name).read_text(encoding="utf-8")


def test_deploy_unit_yml_has_poc_gate():
    """The staging->prod chain moved into the reusable deploy-unit.yml so it can
    run per governed unit; the poc guard must survive the extraction."""
    t = wf_text("deploy-unit.yml")
    assert "run_manifest.py --root '${{ inputs.unit }}' mode" in t
    assert "gate:" in t
    # preflight only runs when not a poc merge
    assert "needs.gate.outputs.mode != 'poc'" in t


def test_deploy_yml_dispatches_the_unit_chain():
    t = wf_text("deploy.yml")
    assert "deploy-unit.yml" in t
    assert "units.py matrix" in t


def test_all_workflows_parse_if_yaml_available():
    yaml = pytest.importorskip("yaml")
    for name in ["deploy.yml", "deploy-unit.yml", "deploy-poc.yml",
                 "specdev-build.yml", "specdev-sweep.yml"]:
        path = WF_DIR / name
        if path.exists():
            yaml.safe_load(path.read_text(encoding="utf-8"))


def test_deploy_poc_is_reusable_and_poc_only():
    t = wf_text("deploy-poc.yml")
    assert "workflow_call:" in t
    assert "--env ${{ inputs.environment }}" in t or "--env poc" in t
    assert "post-deploy-qa.yml" in t
    # No production promotion in the poc path.
    assert "environment: production" not in t


def test_specdev_build_triggers_and_paths():
    t = wf_text("specdev-build.yml")
    # three triggers
    assert "pull_request:" in t and "types: [closed]" in t
    assert "'poc/**'" in t or "poc/**" in t
    assert "workflow_dispatch:" in t
    # runs claude-code-action and the only required secret
    assert "anthropics/claude-code-action" in t
    assert "ANTHROPIC_API_KEY" in t
    # invokes the reusable poc deploy
    assert "uses: ./.github/workflows/deploy-poc.yml" in t
    # records the manifest for the deploy guard
    assert "run_manifest.py init" in t


def test_specdev_build_does_not_require_a_pat():
    t = wf_text("specdev-build.yml")
    assert "SPECDEV_PAT" not in t


# ---- init vendoring + housekeeping --------------------------------------

def test_vendor_sources_exist():
    # init must have something to copy into a target repo's .claude/
    assert (ROOT / "skills" / "specdev" / "SKILL.md").exists()
    for a in ["component-builder", "qa-verifier", "adr-checker", "spec-explorer"]:
        assert (ROOT / "agents" / f"{a}.md").exists(), a


def test_init_documents_vendoring_and_secret():
    t = (ROOT / "commands" / "init.md").read_text(encoding="utf-8")
    assert ".claude/skills/specdev" in t
    assert ".claude/agents" in t
    assert "ANTHROPIC_API_KEY" in t


def test_lint_command_covers_new_tool():
    cfg = json.loads((ROOT / ".sdlc" / "config.json").read_text(encoding="utf-8"))
    assert "run_manifest.py" in cfg["commands"]["lint"]


# ---- workflow expression validity ---------------------------------------
# hashFiles() is a step-only function. Used in a job-level `if:` GitHub rejects
# the whole file, so the workflow never registers and no job ever runs. These
# templates ship to every initialized repo and are never exercised by this
# repo's own CI, so guard the rule here.

STEP_ONLY_FUNCS = ("hashFiles",)


def iter_workflows():
    for path in sorted(WF_DIR.glob("*.yml")):
        yield path, __import__("yaml").safe_load(path.read_text(encoding="utf-8"))


def test_no_job_level_step_only_functions():
    pytest.importorskip("yaml")
    offenders = []
    for path, doc in iter_workflows():
        for job_id, job in (doc.get("jobs") or {}).items():
            cond = str((job or {}).get("if", ""))
            for fn in STEP_ONLY_FUNCS:
                if fn in cond:
                    offenders.append(f"{path.name}:jobs.{job_id}.if uses {fn}()")
    assert offenders == [], (
        "step-only functions in a job-level `if:` make the workflow invalid: "
        + "; ".join(offenders))


def _steps(doc, job_id):
    return (doc.get("jobs") or {}).get(job_id, {}).get("steps") or []


def _checkout_index(steps):
    for i, s in enumerate(steps):
        if "checkout" in str(s.get("uses", "")):
            return i
    return None


def test_org_adr_check_guard_is_step_level_and_after_checkout():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(wf_text("org-adr-check.yml"))
    steps = _steps(doc, "check")
    guarded = [s for s in steps if "hashFiles" in str(s.get("if", ""))]
    assert guarded, "org.json guard must survive as a step-level `if:`"
    assert all(".specdev/org.json" in str(s["if"]) for s in guarded)
    # hashFiles resolves against the workspace, which is empty until checkout.
    co = _checkout_index(steps)
    assert co is not None
    assert all(steps.index(s) > co for s in guarded)


def test_compliance_guard_is_step_level_on_every_running_step():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(wf_text("compliance.yml"))
    steps = _steps(doc, "controls")
    co = _checkout_index(steps)
    assert co is not None
    # Every step after setup that does compliance work must carry the guard,
    # including the always() ones — otherwise they run in unadopted repos.
    work = [s for s in steps[co + 1:] if "uses" not in s or "upload-artifact" in str(s.get("uses"))]
    assert work, "expected compliance work steps"
    for s in work:
        cond = str(s.get("if", ""))
        assert "hashFiles" in cond and "compliance.config.json" in cond, s.get("name")
    # always() semantics preserved where they existed.
    always = [s for s in work if "always()" in str(s.get("if", ""))]
    assert len(always) == 2


# ---- deploy: 'manual' target means "no automatic deployment" -------------
# detect_deploy.py writes target 'manual' for a new product and init documents
# that as expected, so the deploy chain must skip rather than fail preflight.

DEPLOY_PATH = ROOT / "assets" / "specdev" / "tools" / "deploy.py"


def write_profile(root, target):
    (root / ".specdev").mkdir(parents=True, exist_ok=True)
    (root / ".specdev" / "deploy.profile.json").write_text(
        json.dumps({"target": target, "params": {}, "environments": {}}), encoding="utf-8")


def deploy_cli(root, *args):
    return subprocess.run([sys.executable, str(DEPLOY_PATH), *args],
                          cwd=str(root), capture_output=True, text=True)


def test_deploy_target_reports_manual_for_new_product(tmp_path):
    write_profile(tmp_path, "manual")
    out = deploy_cli(tmp_path, "target")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "manual"


def test_deploy_target_reports_concrete_target(tmp_path):
    write_profile(tmp_path, "fly")
    out = deploy_cli(tmp_path, "target")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "fly"


def test_deploy_target_treats_missing_profile_as_manual(tmp_path):
    # No profile at all is also "nothing to deploy" — the gate job must not
    # go red just because the repo never ran detect_deploy.py.
    out = deploy_cli(tmp_path, "target")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "manual"


def test_deploy_unit_yml_skips_chain_when_target_is_manual():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(wf_text("deploy-unit.yml"))
    jobs = doc["jobs"]
    assert "target" in jobs["gate"]["outputs"], "gate must expose the target"
    cond = jobs["preflight"]["if"]
    assert "needs.gate.outputs.mode != 'poc'" in cond, "poc guard must survive"
    assert "needs.gate.outputs.target != 'manual'" in cond
    # The target is read with the tool, not an inline json.load that would
    # crash the gate job when the profile is absent.
    assert "deploy.py target" in wf_text("deploy-unit.yml")


# ---- multi-unit -----------------------------------------------------------

def test_run_json_is_per_unit(tmp_path):
    (tmp_path / "infra" / ".specdev").mkdir(parents=True)
    (tmp_path / "demos" / ".specdev").mkdir(parents=True)
    rm.save({"schema_version": 1, "feat": "FEAT-001", "mode": "prod"},
            tmp_path / "infra")
    rm.save({"schema_version": 1, "feat": "FEAT-002", "mode": "poc",
             "poc_environment": "poc"}, tmp_path / "demos")
    assert rm.mode_of(tmp_path / "infra") == "prod"
    assert rm.mode_of(tmp_path / "demos") == "poc"
    assert rm.load(tmp_path / "infra")["feat"] == "FEAT-001"
    assert rm.load(tmp_path / "demos")["feat"] == "FEAT-002"


def test_ci_get_falls_back_to_repo_root(tmp_path):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "ci.json").write_text(
        json.dumps({"runner": "self-hosted"}), encoding="utf-8")
    (tmp_path / "infra" / ".specdev").mkdir(parents=True)
    assert rm.ci_get("runner", tmp_path / "infra", repo_root=tmp_path) == "self-hosted"


def test_ci_get_unit_overrides_repo_root(tmp_path):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "ci.json").write_text(
        json.dumps({"runner": "self-hosted"}), encoding="utf-8")
    (tmp_path / "infra" / ".specdev").mkdir(parents=True)
    (tmp_path / "infra" / ".specdev" / "ci.json").write_text(
        json.dumps({"runner": "ubuntu-24.04"}), encoding="utf-8")
    assert rm.ci_get("runner", tmp_path / "infra", repo_root=tmp_path) == "ubuntu-24.04"


def test_ci_get_missing_key_fails_loudly(tmp_path):
    """A missing key must not print the string 'None' at exit 0 — that value
    flowed into an inference-metadata record as \"model\": \"None\"."""
    rc = subprocess.run(
        [sys.executable, str(RM_PATH), "--root", str(tmp_path), "ci",
         "--get", "no_such_key"],
        capture_output=True, text=True)
    assert rc.returncode != 0
    assert "None" not in rc.stdout


def test_ci_get_known_key_still_prints(tmp_path):
    rc = subprocess.run(
        [sys.executable, str(RM_PATH), "--root", str(tmp_path), "ci",
         "--get", "runner"],
        capture_output=True, text=True)
    assert rc.returncode == 0
    assert rc.stdout.strip() == "ubuntu-latest"


def test_init_resolves_unit_from_ref(tmp_path):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "units.json").write_text(json.dumps({
        "schema_version": 1, "units": ["infra", "demos"]}), encoding="utf-8")
    (tmp_path / "infra" / ".specdev").mkdir(parents=True)
    (tmp_path / "demos" / ".specdev").mkdir(parents=True)

    rc = subprocess.run(
        [sys.executable, str(RM_PATH), "--root", str(tmp_path), "init",
         "--feat", "FEAT-007", "--mode", "prod",
         "--ref", "spec/demos/some-feature"],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert (tmp_path / "demos" / ".specdev" / "run.json").exists()
    assert not (tmp_path / "infra" / ".specdev" / "run.json").exists()
    assert not (tmp_path / ".specdev" / "run.json").exists()


def test_init_without_ref_writes_root(tmp_path):
    """Back-compat: no --unit and no --ref means the root unit, exactly as
    before multi-unit support."""
    rc = subprocess.run(
        [sys.executable, str(RM_PATH), "--root", str(tmp_path), "init",
         "--feat", "FEAT-001", "--mode", "prod"],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert (tmp_path / ".specdev" / "run.json").exists()
