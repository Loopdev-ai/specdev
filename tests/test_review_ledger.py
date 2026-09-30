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
