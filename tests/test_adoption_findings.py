"""Regression tests for the six findings from the Faro Health adoption report.

Each of these is a failure mode that reports success while doing nothing, or
fails in a way that misdirects the reader. They are cheap to reintroduce by
accident, so each gets a test.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / "assets" / "workflows"
TOOLS_DIR = ROOT / "assets" / "specdev" / "tools"


def wf(name):
    return (WF_DIR / name).read_text(encoding="utf-8")


# ---- 1: agent tool surface ---------------------------------------------

def test_build_agent_declares_an_explicit_tool_allowlist():
    """Without --allowedTools the agent runs with the FULL default surface on a
    job that writes code and, in poc mode, self-merges to main."""
    t = wf("specdev-build.yml")
    assert "--allowedTools" in t
    m = re.search(r'--allowedTools\s+"([^"]+)"', t)
    assert m, "allowlist must be a quoted, explicit list"
    tools = {x.strip() for x in m.group(1).split(",")}
    assert "Task" in tools, "the coordinator must be able to dispatch subagents"
    for forbidden in ("WebFetch", "WebSearch"):
        assert forbidden not in tools


# ---- 2: gitleaks licence -----------------------------------------------

def test_secret_scan_does_not_use_the_paid_action():
    """gitleaks/gitleaks-action@v2 needs a paid licence for GitHub orgs and
    fails closed — it errors BEFORE scanning, so the gate reports failure while
    having verified nothing. The binary is MIT and actually scans."""
    t = wf("post-dev-qa.yml")
    # Checks USE, not mention — the workflow explains in a comment why the
    # action is avoided, and that prose must not trip this test.
    uses = [ln.strip() for ln in t.splitlines()
            if ln.strip().startswith("- uses:") or ln.strip().startswith("uses:")]
    assert not any("gitleaks-action" in u for u in uses), \
        f"post-dev-qa must not `uses:` the paid action: {uses}"
    assert "gitleaks detect" in t
    assert "--redact" in t, "found secrets must not be echoed into public logs"


def test_secret_scan_uses_the_repo_gitleaks_config_when_present():
    t = wf("post-dev-qa.yml")
    assert ".gitleaks.toml" in t


# ---- 3: secrets in steps.if --------------------------------------------

def test_no_workflow_gates_a_step_on_the_secrets_context():
    """`secrets` is unavailable in jobs.<id>.steps.if. It evaluates EMPTY rather
    than erroring, so a step guarded that way silently never runs. Surface the
    secret as job-level env and gate on env instead."""
    offenders = []
    for p in sorted(WF_DIR.glob("*.yml")):
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("if:") and "secrets." in s:
                offenders.append(f"{p.name}: {s}")
    assert not offenders, (
        "step-level `if` cannot see the secrets context:\n" + "\n".join(offenders))


def test_build_requires_the_api_key_via_job_level_env():
    t = wf("specdev-build.yml")
    assert "HAS_ANTHROPIC_KEY" in t
    assert "env.HAS_ANTHROPIC_KEY" in t, "the guard must read env, not secrets"


# ---- 5: QA stubs must not report green ---------------------------------

@pytest.mark.parametrize("gate", ["Lint", "Unit + integration tests",
                                  "Coverage gate", "Dependency / SAST scan"])
def test_unwired_qa_gates_fail_instead_of_echoing(gate):
    """An `echo \"TODO\"` exits 0, so an unwired repo has a QA gate reporting
    green while verifying nothing — and with automatic prod this gate is most of
    what stands between a merge and production."""
    t = wf("post-dev-qa.yml")
    i = t.index(f"- name: {gate}")
    block = t[i:i + 600]
    nxt = block.find("\n      - name:")
    block = block[:nxt if nxt > 0 else len(block)]
    assert "exit 1" in block, f"the {gate!r} stub must fail, not report green"
    assert not re.search(r"run:\s*'echo \"TODO", block)


def test_traceability_gate_is_real_not_a_stub():
    """This one is genuinely wired and must stay that way."""
    t = wf("post-dev-qa.yml")
    assert "gen_traceability.py --check-gaps" in t


# ---- 6: Python version floor -------------------------------------------

TOOLS = sorted(p.name for p in TOOLS_DIR.glob("*.py"))


def test_every_tool_is_guarded():
    assert TOOLS, "no tools found"
    for name in TOOLS:
        s = (TOOLS_DIR / name).read_text(encoding="utf-8")
        assert "sys.version_info < (3, 10)" in s, f"{name} has no version guard"


@pytest.mark.parametrize("name", TOOLS)
def test_version_guard_precedes_any_sibling_import(name):
    """units.py carries the same PEP 604 annotations, so importing it before the
    guard would raise the opaque TypeError the guard exists to prevent."""
    s = (TOOLS_DIR / name).read_text(encoding="utf-8")
    guard = s.index("sys.version_info < (3, 10)")
    imp = s.find("import units")
    if imp != -1:
        assert guard < imp, f"{name}: guard must precede 'import units'"


@pytest.mark.parametrize("name", TOOLS)
def test_version_guard_message_is_ascii(name):
    """The guard runs BEFORE the stdout UTF-8 reconfigure, so a non-ASCII byte
    here raises UnicodeEncodeError on a cp1252 console and replaces the
    explanation with a traceback."""
    s = (TOOLS_DIR / name).read_text(encoding="utf-8")
    i = s.index("if sys.version_info < (3, 10):")
    block = s[i:s.index("\n\n", i)]
    assert all(ord(c) < 128 for c in block), f"{name}: guard message must be ASCII"


@pytest.mark.parametrize("name", TOOLS)
def test_version_guard_fires_under_simulated_39(name, tmp_path):
    harness = tmp_path / "fake39.py"
    harness.write_text(
        "import sys, runpy, collections\n"
        "V = collections.namedtuple('v', 'major minor micro releaselevel serial')\n"
        "sys.version_info = V(3, 9, 18, 'final', 0)\n"
        "try:\n"
        "    runpy.run_path(sys.argv[1], run_name='__main__')\n"
        "except SystemExit as e:\n"
        "    print('GUARD:', e)\n",
        encoding="utf-8")
    rc = subprocess.run(
        [sys.executable, str(harness), str(TOOLS_DIR / name)],
        capture_output=True, text=True, cwd=tmp_path)
    assert "require Python 3.10+" in rc.stdout, (
        f"{name} did not refuse to run on 3.9:\n{rc.stdout}\n{rc.stderr}")
