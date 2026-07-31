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


# ---- F16: evidence about this run must have been produced BY this run ----
#
# The spec-PR exclusion above was ONE enumerated contaminant. The invariant it
# belongs to is general: any artifact this run did not create is not evidence
# about this run. These fix the class, not the instance.

RUN_START = "2026-07-31T09:00:00Z"
BOT = "github-actions[bot]"


@pytest.fixture
def checkpointed(tmp_path):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-002\n\nWave 1 green.\n",
        encoding="utf-8")
    return tmp_path


def _pr(**over):
    pr = {"number": 7, "title": "FEAT-002 impl", "state": "OPEN",
          "headRefName": "impl/FEAT-002", "baseRefName": "main",
          "url": "u", "body": "", "createdAt": "2026-07-31T10:00:00Z",
          "author": {"login": BOT}}
    pr.update(over)
    return pr


def _verified(root, prs, monkeypatch, **kw):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: prs)
    kw.setdefault("since", RUN_START)
    kw.setdefault("author", [BOT])
    return bo.verify(root, "FEAT-002", kw.pop("mode", "prod"), ".", "main",
                     None, **kw)


def test_a_humans_pre_existing_pr_does_not_satisfy_the_terminal_state(
        checkpointed, monkeypatch):
    """The observed failure: two human infrastructure PRs discussing the
    feature at length were attributed to a build that merged nothing. Merged
    weeks earlier, by a person, mentioning the FEAT id — and in prod mode that
    made ok=True for a run with no branch, no commit and no PR."""
    human = _pr(number=101, title="infra: CI runners for FEAT-002",
                state="MERGED", headRefName="infra/runners",
                createdAt="2026-07-01T08:00:00Z",
                author={"login": "a-human"})
    rec = _verified(checkpointed, [human], monkeypatch)
    assert rec["ok"] is False
    assert any("no Implementation PR" in p for p in rec["problems"])
    assert rec["implementation_prs"] == []
    # ...and it must SAY what it rejected and why, or a reader cannot tell
    # "produced nothing" from "the filters were off".
    assert len(rec["rejected_prs"]) == 1
    assert "a-human" in rec["rejected_prs"][0]["reason"]


def test_a_pr_that_predates_this_run_is_not_this_runs_output(
        checkpointed, monkeypatch):
    """Same-author, right shape, wrong run: a re-dispatch must not verify
    itself against the previous dispatch's PR."""
    old = _pr(number=55, createdAt="2026-07-30T23:59:59Z")
    rec = _verified(checkpointed, [old], monkeypatch)
    assert rec["ok"] is False
    assert "before this run started" in rec["rejected_prs"][0]["reason"]


def test_poc_is_not_satisfied_by_an_unrelated_merged_pr(
        checkpointed, monkeypatch):
    """poc requires a MERGED PR, so an old merged PR satisfied it outright."""
    old = _pr(number=55, state="MERGED", createdAt="2026-01-01T00:00:00Z")
    assert _verified(checkpointed, [old], monkeypatch, mode="poc")["ok"] is False


def test_a_neighbouring_feat_id_does_not_match(checkpointed, monkeypatch):
    """Both text matches were unanchored substring tests, so 'FEAT-002' in
    'FEAT-0021' was True and FEAT-0021's PR satisfied FEAT-002's terminal
    state. Provenance filters make this less likely to fire; a same-run bot PR
    for a neighbouring id still slips through without an anchor."""
    neighbour = _pr(number=9, title="FEAT-0021 impl",
                    headRefName="impl/FEAT-0021", body="closes FEAT-0021")
    rec = _verified(checkpointed, [neighbour], monkeypatch)
    assert rec["ok"] is False
    assert "whole id" in rec["rejected_prs"][0]["reason"]


def test_a_feat_id_embedded_in_a_longer_token_does_not_match(checkpointed,
                                                             monkeypatch):
    assert bo._token_re("FEAT-002").search("FEAT-0021") is None
    assert bo._token_re("FEAT-002").search("xFEAT-002") is None
    assert bo._token_re("FEAT-002").search("impl/FEAT-002/wave-1") is not None


def test_another_units_pr_does_not_match_on_a_substring(checkpointed,
                                                        monkeypatch):
    """A unit named 'api' matched a branch named 'rapid-sync' — 'rapid'
    contains 'api'. The unit must match on path/branch segments."""
    other = _pr(number=12, title="FEAT-002 impl", headRefName="impl/rapid-sync",
                body="FEAT-002 for the sync service")
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [other])
    rec = bo.verify(checkpointed, "FEAT-002", "prod", "api", "main", None,
                    since=RUN_START, author=[BOT])
    assert rec["ok"] is False
    assert "does not name unit" in rec["rejected_prs"][0]["reason"]
    # ...while the unit's own PR still matches, across a '/' boundary.
    mine = _pr(number=13, headRefName="impl/api/FEAT-002",
               title="FEAT-002 impl")
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [mine])
    assert bo.verify(checkpointed, "FEAT-002", "prod", "api", "main", None,
                     since=RUN_START, author=[BOT])["ok"] is True


def test_this_runs_own_pr_still_passes_every_filter(checkpointed, monkeypatch):
    """The filters must not be so tight that a real build cannot verify. Both
    bot spellings resolve to the same identity."""
    for login in (BOT, "app/github-actions", "github-actions"):
        rec = _verified(checkpointed, [_pr(author={"login": login})], monkeypatch)
        assert rec["ok"] is True, f"{login} should be recognised as the builder"
        assert rec["rejected_prs"] == []
        assert rec["warnings"] == []


def test_unfiltered_matching_still_reproduces_the_original_behaviour(
        checkpointed, monkeypatch):
    """The inverted half of the provenance test: without filters the old
    permissive behaviour is what you get — AND you are told so. Paired with the
    tests above so a refactor cannot quietly stop exercising the filters while
    the regression tests stay green."""
    human = _pr(number=101, state="MERGED", createdAt="2026-07-01T08:00:00Z",
                author={"login": "a-human"})
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [human])
    rec = bo.verify(checkpointed, "FEAT-002", "prod", ".", "main")
    assert rec["ok"] is True, "unfiltered, this is still just a text match"
    assert rec["provenance_filters"]["applied"] is False
    assert any("TEXT MATCH" in w for w in rec["warnings"]), \
        "an unqualified verdict must say it is unqualified"


def test_verify_cli_warns_rather_than_implying_a_certainty_it_lacks(
        checkpointed):
    """The warning has to reach the RUN LOG, not just the JSON record."""
    rc = subprocess.run(
        [sys.executable, str(TOOLS / "build_outcome.py"), "--root",
         str(checkpointed), "verify", "--feat", "FEAT-002", "--mode", "prod"],
        capture_output=True, text=True)
    assert "::warning" in rc.stdout, \
        "a check running without provenance filters must say so"
    with_filters = subprocess.run(
        [sys.executable, str(TOOLS / "build_outcome.py"), "--root",
         str(checkpointed), "verify", "--feat", "FEAT-002", "--mode", "prod",
         "--since", RUN_START, "--author", BOT],
        capture_output=True, text=True)
    assert "::warning" not in with_filters.stdout, \
        "a qualified check must not cry wolf"


def test_a_run_that_wrote_code_but_opened_no_pr_fails_with_the_right_reason(
        checkpointed, monkeypatch):
    """A real checkpoint is not a terminal state. The build wrote code, waves
    went green, and it stopped before opening the PR — that is a failure whose
    reason must name the missing PR, not the checkpoint."""
    rec = _verified(checkpointed, [], monkeypatch)
    assert rec["ok"] is False
    assert len(rec["problems"]) == 1
    assert "no Implementation PR" in rec["problems"][0]
    assert "BUILD.md" not in rec["problems"][0]


def test_verify_json_carries_the_filters_and_what_they_rejected(
        checkpointed, monkeypatch, tmp_path):
    """so a reader can tell 'no PR was produced' from 'the filters were off'
    without re-deriving it."""
    out = tmp_path / "verify.json"
    rec = _verified(checkpointed, [_pr(number=101, author={"login": "a-human"})],
                    monkeypatch)
    out.write_text(json.dumps(rec), encoding="utf-8")
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["provenance_filters"] == {
        "since": RUN_START, "authors": [BOT], "applied": True}
    assert doc["rejected_prs"][0]["number"] == 101
    assert doc["rejected_prs"][0]["reason"]


def test_the_build_workflow_actually_passes_the_provenance_filters():
    """A filter the workflow never supplies is a filter that does not exist."""
    t = wf("specdev-build.yml")
    assert "--since" in t and "steps.start.outputs.at" in t, \
        "verify must be told when this run started"
    assert "--author" in t, "verify must be told whose PRs count as this run's"
    assert re.search(r'echo "at=\$\(date -u', t), \
        "the start instant must be captured alongside the start SHA"


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
    """The inverted half of the test above."""
    _breaker(tmp_path, monkeypatch)
    for output in ("Error: permission denied",
                   "This tool has not been granted to this session",
                   "Bash(curl:*) is not allowed",
                   "tool use was rejected"):
        cb.handle({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_output": output})
    assert cb.load_state()["denials"] == 4
    assert cb.load_state()["denied_tools"] == {"Bash": 4}


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


# ---- U7/U10: provenance must anchor to the BUILD, not to the attempt -----
#
# `auto_resume` makes a re-dispatch a continuation of the same logical build.
# Anchoring the provenance window to the job's own start instant therefore
# contradicted it: a build that had already opened its Implementation PR could
# never verify again, because the PR only gets older and each dispatch rejected
# it for predating the job. Terminal, not transient — and in poc, where
# deploy-poc is gated on terminal_ok, a build whose PR was already merged could
# never deploy, silently, with "no Implementation PR was found" as the reason.

BUILD_START = "2026-07-31T08:00:00Z"   # attempt 1 began here
PR_OPENED = "2026-07-31T10:00:00Z"     # attempt 1 opened the PR, then died
JOB_START = "2026-07-31T12:00:00Z"     # attempt 2 (the re-dispatch) begins


def test_a_resumed_dispatch_still_recognises_the_pr_an_earlier_attempt_opened(
        checkpointed, monkeypatch):
    pr = _pr(createdAt=PR_OPENED)
    rec = _verified(checkpointed, [pr], monkeypatch, since=BUILD_START)
    assert rec["ok"] is True, (
        "anchored to the logical build, the build's own PR must still count - "
        "otherwise no re-dispatch of this feature can ever reach green")
    assert rec["rejected_prs"] == []


def test_anchoring_to_the_attempt_rejects_the_builds_own_pr(checkpointed,
                                                            monkeypatch):
    """The inverted half: this is what the job-anchored window did, kept as an
    executable statement of why the anchor moved."""
    pr = _pr(createdAt=PR_OPENED)
    rec = _verified(checkpointed, [pr], monkeypatch, since=JOB_START)
    assert rec["ok"] is False
    assert "before this run started" in rec["rejected_prs"][0]["reason"]


def test_a_poc_build_whose_pr_was_already_merged_can_still_deploy(
        checkpointed, monkeypatch):
    """The worst limb: deploy-poc is gated on terminal_ok, so this failed
    closed AND silently, forever, for a build that had fully succeeded."""
    merged = _pr(state="MERGED", createdAt=PR_OPENED)
    rec = _verified(checkpointed, [merged], monkeypatch, mode="poc",
                    since=BUILD_START)
    assert rec["ok"] is True
    assert _verified(checkpointed, [merged], monkeypatch, mode="poc",
                     since=JOB_START)["ok"] is False


def test_a_humans_older_pr_is_still_rejected_under_the_build_anchor(
        checkpointed, monkeypatch):
    """Widening the window to the logical build must not reopen U1: a PR that
    predates the BUILD, or belongs to someone else, is still not evidence."""
    human = _pr(number=101, state="MERGED", createdAt="2026-07-01T08:00:00Z",
                author={"login": "a-human"})
    rec = _verified(checkpointed, [human], monkeypatch, since=BUILD_START)
    assert rec["ok"] is False
    assert rec["rejected_prs"][0]["reason"]


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

def test_an_unattributed_poc_failure_does_not_accuse_a_strangers_pr(
        checkpointed, monkeypatch):
    """The verdict was right and the stated reason was false: it told a reader
    the build had opened a PR and failed to merge it, and sent them to a
    stranger's PR to find out why. That is the part a reader acts on."""
    human = _pr(number=101, state="OPEN", headRefName="infra/runners",
                title="infra: CI runners", body="needed for FEAT-002",
                author={"login": "a-human"})
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [human])
    rec = bo.verify(checkpointed, "FEAT-002", "poc", ".", "main")
    assert rec["ok"] is False
    reason = " ".join(rec["problems"])
    assert "cannot be attributed" in reason or "no PR" in reason
    assert "may belong to anyone" in reason
    assert not re.search(r"found \d+ unmerged", reason), \
        "that phrasing presupposes the PRs are this build's output"


def test_an_attributed_poc_failure_still_names_the_pr(checkpointed, monkeypatch):
    """The inverted half: when the check CAN attribute, the specific, named
    claim is exactly right and must not be softened away."""
    rec = _verified(checkpointed, [_pr(number=7, state="OPEN")], monkeypatch,
                    mode="poc", since=BUILD_START)
    assert rec["ok"] is False
    reason = " ".join(rec["problems"])
    assert "found 1 unmerged (#7)" in reason
    assert "may belong to anyone" not in reason


def test_the_report_does_not_call_unattributed_matches_implementation_prs():
    rec = {"ok": True, "feat": "FEAT-002", "mode": "prod", "unit": ".",
           "problems": [], "provenance_filters": {"applied": False},
           "implementation_prs": [{"number": 101, "url": "u", "state": "OPEN"}]}
    md = bo._md_report(rec, None)
    assert "NOT attributed to this run" in md
    assert "**Implementation PRs found:**" not in md
    rec["provenance_filters"] = {"applied": True, "since": BUILD_START,
                                 "authors": [BOT]}
    assert "**Implementation PRs found:**" in bo._md_report(rec, None)


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


def test_the_terminal_state_is_phrased_in_the_words_verify_uses():
    """Both halves must be recognisably the same sentence, or the prompt drifts
    from the assertion again."""
    required = bo.verify(ROOT, "FEAT-002", "prod", ".", "main")
    assert required["required_terminal_state"] == \
        "Implementation PR open against main"
    build_md = (ROOT / "commands" / "build.md").read_text(encoding="utf-8")
    assert "Implementation PR" in build_md and "base branch" in build_md


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
