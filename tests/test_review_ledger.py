"""The pre-PR review loop: review_ledger.py and everything that asserts it.

Neither PR used to be READ by anything before a human opened it — qa-verifier
runs the tests the builder wrote, so a bug the builder never imagined passed
green and Copilot found it after the PR was open. These tests pin the ledger
that makes the review loop a mechanical part of the pipeline rather than
something the coordinator may or may not choose to do.
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
TOOL = TOOLS / "review_ledger.py"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


rl = load_mod(TOOL, "review_ledger_t")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=str(root), check=True,
                          capture_output=True, text=True).stdout.strip()


SPEC = """# Product Spec - Widget

**Feature ID:** FEAT-007
**Status:** draft
**Spec PR:** #
**Author:** someone
**Date:** 2026-09-29

## Original Request

Make the widget page through results.

## Requirements

### REQ-001 - Paging

- **Acceptance:** page 1 shows the first 20 rows.

## Out of Scope

- Sorting.

## Open Questions

- none yet
"""


@pytest.fixture
def repo(tmp_path):
    """A committed single-unit repo with a spec and one source file."""
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


def report(*findings):
    return {"findings": list(findings)}


def bug(where="widget.py:2", summary="page 0 returns the last page", **kw):
    return {"severity": "blocking", "kind": "bug", "where": where,
            "summary": summary, "scenario": "page(0) -> last page", **kw}


def nit(summary="name is vague"):
    return {"severity": "minor", "kind": "bug", "where": "widget.py:1",
            "summary": summary}


def rec(root, *reports, phase="impl"):
    return rl.record(root, phase, "FEAT-007", list(reports), repo_root=root)


def set_cap(root, n):
    (root / ".specdev" / "ci.json").write_text(
        json.dumps({"max_review_iterations": n}), encoding="utf-8")


# ---- Task 1: config, record, dismiss, status ------------------------------

def test_shipped_ci_json_caps_the_loop_at_ten():
    shipped = json.loads(
        (ROOT / "assets" / "specdev" / "ci.json").read_text("utf-8"))
    assert shipped["max_review_iterations"] == 10


def test_the_cap_resolves_from_ci_json(repo):
    assert rl.max_iterations(repo, repo) == 10
    set_cap(repo, 3)
    assert rl.max_iterations(repo, repo) == 3


@pytest.mark.parametrize("bad", [0, -1, 2.5, True, "ten"])
def test_zero_or_junk_does_not_disable_the_loop(repo, bad):
    set_cap(repo, bad)
    with pytest.raises(rl.LedgerError, match="integer >= 1"):
        rl.max_iterations(repo, repo)


def test_record_assigns_ids_and_counts_from_the_json(repo):
    entry = rec(repo, report(bug(), nit()))
    assert entry["pass"] == 1
    assert [f["id"] for f in entry["findings"]] == ["I1-1", "I1-2"]
    assert entry["reviewed"] == git(repo, "rev-parse", "HEAD")
    ph = rl.load(repo)["phases"]["impl"]
    assert [f["id"] for f in rl.open_blocking(ph)] == ["I1-1"], \
        "a minor finding never keeps the loop open"


def test_two_reviewers_in_one_pass_share_one_numbering(repo):
    entry = rec(repo, report(bug()), report(bug(summary="off by one")))
    assert [f["id"] for f in entry["findings"]] == ["I1-1", "I1-2"]


def test_a_list_of_reports_is_accepted(repo):
    entry = rec(repo, [report(bug()), report()])
    assert len(entry["findings"]) == 1


def test_pass_numbers_run_on(repo):
    rec(repo, report(bug()))
    (repo / "widget.py").write_text("def page(n):\n    return max(n, 1)\n",
                                    encoding="utf-8")
    git(repo, "commit", "-q", "-am", "fix I1-1")
    entry = rec(repo, report())
    assert entry["pass"] == 2 and entry["findings"] == []


def test_record_refuses_uncommitted_code(repo):
    (repo / "widget.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(rl.LedgerError, match="commit"):
        rec(repo, report())


def test_specdev_bookkeeping_is_not_uncommitted_code(repo):
    (repo / ".specdev" / "spec.md").write_text(SPEC + "\n", encoding="utf-8")
    (repo / ".specdev" / "BUILD.md").write_text("wave 1 green\n",
                                                encoding="utf-8")
    assert rec(repo, report())["pass"] == 1


def test_record_refuses_a_pass_beyond_the_cap(repo):
    set_cap(repo, 2)
    rec(repo, report(bug()))
    rec(repo, report(bug()))
    with pytest.raises(rl.LedgerError, match="cap is reached"):
        rec(repo, report())


@pytest.mark.parametrize("finding,msg", [
    ({"severity": "urgent", "kind": "bug", "where": "a", "summary": "s"},
     "severity"),
    ({"severity": "blocking", "kind": "style", "where": "a", "summary": "s"},
     "kind"),
    ({"severity": "blocking", "kind": "bug", "where": "a", "summary": ""},
     "summary"),
    ({"severity": "blocking", "kind": "bug", "where": "", "summary": "s"},
     "where"),
])
def test_malformed_findings_are_refused(repo, finding, msg):
    with pytest.raises(rl.LedgerError, match=msg):
        rec(repo, report(finding))
    assert rl.load(repo) is None, "a refused pass writes nothing"


def test_a_new_feature_starts_a_new_ledger(repo):
    rec(repo, report(bug()))
    entry = rl.record(repo, "impl", "FEAT-008", [report()], repo_root=repo)
    assert rl.load(repo)["feat"] == "FEAT-008" and entry["pass"] == 1


def test_feat_defaults_to_the_specs_feature_id(repo):
    rl.record(repo, "impl", None, [report()], repo_root=repo)
    assert rl.load(repo)["feat"] == "FEAT-007"


def test_spec_phase_reviews_the_spec_hash_not_a_commit(repo):
    entry = rec(repo, report(), phase="spec")
    assert entry["reviewed"].startswith("sha256:")
    assert entry["reviewed"] == rl.spec_hash(repo)


def test_spec_hash_ignores_bookkeeping_and_open_questions(repo):
    before = rl.spec_hash(repo)
    p = repo / ".specdev" / "spec.md"
    edited = (SPEC.replace("**Status:** draft", "**Status:** in-review")
                  .replace("**Spec PR:** #", "**Spec PR:** #41")
                  .replace("- none yet", "- S3-1 unresolved: paging past the end"))
    p.write_text(edited, encoding="utf-8")
    assert rl.spec_hash(repo) == before
    p.write_text(edited.replace("first 20 rows", "first 25 rows"),
                 encoding="utf-8")
    assert rl.spec_hash(repo) != before, \
        "a change to a REQ is a change to what was reviewed"


def test_dismiss_closes_a_finding_and_needs_a_reason(repo):
    rec(repo, report(bug(), bug(summary="second")))
    with pytest.raises(rl.LedgerError, match="reason"):
        rl.dismiss(repo, "impl", "I1-1", "  ")
    rl.dismiss(repo, "impl", "I1-1",
               "not reproducible: page(0) raises ValueError, test_page_zero")
    ph = rl.load(repo)["phases"]["impl"]
    assert [f["id"] for f in rl.open_blocking(ph)] == ["I1-2"]
    with pytest.raises(rl.LedgerError, match="already"):
        rl.dismiss(repo, "impl", "I1-1", "again")
    with pytest.raises(rl.LedgerError, match="no impl finding"):
        rl.dismiss(repo, "impl", "I9-9", "x")


def test_status_is_the_resume_point(repo):
    assert rl.status(repo, "impl", repo)["state"] == "not-started"
    rec(repo, report(bug()))
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "needs-fixes" and st["next_pass_in_run"] == 2
    assert [f["id"] for f in st["open_blocking"]] == ["I1-1"]
    rl.dismiss(repo, "impl", "I1-1", "contradicts REQ-001 Out of Scope")
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "clean"
    assert st["dismissed"][0]["id"] == "I1-1"


def test_status_reports_the_cap(repo):
    set_cap(repo, 1)
    rec(repo, report(bug()))
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "cap-reached" and st["next_pass_in_run"] is None


def test_status_ignores_a_previous_features_ledger(repo):
    rec(repo, report(bug()))
    p = repo / ".specdev" / "spec.md"
    p.write_text(SPEC.replace("FEAT-007", "FEAT-008"), encoding="utf-8")
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "not-started" and st["open_blocking"] == []


def test_cli_record_reads_stdin_and_prints_the_ids(repo):
    p = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(repo), "record", "--phase",
         "impl", "--repo-root", str(repo), "--findings-json", "-"],
        input=json.dumps(report(bug())), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "I1-1" in p.stdout and "1 blocking" in p.stdout


def test_cli_refusals_exit_nonzero_with_the_reason(repo):
    p = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(repo), "dismiss", "--phase",
         "impl", "--repo-root", str(repo), "--id", "I1-1", "--reason", "x"],
        capture_output=True, text=True)
    assert p.returncode == 1 and "no review ledger" in p.stderr


# ---- Task 2: staleness, new-run, check, render ---------------------------

def fix_and_commit(repo, body="def page(n):\n    return max(n, 1)\n",
                   msg="fix"):
    (repo / "widget.py").write_text(body, encoding="utf-8")
    git(repo, "commit", "-q", "-am", msg)


def check(repo, phase="impl", **kw):
    return rl.check(repo, phase, repo_root=repo, **kw)


def test_check_fails_when_the_loop_never_ran(repo):
    probs = check(repo)
    assert probs and "never ran" in probs[0]


def test_a_clean_last_pass_over_the_handed_off_commit_passes(repo):
    rec(repo, report(bug()))
    fix_and_commit(repo)
    rec(repo, report(nit()))
    assert check(repo) == []


def test_bookkeeping_after_the_last_pass_does_not_make_it_stale(repo):
    rec(repo, report())
    (repo / ".specdev" / "PR_BODY.md").write_text("# FEAT-007\n",
                                                  encoding="utf-8")
    (repo / ".specdev" / "BUILD.md").write_text("done\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "checkpoint [skip ci]")
    assert check(repo) == []


def test_code_changed_after_the_last_pass_is_stale(repo):
    rec(repo, report())
    fix_and_commit(repo, msg="an unreviewed change")
    probs = check(repo)
    assert any("widget.py" in p and "changed after pass 1" in p
               for p in probs)


def test_check_compares_against_the_given_head(repo):
    """verify() passes the implementation branch's tip, not the checkout."""
    rec(repo, report())
    reviewed = git(repo, "rev-parse", "HEAD")
    fix_and_commit(repo, msg="later")
    assert check(repo, head=reviewed) == []


def test_an_unknown_reviewed_commit_is_a_failure_not_a_pass(repo):
    rec(repo, report())
    doc = rl.load(repo)
    doc["phases"]["impl"]["runs"][0]["passes"][0]["reviewed"] = "deadbeef" * 5
    rl.save(doc, repo)
    assert any("cannot resolve" in p for p in check(repo))


def test_stopping_short_of_the_cap_with_findings_open_fails(repo):
    rec(repo, report(bug()))
    probs = check(repo)
    assert any("stopped after 1 of 10" in p and "I1-1" in p for p in probs)


def test_a_dismissed_finding_is_not_open(repo):
    rec(repo, report(bug()))
    rl.dismiss(repo, "impl", "I1-1", "not reproducible: see test_page_zero")
    assert check(repo) == []


def test_at_the_cap_open_findings_must_be_handed_off_in_the_pr_body(repo):
    set_cap(repo, 1)
    rec(repo, report(bug(), bug(summary="second")))
    body = repo / ".specdev" / "PR_BODY.md"
    body.write_text("# FEAT-007\n\n## Unresolved review findings\n\n"
                    "- `I1-1` page 0\n", encoding="utf-8")
    probs = check(repo)
    assert any("I1-2" in p and "not listed" in p for p in probs)
    assert not any("I1-1" in p and "not listed" in p for p in probs)
    body.write_text(body.read_text(encoding="utf-8") + "- `I1-2` second\n",
                    encoding="utf-8")
    assert check(repo) == []


def test_an_id_is_matched_whole_not_as_a_prefix(repo):
    set_cap(repo, 1)
    rec(repo, report(bug()))
    (repo / ".specdev" / "PR_BODY.md").write_text(
        "## Unresolved review findings\n\n- `I1-12` something else\n",
        encoding="utf-8")
    assert any("I1-1" in p for p in check(repo))


def test_an_id_outside_the_handoff_section_does_not_count(repo):
    set_cap(repo, 1)
    rec(repo, report(bug()))
    (repo / ".specdev" / "PR_BODY.md").write_text(
        "## Notes for the reviewer\n\n- I1-1 is fine\n\n"
        "## Unresolved review findings\n\nNone\n", encoding="utf-8")
    assert any("not listed" in p for p in check(repo))


def test_spec_phase_hands_off_in_open_questions(repo):
    set_cap(repo, 1)
    rec(repo, report({"severity": "blocking", "kind": "spec",
                      "where": "REQ-001",
                      "summary": "page size unstated for the last page"}),
        phase="spec")
    assert any("not listed" in p for p in check(repo, "spec"))
    p = repo / ".specdev" / "spec.md"
    p.write_text(p.read_text(encoding="utf-8")
                 .replace("- none yet", "- S1-1: last page size"),
                 encoding="utf-8")
    assert check(repo, "spec") == [], \
        "Open Questions is outside the hash, so the hand-off is not stale"


def test_a_spec_edit_after_the_last_pass_is_stale(repo):
    rec(repo, report(), phase="spec")
    p = repo / ".specdev" / "spec.md"
    p.write_text(p.read_text(encoding="utf-8")
                 .replace("Sorting.", "Sorting, filtering."),
                 encoding="utf-8")
    assert any("spec changed after pass 1" in x for x in check(repo, "spec"))


def test_the_ledger_must_be_for_this_feature(repo):
    rec(repo, report())
    probs = check(repo, feat="FEAT-008")
    assert probs and "FEAT-007, not FEAT-008" in probs[0]


def test_new_run_refuses_when_nothing_changed(repo):
    set_cap(repo, 1)
    rec(repo, report(bug()))
    with pytest.raises(rl.LedgerError, match="nothing changed"):
        rl.new_run(repo, "impl", repo_root=repo)


def test_new_run_after_a_change_resets_the_pass_count(repo):
    set_cap(repo, 1)
    rec(repo, report(bug()))
    fix_and_commit(repo, msg="human edit on the open PR")
    rl.new_run(repo, "impl", repo_root=repo)
    st = rl.status(repo, "impl", repo)
    assert st["run"] == 2 and st["passes_in_run"] == 0
    assert st["state"] == "not-started"
    entry = rec(repo, report())
    assert entry["pass"] == 2, "pass numbers stay unique across runs"
    assert check(repo) == []


def test_render_produces_both_pr_body_sections(repo):
    set_cap(repo, 2)
    rec(repo, report(bug(summary="first")))
    rl.dismiss(repo, "impl", "I1-1", "not reproducible")
    fix_and_commit(repo)
    rec(repo, report(bug(summary="still broken"), nit()))
    md = rl.render(repo, "impl", repo)
    assert "## Review loop" in md and "## Unresolved review findings" in md
    assert "2 of 2" in md and "cap-reached" in md
    assert "`I1-1`" in md and "not reproducible" in md
    assert "`I2-1`" in md and "still broken" in md
    (repo / ".specdev" / "PR_BODY.md").write_text("# FEAT-007\n\n" + md,
                                                  encoding="utf-8")
    assert check(repo) == []


def test_render_says_clean_when_it_is(repo):
    rec(repo, report())
    assert "None — the final review pass was clean." in \
        rl.render(repo, "impl", repo)


def test_cli_check_exit_codes(repo):
    def run():
        return subprocess.run(
            [sys.executable, str(TOOL), "--root", str(repo), "check",
             "--phase", "impl", "--repo-root", str(repo)],
            capture_output=True, text=True)
    p = run()
    assert p.returncode == 1 and "never ran" in p.stderr
    rec(repo, report())
    p = run()
    assert p.returncode == 0, p.stderr
    assert "review loop ok" in p.stdout


# ---- Task 3: the prod terminal state asserts the loop ---------------------

bo = load_mod(TOOLS / "build_outcome.py", "build_outcome_rl")


def _impl(repo):
    """Commit a real checkpoint + PR body and the implementation ref the
    workflow pushes; leave it checked out, as the CI runner has it."""
    (repo / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-007\n\nWave 1 green.\n",
        encoding="utf-8")
    (repo / ".specdev" / "PR_BODY.md").write_text(
        "# FEAT-007 - Widget\n\nImplements REQ-001.\n", encoding="utf-8")
    ref = bo.implementation_ref(".", "FEAT-007")
    git(repo, "checkout", "-q", "-B", ref)
    (repo / "widget.py").write_text("def page(n):\n    return max(n, 1)\n",
                                    encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feat(widget): REQ-001")
    return ref


def _verify(repo, mode, monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    return bo.verify(repo, "FEAT-007", mode, ".", "main", None, repo_dir=repo)


def test_prod_terminal_state_requires_the_review_loop(repo, monkeypatch):
    _impl(repo)
    out = _verify(repo, "prod", monkeypatch)
    assert out["ok"] is False
    assert any("review loop never ran" in p for p in out["problems"])
    rec(repo, report())
    out = _verify(repo, "prod", monkeypatch)
    assert out["ok"] is True, out["problems"]
    assert "review loop" in out["required_terminal_state"]


def test_prod_rejects_code_pushed_after_the_last_review(repo, monkeypatch):
    _impl(repo)
    rec(repo, report())
    (repo / "widget.py").write_text("def page(n):\n    return 1\n",
                                    encoding="utf-8")
    git(repo, "commit", "-q", "-am", "unreviewed")
    out = _verify(repo, "prod", monkeypatch)
    assert out["ok"] is False
    assert any("changed after pass 1" in p for p in out["problems"])


def test_poc_terminal_state_does_not_involve_the_review_loop(repo,
                                                             monkeypatch):
    _impl(repo)
    b = repo / ".specdev" / "BUILD.md"
    b.write_text(b.read_text(encoding="utf-8")
                 + "\n## Findings\n\nPaging works at 20 rows.\n",
                 encoding="utf-8")
    out = _verify(repo, "poc", monkeypatch)
    assert out["ok"] is True, out["problems"]
    assert "review loop" not in out["required_terminal_state"]


# ---- Task 4: reviewer agents ---------------------------------------------

AGENTS = ROOT / "agents"
REVIEWERS = ("spec-reviewer", "code-reviewer", "intent-reviewer")


def _agent(name):
    text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{name}.md has no frontmatter"
    fm = dict(line.split(":", 1) for line in m.group(1).splitlines()
              if ":" in line)
    return {k.strip(): v.strip() for k, v in fm.items()}, text


@pytest.mark.parametrize("name", REVIEWERS)
def test_reviewers_judge_and_never_fix(name):
    fm, _ = _agent(name)
    assert fm["name"] == name
    tools = {t.strip() for t in fm["tools"].split(",")}
    assert not tools & {"Write", "Edit", "NotebookEdit"}, \
        f"{name} must not be able to change what it reviews"
    assert {"Read", "Grep", "Glob", "Bash"} <= tools


@pytest.mark.parametrize("name", REVIEWERS)
def test_reviewers_end_with_the_json_the_ledger_reads(name):
    _, text = _agent(name)
    for key in ('"findings"', '"severity"', '"kind"', '"where"', '"summary"',
                "blocking", "minor"):
        assert key in text, f"{name} must specify {key} in its JSON block"
    assert "dismissed" in text.lower(), \
        f"{name} must honour the dismissed list"


@pytest.mark.parametrize("name", REVIEWERS)
def test_every_example_block_parses_and_the_ledger_accepts_it(name, repo):
    _, text = _agent(name)
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    assert blocks, f"{name} shows no example JSON block"
    phase = "spec" if name == "spec-reviewer" else "impl"
    for b in blocks:
        rec(repo, json.loads(b), phase=phase)


def test_code_reviewer_demands_a_location_and_a_failure_scenario():
    _, text = _agent("code-reviewer")
    assert "file:line" in text and "scenario" in text.lower()


def test_intent_reviewer_walks_every_req_against_the_original_request():
    _, text = _agent("intent-reviewer")
    for s in ("Original Request", "Acceptance", "end to end"):
        assert s in text


def test_spec_reviewer_blocks_on_a_missing_original_request():
    _, text = _agent("spec-reviewer")
    assert "Original Request" in text and "needs_human" in text


def test_component_builder_reproduces_a_finding_before_fixing_it():
    text = (AGENTS / "component-builder.md").read_text(encoding="utf-8")
    assert "Review-fix mode" in text
    assert "not-reproducible" in text and "fail" in text


# ---- Task 5: templates ----------------------------------------------------

TEMPLATES = ROOT / "assets" / "specdev"


def test_spec_template_records_the_original_request_first():
    text = (TEMPLATES / "spec.md").read_text(encoding="utf-8")
    assert re.search(r"^## Original Request\s*$", text, re.M)
    assert text.index("## Original Request") < text.index("## Requirements")
    assert "verbatim" in text


def test_pr_body_template_carries_the_review_sections():
    text = (TEMPLATES / "PR_BODY.md").read_text(encoding="utf-8")
    for heading in ("## Review loop", "## Unresolved review findings"):
        assert re.search(rf"^{heading}\s*$", text, re.M), heading
    assert text.index("## Unresolved review findings") < \
        text.index("## Deployment facts")
    assert "review_ledger.py check --phase impl" in text


def test_render_pasted_into_the_template_satisfies_the_check(repo):
    """The hand-off path end to end: at the cap, the coordinator pastes
    render's output over the template's two review sections."""
    set_cap(repo, 1)
    rec(repo, report(bug()))
    template = (TEMPLATES / "PR_BODY.md").read_text(encoding="utf-8")
    body = re.sub(r"^## Review loop.*?(?=^## Deployment facts)",
                  rl.render(repo, "impl", repo) + "\n", template,
                  flags=re.M | re.S)
    (repo / ".specdev" / "PR_BODY.md").write_text(body, encoding="utf-8")
    assert check(repo) == []


def test_the_unfilled_template_does_not_hand_anything_off(repo):
    set_cap(repo, 1)
    rec(repo, report(bug()))
    (repo / ".specdev" / "PR_BODY.md").write_text(
        (TEMPLATES / "PR_BODY.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    assert any("not listed" in p for p in check(repo))


def test_build_template_points_at_the_review_resume_point():
    text = (TEMPLATES / "BUILD.md").read_text(encoding="utf-8")
    assert "review_ledger.py" in text and "status --phase impl" in text


# ---- Task 6: the skill and commands drive the loop ------------------------

SKILL = ROOT / "skills" / "specdev" / "SKILL.md"
BUILD_CMD = ROOT / "commands" / "build.md"
NEW_FEATURE = ROOT / "commands" / "new-feature.md"


def _flat(path):
    return " ".join(path.read_text(encoding="utf-8").split())


def _between(text, start, end):
    return text[text.index(start):text.index(end)]


def test_skill_defines_the_pre_pr_review_loop():
    text = SKILL.read_text(encoding="utf-8")
    assert re.search(r"^## Pre-PR review loop", text, re.M)
    flat = _flat(SKILL)
    for s in ("max_review_iterations", "review_ledger.py", "spec-reviewer",
              "code-reviewer", "intent-reviewer", "fresh", "dismiss",
              "new-run", "check --phase", "render --phase impl"):
        assert s in flat, s


def test_both_pr_steps_invoke_the_loop():
    text = SKILL.read_text(encoding="utf-8")
    step5 = _between(text, "5. **Open the Spec PR.**", "6. **Build on")
    step7 = _between(text, "7. **Open the Implementation PR",
                     "8. **Merge is the deploy trigger")
    assert "Pre-PR review loop" in step5 and "Pre-PR review loop" in step7
    assert "review_ledger.py check --phase impl" in " ".join(step7.split())


def test_skill_brainstorm_captures_the_original_request():
    text = SKILL.read_text(encoding="utf-8")
    step2 = _between(text, "2. **Brainstorm.**", "3. **Spec")
    assert "Original Request" in step2 and "verbatim" in step2


def test_skill_terminal_state_and_guardrails_name_the_loop():
    text = SKILL.read_text(encoding="utf-8")
    terminal = _between(text, "## Terminal state", "## Guardrails")
    guard = _between(text, "## Guardrails", "## Helper commands")
    assert "review.json" in terminal
    assert "review_ledger.py check" in " ".join(guard.split())


def test_build_command_runs_the_loop_before_the_pr_body():
    text = BUILD_CMD.read_text(encoding="utf-8")
    after = " ".join(_between(text, "## After the final wave",
                              "## Guardrails").split())
    for s in ("code-reviewer", "intent-reviewer", "record --phase impl",
              "render --phase impl", "check --phase impl"):
        assert s in after, s
    assert after.index("code-reviewer") < after.index("render --phase impl")
    terminal = _between(text, "## Terminal state", "## Build loop")
    assert "review loop" in terminal.lower()


def test_the_terminal_state_names_the_review_loop_in_both_halves(repo,
                                                                 monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    required = bo.verify(repo, "FEAT-007", "prod", ".", "main", None,
                         repo_dir=repo)["required_terminal_state"]
    assert "review loop" in required
    assert "review loop" in _flat(BUILD_CMD).lower()


def test_new_feature_captures_intent_and_runs_the_spec_loop():
    flat = _flat(NEW_FEATURE)
    for s in ("Original Request", "verbatim", "spec-reviewer",
              "record --phase spec", "check --phase spec", "spec_pr",
              "Open Questions"):
        assert s in flat, s


# ---- Task 7: workflows -----------------------------------------------------

WF = ROOT / "assets" / "workflows"


def test_spec_validate_runs_the_spec_check_only_on_spec_prs():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((WF / "spec-validate.yml").read_text(encoding="utf-8"))
    steps = doc["jobs"]["validate"]["steps"]
    step = next(s for s in steps if "review_ledger.py" in str(s.get("run", "")))
    assert "check --phase spec" in " ".join(step["run"].split())
    cond = str(step.get("if", ""))
    assert "startsWith(github.head_ref, 'spec/')" in cond, \
        "must never fire on an Implementation PR or an unrelated PR"
    assert "].spec_pr" in cond, "a unit with no Spec PR is never asked for one"


def test_every_build_attempt_is_told_about_the_review_loop():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((WF / "specdev-build.yml").read_text(encoding="utf-8"))
    build = doc["jobs"]["build"]
    cont = " ".join(build["env"]["CONTINUE_PROMPT"].split())
    assert "review_ledger.py" in cont and "status --phase impl" in cont
    agent1 = next(s for s in build["steps"] if s.get("id") == "agent1")
    prompt = " ".join(agent1["with"]["prompt"].split())
    assert "review_ledger.py check --phase impl" in prompt
    assert "code-reviewer" in prompt and "intent-reviewer" in prompt


def test_install_smoke_runs_the_installed_review_ledger():
    text = (ROOT / ".github" / "workflows" / "tests.yml").read_text("utf-8")
    assert "python .specdev/tools/review_ledger.py --root . status" in text


# ---- Task 8: docs, vendoring, lint ------------------------------------------

def test_readme_documents_the_loop_and_its_budget():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for s in ("spec-reviewer", "code-reviewer", "intent-reviewer",
              "max_review_iterations", "review_ledger.py"):
        assert s in text, s
    assert "spends inside these limits" in text, \
        "the interaction with the breaker and continuation caps is documented"


def test_init_vendors_the_reviewers():
    text = " ".join((ROOT / "commands" / "init.md")
                    .read_text(encoding="utf-8").split())
    for a in ("spec-reviewer", "code-reviewer", "intent-reviewer"):
        assert a in text, a


def test_lint_command_covers_the_review_ledger():
    cfg = json.loads((ROOT / ".sdlc" / "config.json").read_text("utf-8"))
    assert "review_ledger.py" in cfg["commands"]["lint"]


# ---- Final review fixes ----------------------------------------------------

def _head(repo):
    return git(repo, "rev-parse", "HEAD")


def test_record_with_reviewed_equal_to_head_stamps_it(repo):
    sha = _head(repo)
    entry = rl.record(repo, "impl", "FEAT-007", [report()], repo_root=repo,
                      reviewed=sha)
    assert entry["reviewed"] == sha


def test_record_refuses_code_changed_since_dispatch(repo):
    old = _head(repo)
    fix_and_commit(repo)
    with pytest.raises(rl.LedgerError, match="code changed between dispatch"):
        rl.record(repo, "impl", "FEAT-007", [report()], repo_root=repo,
                  reviewed=old)
    assert rl.load(repo) is None


def test_record_stamps_the_reviewed_sha_when_only_bookkeeping_moved(repo):
    old = _head(repo)
    (repo / ".specdev" / "BUILD.md").write_text("wave 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bookkeeping")
    assert _head(repo) != old
    entry = rl.record(repo, "impl", "FEAT-007", [report()], repo_root=repo,
                      reviewed=old)
    assert entry["reviewed"] == old


def test_reviewed_is_an_impl_only_option(repo):
    with pytest.raises(rl.LedgerError, match="impl phase only"):
        rl.record(repo, "spec", "FEAT-007", [report()], repo_root=repo,
                  reviewed=_head(repo))


def test_reworded_dirty_refusal_says_to_discard(repo):
    (repo / "widget.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(rl.LedgerError) as e:
        rec(repo, report())
    assert "commit" in str(e.value) and "discard" in str(e.value)


def test_status_reports_code_changed_after_the_last_pass(repo):
    rec(repo, report())
    fix_and_commit(repo)
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "stale" and st["next_pass_in_run"] == 2
    assert "widget.py" in st["stale_reason"]
    assert "reviewed state changed" in rl._status_text(st)


def test_status_reports_stale_at_cap(repo):
    set_cap(repo, 1)
    rec(repo, report())
    fix_and_commit(repo)
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "stale-at-cap"
    assert "new-run --phase impl" in rl._status_text(st)


def test_status_reports_a_spec_edited_after_the_last_pass(repo):
    rec(repo, report(), phase="spec")
    p = repo / ".specdev" / "spec.md"
    p.write_text(SPEC.replace("first 20 rows", "first 25 rows"),
                 encoding="utf-8")
    assert rl.status(repo, "spec", repo)["state"] == "stale"


def test_status_stays_clean_when_only_bookkeeping_changed(repo):
    rec(repo, report())
    (repo / ".specdev" / "BUILD.md").write_text("wave 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bookkeeping")
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "clean" and st["stale_reason"] == ""


def test_refs_does_not_need_review_ledger(repo, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for f in TOOLS.glob("*.py"):
        if f.name != "review_ledger.py":
            (tools / f.name).write_bytes(f.read_bytes())
    p = subprocess.run(
        [sys.executable, str(tools / "build_outcome.py"), "--root", str(repo),
         "refs", "--unit", ".", "--feat", "FEAT-001"],
        capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "impl=" in p.stdout


def test_check_fails_closed_on_a_malformed_ledger(repo):
    doc = rl._empty("FEAT-007")
    doc["phases"]["impl"] = {"runs": [{"passes": [{}]}], "dismissed": []}
    rl.save(doc, repo)
    probs = check(repo)
    assert probs and isinstance(probs, list)


def test_a_non_finite_cap_is_refused(repo):
    set_cap(repo, float("inf"))
    with pytest.raises(rl.LedgerError, match="integer >= 1"):
        rl.max_iterations(repo, repo)


def test_render_says_dismissed_not_clean_when_findings_were_dismissed(repo):
    rec(repo, report(bug()))
    rl.dismiss(repo, "impl", "I1-1", "contradicts REQ-001 Out of Scope")
    out = rl.render(repo, "impl", repo)
    assert "was dismissed" in out and "was clean" not in out


def test_spec_reviewers_are_authorized_to_be_dispatched():
    assert "authorization to spawn" in _flat(NEW_FEATURE)
    skill = _flat(SKILL)
    section = skill[skill.index("## Pre-PR review loop"):]
    assert "never review inline" in section.lower()


def test_instructions_carry_the_exact_new_run_and_reviewed_commands():
    skill = _flat(SKILL)
    for s in ("new-run --phase <p>", "stale-at-cap", "--reviewed"):
        assert s in skill, s
    step5 = _between(skill, "5. **Open the Spec PR.**", "6. **Build on")
    assert "new-run --phase spec" in step5
    build = _flat(BUILD_CMD)
    after = build[build.index("## After the final wave"):]
    assert "--reviewed" in after and "stale-at-cap" in after
    nf = _flat(NEW_FEATURE)
    assert "--root <unit> dismiss --phase spec" in nf and "stale-at-cap" in nf
    for path in (SKILL, BUILD_CMD, NEW_FEATURE):
        assert re.search(r"new-run(?! --phase)", _flat(path)) is None, path


def test_ci_prompts_name_the_reviewers_and_the_asserted_loop():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((WF / "specdev-build.yml").read_text(encoding="utf-8"))
    build = doc["jobs"]["build"]
    cont = " ".join(build["env"]["CONTINUE_PROMPT"].split())
    agent1 = next(s for s in build["steps"] if s.get("id") == "agent1")
    prompt = " ".join(agent1["with"]["prompt"].split())
    for text in (cont, prompt):
        assert "code-reviewer" in text and "intent-reviewer" in text
    assert "the review loop is complete" in prompt


# ---- Follow-up fixes ------------------------------------------------------

def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_stale_with_open_findings_says_fix_or_dismiss_first(repo):
    set_cap(repo, 2)
    rec(repo, report(bug(), bug(where="widget.py:1", summary="other")))
    (repo / "widget.py").write_text("def page(n):\n    return n + 1\n",
                                    encoding="utf-8")
    git(repo, "commit", "-q", "-am", "partial fix")
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "stale"
    assert ("2 blocking finding(s) from the last pass are still open"
            in rl._status_text(st))


def test_docs_say_to_clear_open_findings_before_a_stale_pass():
    assert "fixed or dismissed, then run the next pass" in _flat(
        "skills/specdev/SKILL.md")
    assert "fix or dismiss every finding" in _flat("commands/build.md")


def test_skill_does_not_tell_the_coordinator_to_commit_side_effects():
    s = _flat("skills/specdev/SKILL.md")
    assert "never commit them" in s
    assert "commit first; it refuses a dirty tree" not in s


def test_stale_at_cap_with_unverifiable_commit_can_new_run(repo):
    set_cap(repo, 1)
    rec(repo, report(bug()))
    doc = rl.load(repo)
    doc["phases"]["impl"]["runs"][-1]["passes"][-1]["reviewed"] = "deadbeef" * 5
    rl.save(doc, repo)
    st = rl.status(repo, "impl", repo)
    assert st["state"] == "stale-at-cap" and st["stale_unverified"] is True
    assert "cannot be verified" in rl._status_text(st)
    rl.new_run(repo, "impl", repo_root=repo)
    assert rl.status(repo, "impl", repo)["run"] == 2


def test_a_huge_integer_cap_does_not_overflow(repo):
    set_cap(repo, 10**400)
    assert rl.max_iterations(repo, repo) == 10**400


def test_prod_fails_closed_when_the_ledger_tool_cannot_import(repo,
                                                              monkeypatch):
    _impl(repo)
    rec(repo, report())
    monkeypatch.setitem(sys.modules, "review_ledger", None)
    out = _verify(repo, "prod", monkeypatch)
    assert out["ok"] is False
    assert any("could not be imported" in p for p in out["problems"])


def test_cli_record_pins_reviewed_through_the_flag(repo):
    old = git(repo, "rev-parse", "HEAD").strip()
    fix_and_commit(repo)
    new = git(repo, "rev-parse", "HEAD").strip()

    def run(sha):
        return subprocess.run(
            [sys.executable, str(TOOL), "--root", str(repo), "record",
             "--phase", "impl", "--repo-root", str(repo), "--reviewed", sha,
             "--findings-json", "-"],
            input=json.dumps(report()), capture_output=True, text=True)
    p = run(old)
    assert p.returncode == 1 and "code changed between dispatch" in p.stderr
    p = run(new)
    assert p.returncode == 0, p.stderr
