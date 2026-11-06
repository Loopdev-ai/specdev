"""Higher accuracy with fewer human stops - Phases 0 and 1
(docs/superpowers/specs/2026-11-05-accuracy-with-fewer-human-stops-design.md).

Phase 0 measures the review loop (`review_ledger.py stats`, the outcome
record, the escapes workflow). Phase 1 makes its plumbing mechanical (the
review hook, `record --from-hook`, the CI Stop hook), raises reviewer effort,
and removes interruptions that are not decisions (assume-and-record,
interrogate mode, deployment facts at init).
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "assets" / "specdev" / "tools"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


sys.path.insert(0, str(TOOLS))
rl = load_mod(TOOLS / "review_ledger.py", "review_ledger")
rh = load_mod(TOOLS / "review_hook.py", "review_hook_t")
bo = load_mod(TOOLS / "build_outcome.py", "build_outcome_t")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=str(root), check=True,
                          capture_output=True, text=True).stdout.strip()


SPEC = """# Product Spec - Widget

**Feature ID:** FEAT-007
**Status:** draft

## Original Request

Make the widget page through results.

## Requirements

### REQ-001 - Paging

- **Acceptance:** page 1 shows the first 20 rows.

## Out of Scope

- Sorting.

## Open Questions

- none
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".specdev").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / ".specdev" / "spec.md").write_text(SPEC, encoding="utf-8")
    (root / "widget.py").write_text("def page(n):\n    return n\n",
                                    encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


def bug(summary="page 0 returns the last page", **kw):
    return {"severity": "blocking", "kind": "bug", "where": "widget.py:2",
            "summary": summary, "scenario": "page(0) -> last page", **kw}


def nit(summary="name is vague"):
    return {"severity": "minor", "kind": "bug", "where": "widget.py:1",
            "summary": summary}


def rep(*f):
    return {"findings": list(f)}


def run_tool(name, *args, cwd, stdin=None, env=None):
    import os
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, str(TOOLS / name), *args],
                          cwd=str(cwd), input=stdin, capture_output=True,
                          text=True, env=e)


# ---- Phase 0: stats --------------------------------------------------------

def test_stats_with_no_ledger_reports_nothing_recorded(repo):
    st = rl.stats(repo)
    assert st["feat"] is None
    assert st["phases"]["impl"]["passes"] == 0


def test_stats_counts_passes_kinds_dismissals_and_reraises(repo):
    rl.record(repo, "impl", "FEAT-007", [rep(bug(), nit())], repo_root=repo)
    rl.dismiss(repo, "impl", "I1-1", "not-reproducible: test passes")
    # The next pass raises the dismissed finding again, plus a test finding.
    rl.record(repo, "impl", "FEAT-007",
              [rep(bug(), {"severity": "blocking", "kind": "test",
                           "where": "REQ-001", "summary": "asserts nothing",
                           "scenario": "x"})], repo_root=repo)
    ph = rl.stats(repo, "impl")["phases"]["impl"]
    assert ph["passes"] == 2 and ph["passes_per_run"] == [2]
    assert ph["blocking_per_pass"] == [1, 2]
    assert ph["findings"] == {"blocking": 3, "minor": 1}
    assert ph["by_kind"]["bug"] == {"blocking": 2, "minor": 1}
    assert ph["by_kind"]["test"] == {"blocking": 1, "minor": 0}
    assert ph["dismissed"] == 1 and ph["not_reproducible"] == 1
    assert ph["reraised_after_dismissal"] == 1
    assert ph["final_pass_clean"] is False
    assert "impl" in rl.stats(repo, "impl")["phases"]
    assert "spec" not in rl.stats(repo, "impl")["phases"]


def test_stats_cli_prints_text_and_json(repo):
    rl.record(repo, "impl", "FEAT-007", [rep()], repo_root=repo)
    p = run_tool("review_ledger.py", "stats", cwd=repo)
    assert p.returncode == 0, p.stderr
    assert "impl: 1 pass(es)" in p.stdout and "clean" in p.stdout
    p = run_tool("review_ledger.py", "stats", "--phase", "impl", "--json",
                 cwd=repo)
    assert json.loads(p.stdout)["phases"]["impl"]["final_pass_clean"]


def test_verify_record_carries_review_stats_and_report_shows_them(repo):
    rl.record(repo, "impl", "FEAT-007", [rep(bug())], repo_root=repo)
    rec = bo.verify(str(repo), "FEAT-007", "prod", repo_dir=str(repo))
    assert rec["review_stats"]["phases"]["impl"]["passes"] == 1
    md = bo._md_report(rec, None)
    assert "Review loop (impl):" in md and "1 pass(es)" in md


def test_review_stats_never_fail_verify(repo, monkeypatch):
    monkeypatch.setattr(rl, "stats", lambda *a, **k: 1 / 0)
    assert bo._review_stats(str(repo), "FEAT-007") is None


def test_escapes_workflow_is_read_only_and_scoped_to_impl_branches():
    yaml = pytest.importorskip("yaml")
    path = ROOT / "assets" / "workflows" / "specdev-escapes.yml"
    doc = yaml.safe_load(path.read_text("utf-8"))
    on = doc.get(True, doc.get("on"))
    assert "pull_request_review" in on
    assert set(doc["permissions"].values()) == {"read"}
    job = doc["jobs"]["count"]
    assert "specdev/impl/" in job["if"]
    text = path.read_text("utf-8")
    for verb in ("gh pr comment", "gh pr review", "--method POST", "-X POST"):
        assert verb not in text, "measurement only: it must never post"


# ---- Phase 1: one schema, used by the hook and by record -------------------

def test_report_problems_shares_records_rules_and_strict_adds_scenario():
    assert rl.report_problems(rep(bug(), nit())) == []
    no_where = dict(bug(), where="")
    assert any("where" in p for p in rl.report_problems(rep(no_where)))
    no_scenario = dict(bug(), scenario="")
    assert rl.report_problems(rep(no_scenario)) == []
    assert any("scenario" in p
               for p in rl.report_problems(rep(no_scenario), strict=True))
    assert rl.report_problems({"nope": 1})
    assert any("severity" in p for p in
               rl.report_problems(rep(dict(bug(), severity="huge"))))


def test_record_keeps_a_needs_human_default(repo):
    f = {"severity": "blocking", "kind": "spec", "where": "REQ-001",
         "summary": "empty page?", "scenario": "page past the end",
         "needs_human": True, "default": "an empty list"}
    entry = rl.record(repo, "spec", "FEAT-007", [rep(f)], repo_root=repo)
    assert entry["findings"][0]["default"] == "an empty list"
    text = rl._status_text(rl.status(repo, "spec", repo))
    assert "default: an empty list" in text


# ---- Phase 1: review_hook.py ----------------------------------------------

def msg(report_obj, prose="2 blocking"):
    return f"{prose}\n\n```json\n{json.dumps(report_obj)}\n```\n"


def payload(agent="code-reviewer", message=None, agent_id="a1", **kw):
    return {"hook_event_name": "SubagentStop", "agent_type": agent,
            "agent_id": agent_id, "last_assistant_message": message, **kw}


def test_hook_ignores_agents_that_are_not_reviewers(tmp_path):
    assert rh.handle(payload("component-builder", "no json"),
                     tmp_path) is None
    assert not list(tmp_path.rglob("*.json"))


def test_hook_saves_a_valid_report_under_its_phase(tmp_path):
    assert rh.handle(payload("specdev:code-reviewer", msg(rep(bug()))),
                     tmp_path) is None
    saved = tmp_path / "impl" / "code-reviewer--a1.json"
    assert json.loads(saved.read_text())["findings"][0]["summary"]
    rh.handle(payload("spec-reviewer", msg(rep())), tmp_path)
    assert (tmp_path / "spec" / "spec-reviewer--a1.json").exists()


def test_hook_takes_the_last_fenced_block(tmp_path):
    two = ("quoting input:\n```json\n{\"not\": \"a report\"}\n```\n"
           + msg(rep(nit())))
    assert rh.handle(payload(message=two), tmp_path) is None


@pytest.mark.parametrize("bad", [
    "no block at all",
    "```json\n{not json\n```",
    msg(rep(dict(bug(), scenario=""))),
    msg({"findings": "nope"}),
])
def test_hook_blocks_an_unrecordable_report(tmp_path, bad):
    out = rh.handle(payload(message=bad), tmp_path)
    assert out and out["decision"] == "block"
    assert "Do not redo the review" in out["reason"]
    assert not (tmp_path / "impl").exists()


def test_hook_lets_go_after_two_blocks(tmp_path):
    bad = payload(message="nothing")
    assert rh.handle(bad, tmp_path)["decision"] == "block"
    assert rh.handle(bad, tmp_path)["decision"] == "block"
    assert rh.handle(bad, tmp_path) is None, "a hook must never trap an agent"
    # Another agent has its own budget.
    assert rh.handle(payload(message="nothing", agent_id="a2"),
                     tmp_path)["decision"] == "block"


def test_hook_fails_open_without_a_message(tmp_path):
    assert rh.handle(payload(message=None), tmp_path) is None


def test_hook_cli_fails_open_and_plugin_copy_stands_down_in_ci(repo):
    p = run_tool("review_hook.py", cwd=repo, stdin="{not json")
    assert p.returncode == 0 and p.stdout == ""
    bad = json.dumps(payload(message="nothing"))
    p = run_tool("review_hook.py", "--source", "plugin", cwd=repo, stdin=bad,
                 env={"SPECDEV_CI_HOOKS": "1"})
    assert p.returncode == 0 and p.stdout == ""
    p = run_tool("review_hook.py", cwd=repo, stdin=bad,
                 env={"SPECDEV_CI_HOOKS": "1"})
    assert json.loads(p.stdout)["decision"] == "block"


def test_hook_dir_lives_in_the_git_dir_and_is_never_tracked(repo):
    d = rl.hook_dir(repo)
    assert d.name == "specdev-review" and d.parent.name == ".git"


# ---- Phase 1: record --from-hook -------------------------------------------

def test_record_from_hook_records_one_report_per_reviewer_and_consumes(repo):
    d = rl.hook_dir(repo)
    rh.handle(payload("code-reviewer", msg(rep(bug()))), d)
    p = run_tool("review_ledger.py", "record", "--phase", "impl",
                 "--from-hook", cwd=repo)
    assert p.returncode == 1 and "intent-reviewer" in p.stderr, \
        "half a review must not record as a pass"
    rh.handle(payload("intent-reviewer", msg(rep())), d)
    p = run_tool("review_ledger.py", "record", "--phase", "impl",
                 "--from-hook", cwd=repo)
    assert p.returncode == 0, p.stderr
    assert "1 blocking" in p.stdout
    assert not list((d / "impl").glob("*.json")), "consumed"
    assert len(list((d / "impl" / "recorded").glob("*.json"))) == 2
    p = run_tool("review_ledger.py", "record", "--phase", "impl",
                 "--from-hook", cwd=repo)
    assert p.returncode == 1, "a recorded report is never reused"


def test_record_from_hook_refuses_a_duplicate_reviewer(repo):
    d = rl.hook_dir(repo)
    rh.handle(payload("spec-reviewer", msg(rep()), agent_id="x"), d)
    rh.handle(payload("spec-reviewer", msg(rep()), agent_id="y"), d)
    p = run_tool("review_ledger.py", "record", "--phase", "spec",
                 "--from-hook", cwd=repo)
    assert p.returncode == 1 and "2 saved reports" in p.stderr


def test_record_needs_exactly_one_source(repo):
    p = run_tool("review_ledger.py", "record", "--phase", "impl", cwd=repo)
    assert p.returncode == 1 and "exactly one" in p.stderr


# ---- Phase 1: the CI Stop hook --------------------------------------------

BUILD_REAL = "# Build\n\n**Status:** building\n\nwave 1 green\n"
PR_REAL = "# FEAT-007 - Widget paging\n\nImplements REQ-001.\n"


@pytest.fixture
def build(repo):
    """A prod build in progress on a feature branch off main."""
    git(repo, "checkout", "-q", "-b", "feat/widget")
    (repo / ".specdev" / "BUILD.md").write_text(BUILD_REAL, encoding="utf-8")
    (repo / "widget.py").write_text("def page(n):\n    return max(n, 1)\n",
                                    encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "wave 1")
    return repo


ON = {"SPECDEV_STOP_HOOK": "1"}


def decide(root, tmp_path, env=ON, max_blocks=5):
    return bo.stop_decision(str(root), "FEAT-007", "prod", "main", str(root),
                            tmp_path / "stop.json", max_blocks, env)


def test_stop_hook_is_off_outside_ci(build, tmp_path):
    assert decide(build, tmp_path, env={}) is None
    assert decide(build, tmp_path, max_blocks=0) is None


def test_stop_hook_names_what_is_missing(build, tmp_path):
    out = decide(build, tmp_path)
    assert out["decision"] == "block"
    assert "PR_BODY.md" in out["reason"]
    assert "review" in out["reason"]
    assert "## Stopped" in out["reason"]


def test_stop_hook_never_blocks_twice_without_a_new_commit(build, tmp_path):
    assert decide(build, tmp_path)["decision"] == "block"
    assert decide(build, tmp_path) is None
    (build / "widget.py").write_text("def page(n):\n    return n or 1\n")
    git(build, "commit", "-qam", "progress")
    assert decide(build, tmp_path)["decision"] == "block"


def test_stop_hook_respects_its_budget(build, tmp_path):
    for i in range(2):
        assert decide(build, tmp_path, max_blocks=2)["decision"] == "block"
        (build / "widget.py").write_text(f"x = {i}\n")
        git(build, "commit", "-qam", f"p{i}")
    assert decide(build, tmp_path, max_blocks=2) is None


def test_a_filled_stopped_section_is_a_deliberate_stop(build, tmp_path):
    (build / ".specdev" / "BUILD.md").write_text(
        BUILD_REAL + "\n## Stopped\n\n> explain here\n\n<placeholder>\n")
    assert decide(build, tmp_path)["decision"] == "block", \
        "the template's own text is not a stop"
    (build / ".specdev" / "BUILD.md").write_text(
        BUILD_REAL + "\n## Stopped\n\nREQ-001 contract needs a human: the "
                     "API has no page size.\n")
    assert decide(build, tmp_path) is None


def test_stop_hook_lets_a_finished_build_stop(build, tmp_path):
    (build / ".specdev" / "PR_BODY.md").write_text(PR_REAL)
    rl.record(build, "impl", "FEAT-007", [rep()], repo_root=build)
    git(build, "add", "-A")
    git(build, "commit", "-q", "-m", "ledger")
    assert bo.local_terminal_problems(str(build), "FEAT-007", "prod", "main",
                                      str(build)) == []
    assert decide(build, tmp_path) is None


def test_stop_hook_sees_nothing_committed_beyond_base(repo, tmp_path):
    probs = bo.local_terminal_problems(str(repo), "FEAT-007", "poc", "main",
                                       str(repo))
    assert any("nothing is committed beyond" in p for p in probs)


def test_stop_hook_cli_reads_env_and_fails_open(build, tmp_path):
    env = {"SPECDEV_STOP_HOOK": "1", "FEAT": "FEAT-007", "MODE": "prod",
           "UNIT": ".", "BASE_BRANCH": "main",
           "SPECDEV_STOP_STATE": str(tmp_path / "s.json"),
           "SPECDEV_MAX_STOP_BLOCKS": "3"}
    p = run_tool("build_outcome.py", "stop-hook", cwd=build, stdin="{}",
                 env=env)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["decision"] == "block"
    p = run_tool("build_outcome.py", "stop-hook", cwd=build, stdin="{}",
                 env={**env, "SPECDEV_MAX_STOP_BLOCKS": "lots"})
    assert p.returncode == 0 and p.stdout == ""
    assert "internal error" in p.stderr


# ---- Phase 1: configuration, agents, wiring -------------------------------

def test_new_ci_keys_ship_with_safe_defaults():
    ci = json.loads((ROOT / "assets" / "specdev" / "ci.json").read_text())
    assert ci["max_stop_blocks"] == 5
    assert ci["reviewer_model"] == "inherit"


EFFORT = {"spec-reviewer": "xhigh", "code-reviewer": "xhigh",
          "intent-reviewer": "xhigh", "component-builder": "high",
          "qa-verifier": "high"}


@pytest.mark.parametrize("name,level", sorted(EFFORT.items()))
def test_reviewers_think_harder_than_builders(name, level):
    text = (ROOT / "agents" / f"{name}.md").read_text("utf-8")
    fm = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
    assert re.search(rf"^effort:\s*{level}\s*$", fm, re.M)
    assert re.search(r"^model:\s*inherit\s*$", fm, re.M), \
        "the shipped model stays inherit; reviewer_model is opt-in at init"


@pytest.mark.parametrize("name", ["spec-reviewer", "code-reviewer",
                                  "intent-reviewer"])
def test_reviewers_know_the_hook_checks_their_report(name):
    assert "A hook checks this block" in (
        ROOT / "agents" / f"{name}.md").read_text("utf-8")


def test_spec_reviewer_has_interrogate_mode_and_defaults():
    t = (ROOT / "agents" / "spec-reviewer.md").read_text("utf-8")
    assert "## Interrogate mode" in t and '"default"' in t


def _settings_from_workflow():
    t = (ROOT / "assets" / "workflows" / "specdev-build.yml").read_text()
    m = re.search(r"<<'JSON'\n(.*?)\n\s*JSON\n", t, re.S)
    assert m, "the breaker settings heredoc moved"
    return json.loads(m.group(1)), t


def test_ci_settings_wire_both_hooks():
    settings, t = _settings_from_workflow()
    hooks = settings["hooks"]
    sub = hooks["SubagentStop"][0]["hooks"][0]["command"]
    stop = hooks["Stop"][0]["hooks"][0]["command"]
    assert sub == "python .specdev/tools/review_hook.py"
    assert stop == "python .specdev/tools/build_outcome.py stop-hook"
    for env in ("SPECDEV_CI_HOOKS: '1'", "SPECDEV_STOP_HOOK: '1'",
                "BASE_BRANCH:", "SPECDEV_MAX_STOP_BLOCKS", "max_stop_blocks",
                "SPECDEV_STOP_STATE"):
        assert env in t, env


def test_plugin_ships_the_review_hook_for_interactive_sessions():
    doc = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    cmd = doc["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    assert "${CLAUDE_PLUGIN_ROOT}/assets/specdev/tools/review_hook.py" in cmd
    assert cmd.endswith("--source plugin"), \
        "without it the plugin copy cannot stand down in CI"
    assert (ROOT / "assets" / "specdev" / "tools" / "review_hook.py").exists()


def test_build_template_has_a_stopped_section_the_hook_ignores_unfilled():
    text = (ROOT / "assets" / "specdev" / "BUILD.md").read_text("utf-8")
    assert "## Stopped" in text
    assert not bo._section_filled(text, "Stopped")


def test_spec_template_assumed_block_sits_inside_open_questions():
    text = (ROOT / "assets" / "specdev" / "spec.md").read_text("utf-8")
    oq = rl._section(text, "Open Questions")
    assert oq and "**Assumed:**" in oq, \
        "outside Open Questions, recording an assumption would stale the hash"


@pytest.mark.parametrize("path,needles", [
    ("skills/specdev/SKILL.md", ["--from-hook", "interrogate", "assumed:",
                                 "max_stop_blocks", "## Stopped",
                                 "do not stop the build"]),
    ("commands/build.md", ["--from-hook", "## Stopped",
                           "Deployment facts unresolved"]),
    ("commands/new-feature.md", ["interrogate mode", "--from-hook",
                                 "assumed: <the reading>"]),
    ("commands/init.md", ["reviewer_model --require-explicit",
                          "deploy.py preflight --env staging"]),
    ("assets/specdev/PR_BODY.md", ["Deployment facts unresolved"]),
    ("README.md", ["specdev-escapes.yml", "review_hook.py",
                   "max_stop_blocks", "stats"]),
])
def test_the_procedure_is_written_where_it_is_followed(path, needles):
    text = (ROOT / path).read_text("utf-8")
    for n in needles:
        assert n in text, f"{path} does not mention {n!r}"
