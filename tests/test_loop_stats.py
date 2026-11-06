"""loop_stats.py: the review loop's numbers across every feature, from git
history (which does not expire) plus the escapes artifacts (which do), with
escape counts carried forward between snapshots."""
import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "assets" / "specdev" / "tools"
sys.path.insert(0, str(TOOLS))


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


ls = load_mod(TOOLS / "loop_stats.py", "loop_stats_t")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=str(root), check=True,
                          capture_output=True, text=True).stdout.strip()


def ledger(feat, impl_passes=(), spec_passes=(), dismissed=()):
    """A schema-1 ledger. Each pass is a list of (severity, kind)."""
    def passes(ps, prefix):
        return [{"pass": i + 1, "at": "2026-11-01T00:00:00Z",
                 "reviewed": "x",
                 "findings": [{"id": f"{prefix}{i + 1}-{j + 1}",
                               "severity": sev, "kind": kind,
                               "where": "a.py:1", "summary": f"s{i}{j}",
                               "needs_human": kind == "spec"}
                              for j, (sev, kind) in enumerate(p)]}
                for i, p in enumerate(ps)]
    return {"schema_version": 1, "feat": feat, "phases": {
        "spec": {"runs": [{"started_at": "t",
                           "passes": passes(spec_passes, "S")}]
                 if spec_passes else [], "dismissed": []},
        "impl": {"runs": [{"started_at": "t",
                           "passes": passes(impl_passes, "I")}]
                 if impl_passes else [],
                 "dismissed": [{"id": i, "summary": "s", "reason": r,
                                "at": "t"} for i, r in dismissed]}}}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "README").write_text("x")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


def commit_ledger(root, doc, unit=".", msg="ledger", date=None):
    d = (root / unit / ".specdev") if unit != "." else root / ".specdev"
    d.mkdir(parents=True, exist_ok=True)
    (d / "review.json").write_text(json.dumps(doc), encoding="utf-8")
    git(root, "add", "-A")
    env = dict(os.environ)
    if date:
        env.update(GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(root),
                   check=True, env=env, capture_output=True)


B, M = ("blocking", "bug"), ("minor", "bug")


def test_each_feature_counts_once_with_its_final_ledger(repo):
    # FEAT-001 grows over two commits, then FEAT-002 replaces the file.
    commit_ledger(repo, ledger("FEAT-001", [[B, M]]),
                  date="2026-10-01T00:00:00Z")
    commit_ledger(repo, ledger("FEAT-001", [[B, M], [B], []],
                               dismissed=[("I2-1", "not-reproducible: x")]),
                  date="2026-10-02T00:00:00Z")
    commit_ledger(repo, ledger("FEAT-002", [[]], spec_passes=[[("blocking",
                                                                  "spec")]]),
                  date="2026-10-03T00:00:00Z")
    rows, warnings = ls.build_rows(repo, ("HEAD",))
    assert warnings == []
    by = {r["feat"]: r for r in rows}
    assert set(by) == {"FEAT-001", "FEAT-002"}, \
        "the replaced ledger must still be found in history"
    f1 = by["FEAT-001"]
    assert f1["impl_passes"] == 3 and f1["impl_blocking"] == 2
    assert f1["impl_minor"] == 1 and f1["impl_bug"] == 2
    assert f1["impl_dismissed"] == 1 and f1["impl_not_reproducible"] == 1
    assert f1["impl_final_clean"] is True
    assert by["FEAT-002"]["spec_passes"] == 1
    assert by["FEAT-002"]["spec_needs_human"] == 1
    assert [r["feat"] for r in rows] == ["FEAT-001", "FEAT-002"]


def test_monorepo_units_and_removed_units_are_found(repo):
    commit_ledger(repo, ledger("FEAT-001", [[]]), unit="svc/api")
    commit_ledger(repo, ledger("FEAT-001", [[B], []]), unit="web")
    git(repo, "rm", "-q", "-r", "web")
    git(repo, "commit", "-q", "-m", "drop web")
    rows, _ = ls.build_rows(repo, ("HEAD",))
    assert {(r["unit"], r["feat"]) for r in rows} == {
        ("svc/api", "FEAT-001"), ("web", "FEAT-001")}


def test_a_corrupt_ledger_version_is_skipped_with_a_warning(repo):
    d = repo / ".specdev"
    d.mkdir()
    (d / "review.json").write_text("{nope")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bad")
    commit_ledger(repo, ledger("FEAT-003", [[]]))
    rows, warnings = ls.build_rows(repo, ("HEAD",))
    assert [r["feat"] for r in rows] == ["FEAT-003"]
    assert warnings and "not valid JSON" in warnings[0]


def test_no_ledgers_is_an_empty_report_not_an_error(repo):
    rows, _ = ls.build_rows(repo, ("HEAD",))
    assert rows == []
    s = ls.summary(rows)
    assert s["features"] == 0 and s["impl"]["median_passes"] is None
    assert "0 feature(s)" in ls.render_md(rows, s, ("HEAD",), [])


def write_escapes(d, pr, head, bot, human):
    p = d / f"specdev-escapes-pr-{pr}"
    p.mkdir(parents=True)
    (p / "escapes.json").write_text(json.dumps(
        {"pr": pr, "head": head, "by_bot": bot, "by_human": human,
         "reviews_with_body": 1}))


def test_escapes_join_on_the_impl_branch_newest_pr_wins(repo, tmp_path):
    commit_ledger(repo, ledger("FEAT-001", [[]]))
    commit_ledger(repo, ledger("FEAT-002", [[]]), unit="svc/api")
    esc = tmp_path / "esc"
    write_escapes(esc, 10, "specdev/impl/root/FEAT-001", 7, 1)
    write_escapes(esc, 12, "specdev/impl/root/FEAT-001", 3, 0)
    write_escapes(esc, 11, "specdev/impl/svc/api/FEAT-002", 2, 2)
    write_escapes(esc, 13, "feature/unrelated", 99, 99)
    rows, _ = ls.build_rows(repo, ("HEAD",), escapes_dir=esc)
    by = {r["feat"]: r for r in rows}
    assert (by["FEAT-001"]["escapes_pr"], by["FEAT-001"]["escapes_bot"]) \
        == (12, 3)
    assert by["FEAT-002"]["escapes_human"] == 2
    assert by["FEAT-001"]["escapes_source"] == "artifact"


def test_escapes_carry_forward_from_the_previous_snapshot(repo, tmp_path):
    commit_ledger(repo, ledger("FEAT-001", [[]]))
    esc = tmp_path / "esc"
    write_escapes(esc, 10, "specdev/impl/root/FEAT-001", 4, 1)
    rows, _ = ls.build_rows(repo, ("HEAD",), escapes_dir=esc)
    prev = tmp_path / "prev.csv"
    ls.write_csv(rows, prev)
    # The artifact has expired: nothing in the escapes dir any more.
    rows, _ = ls.build_rows(repo, ("HEAD",), escapes_dir=tmp_path / "none",
                            previous=prev)
    assert rows[0]["escapes_bot"] == "4"
    assert rows[0]["escapes_source"] == "carried"
    assert "(carried)" in ls.render_md(rows, ls.summary(rows), ("HEAD",), [])


def test_cli_writes_csv_json_and_markdown(repo, tmp_path):
    commit_ledger(repo, ledger("FEAT-001", [[B], []],
                               spec_passes=[[], ]))
    out = tmp_path / "o"
    out.mkdir()
    p = subprocess.run(
        [sys.executable, str(TOOLS / "loop_stats.py"), "--repo-dir",
         str(repo), "--ref", "main", "--csv", str(out / "s.csv"),
         "--json", str(out / "s.json"), "--md", str(out / "s.md")],
        capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "# Review loop statistics" in p.stdout
    assert "median 2.0 pass(es)" in p.stdout
    assert "final pass clean in 100%" in p.stdout
    with open(out / "s.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["feat"] == "FEAT-001"
    assert rows[0]["impl_final_clean"] == "true"
    assert list(rows[0]) == ls.COLUMNS
    doc = json.loads((out / "s.json").read_text())
    assert doc["summary"]["impl"]["blocking_by_kind"]["bug"] == 1
    assert (out / "s.md").read_text() == p.stdout


def test_unit_and_ref_naming_matches_build_outcome():
    bo = load_mod(TOOLS / "build_outcome.py", "build_outcome_ls")
    for unit in (".", "", "svc/api", "./web/"):
        assert ls.ref_unit(unit) == bo.ref_unit(unit)
    assert ls.unit_of(".specdev/review.json") == "."
    assert ls.unit_of("svc/api/.specdev/review.json") == "svc/api"


# ---- the workflow ----------------------------------------------------------

def _wf():
    yaml = pytest.importorskip("yaml")
    path = ROOT / "assets" / "workflows" / "specdev-loop-stats.yml"
    return yaml.safe_load(path.read_text("utf-8")), path.read_text("utf-8")


def test_workflow_is_scheduled_and_least_privileged():
    doc, _ = _wf()
    on = doc.get(True, doc.get("on"))
    assert "schedule" in on and "workflow_dispatch" in on
    assert doc["permissions"] == {"contents": "write", "actions": "read"}


def test_workflow_writes_only_the_stats_branch():
    _, text = _wf()
    pushes = [ln for ln in text.splitlines() if "git push" in ln]
    assert pushes and all("refs/heads/${STATS_REF}" in ln for ln in pushes)
    assert "STATS_REF: specdev/stats" in text
    for forbidden in ("git checkout", "git commit ", "gh pr", "git add"):
        assert forbidden not in text, \
            f"{forbidden!r}: the default branch's checkout must not change"


def test_workflow_carries_escapes_forward_and_reads_the_default_branch():
    _, text = _wf()
    assert "--previous" in text and "loop-stats.csv" in text
    assert '--ref "origin/${DEFAULT_BRANCH}"' in text
    assert "specdev-escapes-pr-" in text and "select(.expired | not)" in text
