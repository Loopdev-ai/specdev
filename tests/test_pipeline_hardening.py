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


# ---- F13/U15: success that meant nothing, and what "success" now means ---
#
# The terminal state is REVIEWABLE WORK — an implementation branch this run
# pushed, carrying commits beyond the base, with a prepared PR body — not "a
# PR exists".
#
# Asserting a PR forced every prod adopter to enable "Allow GitHub Actions to
# create and approve pull requests", a single switch granting create AND
# approve. Off, the coordinator got a 403 and the terminal state was
# unreachable — a completed build with nowhere to put its result. On, Actions
# could approve PRs, bypassing the review the merge gate rests on. The hazard
# and the capability are the same switch, so the ASSERTION had to change.

RUN_START = "2026-07-31T09:00:00Z"
BUILD_START = "2026-07-31T08:00:00Z"   # attempt 1 began here
WORK_PUSHED = "2026-07-31T10:00:00Z"   # attempt 1 pushed the branch, then died
JOB_START = "2026-07-31T12:00:00Z"     # attempt 2 (the re-dispatch) begins
BOT = "github-actions[bot]"
BOT_EMAIL = "specdev-bot@users.noreply.github.com"


def git_at(root, when, *args, name="specdev-bot", email=BOT_EMAIL):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
           "GIT_AUTHOR_NAME": name, "GIT_COMMITTER_NAME": name,
           "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
    subprocess.run(["git", *args], cwd=str(root), check=True,
                   capture_output=True, text=True, env=env)


@pytest.fixture
def built(tmp_path):
    """A repo where a build has run: a real checkpoint, a prepared PR body,
    and the implementation branch pushed."""
    root = tmp_path / "repo"
    (root / ".specdev").mkdir(parents=True)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", BOT_EMAIL)
    git(root, "config", "user.name", "specdev-bot")
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    (root / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-002\n\nWave 1 green.\n",
        encoding="utf-8")
    (root / ".specdev" / "PR_BODY.md").write_text(
        "# FEAT-002 - Widget\n\nImplements REQ-001.\n", encoding="utf-8")
    git_at(root, BUILD_START, "add", "-A")
    git_at(root, BUILD_START, "commit", "-m", "seed")
    return root


def push_impl(root, feat="FEAT-002", unit=".", when=WORK_PUSHED,
              msg="feat(widget): REQ-001", name="specdev-bot",
              email=BOT_EMAIL, body="widget"):
    """Create the implementation ref the workflow pushes, with a commit."""
    ref = bo.implementation_ref(unit, feat)
    git(root, "checkout", "-q", "-B", ref, "main")
    (root / "widget.py").write_text(body, encoding="utf-8")
    git_at(root, when, "add", "-A", name=name, email=email)
    git_at(root, when, "commit", "-m", msg, name=name, email=email)
    git(root, "checkout", "-q", "main")
    return ref


def _verify(root, mode="prod", **kw):
    kw.setdefault("since", BUILD_START)
    kw.setdefault("author", [BOT])
    return bo.verify(root, "FEAT-002", mode, ".", "main", None,
                     repo_dir=root, **kw)


def test_verify_fails_when_the_run_pushed_no_branch(built, monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    rec = _verify(built)
    assert rec["ok"] is False
    assert any("no implementation branch" in p for p in rec["problems"])


def test_a_pushed_branch_with_real_commits_is_the_terminal_state(built,
                                                                 monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built)
    rec = _verify(built)
    assert rec["ok"] is True, rec["problems"]
    assert rec["implementation_branch"]["commits_ahead"] == 1
    assert "human opens the PR" in rec["required_terminal_state"]


def test_a_branch_with_no_commits_beyond_base_is_not_work(built, monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    git(built, "branch", "-f", bo.implementation_ref(".", "FEAT-002"), "main")
    rec = _verify(built)
    assert rec["ok"] is False
    assert any("no commits beyond" in p for p in rec["problems"])


def test_no_pr_is_required_and_no_pr_can_satisfy_the_assertion(built,
                                                               monkeypatch):
    """The switch is never needed: with a branch and no PR at all, prod and
    poc both pass. And a PR alone, without a branch, never passes."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built)
    assert _verify(built, "prod")["ok"] is True
    assert _verify(built, "poc")["ok"] is True

    root2 = built.parent / "nobranch"
    subprocess.run(["cp", "-r", str(built), str(root2)], check=True)
    git(root2, "branch", "-D", bo.implementation_ref(".", "FEAT-002"))
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [
        {"number": 7, "title": "FEAT-002 impl", "state": "MERGED",
         "headRefName": "impl/FEAT-002", "baseRefName": "main", "url": "u",
         "body": "", "createdAt": WORK_PUSHED, "author": {"login": BOT}}])
    assert _verify(root2, "prod")["ok"] is False
    assert _verify(root2, "poc")["ok"] is False


def test_poc_no_longer_requires_a_merged_pr(built, monkeypatch):
    """poc's terminal state was a MERGED PR, which needed the same switch AND
    autonomous merging to main - the riskier half. It now deploys from the
    branch."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built)
    rec = _verify(built, "poc")
    assert rec["ok"] is True
    assert "deploys from this branch" in rec["required_terminal_state"]


def test_a_stock_pr_body_is_not_a_prepared_one(built, monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built)
    stock = (ROOT / "assets" / "specdev" / "PR_BODY.md").read_text("utf-8")
    (built / ".specdev" / "PR_BODY.md").write_text(stock, encoding="utf-8")
    rec = _verify(built)
    assert rec["ok"] is False
    assert any("stock template" in p for p in rec["problems"])
    (built / ".specdev" / "PR_BODY.md").unlink()
    assert any("prepared no PR body" in p for p in _verify(built)["problems"])


# ---- U18: a [skip ci] head produces a PR with no checks, silently --------

def test_a_skip_ci_commit_may_not_be_the_head_of_the_work_branch(built,
                                                                 monkeypatch):
    """The checkpoint commit carries [skip ci] - correct on the checkpoint
    ref, poisonous at the head of a work branch. GitHub skips every workflow
    on a PR whose head carries it: no error, no skipped-run entry, just a PR
    with zero checks that looks like CI has not started."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built, msg="chore(specdev): checkpoint FEAT-002 on . [skip ci]")
    rec = _verify(built)
    assert rec["ok"] is False
    problem = " ".join(rec["problems"])
    assert "[skip ci]" in problem and "no checks" in problem


@pytest.mark.parametrize("marker", ["[skip ci]", "[ci skip]", "[no ci]"])
def test_every_skip_marker_github_honours_is_refused(built, monkeypatch,
                                                     marker):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built, msg=f"chore: checkpoint {marker}")
    assert _verify(built)["ok"] is False


def test_an_ordinary_commit_subject_is_not_mistaken_for_a_skip_marker(
        built, monkeypatch):
    """The inverted half - 'skip' in prose must not fail a real build."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built, msg="feat(widget): skip empty rows when parsing")
    assert _verify(built)["ok"] is True


# ---- F16/U7: evidence about this build must have been produced BY it -----
#
# The spec-PR exclusion was ONE enumerated contaminant. The invariant is
# general: any artifact this build did not create is not evidence about it.

def test_a_branch_left_by_an_earlier_build_is_not_this_ones_output(built,
                                                                   monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built, when="2026-07-01T08:00:00Z")
    rec = _verify(built, since=BUILD_START)
    assert rec["ok"] is False
    assert any("before this build started" in p for p in rec["problems"])


def test_a_resumed_dispatch_still_recognises_the_branch_it_already_pushed(
        built, monkeypatch):
    """auto_resume makes a re-dispatch the same LOGICAL build. Anchored to the
    job instead, a build that had already pushed its work could never verify:
    the branch only gets older, so each dispatch rejects it, terminally."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built, when=WORK_PUSHED)
    assert _verify(built, since=BUILD_START)["ok"] is True
    assert _verify(built, since=JOB_START)["ok"] is False, \
        "this is what anchoring to the attempt did - kept as the inverted half"


def test_authorship_that_does_not_match_is_a_warning_not_a_verdict(built,
                                                                   monkeypatch):
    """A ref and its timing are checkable; a committer identity is weaker
    evidence, so a mismatch is reported rather than failing a real build."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built, name="a-human", email="human@example.com")
    rec = _verify(built)
    assert rec["ok"] is True
    assert any("does not match this run" in w for w in rec["warnings"])


def test_existing_prs_are_reported_but_never_required(built, monkeypatch):
    """The provenance work is preserved as INFORMATION: 'a PR already exists
    for this work' is useful to whoever reads the record."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [
        {"number": 7, "title": "FEAT-002 impl", "state": "OPEN",
         "headRefName": "specdev/impl/./FEAT-002", "baseRefName": "main",
         "url": "u", "body": "", "createdAt": WORK_PUSHED,
         "author": {"login": BOT}}])
    push_impl(built)
    rec = _verify(built)
    assert rec["ok"] is True
    assert rec["implementation_prs"][0]["number"] == 7
    md = bo._md_report(rec, None)
    assert "informational" in md and "not the terminal state" in md


def test_the_report_points_the_human_at_the_branch_to_open(built, monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built)
    md = bo._md_report(_verify(built), None)
    assert "specdev/impl/root/FEAT-002" in md
    assert "Open the PR from" in md
    assert "PR_BODY.md" in md


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


def _denial_lim(**over):
    lim = dict(max_permission_denials=15, max_denial_rate=0.10,
               max_consecutive_tool_failures=0, max_cost_usd=0,
               max_wall_minutes=0, max_tool_calls=0)
    lim.update(over)
    return lim


def test_breaker_trips_on_denials(tmp_path):
    lim = _denial_lim()
    assert cb.evaluate(_state(tmp_path, denials=14, tool_calls=40), lim) is None
    assert "permission denials" in cb.evaluate(
        _state(tmp_path, denials=15, tool_calls=40), lim)


# ---- U13: an absolute denial ceiling is scale-dependent ------------------
#
# A bare count of 15 means opposite things at different run lengths. Trip only
# when BOTH hold: a substantial number of refusals, AND refusals being a
# substantial fraction of what the run is doing. Lowering the count makes the
# long-run false positive worse; raising it delays the short-run true positive.

@pytest.mark.parametrize("denials,calls,trips,why", [
    (16, 900, False, "scattered noise in a long healthy build"),
    (86, 300, True, "the motivating pathology"),
    (20, 40, True, "short, clearly broken"),
    (2, 5, False, "the floor is the minimum-sample guard"),
])
def test_the_denial_ceiling_needs_both_a_floor_and_a_rate(tmp_path, denials,
                                                          calls, trips, why):
    reason = cb.evaluate(_state(tmp_path, denials=denials, tool_calls=calls),
                         _denial_lim())
    assert bool(reason) is trips, f"{denials}/{calls} - {why}"
    if trips:
        assert f"{denials} permission denials in {calls} tool calls" in reason
        assert "% of calls" in reason, \
            "the reason must show the rate the decision was made on"


def test_a_bare_count_would_have_failed_the_long_healthy_build(tmp_path):
    """The inverted half: with no rate ceiling armed, the old scale-dependent
    behaviour is what you get — kept executable so the rate cannot quietly
    stop being applied."""
    st = _state(tmp_path, denials=16, tool_calls=900)
    assert cb.evaluate(st, _denial_lim()) is None
    assert cb.evaluate(st, _denial_lim(max_denial_rate=0)) is not None


def test_the_rate_is_never_evaluated_before_the_first_tool_call(tmp_path):
    """Guard the division structurally, not just by relying on the floor."""
    assert cb.denial_rate(_state(tmp_path, denials=3, tool_calls=0)) is None
    assert cb.evaluate(_state(tmp_path, denials=99, tool_calls=0),
                       _denial_lim()) is None


def test_the_rate_is_not_extended_to_the_consecutive_failure_bound(tmp_path):
    """A streak resets on success, so a percentage of it is not a meaningful
    quantity. It stays an absolute bound."""
    lim = _denial_lim(max_consecutive_tool_failures=15, max_denial_rate=0.10)
    reason = cb.evaluate(
        _state(tmp_path, consecutive_failures=15, tool_calls=9000), lim)
    assert reason and "consecutive tool failures" in reason


# ---- U12: the denial ceiling was spent by ordinary tool failures ---------

RED_TEST = ("FAILED tests/test_widget.py::test_adds - AssertionError\n"
            "Traceback (most recent call last):\n"
            "  File \"widget.py\", line 3, in add\n"
            "Exception: not implemented yet\n"
            "Error: 1 test failed")


def _breaker(tmp_path, monkeypatch, **env):
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    for var in ("SPECDEV_MAX_DENIALS", "SPECDEV_MAX_CONSECUTIVE_FAILURES",
                "SPECDEV_MAX_COST_USD", "SPECDEV_MAX_WALL_MINUTES",
                "SPECDEV_MAX_TOOL_CALLS"):
        monkeypatch.setenv(var, env.get(var, "0"))
    return cb


def test_red_tests_do_not_spend_the_permission_denial_ceiling(tmp_path,
                                                              monkeypatch):
    """This pipeline is TDD: a red test run is the normal first half of every
    component, and it prints 'Error:', 'Exception' and 'Traceback'. One
    predicate fed both counters, so a healthy multi-wave build accumulated
    them against a ceiling named max_permission_denials and could trip on
    doing exactly what it is supposed to do."""
    _breaker(tmp_path, monkeypatch)
    for _ in range(30):
        cb.handle({"hook_event_name": "PreToolUse", "tool_name": "Bash"})
        cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_output": RED_TEST})
    st = cb.load_state()
    assert st["denials"] == 0, \
        "a failing test is not a harness refusal and must not spend the ceiling"
    assert st["denied_tools"] == {}, \
        "the histogram used to fix an allowlist must not list undenied tools"
    assert st["consecutive_failures"] == 30, \
        "the streak bound must still see them - it resets on the first success"


def test_a_real_refusal_still_counts_as_a_denial(tmp_path, monkeypatch):
    """The inverted half of the test above: an AUTHORITATIVE refusal — a
    structured decision — is what the ceiling counts."""
    _breaker(tmp_path, monkeypatch)
    for _ in range(4):
        cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_response": {"permissionDecision": "deny"}})
    assert cb.load_state()["denials"] == 4
    assert cb.load_state()["denied_tools"] == {"Bash": 4}


def test_refusal_shaped_text_is_a_hint_not_a_denial(tmp_path, monkeypatch):
    """Text cannot answer the only question a cumulative denial ceiling
    exists to answer. A 403 from a remote API reads exactly like a harness
    refusal, and a checkpoint quoting one reads like it forever after."""
    _breaker(tmp_path, monkeypatch)
    for output in ("Error: permission denied",
                   "This tool has not been granted to this session",
                   "Bash(curl:*) is not allowed",
                   "tool use was rejected"):
        cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_output": output})
    st = cb.load_state()
    assert st["denials"] == 0, "text must not spend the ceiling"
    assert st["denied_tools"] == {}
    assert st["denial_text_hints"] == {"Bash": 4}, \
        "the uncertain case must be measurable, not discarded"
    assert st["consecutive_failures"] == 4, \
        "a hint is still a failure, so a genuinely stuck agent stalls out"


def test_the_403_that_aborted_a_finished_build_is_not_a_denial(tmp_path,
                                                               monkeypatch):
    """The real run: a remote 403 was written into BUILD.md, and every
    subsequent Read of that file scored another 'denial'. 15 in 865 calls
    aborted a six-wave build whose work was complete."""
    _breaker(tmp_path, monkeypatch)
    api_403 = ("403: GitHub Actions is not permitted to create or approve "
               "pull requests")
    cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_output": api_403})
    # ...and then read back out of the checkpoint, over and over.
    for _ in range(20):
        cb.handle({"hook_event_name": "PreToolUse", "tool_name": "Read"})
        cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Read",
                   "tool_output": f"# BUILD.md\n\nBlocked: {api_403}\n"})
    st = cb.load_state()
    assert st["denials"] == 0
    assert not st["tripped"], \
        "a remote service refusing an API call says nothing about --allowedTools"


# ---- U14: a rate limit is the opposite of a stuck agent ------------------

def _throttled(tool="Task"):
    """A realistic 429 tool result. Deliberately NOT a bare string matched
    against the regex: `429` alone does not match the error markers — it
    matched via a literal `Error:` when one happened to be present, so a
    regex-shaped fixture can pass for the wrong reason and stop protecting
    anything the moment the payload shape changes."""
    return {"hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_response": {"is_error": True, "status": 429,
                              "error": {"type": "rate_limit_error",
                                        "message": "Number of requests has "
                                                   "exceeded your rate limit"}}}


def test_a_throttle_does_not_extend_the_consecutive_failure_streak(
        tmp_path, monkeypatch):
    """Fanning out to subagents is precisely when 429s arrive, so an
    unclassified throttle made the control fire hardest when nothing was
    wrong."""
    _breaker(tmp_path, monkeypatch)
    for _ in range(30):
        cb.handle(_throttled())
    st = cb.load_state()
    assert st["consecutive_failures"] == 0
    assert st["transient_events"] == 30, "counted and reported, not ignored"
    assert st["transient_tools"] == {"Task": 30}
    assert st["denials"] == 0


def test_a_throttle_does_not_reset_a_genuine_failure_streak(tmp_path,
                                                            monkeypatch):
    """It must not HIDE a streak either: a throttle sprinkled through a real
    failure run tells you nothing in either direction."""
    _breaker(tmp_path, monkeypatch)
    real = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_response": {"is_error": True, "output": "E   assert False"}}
    cb.handle(real)
    cb.handle(real)
    assert cb.load_state()["consecutive_failures"] == 2
    cb.handle(_throttled("Bash"))
    assert cb.load_state()["consecutive_failures"] == 2, \
        "the throttle must neither extend nor reset the streak"
    cb.handle(real)
    assert cb.load_state()["consecutive_failures"] == 3


def test_a_red_test_is_still_a_failure(tmp_path, monkeypatch):
    """The inverted half. `E   assert False` matches no error-marker pattern,
    so this only works because is_failure honours the is_error flag — which is
    exactly why the fixture carries one."""
    _breaker(tmp_path, monkeypatch)
    cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_response": {"is_error": True,
                                 "output": "E   assert False\n1 failed"}})
    st = cb.load_state()
    assert st["consecutive_failures"] == 1, "that is the streak's actual job"
    assert st["transient_events"] == 0
    assert st["denials"] == 0


@pytest.mark.parametrize("text", [
    "429 Too Many Requests", "Error: rate limit exceeded",
    "503 Service Unavailable", "upstream connect error: ECONNRESET",
    "Overloaded", "504 Gateway Timeout",
])
def test_the_usual_transient_spellings_are_classified(text):
    assert cb.is_transient({"tool_output": text}), text


def test_a_structured_deny_counts_on_either_hook_event(tmp_path, monkeypatch):
    """Which event carries a refusal is runtime-dependent, so both are read."""
    _breaker(tmp_path, monkeypatch)
    cb.handle({"hook_event_name": "PreToolUse", "tool_name": "WebFetch",
               "hookSpecificOutput": {"permissionDecision": "deny"}})
    cb.handle({"hook_event_name": "PostToolUse", "tool_name": "WebSearch",
               "tool_response": {"permissionDecision": "block"}})
    st = cb.load_state()
    assert st["denials"] == 2
    assert st["denied_tools"] == {"WebFetch": 1, "WebSearch": 1}


def test_the_trip_reason_names_denials_only_when_they_are_denials(tmp_path,
                                                                  monkeypatch):
    """A run whose failures were all red tests must not be told it hit a
    permission-denial ceiling - that sends the reader to audit an allowlist
    which was never the problem."""
    _breaker(tmp_path, monkeypatch, SPECDEV_MAX_CONSECUTIVE_FAILURES="5")
    for _ in range(6):
        cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_output": RED_TEST})
    st = cb.load_state()
    assert st["tripped"]
    assert "consecutive tool failures" in st["trip_reason"]
    assert "permission denial" not in st["trip_reason"]


def test_every_attempted_tool_is_recorded_even_when_denials_are_invisible(
        tmp_path, monkeypatch):
    """A harness that refuses a call outright may surface it to neither hook,
    leaving `denied_tools` empty and nothing to fix an allowlist from.
    PreToolUse is the one signal the design notes call certain, so the record
    of what was ATTEMPTED survives regardless."""
    _breaker(tmp_path, monkeypatch)
    for tool in ("Bash", "Bash", "WebFetch"):
        cb.handle({"hook_event_name": "PreToolUse", "tool_name": tool})
    st = cb.load_state()
    assert st["attempted_tools"] == {"Bash": 2, "WebFetch": 1}
    assert st["denied_tools"] == {}, "nothing was observably denied"


def test_a_state_file_from_an_older_version_does_not_crash_the_hook(tmp_path,
                                                                    monkeypatch):
    """The hook must never take the build down, including on a state file
    written before `attempted_tools` existed."""
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    (tmp_path / "b.json").write_text(json.dumps(
        {"tool_calls": 3, "denials": 1, "denied_tools": {"Bash": 1}}),
        encoding="utf-8")
    out, st = cb.handle({"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    assert st["attempted_tools"] == {"Bash": 1}
    assert st["tool_calls"] == 4


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
               "tool_response": {"permissionDecision": "deny"}})
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
                   "tool_response": {"permissionDecision": "deny"}})
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


# ---- U7/U10: the provenance anchor is the BUILD, not the attempt ---------
#
# `auto_resume` makes a re-dispatch a continuation of the same logical build,
# so the window has to span every attempt at this unit+FEAT. The branch-based
# half of this lives with the terminal-state tests above
# (test_a_resumed_dispatch_still_recognises_the_branch_it_already_pushed);
# what follows is the run.json plumbing that carries the clock across.



def test_init_stamps_the_logical_builds_clock(tmp_path):
    (tmp_path / ".specdev").mkdir()
    subprocess.run([sys.executable, str(TOOLS / "run_manifest.py"), "--root",
                    str(tmp_path), "init", "--feat", "FEAT-002", "--mode",
                    "prod", "--unit", "."], check=True, capture_output=True)
    doc = json.loads((tmp_path / ".specdev" / "run.json").read_text("utf-8"))
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
                    doc.get("started_at", "")), \
        "the provenance anchor has to exist before it can survive a resume"


def test_resume_carries_the_build_clock_but_not_the_previous_dispatch(tmp_path):
    """U10: the resume restored run.json WHOLESALE, so a re-dispatch in a
    different mode silently inherited the checkpoint's mode while
    needs.setup.outputs.mode said otherwise. Only RESUME_CARRIES crosses."""
    rm = _rm()
    (tmp_path / ".specdev").mkdir()
    prev = tmp_path / "prev-run.json"
    prev.write_text(json.dumps({
        "schema_version": 1, "feat": "FEAT-002", "mode": "poc",
        "poc_environment": "poc", "started_at": BUILD_START}), encoding="utf-8")
    # this dispatch: same feature, prod mode
    rm.save({"schema_version": 1, "feat": "FEAT-002", "mode": "prod"}, tmp_path)
    doc = rm.resume_from(json.loads(prev.read_text("utf-8")), tmp_path)
    assert doc["started_at"] == BUILD_START, "the build clock must carry"
    assert doc["mode"] == "prod", \
        "the checkpoint must not override THIS dispatch's mode"
    assert "poc_environment" not in doc
    assert rm.mode_of(tmp_path) == "prod", \
        "run.json's consumers must agree with the dispatch, not the checkpoint"
    assert rm.RESUME_CARRIES == ("started_at",), \
        "widening what a resume carries reopens the override"


def test_the_workflow_anchors_since_to_the_build_and_says_which_anchor_it_used():
    t = wf("specdev-build.yml")
    assert "started-at" in t, "--since must come from run.json, not the job"
    assert '--since "$SINCE"' in t
    assert "JOB_STARTED_AT" in t, \
        "a build with no clock must still fall back to the job's own start"
    assert "::warning title=No build clock" in t, \
        "falling back to the attempt's clock must be stated, not silent"


def test_the_workflow_does_not_restore_run_json_wholesale():
    t = wf("specdev-build.yml")
    resume = t.split("id: resume")[1].split("- name: Arm")[0]
    assert "run_manifest.py --root \"$UNIT\" resume" in resume
    assert not re.search(r'git checkout "origin/\$\{REF\}" --[^\n]*run\.json',
                         resume), \
        "restoring run.json wholesale overrides this dispatch's own inputs"
    assert "BUILD.md" in resume, "the checkpoint body is still restored"


# ---- U8: a control that cannot attribute must not claim attribution ------
#
# The poc limb that named a stranger's PR as "the Implementation PR ... found
# 1 unmerged" is gone entirely with U15 - no limb reads PRs for a verdict any
# more. What survives is the rule it stood for, applied to the listing that
# remains: an unattributed match is never labelled as this run's output.

def test_the_report_does_not_call_unattributed_matches_this_runs_output():
    rec = {"ok": True, "feat": "FEAT-002", "mode": "prod", "unit": ".",
           "problems": [], "provenance_filters": {"applied": False},
           "implementation_prs": [{"number": 101, "url": "u", "state": "OPEN"}]}
    md = bo._md_report(rec, None)
    assert "NOT attributed to this run" in md
    assert "Existing PRs for this feature" not in md
    rec["provenance_filters"] = {"applied": True, "since": BUILD_START,
                                 "authors": [BOT]}
    md2 = bo._md_report(rec, None)
    assert "Existing PRs for this feature" in md2
    assert "not the terminal state" in md2, \
        "a PR listing must never read as the thing being asserted"



# ---- U9: the resolver must be able to say "configured" vs "inherited" ----

def test_ci_explicit_separates_a_committed_choice_from_an_inherited_default():
    """Loading CI_DEFAULTS from the bundled ci.json fixed the drift trap and
    made every shipped key resolvable everywhere — so a control of the form
    "this must be a deliberate, committed choice" became unwritable, failing
    open and silently."""
    rm = _rm()
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / ".specdev").mkdir()
        assert rm.ci_get("max_cost_usd", root) == rm.CI_DEFAULTS["max_cost_usd"]
        assert rm.ci_explicit("max_cost_usd", root) is False, \
            "a value nobody set is not a decision"
        (root / ".specdev" / "ci.json").write_text(
            json.dumps({"max_cost_usd": 25}), encoding="utf-8")
        assert rm.ci_get("max_cost_usd", root) == 25
        assert rm.ci_explicit("max_cost_usd", root) is True
        assert rm.ci_explicit("runner", root) is False, \
            "other keys in the same repo are still inherited"


def test_ci_explicit_survives_the_installed_layout_aliasing(tmp_path):
    """In an installed tree BUNDLED_CI and <root>/.specdev/ci.json are the SAME
    file, so answering this by comparing sources against CI_DEFAULTS_SOURCE
    either aliases the bundle to the adopter's pin or turns on whether the path
    arrived relative or absolute. Membership in what a candidate file supplied
    does neither."""
    shutil = __import__("shutil")
    shutil.copytree(ROOT / "assets" / "specdev", tmp_path / ".specdev")
    rm = load_mod(tmp_path / ".specdev" / "tools" / "run_manifest.py",
                  "rm_installed_ph")
    assert rm.BUNDLED_CI.resolve() == (tmp_path / ".specdev" / "ci.json").resolve(), \
        "this test is only meaningful when the bundle IS the adopter's file"
    assert rm.ci_explicit("max_cost_usd", tmp_path) is True
    assert rm.ci_explicit("max_cost_usd", tmp_path,
                          repo_root=tmp_path) is True
    # A key the adopter removed resolves (from the fallback) but is NOT a choice.
    cfg = json.loads((tmp_path / ".specdev" / "ci.json").read_text("utf-8"))
    cfg.pop("max_cost_usd")
    (tmp_path / ".specdev" / "ci.json").write_text(json.dumps(cfg), "utf-8")
    rm2 = load_mod(tmp_path / ".specdev" / "tools" / "run_manifest.py",
                   "rm_installed_ph2")
    assert rm2.ci_get("max_cost_usd", tmp_path) is not rm2._MISSING
    assert rm2.ci_explicit("max_cost_usd", tmp_path) is False, \
        "a key deleted from the repo's own config is no longer a choice"


def test_require_explicit_fails_a_key_the_repo_never_set(tmp_path):
    (tmp_path / ".specdev").mkdir()

    def run(*extra):
        return subprocess.run(
            [sys.executable, str(TOOLS / "run_manifest.py"), "--root",
             str(tmp_path), "ci", "--get", "max_cost_usd", *extra],
            capture_output=True, text=True)

    assert run().returncode == 0, "resolution itself is unchanged"
    p = run("--require-explicit")
    assert p.returncode == 1
    assert "::error" in p.stderr and "not a decision" in p.stderr
    (tmp_path / ".specdev" / "ci.json").write_text(
        json.dumps({"max_cost_usd": 25}), encoding="utf-8")
    assert run("--require-explicit").returncode == 0


# ---- F17: bounds that cannot engage, and a report that hides it ----------

def _rm():
    return load_mod(TOOLS / "run_manifest.py", "run_manifest_ph2")


def test_the_slow_runaway_bounds_are_configurable_and_shipped_on():
    """The cost ceiling is opportunistic: it needs a mid-run cost the agent
    transcript usually does not expose until the terminal record, so it may
    never arm at all. The module docstring's answer to that was
    max_wall_minutes and max_tool_calls — both implemented, both absent from
    ci.json and from the arming step, both defaulting to 0/off. Of three
    runaway bounds, only the fast one existed."""
    cfg = json.loads((ROOT / "assets" / "specdev" / "ci.json").read_text("utf-8"))
    for key in ("max_wall_minutes", "max_tool_calls"):
        assert key in cfg, f"{key} is documented as a bound but not configurable"
        assert cfg[key] > 0, f"{key} ships disabled, so the bound does not exist"
    assert cfg["max_wall_minutes"] < cfg["max_session_minutes"], \
        "a wall ceiling at or above the job timeout can never trip first"


def test_every_breaker_limit_is_exported_by_the_arming_step():
    """The arming step exported three of five limits by hand. The env mapping
    now lives beside the defaults so a limit cannot be added and left unwired."""
    rm = _rm()
    assert set(rm.BREAKER_ENV) == set(cb.limits()), \
        "circuit_breaker reads a limit set the arming step does not know about"
    for key in rm.BREAKER_ENV:
        assert key in rm.CI_DEFAULTS, f"{key} has no default"
    env, sources, errors = rm.breaker_env(ROOT / "assets" / "specdev")
    assert not errors, errors
    assert set(env) == set(rm.BREAKER_ENV.values())
    assert all(v > 0 for v in env.values()), \
        "a limit resolved to 0 is a bound that will not apply"


def test_limit_defaults_have_exactly_one_source_of_truth():
    """ci.json, CI_DEFAULTS and circuit_breaker's literals each carried an
    independent default for the same limits. They agreed on main, so it was a
    drift trap rather than a bug — and the resolution path made drift silent
    AND asymmetric. Aligning the numbers fixes the instance; this fixes the
    class."""
    rm = _rm()
    shipped = json.loads((ROOT / "assets" / "specdev" / "ci.json").read_text("utf-8"))
    shipped.pop("schema_version", None)
    assert rm.CI_DEFAULTS_SOURCE.endswith("ci.json"), \
        "the defaults must be LOADED from the shipped config, not restated"
    for key, val in shipped.items():
        assert rm.CI_DEFAULTS[key] == val
    # The only surviving copy is the fallback for an unreadable bundle. It is
    # not a second opinion, so it must agree key for key.
    for key, val in rm._FALLBACK_DEFAULTS.items():
        assert key in shipped, f"{key} is a default with no shipped config"
        assert shipped[key] == val, (
            f"{key}: ci.json says {shipped[key]}, the fallback says {val} - "
            f"which one a repo gets depends on which resolution path it hit")
    assert cb.DEFAULTS == rm.CI_DEFAULTS, \
        "circuit_breaker must not carry its own opinion about the limits"


def test_an_unmeasurable_cost_is_reported_as_inactive_not_as_zero(tmp_path):
    """`- Cost: $0.0 / $10` in the one artifact whose whole purpose is being
    read after a failure says 'this run was free'. It meant 'nothing was ever
    measured, and the ceiling you configured never engaged'."""
    rec = {"ok": False, "feat": "FEAT-002", "mode": "prod", "unit": ".",
           "problems": [], "implementation_prs": []}
    breaker = {"tripped": False, "cost_usd": 0.0, "cost_source": "unavailable",
               "tool_calls": 900, "denials": 0,
               "limits": {"max_cost_usd": 10, "max_wall_minutes": 240,
                          "max_tool_calls": 3000}}
    md = bo._md_report(rec, breaker)
    assert "not measured" in md and "INACTIVE" in md
    assert "$0.0 / $10" not in md, "an unread cost must never render as $0"
    assert "total_cost_usd" in md, \
        "point the reader at the figure that IS available"
    known = dict(breaker, cost_usd=7.5, cost_source="transcript")
    md2 = bo._md_report(rec, known)
    assert "7.5" in md2 and "transcript" in md2 and "INACTIVE" not in md2


def test_the_report_shows_both_halves_of_the_denial_ceiling(tmp_path):
    """A reader who sees only "12 / 15" cannot tell a run nowhere near
    tripping from one held back solely by the rate."""
    rec = {"ok": False, "feat": "FEAT-002", "mode": "prod", "unit": ".",
           "problems": [], "implementation_prs": []}
    breaker = {"tripped": False, "denials": 16, "tool_calls": 900,
               "cost_source": "transcript", "cost_usd": 3.2,
               "denied_tools": {"WebFetch": 16},
               "attempted_tools": {"Bash": 700, "WebFetch": 16},
               "limits": {"max_permission_denials": 15, "max_denial_rate": 0.1,
                          "max_cost_usd": 10}}
    md = bo._md_report(rec, breaker)
    assert "16 / 15" in md, "the floor was cleared and must be shown as such"
    assert "2% of 900 tool calls / 10%" in md, "the rate is why it did not trip"
    assert "BOTH" in md
    assert "harness refusals only" in md, \
        "the label must not imply ordinary failures were counted"
    assert "**Tools attempted**" in md and "| `Bash` | 700 | 0 |" in md


def test_the_breaker_verdict_says_the_ceiling_never_engaged(tmp_path, monkeypatch,
                                                            capsys):
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    cb.save_state(_state(tmp_path, cost_source="unavailable",
                         limits={"max_cost_usd": 10}))
    cb.verdict()
    out = capsys.readouterr().out
    assert "::warning" in out and "Cost ceiling inactive" in out
    assert "not $0" in out.lower()


def test_a_disabled_bound_is_reported_rather_than_left_to_be_inferred(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cb.STATE_ENV, str(tmp_path / "b.json"))
    cb.save_state(_state(tmp_path, unarmed_limits=["max_tool_calls"]))
    cb.verdict()
    assert "max_tool_calls" in capsys.readouterr().out


# ---- F18: an arming step that fell back to library constants in silence ---

def test_arming_fails_loudly_instead_of_arming_a_guess(tmp_path):
    """`echo "X=$(get ...)"` cannot fail: a command substitution's nonzero exit
    does not trip `set -e`, because the compound command's status is echo's. A
    malformed ci.json therefore wrote empty values, circuit_breaker fell
    through to its own literals, and the run presented as configured."""
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "ci.json").write_text("{ not json", encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(TOOLS / "run_manifest.py"), "--root", str(tmp_path),
         "breaker-env"], capture_output=True, text=True)
    assert p.returncode != 0, "a broken ci.json must not arm the breaker"
    assert not p.stdout.strip(), "nothing may be exported from a failed resolve"
    assert "::error" in p.stderr


def test_an_unknown_limit_key_is_an_error_not_an_empty_value(tmp_path):
    rm = _rm()
    _, _, errors = rm.breaker_env(tmp_path)
    assert not errors, "the bundled defaults cover every key"
    monkeyed = dict(rm.BREAKER_ENV, no_such_key="SPECDEV_NO_SUCH")
    rm.BREAKER_ENV = monkeyed
    _, _, errors = rm.breaker_env(tmp_path)
    assert any("no such ci.json key" in e for e in errors)


def test_an_empty_env_value_no_longer_silently_picks_a_different_number(
        tmp_path, monkeypatch):
    """The fallback an empty SPECDEV_MAX_COST_USD lands on must be the shipped
    config, not a literal in the hook."""
    monkeypatch.setenv("SPECDEV_MAX_COST_USD", "")
    shipped = json.loads((ROOT / "assets" / "specdev" / "ci.json").read_text("utf-8"))
    assert cb.limits()["max_cost_usd"] == float(shipped["max_cost_usd"])


def test_the_arming_step_resolves_every_limit_in_one_checked_call():
    t = wf("specdev-build.yml")
    assert "breaker-env" in t, \
        "the limits must be resolved by one call that can fail the step"
    m = re.search(r"name: Arm the circuit breaker\n\s+run: \|\n((?:.+\n)+?)\s*- ", t)
    assert m, "the arming step must exist"
    body = m.group(1)
    assert "set -euo pipefail" in body
    assert not re.search(r'echo "SPECDEV_MAX_\w+=\$\(', body), \
        "a $(...) inside echo cannot fail the step - that is the whole bug"
    assert "GITHUB_STEP_SUMMARY" in body, \
        "the resolved limits and their source must reach the run page"


# ---- F19: the terminal state the prompt never named ----------------------

def test_the_coordinator_is_told_the_terminal_state_the_workflow_asserts():
    """The hardening added a mechanical assertion for a terminal state the
    prompt never stated, so the two halves of the pipeline disagreed about when
    the job was done — and only one of them was enforced. A coordinator ended
    its turn voluntarily mid-build, well inside its turn budget, with a wave's
    QA still in flight."""
    for path in (ROOT / "commands" / "build.md",
                 ROOT / "skills" / "specdev" / "SKILL.md"):
        text = path.read_text(encoding="utf-8").lower()
        assert "terminal state" in text, f"{path.name} never names it"
        assert "implementation pr" in text
        assert "merged" in text, "poc's terminal state is a MERGED PR"
        assert "build.md" in text and "commit" in text, \
            f"{path.name} must say to record WHY it stopped, and commit it"


def test_the_terminal_state_is_phrased_in_the_words_verify_uses(built,
                                                                monkeypatch):
    """Both halves must be recognisably the same sentence, or the prompt
    drifts from the assertion again."""
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    push_impl(built)
    required = _verify(built)["required_terminal_state"]
    assert "implementation branch" in required and "PR body" in required
    build_md = " ".join(
        (ROOT / "commands" / "build.md").read_text(encoding="utf-8").split()).lower()
    for phrase in ("implementation branch", "pr_body.md",
                   "a human opens the pr", "do not open the pr"):
        assert phrase in build_md, \
            f"commands/build.md must state the terminal state: {phrase!r}"
    skill = " ".join(
        (ROOT / "skills" / "specdev" / "SKILL.md").read_text("utf-8").split()).lower()
    assert "never opens or merges a pr" in skill


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
        "cost_usd": 12.50, "cost_source": "transcript",
        "max_consecutive_seen": 4}), encoding="utf-8")

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


# ---- U16: the session could approve and merge PRs it did not author ------

def test_the_build_may_not_review_or_merge_pull_requests():
    """`pull-requests: write` plus bare Bash put `gh pr review --approve` on
    somebody else's PR inside the granted surface — and a human PR awaiting
    review is usually exactly what is open. Unlike the rest of the deny list
    this is a boundary, not a speed bump: the coordinator has no legitimate
    use for these verbs in ANY mode, so removing them removes a capability
    rather than inconveniencing one."""
    m = re.search(r'--disallowedTools\s+"([^"]+)"', wf("specdev-build.yml"))
    assert m
    denied = m.group(1)
    for verb in ("Bash(gh pr review:*)", "Bash(gh pr merge:*)",
                 "Bash(gh pr create:*)"):
        assert verb in denied, f"{verb} must be denied to the build session"


def test_no_poc_carve_out_is_needed_because_poc_no_longer_merges():
    """The carve-out that would have been required for poc is unnecessary:
    with U15, poc deploys from the branch instead of merging a PR."""
    t = wf("specdev-build.yml")
    m = re.search(r'--disallowedTools\s+"([^"]+)"', t)
    assert "Bash(gh pr merge:*)" in m.group(1), \
        "poc would have needed a carve-out here; it does not, because it no " \
        "longer merges anything"
    assert "FROM THE BUILT BRANCH" in t
    # ...and the prompt must not tell poc to merge, in any wording.
    prompt = t.split("prompt: |")[1].split("claude_args")[0]
    assert "merge it into main" not in prompt
    assert "self-merge" not in prompt.lower()


# ---- U17: the coordinator ends its turn and the run ends with it ---------

def _gate(ok=False, attempt=1, max_attempts=3, spent="1.50", cap="25",
          before="aaa", now="bbb"):
    return bo.continuation_gate({"ok": ok}, attempt, max_attempts, spent, cap,
                                before, now)


def test_an_unfinished_build_gets_another_attempt(tmp_path):
    """One run stopped at turn 35 of 200 with five waves untouched: no error,
    no trip, no turn exhaustion. Sharpening the prompt did not fix it — the
    next run stopped after 6 turns with the corrected wording in place. What
    built the feature was a second invocation."""
    g = _gate()
    assert g["go"] is True and g["next_attempt"] == 2, g["reasons"]


def test_a_finished_build_does_not_get_another_attempt(tmp_path):
    assert _gate(ok=True)["go"] is False


def test_an_attempt_that_committed_nothing_does_not_buy_another(tmp_path):
    """The condition that makes the loop safe: no new information, same
    checkpoint, same context - another attempt is just spend."""
    g = _gate(before="same", now="same")
    assert g["go"] is False
    assert any("committed nothing" in r for r in g["reasons"])


def test_an_unreadable_cost_stops_the_loop_rather_than_continuing(tmp_path):
    """The breaker's figure reads 'unavailable' on hosted runners by design,
    so cost must come from the action's own output — and treating unknown as
    $0 is the mistake the cost reporting already refuses to make."""
    g = _gate(spent="")
    assert g["go"] is False
    assert any("could not be read" in r for r in g["reasons"])
    assert any("treating unknown as $0" in r for r in g["reasons"])


def test_the_spend_cap_is_a_start_gate_not_a_ceiling(tmp_path):
    assert _gate(spent="24.99", cap="25")["go"] is True
    assert _gate(spent="25.00", cap="25")["go"] is False
    assert _gate()["cap_is_a_start_gate"] is True, \
        "the cap bounds whether the NEXT attempt begins, not what it spends"


def test_the_attempt_ceiling_is_honoured(tmp_path):
    assert _gate(attempt=3, max_attempts=3)["go"] is False
    assert _gate(attempt=1, max_attempts=1)["go"] is False


def test_continuation_is_steps_in_one_job_never_a_self_dispatch():
    """GitHub does not create workflow runs from events authored by the
    default token, so a workflow calling `gh workflow run` on itself is a
    silent no-op — the same rule that stops bot-authored PRs firing
    on: pull_request."""
    t = wf("specdev-build.yml")
    code = "\n".join(l for l in t.split("\n") if not l.strip().startswith("#"))
    assert "gh workflow run" not in code, "self-dispatch is a silent no-op"
    assert t.count("uses: anthropics/claude-code-action@v1") >= 2, \
        "continuation must be additional steps in the same job"
    assert "continue-gate" in t
    assert "steps.agent1.outputs.total_cost_usd" in t, \
        "cost must come from the action's own output, not the breaker's"


def test_continuation_is_configurable_and_shipped_on():
    cfg = json.loads((ROOT / "assets" / "specdev" / "ci.json").read_text("utf-8"))
    assert cfg["max_build_attempts"] >= 2
    assert cfg["continuation_cap_usd"] > 0
    assert cfg["continuation_cap_usd"] > cfg["max_cost_usd"], \
        "a continuation cap below one run's own ceiling could never continue"


# ---- U18: the checkpoint's [skip ci] must never head the work branch -----

def test_the_implementation_branch_is_pushed_before_the_checkpoint_commit():
    """Ordering is load-bearing: the checkpoint commit carries [skip ci], and
    a PR whose head carries it gets no checks at all, silently."""
    t = wf("specdev-build.yml")
    impl = t.index("id: impl")
    ckpt = t.index("id: checkpoint")
    assert impl < ckpt, \
        "pushing the work branch after the checkpoint commit gives it a " \
        "[skip ci] head"
    assert "[skip ci]" in t[ckpt:], "the checkpoint commit still carries it"


# ---- U20: the breaker state never reached the artifact -------------------

def test_the_breaker_state_is_not_a_dotfile_and_is_uploaded():
    """upload-artifact's globber skips hidden files, so `.specdev-breaker.json`
    silently never arrived — and if-no-files-found: warn stayed quiet because
    the other two paths matched."""
    t = wf("specdev-build.yml")
    m = re.search(r"BREAKER_STATE:\s*(\S+)", t)
    assert m, "the breaker state path must be declared"
    assert not Path(m.group(1)).name.startswith("."), \
        "a dotfile is skipped by the artifact globber"
    assert "if-no-files-found: error" in t, \
        "a missing artifact path must not stay silent"


# ---- U19: the GitHub rule that produces two different surprises ----------

def test_the_readme_documents_the_bot_identity_rule_once():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Two things GitHub will not do for a bot" in readme
    for topic in ("on: pull_request", "workflow run", "PAT"):
        assert topic in readme, f"the rule's consequences must name {topic!r}"
    assert "close" in readme and "reopen" in readme, \
        "the safe nudge must be documented alongside it"
