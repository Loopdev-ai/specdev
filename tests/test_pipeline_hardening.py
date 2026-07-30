"""Regression tests for the CI-pipeline findings from the it-management report.

The failure they all share: a run that verifies nothing, reports success, and
leaves nothing behind — no branch, no commit, no PR, and a still-stock
BUILD.md, on a job that reported green.

Findings from that report about the ADOPTER's own fork (its narrowed Bash
prefix list, its ADR-0008 conformance item, its pre-run inference record) are
deliberately not tested here — none of them ship in this package.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "assets" / "specdev" / "tools"
WF_DIR = ROOT / "assets" / "workflows"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


un = load_mod(TOOLS / "units.py", "units_ph")
bo = load_mod(TOOLS / "build_outcome.py", "build_outcome_ph")
cb = load_mod(TOOLS / "circuit_breaker.py", "circuit_breaker_ph")


def wf(name):
    return (WF_DIR / name).read_text(encoding="utf-8")


def git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "seed")
    return tmp_path


# ---- F14: the scope guard that passed having checked nothing -------------

def test_out_of_scope_refuses_an_empty_base(repo):
    """On workflow_dispatch both pull_request.base.sha and event.before are
    empty. The guard used to diff nothing, find nothing, and report green."""
    with pytest.raises(un.ScopeBaseError):
        un.out_of_scope(repo, "infra", "")
    with pytest.raises(un.ScopeBaseError):
        un.out_of_scope(repo, "infra", None)


def test_out_of_scope_refuses_the_null_sha(repo):
    """github.event.before is 40 zeros on a branch's first push."""
    with pytest.raises(un.ScopeBaseError):
        un.out_of_scope(repo, "infra", "0" * 40)


def test_out_of_scope_refuses_an_unresolvable_ref(repo):
    with pytest.raises(un.ScopeBaseError):
        un.out_of_scope(repo, "infra", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")


def test_scope_check_cli_exits_nonzero_on_an_empty_base(repo):
    p = subprocess.run(
        [sys.executable, str(TOOLS / "units.py"), "--root", str(repo),
         "scope-check", "--unit", "infra", "--changed-from", ""],
        capture_output=True, text=True)
    assert p.returncode != 0, "an unresolvable base must not report success"
    assert "without checking anything" in p.stderr.lower() or \
           "compares nothing" in p.stderr.lower()


def test_changed_units_still_widens_on_an_unusable_base(repo):
    """For a verification MATRIX the fail-safe direction is the opposite one:
    verify every unit rather than none."""
    (repo / ".specdev").mkdir()
    (repo / "units.json").write_text("{}", encoding="utf-8")
    assert un.changed_units(repo, "") == un.changed_units(repo, None)


def test_post_build_scope_check_runs_against_the_captured_start_sha():
    t = wf("specdev-build.yml")
    assert "Post-build scope check" in t
    assert "steps.start.outputs.sha" in t, \
        "the post-build check must use the run's own start SHA, not the event payload"
    m = re.search(r"name: Post-build scope check\n\s+if: (.+)", t)
    assert m and "always()" in m.group(1)


def test_start_sha_is_captured_rather_than_read_from_the_event():
    t = wf("specdev-build.yml")
    assert re.search(r"id: start\b", t)
    assert "git rev-parse HEAD" in t


# ---- F5/F8: the checkpoint that never left the runner --------------------

def test_build_pushes_a_checkpoint_unconditionally():
    t = wf("specdev-build.yml")
    m = re.search(r"id: checkpoint\n\s+name: [^\n]+\n\s+if: (.+)", t)
    assert m, "the checkpoint push step must exist"
    assert "always()" in m.group(1), \
        "a checkpoint that only runs on success is useless on the runs that need it"
    assert "git push" in t


def test_checkpoint_goes_to_a_dedicated_ref_never_a_release_branch():
    t = wf("specdev-build.yml")
    assert "specdev/checkpoint/" in t
    push = re.search(r"git push --force origin \"HEAD:refs/heads/\$\{REF\}\"", t)
    assert push, "the checkpoint must be pushed to its own ref"


def test_skill_makes_the_wave_the_unit_of_durability():
    s = (ROOT / "skills" / "specdev" / "SKILL.md").read_text(encoding="utf-8")
    assert "Commit the wave before forming the next one" in s


# ---- F9: the stock template that survived a full build -------------------

def test_stock_template_is_not_accepted_as_a_checkpoint(tmp_path):
    (tmp_path / ".specdev").mkdir()
    stock = (ROOT / "assets" / "specdev" / "BUILD.md").read_text(encoding="utf-8")
    (tmp_path / ".specdev" / "BUILD.md").write_text(stock, encoding="utf-8")
    probs = bo.checkpoint_problems(tmp_path)
    assert probs and "stock template" in probs[0]


def test_missing_and_empty_checkpoints_are_rejected(tmp_path):
    assert bo.checkpoint_problems(tmp_path), "a missing BUILD.md is not a checkpoint"
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "BUILD.md").write_text("   \n", encoding="utf-8")
    assert bo.checkpoint_problems(tmp_path)


def test_a_real_checkpoint_passes(tmp_path):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-002\n\nWave 1 green.\n",
        encoding="utf-8")
    assert bo.checkpoint_problems(tmp_path) == []


# ---- F13: success that meant nothing -------------------------------------

def test_verify_fails_when_no_implementation_pr_exists(tmp_path, monkeypatch):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-002\n", encoding="utf-8")
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    rec = bo.verify(tmp_path, "FEAT-002", "prod", ".", "main")
    assert rec["ok"] is False
    assert any("no Implementation PR" in p for p in rec["problems"])


def test_verify_ignores_the_spec_pr_that_triggered_the_build(tmp_path, monkeypatch):
    """A prod build is TRIGGERED by the Spec PR merging. Counting that PR as the
    terminal state would let a build verify itself against its own trigger."""
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-002\n", encoding="utf-8")
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [
        {"number": 1, "title": "FEAT-002 spec", "state": "MERGED",
         "headRefName": "spec/FEAT-002", "baseRefName": "main",
         "url": "u", "body": ""}])
    rec = bo.verify(tmp_path, "FEAT-002", "prod", ".", "main")
    assert rec["ok"] is False


def test_poc_requires_the_pr_to_be_merged(tmp_path, monkeypatch):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-002\n", encoding="utf-8")
    open_pr = [{"number": 7, "title": "FEAT-002 impl", "state": "OPEN",
                "headRefName": "impl/FEAT-002", "baseRefName": "main",
                "url": "u", "body": ""}]
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: open_pr)
    assert bo.verify(tmp_path, "FEAT-002", "prod", ".", "main")["ok"] is True
    assert bo.verify(tmp_path, "FEAT-002", "poc", ".", "main")["ok"] is False


def test_deploy_poc_is_gated_on_the_assertion_not_the_exit_code():
    t = wf("specdev-build.yml")
    m = re.search(r"deploy-poc:\n(?:.+\n)*?\s+if: (.+)", t)
    assert m, "deploy-poc must have a guard"
    guard = m.group(1)
    assert "terminal_ok" in guard, \
        "gating on build.result lets a build that produced nothing deploy"
    assert "needs.build.result" not in guard


def test_build_job_exposes_the_terminal_state_output():
    t = wf("specdev-build.yml")
    assert re.search(r"terminal_ok: \$\{\{ steps\.verify\.outputs\.ok \}\}", t)


# ---- F10/F15: no circuit breaker, no cost ceiling ------------------------

def test_ci_json_declares_the_breaker_limits():
    cfg = json.loads((ROOT / "assets" / "specdev" / "ci.json").read_text("utf-8"))
    for key in ("max_permission_denials", "max_consecutive_tool_failures",
                "max_cost_usd"):
        assert key in cfg, f"{key} must be configurable per repo"
        assert isinstance(cfg[key], (int, float)) and cfg[key] > 0


def test_breaker_limits_have_tool_side_defaults():
    rm = load_mod(TOOLS / "run_manifest.py", "run_manifest_ph")
    for key in ("max_permission_denials", "max_consecutive_tool_failures",
                "max_cost_usd"):
        assert key in rm.CI_DEFAULTS


def _state(tmp_path, **over):
    st = {"started_at": 0.0, "tool_calls": 0, "denials": 0,
          "consecutive_failures": 0, "max_consecutive_seen": 0,
          "denied_tools": {}, "cost_usd": 0.0, "cost_source": "unavailable",
          "tripped": False, "trip_reason": None}
    st.update(over)
    return st


def test_breaker_trips_on_denials(tmp_path):
    lim = dict(max_permission_denials=15, max_consecutive_tool_failures=0,
               max_cost_usd=0, max_wall_minutes=0, max_tool_calls=0)
    assert cb.evaluate(_state(tmp_path, denials=14), lim) is None
    assert "permission denials" in cb.evaluate(_state(tmp_path, denials=15), lim)


def test_breaker_trips_on_consecutive_failures(tmp_path):
    lim = dict(max_permission_denials=0, max_consecutive_tool_failures=15,
               max_cost_usd=0, max_wall_minutes=0, max_tool_calls=0)
    assert "consecutive tool failures" in cb.evaluate(
        _state(tmp_path, consecutive_failures=15), lim)


def test_breaker_trips_on_cost_only_when_cost_is_actually_known(tmp_path):
    """Mid-run cost is read best-effort from an undocumented transcript format.
    An unreadable cost must never look like $0 — that would silently disable
    the ceiling, which is the exact class of bug this whole file is about."""
    lim = dict(max_permission_denials=0, max_consecutive_tool_failures=0,
               max_cost_usd=10, max_wall_minutes=0, max_tool_calls=0)
    unknown = _state(tmp_path, cost_usd=50.0, cost_source="unavailable")
    assert cb.evaluate(unknown, lim) is None
    known = _state(tmp_path, cost_usd=50.0, cost_source="transcript")
    assert "cost" in cb.evaluate(known, lim)


def test_breaker_hook_denies_and_stops_the_session(tmp_path, monkeypatch):
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    monkeypatch.setenv("SPECDEV_MAX_DENIALS", "1")
    monkeypatch.setenv("SPECDEV_MAX_COST_USD", "0")
    monkeypatch.setenv("SPECDEV_MAX_CONSECUTIVE_FAILURES", "0")
    cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_output": "Error: permission denied"})
    out, st = cb.handle({"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    assert out is not None
    assert out["continue"] is False, "the breaker must stop the session, not just deny"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert st["tripped"] and st["denied_tools"].get("Bash") == 1


def test_breaker_records_a_denied_tool_histogram(tmp_path, monkeypatch):
    """Which tools were denied was unrecoverable from the failing run's log."""
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    monkeypatch.setenv("SPECDEV_MAX_DENIALS", "0")
    monkeypatch.setenv("SPECDEV_MAX_CONSECUTIVE_FAILURES", "0")
    monkeypatch.setenv("SPECDEV_MAX_COST_USD", "0")
    for tool in ("Bash", "Bash", "Skill"):
        cb.handle({"hook_event_name": "PostToolUse", "tool_name": tool,
                   "tool_output": "Error: permission denied"})
    st = cb.load_state()
    assert st["denied_tools"] == {"Bash": 2, "Skill": 1}


def test_breaker_success_resets_the_failure_streak(tmp_path, monkeypatch):
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    for env in ("SPECDEV_MAX_DENIALS", "SPECDEV_MAX_CONSECUTIVE_FAILURES",
                "SPECDEV_MAX_COST_USD"):
        monkeypatch.setenv(env, "0")
    cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_output": "Error: permission denied"})
    assert cb.load_state()["consecutive_failures"] == 1
    cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_output": "ok, 12 tests passed"})
    st = cb.load_state()
    assert st["consecutive_failures"] == 0
    assert st["max_consecutive_seen"] == 1


def test_breaker_verdict_uses_a_status_distinct_from_a_hard_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    cb.save_state(_state(tmp_path, tripped=True, trip_reason="denials"))
    assert cb.verdict() == 3, \
        "a circuit-break should be distinguishable from a code failure"


def test_breaker_hook_never_takes_the_build_down_with_it(tmp_path, monkeypatch):
    """A crashing hook must fail open on the call, not kill the run."""
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    monkeypatch.setattr(cb, "handle",
                        lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("{}"))
    assert cb.main() == 0


def test_build_arms_the_breaker_as_a_hook_not_a_post_run_check():
    t = wf("specdev-build.yml")
    assert "--settings" in t and "breaker-settings.json" in t
    assert "PreToolUse" in t and "PostToolUse" in t
    assert "circuit_breaker.py verdict" in t


# ---- F1: the allowlist that denied the agent its own first instruction ----

def _allowlist():
    m = re.search(r'--allowedTools\s+"([^"]+)"', wf("specdev-build.yml"))
    assert m
    return [x.strip() for x in m.group(1).split(",")]


def test_allowlist_permits_the_skill_the_prompt_orders_it_to_follow():
    t = wf("specdev-build.yml")
    assert "specdev skill" in t, "the prompt still tells the agent to follow the skill"
    assert "Skill" in _allowlist(), \
        "the agent is instructed on turn one to do something it is denied"


def test_allowlist_permits_subagent_dispatch_under_either_tool_name():
    tools = _allowlist()
    assert "Task" in tools and "Agent" in tools, \
        "Task was renamed Agent; both must be listed or delegation breaks"


def test_allowlist_is_a_superset_of_every_vendored_agent_grant():
    """--allowedTools is a SESSION permission and subagents inherit it, so a
    tool missing here is denied to every subagent regardless of its own
    frontmatter. component-builder's whole job is TDD; a narrower session list
    silently overrides its grant and the delegation model cannot work."""
    session = set(_allowlist())
    for agent in (ROOT / "agents").glob("*.md"):
        m = re.search(r"^tools:\s*(.+)$", agent.read_text(encoding="utf-8"),
                      re.M)
        if not m:
            continue
        for tool in (x.strip() for x in m.group(1).split(",")):
            assert tool in session, (
                f"{agent.name} declares '{tool}' but the session allowlist "
                f"omits it, so the subagent is denied it")


def test_containment_is_a_deny_list_on_egress_not_prefix_scarcity():
    t = wf("specdev-build.yml")
    m = re.search(r'--disallowedTools\s+"([^"]+)"', t)
    assert m, "broad Bash is only defensible alongside an egress deny list"
    denied = m.group(1)
    for tool in ("WebFetch", "WebSearch", "Bash(curl:*)", "Bash(wget:*)"):
        assert tool in denied


def test_the_required_floor_is_documented_where_someone_would_tighten_it():
    """The adopter's outage came from following this comment's advice to
    tighten without being told what the coordinator actually needs."""
    t = wf("specdev-build.yml")
    assert "REQUIRED FLOOR" in t


# ---- F6/F7: dead config and a documented capability that did not exist ----

def test_auto_resume_is_actually_read_by_the_build():
    t = wf("specdev-build.yml")
    assert "auto_resume" in t, "auto_resume was declared in ci.json and read by nothing"
    assert "ci --get auto_resume" in t


def test_resume_restores_the_pushed_checkpoint():
    t = wf("specdev-build.yml")
    assert "git fetch origin" in t and "specdev/checkpoint/" in t


def test_header_does_not_promise_a_resume_it_cannot_deliver():
    t = wf("specdev-build.yml")
    header = t.split("on:")[0]
    assert "PUSHES" in header or "pushes" in header, \
        "the header must say what makes a run resumable, not just assert it is"


# ---- F11: a failed run that left no record ------------------------------

def test_build_writes_a_post_run_outcome_record():
    t = wf("specdev-build.yml")
    assert "build_outcome.py --root \"$UNIT\" report" in t
    m = re.search(r"name: Build outcome record\n\s+if: (.+)", t)
    assert m and "always()" in m.group(1)
    assert "upload-artifact" in t


def test_report_reaches_the_step_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "BUILD.md").write_text("# real\n", encoding="utf-8")
    verify_json = tmp_path / "v.json"
    verify_json.write_text(json.dumps({
        "ok": False, "feat": "FEAT-002", "mode": "prod", "unit": ".",
        "required_terminal_state": "Implementation PR open against main",
        "implementation_prs": [], "problems": ["no Implementation PR"]}),
        encoding="utf-8")
    breaker_json = tmp_path / "b.json"
    breaker_json.write_text(json.dumps({
        "tripped": True, "trip_reason": "permission denials reached 15",
        "denials": 15, "denied_tools": {"Bash": 12, "Skill": 3},
        "cost_usd": 12.50, "max_consecutive_seen": 4}), encoding="utf-8")

    rc = subprocess.run(
        [sys.executable, str(TOOLS / "build_outcome.py"), "--root", str(tmp_path),
         "report", "--feat", "FEAT-002", "--mode", "prod",
         "--verify-json", str(verify_json), "--breaker-json", str(breaker_json),
         "--checkpoint-ref", "specdev/checkpoint/./FEAT-002"],
        capture_output=True, text=True, env={**os.environ,
                                             "GITHUB_STEP_SUMMARY": str(summary)})
    assert rc.returncode == 0, rc.stderr
    text = summary.read_text(encoding="utf-8")
    assert "FAIL" in text
    assert "12.5" in text
    assert "`Bash`" in text and "12" in text, "the denial histogram is the missing datum"
    assert "specdev/checkpoint" in text
