# SpecDev CI Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a SpecDev feature start locally in Claude Code and be handed off to a GitHub runner that runs the build headlessly to a mode-specific terminal state (prod: open Implementation PR and stop; poc: self-merge and deploy an isolated environment).

**Architecture:** A new `specdev-build.yml` workflow runs `anthropics/claude-code-action` headlessly; the `specdev` skill + its four subagents are vendored into the target repo's `.claude/` by `init` so the runner's coordinator can offload work and stay context-bounded. A per-feature `.specdev/run.json` manifest records each merge's mode; `deploy.yml` gains a guard that skips the prod chain for poc merges, and a reusable `deploy-poc.yml` (invoked directly by the build) deploys the isolated `poc` environment. Mode rides on the branch prefix (`spec/**`→prod, `poc/**`→poc).

**Tech Stack:** Python 3.x stdlib only (argparse/json/pathlib/re), pytest, GitHub Actions YAML, `anthropics/claude-code-action`.

## Global Constraints

- **Stdlib-only Python.** New tools import only `argparse`, `json`, `pathlib`, `re`, `sys` — matching every existing `assets/specdev/tools/*.py`. No third-party deps.
- **Windows-safe I/O.** Each new tool includes the `sys.stdout.reconfigure(encoding="utf-8")` guard used by the other tools; all file writes use `encoding="utf-8"` and a trailing newline.
- **No secrets in committed files.** `run.json`/`ci.json` hold config only. `ANTHROPIC_API_KEY` is the ONLY required repo/org secret.
- **Seeds ship valid-and-inert.** `assets/specdev/run.json` and `ci.json` pass their own validation on a fresh repo; poc behavior is dormant until a `poc/**` branch is pushed.
- **`run_manifest.py mode` defaults to `prod` when `.specdev/run.json` is absent** — so every existing repo's `deploy.yml` runs its normal chain unchanged.
- **Pin action versions.** Reference `anthropics/claude-code-action` at a pinned major (`@v1`); confirm its exact input names against the action's README at implementation time (the `prompt` / `claude_args` / `anthropic_api_key` inputs used here are the documented ones).
- **Commit trailer.** Every commit ends with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Tests are stdlib + pytest.** Workflow YAML is validated by structural string assertions (dependency-free) plus an optional `yaml.safe_load` parse that `pytest.skip`s if PyYAML is unavailable. Run all tests with `pytest tests/test_specdev_ci.py -v` from the repo root.

## File Structure

| File | Responsibility |
|------|----------------|
| `assets/specdev/tools/run_manifest.py` (new) | Read/write/validate `.specdev/run.json`; expose `mode`/`prod_chain_should_run`; read `.specdev/ci.json` keys. |
| `assets/specdev/run.json` (new) | Seed per-feature run manifest (inert: `feat: null, mode: prod`). |
| `assets/specdev/ci.json` (new) | CI config: `runner` label, `max_session_minutes`, `auto_resume`. |
| `assets/specdev/tools/detect_deploy.py` (edit) | Seed a `poc` environment alongside `staging`/`production` in `deploy.profile.json`. |
| `assets/workflows/deploy.yml` (edit) | `gate` job reads mode; `preflight` (and thus the whole chain) skipped when mode is `poc`. |
| `assets/workflows/deploy-poc.yml` (new) | Reusable (`workflow_call`) poc deploy: preflight→build→deploy `poc`→post-deploy-QA. No prod. |
| `assets/workflows/specdev-build.yml` (new) | The headless runner: decide mode/feat, run claude-code-action, call `deploy-poc.yml` on poc. |
| `commands/init.md` (edit) | Vendor `skills/specdev/` + `agents/*.md` into `.claude/`; document `ANTHROPIC_API_KEY` + `poc` env + `ci.json`. |
| `.sdlc/config.json` (edit) | Add `run_manifest.py` (and the already-missing `arch_config.py`) to the `lint` command. |
| `.claude-plugin/plugin.json` (edit) | Version bump 0.5.0 → 0.6.0; mention CI handoff in the description. |
| `tests/test_specdev_ci.py` (new) | pytest for the manifest tool, detect_deploy poc env, the guard decision, workflow structure, and vendor sources. |

---

### Task 1: Run manifest tool + config seeds

**Files:**
- Create: `assets/specdev/tools/run_manifest.py`
- Create: `assets/specdev/run.json`
- Create: `assets/specdev/ci.json`
- Test: `tests/test_specdev_ci.py`

**Interfaces:**
- Produces (module `run_manifest`):
  - `SCHEMA_VERSION: int = 1`, `MODES = ("prod", "poc")`
  - `load(root=".") -> dict | None`
  - `save(doc: dict, root=".") -> None`
  - `validate(doc: dict) -> list[str]` (empty list = valid)
  - `mode_of(root=".") -> str` (`"prod"` when no file)
  - `prod_chain_should_run(doc: dict | None) -> bool`
  - `ci_get(key: str, root=".") -> object` (from `.specdev/ci.json`, else `CI_DEFAULTS`)
  - CLI: `mode`, `validate`, `init --feat --mode [--poc-env]`, `ci --get KEY`, all honoring `--root`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_specdev_ci.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_specdev_ci.py -v`
Expected: FAIL/ERROR — `run_manifest.py`, `run.json`, and `ci.json` do not exist yet (collection error on `load_mod`).

- [ ] **Step 3: Create `assets/specdev/tools/run_manifest.py`**

```python
#!/usr/bin/env python3
"""Per-feature CI run manifest (.specdev/run.json) for the SpecDev CI handoff.

run.json records which mode a feature's build/merge belongs to so downstream
workflows can branch: a prod merge takes the standard deploy chain; a poc merge
skips it (the poc build deploys an isolated env itself). Also exposes the mode
to deploy.yml's guard and reads .specdev/ci.json for the build workflow.

Usage:
    run_manifest.py mode                        # print governing mode ('prod' if no file)
    run_manifest.py validate                     # exit nonzero on schema error
    run_manifest.py init --feat FEAT-001 --mode poc [--poc-env poc]
    run_manifest.py ci --get runner              # read a .specdev/ci.json key
"""
import argparse
import json
import re
import sys
from pathlib import Path

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCHEMA_VERSION = 1
RUN_REL = ".specdev/run.json"
CI_REL = ".specdev/ci.json"
MODES = ("prod", "poc")
FEAT_RE = re.compile(r"^FEAT-\d{3,}$")
CI_DEFAULTS = {"runner": "ubuntu-latest", "max_session_minutes": 300, "auto_resume": False}


def run_path(root=".") -> Path:
    return Path(root) / RUN_REL


def ci_path(root=".") -> Path:
    return Path(root) / CI_REL


def load(root="."):
    p = run_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(doc: dict, root=".") -> None:
    p = run_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def validate(doc: dict) -> list:
    errs = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version must be {SCHEMA_VERSION}")
    mode = doc.get("mode")
    if mode not in MODES:
        errs.append(f"mode must be one of {MODES}, got {mode!r}")
    feat = doc.get("feat")
    if feat is not None and not FEAT_RE.match(str(feat)):
        errs.append(f"feat must match FEAT-### or be null, got {feat!r}")
    if mode == "poc" and not doc.get("poc_environment"):
        errs.append("mode 'poc' requires a non-empty poc_environment")
    return errs


def mode_of(root=".") -> str:
    """Mode governing the current tree — 'prod' when no manifest (safe default:
    the standard deploy chain runs)."""
    doc = load(root)
    m = (doc or {}).get("mode")
    return m if m in MODES else "prod"


def prod_chain_should_run(doc) -> bool:
    """deploy.yml guard: the standard staging->prod chain runs for everything
    except a poc merge."""
    return (doc or {}).get("mode") != "poc"


def ci_get(key: str, root="."):
    cfg = dict(CI_DEFAULTS)
    p = ci_path(root)
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg.get(key, CI_DEFAULTS.get(key))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mode")
    sub.add_parser("validate")
    pi = sub.add_parser("init")
    pi.add_argument("--feat", required=True)
    pi.add_argument("--mode", required=True, choices=MODES)
    pi.add_argument("--poc-env", default="poc")
    pc = sub.add_parser("ci")
    pc.add_argument("--get", required=True)
    args = ap.parse_args()

    if args.cmd == "mode":
        print(mode_of(args.root))
        return 0
    if args.cmd == "validate":
        doc = load(args.root)
        if doc is None:
            print("no run.json (inert) — ok")
            return 0
        errs = validate(doc)
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1 if errs else 0
    if args.cmd == "init":
        feat = (args.feat or "").strip() or None
        doc = {"schema_version": SCHEMA_VERSION, "feat": feat, "mode": args.mode}
        if args.mode == "poc":
            doc["poc_environment"] = args.poc_env
        errs = validate(doc)
        if errs:
            for e in errs:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        save(doc, args.root)
        print(f"Wrote {run_path(args.root)}")
        return 0
    if args.cmd == "ci":
        print(ci_get(args.get, args.root))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create `assets/specdev/run.json`**

```json
{
  "schema_version": 1,
  "feat": null,
  "mode": "prod"
}
```

- [ ] **Step 5: Create `assets/specdev/ci.json`**

```json
{
  "schema_version": 1,
  "runner": "ubuntu-latest",
  "max_session_minutes": 300,
  "auto_resume": false
}
```

> Note (design reconciliation): the design named the runner knob conceptually
> (`github-hosted` | `self-hosted`); we store the **actual `runs-on` label**
> instead (`ubuntu-latest` default; a self-hosted team sets its runner label),
> so the workflow can use it directly in `runs-on:` with no mapping layer.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_specdev_ci.py -v`
Expected: PASS (all 10 tests).

- [ ] **Step 7: Commit**

```bash
git add assets/specdev/tools/run_manifest.py assets/specdev/run.json assets/specdev/ci.json tests/test_specdev_ci.py
git commit -m "feat: run.json manifest tool + ci.json config for CI handoff"
```

---

### Task 2: `detect_deploy.py` seeds a `poc` environment

**Files:**
- Modify: `assets/specdev/tools/detect_deploy.py:142-145` (the `environments` literal)
- Test: `tests/test_specdev_ci.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `deploy.profile.json` `environments` now always includes a `poc`
  key (`{"url": "https://poc.example.com"}`) unless the user already defined one
  (the existing merge-preserve at `detect_deploy.py:171` keeps edited envs).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_specdev_ci.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_specdev_ci.py::test_detect_deploy_seeds_poc_environment -v`
Expected: FAIL — `KeyError`/assertion: `poc` not in `environments`.

- [ ] **Step 3: Add `poc` to the default environments literal**

In `assets/specdev/tools/detect_deploy.py`, change the `environments` dict (currently at lines 142-145):

```python
    environments = {
        "staging": {"url": "https://staging.example.com"},
        "production": {"url": "https://example.com"},
        "poc": {"url": "https://poc.example.com"},
    }
```

(No other change needed: the merge-preserve block at lines 171-176 already keeps
any `environments` the user has edited, and `deploy.py` resolves any env name
generically via `ctx()`/`preflight`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_specdev_ci.py::test_detect_deploy_seeds_poc_environment -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite (regression check)**

Run: `pytest tests/test_specdev_ci.py -v`
Expected: PASS (11 tests).

- [ ] **Step 6: Commit**

```bash
git add assets/specdev/tools/detect_deploy.py tests/test_specdev_ci.py
git commit -m "feat: detect_deploy seeds an isolated poc environment"
```

---

### Task 3: `deploy.yml` poc guard

**Files:**
- Modify: `assets/workflows/deploy.yml` (add `gate` job; gate `preflight` on it)
- Test: `tests/test_specdev_ci.py`

**Interfaces:**
- Consumes: `run_manifest.py mode` (Task 1).
- Produces: a `deploy.yml` whose entire staging→prod chain is skipped when the
  merged tree's `run.json` mode is `poc`. Prod/absent merges are unaffected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_specdev_ci.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_specdev_ci.py::test_deploy_yml_has_poc_gate -v`
Expected: FAIL — the gate strings are not in `deploy.yml` yet.

- [ ] **Step 3: Add the `gate` job and gate `preflight`**

In `assets/workflows/deploy.yml`, insert a `gate` job as the first job (right
after `jobs:`), and add `needs`/`if` to the existing `preflight` job. The new
top of the `jobs:` block becomes:

```yaml
jobs:
  # Mode guard: a poc merge deploys its own isolated environment via the build
  # workflow's deploy-poc call, so the standard staging->prod chain must NOT run
  # for it. 'prod' (and no-manifest) fall through to the normal chain.
  gate:
    runs-on: ubuntu-latest
    outputs:
      mode: ${{ steps.m.outputs.mode }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - id: m
        run: echo "mode=$(python .specdev/tools/run_manifest.py mode)" >> "$GITHUB_OUTPUT"

  # Verification gate: refuse to deploy unless every destination fact in
  # deploy.profile.json is resolved (discovered or documented) and well-formed.
  preflight:
    needs: gate
    if: needs.gate.outputs.mode != 'poc'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Verify staging deploy facts
        run: python .specdev/tools/deploy.py preflight --env staging
      - name: Verify production deploy facts
        run: python .specdev/tools/deploy.py preflight --env production
```

Leave every downstream job unchanged: they already `needs: preflight` (directly
or transitively), so a skipped `preflight` cascades to skip `build`,
`deploy-staging`, `qa-staging`, `deploy-prod`, `qa-prod`; `rollback`'s
`if: failure()` does not trigger on skipped upstreams.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_specdev_ci.py::test_deploy_yml_has_poc_gate tests/test_specdev_ci.py::test_all_workflows_parse_if_yaml_available -v`
Expected: PASS (parse test may print `SKIPPED` if PyYAML absent — that's fine).

- [ ] **Step 5: Commit**

```bash
git add assets/workflows/deploy.yml tests/test_specdev_ci.py
git commit -m "feat: skip the prod deploy chain for poc merges (deploy.yml gate)"
```

---

### Task 4: `deploy-poc.yml` reusable workflow

**Files:**
- Create: `assets/workflows/deploy-poc.yml`
- Test: `tests/test_specdev_ci.py`

**Interfaces:**
- Consumes: `deploy.py` (`preflight`/`deploy`/`url`) with `--env poc`;
  `post-deploy-qa.yml` (reusable).
- Produces: a reusable workflow (`on: workflow_call`, input `environment`
  default `poc`) that `specdev-build.yml` invokes at job level.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_specdev_ci.py`:

```python
def test_deploy_poc_is_reusable_and_poc_only():
    t = wf_text("deploy-poc.yml")
    assert "workflow_call:" in t
    assert "--env ${{ inputs.environment }}" in t or "--env poc" in t
    assert "post-deploy-qa.yml" in t
    # No production promotion in the poc path.
    assert "environment: production" not in t
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_specdev_ci.py::test_deploy_poc_is_reusable_and_poc_only -v`
Expected: FAIL — `deploy-poc.yml` does not exist (`FileNotFoundError`).

- [ ] **Step 3: Create `assets/workflows/deploy-poc.yml`**

```yaml
name: deploy-poc

# Isolated POC delivery. NOT triggered by push — the specdev-build workflow
# calls this reusable workflow directly (workflow_call) after a poc build's
# self-merge, which sidesteps the GITHUB_TOKEN recursion rule (no PAT needed).
# There is no production job, so a poc can never auto-promote to prod.
on:
  workflow_call:
    inputs:
      environment:
        required: false
        type: string
        default: poc
  workflow_dispatch:
    inputs:
      environment:
        required: false
        type: string
        default: poc

permissions:
  contents: write   # tag releases

jobs:
  preflight:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Verify poc deploy facts
        run: python .specdev/tools/deploy.py preflight --env ${{ inputs.environment }}

  build:
    needs: preflight
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.meta.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - id: meta
        name: Compute poc release tag
        run: echo "tag=poc-$(date +%Y%m%d)-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"
      - name: Build artifact
        run: 'echo "TODO: build command -> dist/"'          # e.g. npm run build
      - uses: actions/upload-artifact@v4
        with:
          name: poc-${{ steps.meta.outputs.tag }}
          path: dist/
          if-no-files-found: ignore

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: ${{ inputs.environment }}
    outputs:
      url: ${{ steps.deploy.outputs.url }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      # TODO: install/auth your platform CLI here (flyctl, kubectl, vercel, ...)
      - id: deploy
        name: Deploy to the isolated poc environment
        run: |
          python .specdev/tools/deploy.py deploy --env ${{ inputs.environment }} --tag "${{ needs.build.outputs.tag }}"
          echo "url=$(python .specdev/tools/deploy.py url --env ${{ inputs.environment }})" >> "$GITHUB_OUTPUT"

  qa:
    needs: [build, deploy]
    uses: ./.github/workflows/post-deploy-qa.yml
    with:
      environment: ${{ inputs.environment }}
      target_url: ${{ needs.deploy.outputs.url }}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_specdev_ci.py::test_deploy_poc_is_reusable_and_poc_only -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add assets/workflows/deploy-poc.yml tests/test_specdev_ci.py
git commit -m "feat: reusable deploy-poc workflow for the isolated poc environment"
```

---

### Task 5: `specdev-build.yml` headless runner

**Files:**
- Create: `assets/workflows/specdev-build.yml`
- Test: `tests/test_specdev_ci.py`

**Interfaces:**
- Consumes: `run_manifest.py` (`ci --get`, `init`), `anthropics/claude-code-action`,
  the vendored `.claude/skills/specdev` + `.claude/agents/*` (Task 6),
  `deploy-poc.yml` (Task 4).
- Produces: the workflow that runs the build; nothing else depends on its outputs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_specdev_ci.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_specdev_ci.py::test_specdev_build_triggers_and_paths -v`
Expected: FAIL — `specdev-build.yml` does not exist.

- [ ] **Step 3: Create `assets/workflows/specdev-build.yml`**

```yaml
name: specdev-build

# Hands-off SpecDev build in CI. Started locally in Claude Code, handed off here:
#   - prod: merging the Spec PR (spec/** -> main) fires this; the runner builds
#     and opens the Implementation PR, then STOPS (a human merges -> deploy.yml).
#   - poc:  pushing a poc/** branch fires this; the runner specs + builds, opens
#     the Implementation PR, self-merges, then deploys the isolated poc env.
# Context stays bounded by the specdev skill's coordinator discipline; state is
# checkpointed to .specdev/BUILD.md so a run can resume across job boundaries.
on:
  pull_request:
    types: [closed]
    branches: [main]
  push:
    branches: ['poc/**']
  workflow_dispatch:
    inputs:
      feat:
        description: 'FEAT-### to build (resume/manual)'
        required: true
        type: string
      mode:
        description: 'prod or poc'
        required: true
        type: string

permissions:
  contents: write
  pull-requests: write

jobs:
  setup:
    runs-on: ubuntu-latest
    outputs:
      run: ${{ steps.decide.outputs.run }}
      mode: ${{ steps.decide.outputs.mode }}
      feat: ${{ steps.decide.outputs.feat }}
      runner: ${{ steps.cfg.outputs.runner }}
      timeout: ${{ steps.cfg.outputs.timeout }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - id: decide
        name: Decide whether and how to run
        run: |
          event="${{ github.event_name }}"
          run=false; mode=""; feat=""
          if [ "$event" = "workflow_dispatch" ]; then
            run=true; mode="${{ inputs.mode }}"; feat="${{ inputs.feat }}"
          elif [ "$event" = "push" ]; then
            run=true; mode="poc"
          elif [ "$event" = "pull_request" ]; then
            if [ "${{ github.event.pull_request.merged }}" = "true" ] && \
               [ "${{ startsWith(github.event.pull_request.head.ref, 'spec/') }}" = "true" ]; then
              run=true; mode="prod"
            fi
          fi
          if [ -z "$feat" ] && [ -f .specdev/spec.md ]; then
            feat=$(grep -oE 'FEAT-[0-9]+' .specdev/spec.md | head -1 || true)
          fi
          {
            echo "run=$run"
            echo "mode=$mode"
            echo "feat=$feat"
          } >> "$GITHUB_OUTPUT"
      - id: cfg
        name: Read .specdev/ci.json
        run: |
          echo "runner=$(python .specdev/tools/run_manifest.py ci --get runner)" >> "$GITHUB_OUTPUT"
          echo "timeout=$(python .specdev/tools/run_manifest.py ci --get max_session_minutes)" >> "$GITHUB_OUTPUT"

  build:
    needs: setup
    if: needs.setup.outputs.run == 'true'
    runs-on: ${{ needs.setup.outputs.runner }}
    timeout-minutes: ${{ fromJSON(needs.setup.outputs.timeout) }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Record run manifest (mode signal for the deploy guard)
        run: |
          python .specdev/tools/run_manifest.py init \
            --feat "${{ needs.setup.outputs.feat }}" \
            --mode "${{ needs.setup.outputs.mode }}"
          git config user.name  "specdev-bot"
          git config user.email "specdev-bot@users.noreply.github.com"
      - name: Run the SpecDev build (headless Claude Code)
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          prompt: |
            You are the SpecDev build COORDINATOR running headlessly in CI.
            Follow the vendored specdev skill at .claude/skills/specdev exactly,
            acting as a strict coordinator: never write component code inline —
            dispatch the component-builder / qa-verifier / adr-checker /
            spec-explorer subagents (vendored in .claude/agents) and keep only
            their summaries.

            Feature: ${{ needs.setup.outputs.feat }}
            Mode:    ${{ needs.setup.outputs.mode }}

            Resume from .specdev/BUILD.md; if the build is incomplete, continue
            the dependency-wave loop until this mode's terminal state:
              - prod: open the Implementation PR against main, then STOP. Do NOT merge.
              - poc:  open the Implementation PR and merge it into main.
            Keep .specdev/BUILD.md and .specdev/run.json current after every phase.
          claude_args: '--max-turns 200'

  # poc only: after the build self-merges, deploy the isolated poc environment
  # by CALLING the reusable workflow directly (no push event, no PAT).
  deploy-poc:
    needs: [setup, build]
    if: needs.setup.outputs.mode == 'poc' && needs.build.result == 'success'
    uses: ./.github/workflows/deploy-poc.yml
    with:
      environment: poc
    secrets: inherit
```

> Resume note: for github-hosted runners the ~6h job cap applies. The
> coordinator checkpoints to `.specdev/BUILD.md` every phase, so a timed-out run
> is resumed by re-dispatching this workflow
> (`gh workflow run specdev-build.yml -f feat=FEAT-### -f mode=<mode>`) — the
> prompt always says "resume from BUILD.md". Teams with long builds set
> `.specdev/ci.json` `runner` to a self-hosted label (no cap). Automatic
> self-re-dispatch is intentionally out of MVP scope (job-timeout cancellation
> is unreliable for an `always()` step); manual/self-hosted resume is the
> supported path.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_specdev_ci.py -k specdev_build -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add assets/workflows/specdev-build.yml tests/test_specdev_ci.py
git commit -m "feat: specdev-build headless runner workflow (prod/poc handoff)"
```

---

### Task 6: Vendor skill + agents in `init`; housekeeping

**Files:**
- Modify: `commands/init.md` (add a vendoring step + checklist items)
- Modify: `.sdlc/config.json` (add tools to `lint`)
- Modify: `.claude-plugin/plugin.json` (version + description)
- Test: `tests/test_specdev_ci.py`

**Interfaces:**
- Consumes: existing plugin sources `skills/specdev/SKILL.md`, `agents/*.md`.
- Produces: an `init` that copies those into a target repo's `.claude/` so the
  headless runner (and local sessions) load the same skill/subagents.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_specdev_ci.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_specdev_ci.py -k "vendor or init_documents or lint_command" -v`
Expected: FAIL — `init.md` lacks the vendoring text; `.sdlc/config.json` lint omits `run_manifest.py`.

- [ ] **Step 3: Add the vendoring step to `commands/init.md`**

Insert a new step after step 3b (the gitleaks copy) in `commands/init.md`:

```markdown
3a2. **Vendor the SpecDev skill + subagents for headless CI.** Copy
   `${CLAUDE_PLUGIN_ROOT}/skills/specdev/` → `./.claude/skills/specdev/` and
   `${CLAUDE_PLUGIN_ROOT}/agents/*.md` → `./.claude/agents/` (create the dirs;
   same no-overwrite rule). This lets `specdev-build.yml` run the build with
   `claude-code-action` — the runner loads the same skill and the
   component-builder / qa-verifier / adr-checker / spec-explorer subagents from
   the repo, so the coordinator can offload work and stay context-bounded.
```

- [ ] **Step 4: Add CI-handoff items to the `init.md` post-install checklist**

In the post-install checklist block of `commands/init.md`, add these bullets:

```markdown
- CI handoff (specdev-build): add the `ANTHROPIC_API_KEY` repo/org secret. Merge
  a Spec PR (`spec/**`) to hand a prod build to the runner; push a `poc/**`
  branch for a hands-off poc build. Tune `.specdev/ci.json` (`runner`,
  `max_session_minutes`) — set `runner` to a self-hosted label for long builds.
- Create a `poc` GitHub Environment (isolated from staging/production) and fill
  `environments.poc.url` in `.specdev/deploy.profile.json`; poc builds deploy
  only there and never promote to prod.
```

- [ ] **Step 5: Add the tools to the `.sdlc/config.json` lint command**

In `.sdlc/config.json`, extend the `lint` command's file list to include the
two currently-missing tools (`run_manifest.py` and `arch_config.py`):

```json
    "lint": "python -m py_compile governance/tools/gen_adr_index.py assets/specdev/tools/arch_config.py assets/specdev/tools/check_org_adrs.py assets/specdev/tools/deploy.py assets/specdev/tools/detect_deploy.py assets/specdev/tools/gen_compliance.py assets/specdev/tools/gen_traceability.py assets/specdev/tools/run_manifest.py assets/specdev/tools/validate_spec.py"
```

- [ ] **Step 6: Bump the plugin version + description**

In `.claude-plugin/plugin.json`, set `"version": "0.6.0"` and append to the
`description` (before the closing quote):
` Adds CI handoff: start a build locally and finish it unattended in a GitHub runner (prod/poc modes).`

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_specdev_ci.py -v`
Expected: PASS (full suite).

- [ ] **Step 8: Verify the tools compile (lint) and commit**

```bash
python -m py_compile assets/specdev/tools/run_manifest.py
git add commands/init.md .sdlc/config.json .claude-plugin/plugin.json tests/test_specdev_ci.py
git commit -m "feat: vendor specdev skill+agents in init; wire CI handoff docs/version"
```

---

## Self-Review

**1. Spec coverage** (design → task):
- Two modes / branch-prefix encoding → Tasks 1 (manifest `mode`), 5 (`setup` path selection). ✓
- Event-driven kickoff (merge/push) + `workflow_dispatch` resume → Task 5 triggers. ✓
- Terminal states (prod stop at Impl PR; poc self-merge) → Task 5 prompt. ✓
- `run.json` manifest (schema, poc_environment iff poc) → Task 1. ✓
- POC isolated env, prod promotion disabled → Task 2 (`poc` env) + Task 4 (no prod job) + Task 3 (deploy.yml skip). ✓
- Reusable `deploy-poc.yml` invoked directly (no PAT) → Task 4 + Task 5 `uses:`. ✓
- Context mgmt: vendored agents + resume-from-BUILD.md → Task 6 (vendoring) + Task 5 (prompt/resume note). ✓
- Auth: `ANTHROPIC_API_KEY` only → Task 5 + Task 6 checklist. ✓
- Runner knob (hosted default, self-hosted option) → Task 1 (`ci.json`) + Task 5 (`runs-on`). ✓
- Testing (run.json, detect_deploy poc, guard decision, workflow structure) → Tasks 1–6 tests. ✓
- `deploy.yml` guard is additive, prod path unchanged → Task 3. ✓

**2. Placeholder scan:** The `TODO:` strings in `deploy-poc.yml` (build command, platform-CLI auth) intentionally mirror the existing `deploy.yml`/`post-deploy-qa.yml` seed placeholders that `init`'s checklist tells the adopter to fill — they are template stubs, not plan gaps. No plan step is left unspecified.

**3. Type consistency:** `run_manifest` symbols (`SCHEMA_VERSION`, `MODES`, `load`, `save`, `validate`, `mode_of`, `prod_chain_should_run`, `ci_get`, CLI `mode`/`validate`/`init`/`ci`) are defined in Task 1 and referenced identically in Tasks 3–6 (tests call `rm.validate`, `rm.mode_of`, `rm.prod_chain_should_run`, `rm.ci_get`; workflows call `run_manifest.py mode`, `run_manifest.py ci --get`, `run_manifest.py init`). The `poc` env key, `environment` input, and `mode`/`feat` output names match across `detect_deploy.py`, `deploy.yml`, `deploy-poc.yml`, and `specdev-build.yml`.
