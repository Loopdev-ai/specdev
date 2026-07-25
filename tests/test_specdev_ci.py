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


def test_deploy_yml_has_poc_gate():
    t = wf_text("deploy.yml")
    assert "run_manifest.py mode" in t
    assert "gate:" in t
    # preflight only runs when not a poc merge
    assert "needs.gate.outputs.mode != 'poc'" in t


def test_all_workflows_parse_if_yaml_available():
    yaml = pytest.importorskip("yaml")
    for name in ["deploy.yml", "deploy-poc.yml", "specdev-build.yml"]:
        path = WF_DIR / name
        if path.exists():
            yaml.safe_load(path.read_text(encoding="utf-8"))
