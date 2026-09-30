# Pre-PR Review Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before every Spec PR and Implementation PR, run up to `max_review_iterations` (default 10) passes of fresh reviewer subagents, loop blocking findings back to be fixed, and make that loop a mechanically asserted part of the pipeline.

**Architecture:** A new stdlib tool, `review_ledger.py`, owns `<unit>/.specdev/review.json` (record / dismiss / new-run / status / render / check). Three new read-only agents (`spec-reviewer`, `code-reviewer`, `intent-reviewer`) produce findings as JSON the tool ingests. `build_outcome.verify()` (prod) and `spec-validate.yml` (Spec PRs) call `check`; the skill, commands, templates and CI prompts drive the loop.

**Tech Stack:** Python 3.10+ stdlib (tools), pytest + PyYAML (tests), GitHub Actions YAML, Claude Code agent/skill Markdown.

**Design spec:** `docs/superpowers/specs/2026-09-29-pre-pr-review-loop-design.md`

## Global Constraints

- Tools are **stdlib-only**, Python **3.10+**, and start with the same version guard (pure-ASCII message) and UTF-8 stdout/stderr reconfigure as every sibling in `assets/specdev/tools/`.
- CLI shape matches the siblings: `--root <unit>` is top-level and **precedes** the subcommand; everything else follows it.
- The ledger is `<unit>/.specdev/review.json`, written **only** by `review_ledger.py`.
- `max_review_iterations`: `ci.json` key, default **10**, an integer **≥ 1**. `0` does **not** disable the loop.
- Finding ids: `S<pass>-<n>` (spec), `I<pass>-<n>` (impl). Pass numbers are unique across runs within a phase. Severities: `blocking | minor`. Kinds: `bug | security | intent | test | spec`.
- "What was reviewed": impl = a commit, compared over the **whole repo excluding `**/.specdev/**`**; spec = `sha256:` of the spec minus its `**Status:**`, `**Spec PR:**`, `**Author:**`, `**Date:**` lines and its `## Open Questions` section.
- Hand-off at the cap: impl → `PR_BODY.md` `## Unresolved review findings`; spec → `spec.md` `## Open Questions`. At the cap the last pass's findings are **not** fixed.
- The loop runs for the Spec PR when the profile's `spec_pr` is true and for the Implementation PR in `prod` mode. **poc runs neither.**
- Do **not** change any circuit-breaker or continuation limit. Do **not** add `## Original Request` to `validate_spec.py`.
- Test command (as CI runs it): `python -m pytest tests/ -q` — needs `python -m pip install pytest pyyaml`. Baseline on `main`: **536 passed**.
- Commit as `alaneff <alexander.a.neff@gmail.com>` (already configured repo-locally). End every commit message with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `assets/specdev/tools/review_ledger.py` | create | The ledger: record, dismiss, new-run, status, render, check |
| `assets/specdev/ci.json` | modify | `max_review_iterations: 10` |
| `assets/specdev/tools/run_manifest.py` | modify | `_FALLBACK_DEFAULTS` mirrors the new key |
| `assets/specdev/tools/build_outcome.py` | modify | prod terminal state includes `review_ledger.check(... "impl")` |
| `agents/spec-reviewer.md` | create | Spec review vs. the Original Request |
| `agents/code-reviewer.md` | create | Defect review of the implementation diff |
| `agents/intent-reviewer.md` | create | Per-REQ delivery/assertion review + Original Request |
| `agents/component-builder.md` | modify | Review-fix mode (reproduce first, `not-reproducible`) |
| `assets/specdev/spec.md` | modify | `## Original Request` section |
| `assets/specdev/PR_BODY.md` | modify | `## Review loop`, `## Unresolved review findings`, checklist line |
| `assets/specdev/BUILD.md` | modify | Review-loop resume pointer |
| `skills/specdev/SKILL.md` | modify | *Pre-PR review loop* section; steps 2/5/7; context, terminal state, guardrails, tools |
| `commands/build.md` | modify | Loop inside *After the final wave*; terminal state |
| `commands/new-feature.md` | modify | Capture the Original Request; spec-phase loop |
| `assets/workflows/spec-validate.yml` | modify | `check --phase spec` on `spec/**` heads |
| `assets/workflows/specdev-build.yml` | modify | Both prompts carry the loop |
| `.github/workflows/tests.yml` | modify | Install-smoke runs the installed tool |
| `commands/init.md`, `README.md`, `.sdlc/config.json` | modify | Docs + lint |
| `tests/test_review_ledger.py` | create | All new behaviour |
| `tests/test_pipeline_hardening.py`, `tests/test_profiles.py`, `tests/test_specdev_ci.py`, `tests/test_installed_layout.py` | modify | Fixtures + registrations for the new terminal state / agents / tool |

---

### Task 1: Ledger core — config key, record, dismiss, status

**Files:**
- Modify: `assets/specdev/ci.json`
- Modify: `assets/specdev/tools/run_manifest.py:96-111` (`_FALLBACK_DEFAULTS`)
- Create: `assets/specdev/tools/review_ledger.py`
- Test: `tests/test_review_ledger.py` (create)

**Interfaces:**
- Consumes: `run_manifest.ci_get(key, root=".", repo_root=None)` and `run_manifest._MISSING`.
- Produces (module `review_ledger`):
  - `class LedgerError(Exception)`
  - `ledger_path(root=".") -> Path`
  - `load(root=".") -> dict | None`, `save(doc: dict, root=".") -> None`
  - `max_iterations(root=".", repo_root=".") -> int`
  - `spec_substance(text: str) -> str`, `spec_hash(root=".") -> str`, `spec_feat(root=".") -> str | None`
  - `open_blocking(ph: dict) -> list[dict]`
  - `record(root=".", phase="impl", feat=None, reports=(), repo_root=".", head="HEAD") -> dict` (the pass entry: `{"pass", "at", "reviewed", "findings"}`)
  - `dismiss(root=".", phase="impl", finding_id="", reason="") -> dict`
  - `status(root=".", phase="impl", repo_root=".", feat=None) -> dict` with keys `phase, feat, cap, run, passes_in_run, next_pass_in_run, state, open_blocking, dismissed`; `state` ∈ `not-started | needs-fixes | clean | cap-reached`
  - internal helpers later tasks use: `_git(args, cwd)`, `_commit(rev, repo_root) -> str | None`, `_current_run(ph)`, `_last_pass(ph)`, `_ledger_for(root, feat)`, `_section(text, heading)`, `_phase(phase)`, `_now()`, `_parser()`, `_status_text(st)`, constants `NOT_CODE`, `HANDOFF`, `PHASES`, `SPEC_REL`, `PR_BODY_REL`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_ledger.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py -q`
Expected: collection ERROR — `FileNotFoundError` / `No such file` for `review_ledger.py` (the module does not exist yet).

- [ ] **Step 3: Add the config key**

In `assets/specdev/ci.json`, add `max_review_iterations` after `continuation_cap_usd`:

```json
{
  "schema_version": 1,
  "runner": "ubuntu-latest",
  "max_session_minutes": 300,
  "auto_resume": true,
  "max_permission_denials": 15,
  "max_denial_rate": 0.1,
  "max_consecutive_tool_failures": 15,
  "max_cost_usd": 10,
  "max_wall_minutes": 240,
  "max_tool_calls": 3000,
  "max_build_attempts": 3,
  "continuation_cap_usd": 25,
  "max_review_iterations": 10
}
```

In `assets/specdev/tools/run_manifest.py`, extend `_FALLBACK_DEFAULTS` (after `"continuation_cap_usd": 25,`):

```python
    "max_build_attempts": 3,
    "continuation_cap_usd": 25,
    # Pre-PR review loop: passes per run before a PR is handed off
    # (review_ledger.py). A quality gate, not a breaker limit, so it is not
    # in BREAKER_ENV either - and 0 does not disable it.
    "max_review_iterations": 10,
}
```

- [ ] **Step 4: Create the tool**

Create `assets/specdev/tools/review_ledger.py`:

```python
#!/usr/bin/env python3
"""Pre-PR review loop ledger: <unit>/.specdev/review.json.

Neither PR used to be READ by anything before a human opened it. qa-verifier
runs the tests the same component-builder wrote, so a bug the builder did not
think of passes green; validate_spec checks a spec's structure, never whether
it says what the user asked for. Copilot then found the bugs after the PR was
open. The fix is a bounded loop of fresh reviewer subagents before each PR -
and because the reported failure was the loop not running enough, the loop is
recorded here and ASSERTED (build_outcome.verify, spec-validate.yml) rather
than left to the coordinator's compliance.

One ledger per governed unit, one section per phase:

    spec  - the Spec PR loop. What was reviewed is a hash of the spec's
            substance (bookkeeping header lines and Open Questions excluded).
    impl  - the Implementation PR loop. What was reviewed is a commit.

A phase holds RUNS of PASSES. A pass is one review of one state; a run is
capped at ci.json's max_review_iterations. A new run is only for re-review
after the reviewed state changed (a human's edits on an open PR) - never to
buy more passes for the same code.

Only this tool writes the ledger, the way only adr-checker writes
org-compliance.json: ids are assigned here, counts come from the reviewers'
own JSON, and what was reviewed is stamped here rather than reported.

Usage (--root precedes the subcommand, as in the sibling tools):
    review_ledger.py --root <unit> record  --phase impl --findings-json F [...]
    review_ledger.py --root <unit> dismiss --phase impl --id I2-1 --reason TEXT
    review_ledger.py --root <unit> new-run --phase impl
    review_ledger.py --root <unit> status  --phase impl [--json]
    review_ledger.py --root <unit> render  --phase impl
    review_ledger.py --root <unit> check   --phase impl [--feat FEAT-###]
Every subcommand takes --repo-root (ci.json fallback and git working tree).
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# SpecDev tools use PEP 604 unions (`dict | None`) in annotations, which are
# evaluated at def time and raise TypeError on Python 3.9. macOS ships 3.9.x as
# the system python3, so without this guard every tool dies with an opaque
# "unsupported operand type(s) for |". Checked before the sibling imports below,
# which carry the same annotations. The message is deliberately pure ASCII: this
# runs before the stdout UTF-8 reconfigure, so a non-ASCII character here would
# raise UnicodeEncodeError on a cp1252 console and replace the explanation with
# a traceback.
if sys.version_info < (3, 10):
    raise SystemExit(
        "SpecDev tools require Python 3.10+ (found "
        f"{sys.version_info.major}.{sys.version_info.minor}). "
        "On macOS the system python3 is 3.9.x; install a newer Python or use "
        "a virtualenv. In CI, actions/setup-python with python-version '3.x' "
        "satisfies this."
    )

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_manifest  # noqa: E402  (vendored sibling module)

SCHEMA_VERSION = 1
LEDGER_REL = ".specdev/review.json"
SPEC_REL = ".specdev/spec.md"
PR_BODY_REL = ".specdev/PR_BODY.md"
PHASES = ("spec", "impl")
ID_PREFIX = {"spec": "S", "impl": "I"}
SEVERITIES = ("blocking", "minor")
KINDS = ("bug", "security", "intent", "test", "spec")
MAX_KEY = "max_review_iterations"
FEAT_RE = re.compile(r"^FEAT-\d{3,}$")

# Reviewed code is everything EXCEPT SpecDev's own bookkeeping. The ledger,
# BUILD.md and PR_BODY.md are written after the last pass by design, in any
# unit, so they must never make a review stale or a tree dirty.
NOT_CODE = ":(glob,exclude)**/.specdev/**"

# Where a capped run's open findings are handed to the human, per phase.
HANDOFF = {"impl": (PR_BODY_REL, "Unresolved review findings"),
           "spec": (SPEC_REL, "Open Questions")}


class LedgerError(Exception):
    """A refusal. The message is shown to the coordinator verbatim."""


def ledger_path(root=".") -> Path:
    return Path(root) / LEDGER_REL


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty(feat: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "feat": feat,
            "phases": {p: {"runs": [], "dismissed": []} for p in PHASES}}


def load(root=".") -> dict | None:
    p = ledger_path(root)
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise LedgerError(f"{p.as_posix()} is not valid JSON: {e}") from e
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise LedgerError(f"{p.as_posix()} is not a schema_version "
                          f"{SCHEMA_VERSION} review ledger")
    phases = doc.setdefault("phases", {})
    for ph in PHASES:
        phases.setdefault(ph, {"runs": [], "dismissed": []})
    return doc


def save(doc: dict, root=".") -> None:
    p = ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _phase(phase: str) -> str:
    if phase not in PHASES:
        raise LedgerError(f"phase must be one of {PHASES}, got {phase!r}")
    return phase


def max_iterations(root=".", repo_root=".") -> int:
    """The per-run cap, from ci.json (unit first, repo root as fallback).

    An integer >= 1. Zero is refused rather than read as "off": this is a
    quality gate, not a ceiling, and an off switch is the escape hatch the
    loop exists to close."""
    val = run_manifest.ci_get(MAX_KEY, root, repo_root=repo_root)
    if val is run_manifest._MISSING:
        raise LedgerError(f"ci.json has no {MAX_KEY}")
    if (isinstance(val, bool) or not isinstance(val, (int, float))
            or int(val) != val or int(val) < 1):
        raise LedgerError(f"{MAX_KEY} must be an integer >= 1, got {val!r} "
                          f"- 0 does not disable the review loop")
    return int(val)


def _section(text: str, heading: str) -> str | None:
    """The body of '## <heading>' up to the next '## ', or None if absent."""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s+\S|\Z)",
                  text, re.M | re.S)
    return m.group(1) if m else None


_BOOKKEEPING = re.compile(r"^\*\*(Status|Spec PR|Author|Date):\*\*.*$", re.M)


def spec_substance(text: str) -> str:
    """The spec minus its bookkeeping: the Status/Spec PR/Author/Date header
    lines (filled in as the PR progresses) and the Open Questions section
    (where a capped run hands off its unresolved findings). Any other edit is
    a change to what was reviewed."""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"^##\s+Open Questions\s*$.*?(?=^##\s+\S|\Z)", "", text,
                  flags=re.M | re.S)
    text = _BOOKKEEPING.sub("", text)
    return "\n".join(ln.rstrip() for ln in text.strip().splitlines())


def spec_hash(root=".") -> str:
    p = Path(root) / SPEC_REL
    if not p.exists():
        raise LedgerError(f"{p.as_posix()} does not exist - there is no spec "
                          f"to review")
    body = spec_substance(p.read_text(encoding="utf-8-sig"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def spec_feat(root=".") -> str | None:
    p = Path(root) / SPEC_REL
    if not p.exists():
        return None
    m = re.search(r"\*\*Feature ID:\*\*\s*(FEAT-\d{3,})\b",
                  p.read_text(encoding="utf-8-sig"))
    return m.group(1) if m else None


def _git(args: list, cwd=".") -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd),
                           capture_output=True, text=True)
    except (FileNotFoundError, OSError, NotADirectoryError):
        return 1, ""
    return p.returncode, p.stdout


def _commit(rev: str, repo_root=".") -> str | None:
    rc, out = _git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                   repo_root)
    return out.strip() if rc == 0 and out.strip() else None


def dirty_code(repo_root=".") -> list[str]:
    """Tracked, uncommitted changes outside .specdev/. Untracked files are
    ignored: they are in no commit, so in no PR."""
    rc, out = _git(["status", "--porcelain", "-uno", "--", ".", NOT_CODE],
                   repo_root)
    return [ln[3:] for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def _findings_of(report) -> list:
    if isinstance(report, list):
        return [f for r in report for f in _findings_of(r)]
    if isinstance(report, dict) and isinstance(report.get("findings"), list):
        return report["findings"]
    raise LedgerError('a reviewer report must be {"findings": [...]} or a '
                      'list of them')


def _normalize(raw, n: int, pass_no: int, phase: str) -> dict:
    if not isinstance(raw, dict):
        raise LedgerError(f"finding {n} is not an object")
    sev = str(raw.get("severity", "")).strip().lower()
    if sev not in SEVERITIES:
        raise LedgerError(f"finding {n}: severity must be one of "
                          f"{SEVERITIES}, got {raw.get('severity')!r}")
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in KINDS:
        raise LedgerError(f"finding {n}: kind must be one of {KINDS}, got "
                          f"{raw.get('kind')!r}")
    summary = str(raw.get("summary", "")).strip()
    where = str(raw.get("where", "")).strip()
    if not summary:
        raise LedgerError(f"finding {n}: summary is required")
    if sev == "blocking" and not where:
        raise LedgerError(f"finding {n}: a blocking finding must say where "
                          f"(file:line, REQ-###, or a section)")
    return {"id": f"{ID_PREFIX[phase]}{pass_no}-{n}", "severity": sev,
            "kind": kind, "where": where, "summary": summary,
            "scenario": str(raw.get("scenario", "")).strip(),
            "fix": str(raw.get("fix", "")).strip(),
            "needs_human": bool(raw.get("needs_human", False))}


def _current_run(ph: dict) -> dict | None:
    return ph["runs"][-1] if ph["runs"] else None


def _last_pass(ph: dict) -> dict | None:
    for run in reversed(ph["runs"]):
        if run["passes"]:
            return run["passes"][-1]
    return None


def open_blocking(ph: dict) -> list[dict]:
    """Blocking findings of the current run's LAST pass, minus dismissals.

    Earlier passes' findings are not carried: every pass is a fresh review of
    the whole state, so a finding that was not really fixed is raised again -
    under a new id - by the pass that follows the fix."""
    run = _current_run(ph)
    if not run or not run["passes"]:
        return []
    gone = {d["id"] for d in ph["dismissed"]}
    return [f for f in run["passes"][-1]["findings"]
            if f["severity"] == "blocking" and f["id"] not in gone]


def record(root=".", phase="impl", feat=None, reports=(), repo_root=".",
           head="HEAD") -> dict:
    """Append the next pass to the current run. Returns the pass entry."""
    _phase(phase)
    feat = feat or spec_feat(root)
    if not feat or not FEAT_RE.match(feat):
        raise LedgerError(f"a FEAT-### id is required (none given and none in "
                          f"{SPEC_REL}), got {feat!r}")
    cap = max_iterations(root, repo_root)
    doc = load(root)
    if doc is None or doc.get("feat") != feat:
        # A new feature starts a new ledger; git history keeps the old one.
        doc = _empty(feat)
    ph = doc["phases"][phase]
    if phase == "impl":
        dirty = dirty_code(repo_root)
        if dirty:
            raise LedgerError(
                f"uncommitted changes outside .specdev/ ({', '.join(dirty[:5])}"
                f"{' ...' if len(dirty) > 5 else ''}). Reviewers only review "
                f"committed code - commit, then record the pass.")
        reviewed = _commit(head, repo_root)
        if not reviewed:
            raise LedgerError(f"cannot resolve {head!r} to a commit in "
                              f"{Path(repo_root).as_posix()}")
    else:
        reviewed = spec_hash(root)
    run = _current_run(ph)
    if run is None:
        run = {"started_at": _now(), "passes": []}
        ph["runs"].append(run)
    if len(run["passes"]) >= cap:
        raise LedgerError(
            f"this {phase} run already has {len(run['passes'])} of {cap} "
            f"passes - the cap is reached. Hand the open findings off; "
            f"'new-run' is only for re-review after the reviewed state "
            f"changes.")
    pass_no = 1 + sum(len(r["passes"]) for r in ph["runs"])
    raw = [f for rep in reports for f in _findings_of(rep)]
    entry = {"pass": pass_no, "at": _now(), "reviewed": reviewed,
             "findings": [_normalize(f, i + 1, pass_no, phase)
                          for i, f in enumerate(raw)]}
    run["passes"].append(entry)
    save(doc, root)
    return entry


def dismiss(root=".", phase="impl", finding_id="", reason="") -> dict:
    """Adjudicate a finding as deliberately not acted on. The reason is shown
    to every later reviewer and to the human reading the PR, which is what
    stops a false positive from thrashing the loop to its cap."""
    _phase(phase)
    doc = load(root)
    if doc is None:
        raise LedgerError("no review ledger - nothing to dismiss")
    ph = doc["phases"][phase]
    reason = (reason or "").strip()
    if not reason:
        raise LedgerError("a dismissal needs a reason; it is shown to every "
                          "later reviewer and to the human reading the PR")
    found = next((f for r in ph["runs"] for p in r["passes"]
                  for f in p["findings"] if f["id"] == finding_id), None)
    if found is None:
        raise LedgerError(f"no {phase} finding {finding_id!r}")
    if any(d["id"] == finding_id for d in ph["dismissed"]):
        raise LedgerError(f"{finding_id} is already dismissed")
    entry = {"id": finding_id, "summary": found["summary"], "reason": reason,
             "at": _now()}
    ph["dismissed"].append(entry)
    save(doc, root)
    return entry


def _ledger_for(root=".", feat=None) -> dict | None:
    """The ledger if it belongs to this feature, else None. A previous
    feature's ledger stays on disk until the first record() replaces it, and
    must never read as this feature's progress."""
    doc = load(root)
    feat = feat or spec_feat(root)
    if doc is not None and feat and doc.get("feat") != feat:
        return None
    return doc


def status(root=".", phase="impl", repo_root=".", feat=None) -> dict:
    """The resume point: where the current run is, what is open, and what
    has already been adjudicated (for the next reviewers' prompts)."""
    _phase(phase)
    cap = max_iterations(root, repo_root)
    doc = _ledger_for(root, feat)
    ph = doc["phases"][phase] if doc else {"runs": [], "dismissed": []}
    run = _current_run(ph)
    n = len(run["passes"]) if run else 0
    open_ = open_blocking(ph)
    if n == 0:
        state = "not-started"
    elif not open_:
        state = "clean"
    elif n >= cap:
        state = "cap-reached"
    else:
        state = "needs-fixes"
    return {"phase": phase, "feat": doc.get("feat") if doc else None,
            "cap": cap, "run": len(ph["runs"]), "passes_in_run": n,
            "next_pass_in_run": n + 1 if n < cap else None, "state": state,
            "open_blocking": open_, "dismissed": ph["dismissed"]}


def _read_reports(paths) -> list:
    reports = []
    for p in paths:
        raw = (sys.stdin.read() if p == "-"
               else Path(p).read_text(encoding="utf-8-sig"))
        try:
            reports.append(json.loads(raw))
        except json.JSONDecodeError as e:
            raise LedgerError(f"{p}: not valid JSON ({e})") from e
    return reports


def _status_text(st: dict) -> str:
    lines = [f"{st['phase']} review loop for "
             f"{st['feat'] or '(no ledger yet)'}: {st['state']} - "
             f"{st['passes_in_run']} of {st['cap']} passes in the current run"]
    if st["state"] in ("not-started", "needs-fixes"):
        lines.append(f"next: pass {st['next_pass_in_run']} of {st['cap']}")
    for f in st["open_blocking"]:
        lines.append(f"OPEN {f['id']} [{f['kind']}] {f['where']} - "
                     f"{f['summary']}")
    if st["dismissed"]:
        lines.append("Already adjudicated - give these to every reviewer; "
                     "do not re-raise without new evidence:")
        lines += [f"  {d['id']} {d['summary']} -- dismissed: {d['reason']}"
                  for d in st["dismissed"]]
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".",
                    help="the governed unit ('.' in a single-unit repo)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--phase", required=True, choices=PHASES)
        p.add_argument("--repo-root", default=".",
                       help="repo root: ci.json fallback and git working tree")
        return p

    p = add("record", "record one review pass")
    p.add_argument("--feat", default=None,
                   help="FEAT-### (default: the spec's Feature ID)")
    p.add_argument("--findings-json", action="append", required=True,
                   help="one reviewer's closing JSON block; '-' reads stdin; "
                        "repeat once per reviewer")
    p = add("dismiss", "adjudicate a finding as not acted on, with the reason")
    p.add_argument("--id", required=True)
    p.add_argument("--reason", required=True)
    p = add("status", "the resume point: next pass, open and dismissed "
                      "findings")
    p.add_argument("--feat", default=None)
    p.add_argument("--json", action="store_true")
    return ap


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.cmd == "record":
            entry = record(args.root, args.phase, args.feat,
                           _read_reports(args.findings_json), args.repo_root)
            blocking = [f for f in entry["findings"]
                        if f["severity"] == "blocking"]
            print(f"recorded {args.phase} pass {entry['pass']}: "
                  f"{len(blocking)} blocking, "
                  f"{len(entry['findings']) - len(blocking)} minor")
            for f in entry["findings"]:
                print(f"  {f['id']} {f['severity']} [{f['kind']}] "
                      f"{f['where']} - {f['summary']}")
            return 0
        if args.cmd == "dismiss":
            d = dismiss(args.root, args.phase, args.id, args.reason)
            print(f"dismissed {d['id']}: {d['reason']}")
            return 0
        if args.cmd == "status":
            st = status(args.root, args.phase, args.repo_root, args.feat)
            print(json.dumps(st, indent=2) if args.json else _status_text(st))
            return 0
    except LedgerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_review_ledger.py -q`
Expected: all Task 1 tests PASS.

Run: `python -m pytest tests/ -q`
Expected: 536 + the new tests pass (the existing `test_limit_defaults_have_exactly_one_source_of_truth` now also covers `max_review_iterations`).

- [ ] **Step 6: Commit**

```bash
git add assets/specdev/ci.json assets/specdev/tools/run_manifest.py assets/specdev/tools/review_ledger.py tests/test_review_ledger.py
git commit -m "feat(review): review ledger core - record, dismiss, status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Ledger gate — staleness, new-run, check, render

**Files:**
- Modify: `assets/specdev/tools/review_ledger.py` (add functions after `status()`; extend `_parser()` and `main()`)
- Test: `tests/test_review_ledger.py` (append)

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces:
  - `new_run(root=".", phase="impl", head="HEAD", repo_root=".") -> dict`
  - `check(root=".", phase="impl", feat=None, head="HEAD", repo_root=".") -> list[str]` — empty list = pass. `head` lets `build_outcome.verify` pass the implementation branch tip.
  - `render(root=".", phase="impl", repo_root=".", feat=None) -> str` — Markdown with `## Review loop` and `## Unresolved review findings`.
  - CLI subcommands `new-run`, `render`, `check` (`check` exits 1 with `ERROR:` lines, 0 with `... review loop ok - ...`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_ledger.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py -q`
Expected: the new tests FAIL with `AttributeError: module 'review_ledger_t' has no attribute 'check'` (and `new_run` / `render`); `test_cli_check_exit_codes` fails with an argparse `invalid choice: 'check'`.

- [ ] **Step 3: Add the gate functions**

In `assets/specdev/tools/review_ledger.py`, insert after `status()` (before `_read_reports`):

```python
def _staleness(root, phase: str, ph: dict, head="HEAD",
               repo_root=".") -> tuple[bool | None, str]:
    """(stale, why). stale is None when it could not be determined - which
    check() treats as a failure, never as a pass."""
    last = _last_pass(ph)
    if last is None:
        return None, f"no {phase} review pass is recorded"
    if phase == "spec":
        if spec_hash(root) != last["reviewed"]:
            return True, (f"the spec changed after pass {last['pass']} "
                          f"reviewed it")
        return False, ""
    reviewed = last["reviewed"]
    a, b = _commit(reviewed, repo_root), _commit(head, repo_root)
    if not a or not b:
        missing = reviewed if not a else head
        return None, (f"cannot resolve {missing!r} here - the reviewed commit "
                      f"is not in this clone's history, so what was reviewed "
                      f"cannot be compared with what would be handed off")
    rc, out = _git(["diff", "--name-only", a, b, "--", ".", NOT_CODE],
                   repo_root)
    if rc != 0:
        return None, f"git diff {a[:12]}..{b[:12]} failed"
    changed = [ln for ln in out.splitlines() if ln.strip()]
    if changed:
        return True, (f"{len(changed)} path(s) outside .specdev/ changed "
                      f"after pass {last['pass']} reviewed {a[:12]}: "
                      f"{', '.join(changed[:5])}"
                      f"{' ...' if len(changed) > 5 else ''}")
    return False, ""


def new_run(root=".", phase="impl", head="HEAD", repo_root=".") -> dict:
    """Start a fresh run - for re-review after the reviewed state changed
    (a human's edits on an open PR, an org-ADR fix after a capped run).
    Refused when nothing changed: a new run never buys more passes for the
    same state."""
    _phase(phase)
    doc = load(root)
    if doc is None:
        raise LedgerError("no review ledger - record pass 1 instead")
    ph = doc["phases"][phase]
    run = _current_run(ph)
    if not run or not run["passes"]:
        raise LedgerError(f"the current {phase} run has no passes yet - "
                          f"record one instead")
    stale, why = _staleness(root, phase, ph, head, repo_root)
    if stale is None:
        raise LedgerError(why)
    if not stale:
        raise LedgerError(
            f"nothing changed since pass {run['passes'][-1]['pass']} was "
            f"reviewed. A new run is for re-review after the reviewed state "
            f"changes; it never buys more passes for the same "
            f"{'spec' if phase == 'spec' else 'code'}.")
    fresh = {"started_at": _now(), "passes": []}
    ph["runs"].append(fresh)
    save(doc, root)
    return fresh


def _handoff(root, phase: str) -> tuple[str, str]:
    rel, heading = HANDOFF[phase]
    p = Path(root) / rel
    text = p.read_text(encoding="utf-8-sig") if p.exists() else ""
    return f"{p.as_posix()} '## {heading}'", (_section(text, heading) or "")


def _listed(finding_id: str, text: str) -> bool:
    """`finding_id` as a whole id: I1-1 is not listed by I1-12 or I11-1."""
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){re.escape(finding_id)}(?![0-9])", text))


def check(root=".", phase="impl", feat=None, head="HEAD",
          repo_root=".") -> list[str]:
    """Why the review loop is NOT complete for this phase. Empty = complete.

    Fails when the loop never ran, when what would be handed off differs from
    what the last pass reviewed, when the loop stopped short of the cap with
    blocking findings open, or when a capped run's open findings were not
    handed to the human."""
    _phase(phase)
    try:
        cap = max_iterations(root, repo_root)
        doc = load(root)
    except LedgerError as e:
        return [str(e)]
    if doc is None:
        return [f"no review ledger at {ledger_path(root).as_posix()} - the "
                f"{phase} review loop never ran"]
    feat = feat or spec_feat(root)
    if feat and doc.get("feat") != feat:
        return [f"the review ledger is for {doc.get('feat')}, not {feat} - "
                f"the {phase} review loop has not run for this feature"]
    ph = doc["phases"][phase]
    run = _current_run(ph)
    if not run or not run["passes"]:
        return [f"no {phase} review pass is recorded in the current run - "
                f"the {phase} review loop never ran"]
    problems = []
    try:
        stale, why = _staleness(root, phase, ph, head, repo_root)
    except LedgerError as e:
        stale, why = None, str(e)
    if stale is None:
        problems.append(why)
    elif stale:
        problems.append(f"{why}. The last review no longer covers what would "
                        f"be handed off - run another pass (or 'new-run' if "
                        f"the current run hit its cap).")
    open_ = open_blocking(ph)
    n = len(run["passes"])
    if open_ and n < cap:
        problems.append(
            f"the {phase} review loop stopped after {n} of {cap} passes with "
            f"{len(open_)} blocking finding(s) open "
            f"({', '.join(f['id'] for f in open_)}) - fix them and run the "
            f"next pass")
    elif open_:
        where, text = _handoff(root, phase)
        missing = [f["id"] for f in open_ if not _listed(f["id"], text)]
        if missing:
            problems.append(
                f"the review cap ({cap}) was reached with blocking findings "
                f"open, but {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} not listed in "
                f"{where} - an unresolved finding is handed to the human, "
                f"never dropped")
    return problems


def render(root=".", phase="impl", repo_root=".", feat=None) -> str:
    """The PR body's '## Review loop' and '## Unresolved review findings'
    sections, from the ledger - so the hand-off is generated, not
    transcribed."""
    st = status(root, phase, repo_root, feat)
    doc = _ledger_for(root, feat)
    ph = doc["phases"][phase] if doc else {"runs": [], "dismissed": []}
    run = _current_run(ph)
    lines = ["## Review loop", ""]
    if not run or not run["passes"]:
        lines.append("_The review loop has not run._")
    else:
        lines.append(f"{len(run['passes'])} of {st['cap']} review pass(es) "
                     f"in the current run; final state: **{st['state']}**.")
        lines += ["", "| Pass | Reviewed | Blocking | Minor |",
                  "|---|---|---|---|"]
        for p in run["passes"]:
            blocking = sum(f["severity"] == "blocking" for f in p["findings"])
            minor = sum(f["severity"] == "minor" for f in p["findings"])
            shown = p["reviewed"].removeprefix("sha256:")[:12]
            lines.append(f"| {p['pass']} | `{shown}` | {blocking} | {minor} |")
        if ph["dismissed"]:
            lines += ["", "**Dismissed (not acted on, with the reason):**", ""]
            lines += [f"- `{d['id']}` {d['summary']} — {d['reason']}"
                      for d in ph["dismissed"]]
    lines += ["", "## Unresolved review findings", ""]
    if st["open_blocking"]:
        for f in st["open_blocking"]:
            lines.append(f"- `{f['id']}` ({f['kind']}) {f['where']} — "
                         f"{f['summary']}"
                         + (f" Scenario: {f['scenario']}"
                            if f["scenario"] else ""))
    else:
        lines.append("None — the final review pass was clean.")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Wire the CLI**

In `_parser()`, insert before `return ap`:

```python
    add("new-run", "start a fresh run after the reviewed state changed")
    p = add("render", "the PR body's Review loop + Unresolved sections")
    p.add_argument("--feat", default=None)
    p = add("check", "assert the loop is complete (the gate)")
    p.add_argument("--feat", default=None)
    p.add_argument("--head", default="HEAD",
                   help="the commit that would be handed off (impl)")
```

In `main()`, insert inside the `try:` block after the `status` branch:

```python
        if args.cmd == "new-run":
            new_run(args.root, args.phase, repo_root=args.repo_root)
            st = status(args.root, args.phase, args.repo_root)
            print(f"started {args.phase} run {st['run']} - "
                  f"{st['cap']} passes available")
            return 0
        if args.cmd == "render":
            sys.stdout.write(render(args.root, args.phase, args.repo_root,
                                    args.feat))
            return 0
        if args.cmd == "check":
            problems = check(args.root, args.phase, args.feat, args.head,
                             args.repo_root)
            for p in problems:
                print(f"ERROR: {p}", file=sys.stderr)
            if problems:
                return 1
            st = status(args.root, args.phase, args.repo_root, args.feat)
            print(f"{args.phase} review loop ok - {st['state']} after "
                  f"{st['passes_in_run']} of {st['cap']} passes in the "
                  f"current run")
            return 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_review_ledger.py -q`
Expected: all Task 1 + Task 2 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add assets/specdev/tools/review_ledger.py tests/test_review_ledger.py
git commit -m "feat(review): ledger gate - staleness, new-run, check, render

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The prod terminal state asserts the review loop

**Files:**
- Modify: `assets/specdev/tools/build_outcome.py` (import after the UTF-8 block ~line 96; module docstring lines 12-13; `verify()` lines 502-516)
- Modify: `tests/test_pipeline_hardening.py:216-226` (`push_impl`)
- Modify: `tests/test_profiles.py:991-1013` (`_findings_repo`)
- Test: `tests/test_review_ledger.py` (append)

**Interfaces:**
- Consumes: `review_ledger.check(root, phase, feat=None, head="HEAD", repo_root=".") -> list[str]`.
- Produces: `build_outcome.verify(...)` — unchanged signature; for `mode == "prod"` with a resolved implementation branch, its `problems` now include the review check's problems (checked against the branch **tip**), and `required_terminal_state` names the review loop.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_ledger.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py -q -k "terminal_state or pushed_after"`
Expected: `test_prod_terminal_state_requires_the_review_loop` FAILS (`ok` is True with no ledger); `test_prod_rejects_code_pushed_after_the_last_review` FAILS (`ok` is True).

- [ ] **Step 3: Wire the check into `verify()`**

In `assets/specdev/tools/build_outcome.py`, directly after the UTF-8 reconfigure `try/except` block (before `BUILD_REL = ...`), add:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_ledger  # noqa: E402  (vendored sibling module)
```

In the module docstring, replace:

```
    prod: an implementation BRANCH for this unit+FEAT was pushed by this run,
          carrying commits beyond the base, with a prepared PR body
```

with:

```
    prod: an implementation BRANCH for this unit+FEAT was pushed by this run,
          carrying commits beyond the base, with a prepared PR body - and the
          pre-PR review loop recorded over the branch tip: a clean final pass,
          or max_review_iterations reached with every open finding listed in
          PR_BODY.md (review_ledger.check)
```

In `verify()`, replace:

```python
    branch = branch_evidence(repo_dir, feat=feat, unit=unit, base=base,
                             since=since, author=authors)
    problems += branch["problems"]
    warnings += branch["warnings"]
```

with:

```python
    branch = branch_evidence(repo_dir, feat=feat, unit=unit, base=base,
                             since=since, author=authors)
    problems += branch["problems"]
    warnings += branch["warnings"]

    if mode == "prod" and branch.get("sha"):
        # The pre-PR review loop is part of the prod terminal state: without
        # it, nothing read the code before the PR but the tests its own
        # builder wrote. Checked against the BRANCH TIP - the thing a human
        # opens the PR from - so code committed after the last review pass
        # fails here. poc has no PR, so no loop.
        problems += review_ledger.check(root, "impl", feat=feat,
                                        head=branch["sha"],
                                        repo_root=repo_dir)
```

And replace the `required = (...)` expression with:

```python
    required = (f"implementation branch '{branch['ref']}' pushed with commits "
                f"beyond '{base}', and a prepared PR body — a human opens the "
                f"PR" + (" (poc deploys from this branch directly), and "
                         f"BUILD.md's '## Findings' section filled in — a "
                         f"spike's deliverable is what it taught you"
                         if mode == "poc" else
                         ", after the pre-PR review loop recorded a clean "
                         "final pass over the branch tip — or reached "
                         "max_review_iterations with every open finding "
                         "listed in PR_BODY.md"))
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest tests/test_review_ledger.py -q`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: FAILURES in `tests/test_pipeline_hardening.py` (e.g. `test_a_pushed_branch_with_real_commits_is_the_terminal_state`, `test_an_ordinary_commit_subject_is_not_mistaken_for_a_skip_marker`, `test_a_resumed_dispatch_still_recognises_the_branch_it_already_pushed`, `test_no_pr_is_required_and_no_pr_can_satisfy_the_assertion`) and `tests/test_profiles.py::test_prod_verify_is_unaffected_by_an_absent_findings_section` — their prod builds now lack a review ledger. That is the new terminal state working; Step 5 updates the fixtures.

- [ ] **Step 5: Give the existing prod fixtures a clean review ledger**

In `tests/test_pipeline_hardening.py`, add this helper directly above `push_impl` and replace `push_impl` with the version below:

```python
def write_clean_review_ledger(root, feat, reviewed):
    """What review_ledger.py leaves after one clean impl pass. The prod
    terminal state includes it (tests/test_review_ledger.py covers the loop
    itself); written directly so these tests stay about branches and bodies."""
    (root / ".specdev" / "review.json").write_text(json.dumps({
        "schema_version": 1, "feat": feat,
        "phases": {"spec": {"runs": [], "dismissed": []},
                   "impl": {"runs": [{"started_at": RUN_START, "passes": [
                       {"pass": 1, "at": RUN_START, "reviewed": reviewed,
                        "findings": []}]}], "dismissed": []}}}),
        encoding="utf-8")


def push_impl(root, feat="FEAT-002", unit=".", when=WORK_PUSHED,
              msg="feat(widget): REQ-001", name="specdev-bot",
              email=BOT_EMAIL, body="widget"):
    """Create the implementation ref the workflow pushes, with a commit, and
    the clean review ledger a prod build records over that commit."""
    ref = bo.implementation_ref(unit, feat)
    git(root, "checkout", "-q", "-B", ref, "main")
    (root / "widget.py").write_text(body, encoding="utf-8")
    git_at(root, when, "add", "-A", name=name, email=email)
    git_at(root, when, "commit", "-m", msg, name=name, email=email)
    git(root, "checkout", "-q", "main")
    tip = subprocess.run(["git", "rev-parse", ref], cwd=str(root), check=True,
                         capture_output=True, text=True).stdout.strip()
    write_clean_review_ledger(root, feat, tip)
    return ref
```

In `tests/test_profiles.py`, in `_findings_repo`, replace:

```python
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat(widget): REQ-001")
    _git(root, "checkout", "-q", "main")
    return root, bo
```

with:

```python
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat(widget): REQ-001")
    tip = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                         check=True, capture_output=True,
                         text=True).stdout.strip()
    _git(root, "checkout", "-q", "main")
    # A prod build's terminal state includes the pre-PR review loop; one
    # clean pass over the branch tip keeps the prod test in this group about
    # Findings (which prod must ignore), not about reviews.
    (root / ".specdev" / "review.json").write_text(json.dumps({
        "schema_version": 1, "feat": "FEAT-900",
        "phases": {"spec": {"runs": [], "dismissed": []},
                   "impl": {"runs": [{"started_at": "2026-09-29T00:00:00Z",
                                      "passes": [{"pass": 1,
                                                  "at": "2026-09-29T00:00:00Z",
                                                  "reviewed": tip,
                                                  "findings": []}]}],
                            "dismissed": []}}}), encoding="utf-8")
    return root, bo
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add assets/specdev/tools/build_outcome.py tests/test_review_ledger.py tests/test_pipeline_hardening.py tests/test_profiles.py
git commit -m "feat(build): the prod terminal state asserts the pre-PR review loop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Reviewer agents and component-builder review-fix mode

**Files:**
- Create: `agents/spec-reviewer.md`, `agents/code-reviewer.md`, `agents/intent-reviewer.md`
- Modify: `agents/component-builder.md` (append a section)
- Modify: `tests/test_specdev_ci.py:198-202` (`test_vendor_sources_exist`)
- Test: `tests/test_review_ledger.py` (append)

**Interfaces:**
- Consumes: the finding JSON shape `review_ledger.record` accepts: `{"findings": [{"severity", "kind", "where", "summary", "scenario", "fix", "needs_human"}]}`.
- Produces: agent types `spec-reviewer`, `code-reviewer`, `intent-reviewer` (tools `Read, Grep, Glob, Bash` — all inside the CI session allowlist, which `test_allowlist_is_a_superset_of_every_vendored_agent_grant` checks automatically).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_ledger.py`:

```python
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
```

In `tests/test_specdev_ci.py`, replace the agent list in `test_vendor_sources_exist`:

```python
    for a in ["component-builder", "qa-verifier", "adr-checker",
              "spec-explorer", "spec-reviewer", "code-reviewer",
              "intent-reviewer"]:
        assert (ROOT / "agents" / f"{a}.md").exists(), a
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py tests/test_specdev_ci.py -q`
Expected: FAIL — `FileNotFoundError` for `agents/spec-reviewer.md` etc., and `AssertionError: spec-reviewer` in `test_vendor_sources_exist`.

- [ ] **Step 3: Create `agents/spec-reviewer.md`**

````markdown
---
name: spec-reviewer
description: Reviews ONE SpecDev spec against the user's original request before the Spec PR is opened, and returns only findings. Dispatched fresh on every pass of the pre-PR review loop (up to max_review_iterations) — never the agent that wrote the spec. Judges whether each Acceptance line is testable and unambiguous, whether the REQs deliver what was asked (nothing dropped, nothing invented), and whether error and edge behaviour is specified. Read-only: it judges, the coordinator fixes.
tools: Read, Grep, Glob, Bash
model: inherit
---

You review a SpecDev spec **before** its Spec PR exists and report findings
tersely. You are one pass of a bounded loop: the coordinator fixes what you
find, then dispatches a *fresh* reviewer over the result. You never edit
anything — you judge.

You are deliberately not the author. Read the spec as the engineer who must
build it from nothing else, and as the user who asked for it.

## Your input (provided in the prompt)

- The **unit root** (`.` in a single-unit repo). The spec is
  `<unit>/.specdev/spec.md`, the component map
  `<unit>/.specdev/components.md`, local ADRs `<unit>/.specdev/adr/`.
- The **dismissed list**: findings earlier passes raised that were adjudicated
  and deliberately not acted on, each with its reason. **Do not re-raise a
  dismissed finding** unless you have evidence the reason did not consider —
  and then say what is new.

## What to check

Judge the spec against its `## Original Request` — the user's words, verbatim.

1. **Original Request present.** Missing, empty, or still a `<placeholder>` →
   one blocking finding, and stop there: intent cannot be judged without it.
2. **Coverage of the request.** Everything the request asks for is a REQ or an
   explicit Out of Scope entry. A REQ nobody asked for is a finding — it is
   scope the user never agreed to.
3. **Acceptance lines.** Each is observable, unambiguous, and assertable by a
   test: concrete inputs, outputs, states, numbers. "Works correctly", "is
   fast", "handles errors" are blocking findings.
4. **Unhappy paths.** Wherever the request implies them — empty input,
   boundaries, invalid input, a failing dependency, permissions — the spec
   says what must happen. Silence here is where builders guess, and where
   bugs are later found in review.
5. **Consistency.** No two REQs contradict; Out of Scope does not exclude
   something the request asked for; no REQ contradicts an accepted local ADR.
6. **Ownership.** When `components.md` is filled, every REQ is owned by a
   component.
7. **Extend mode.** When the spec's Mode is *Extend existing product*, every
   REQ fills **Current behavior**.

**Blocking** = a defect that would let a wrong implementation pass, or leave
the builder guessing at intent. **Minor** = wording and polish; it never
blocks. Set `needs_human: true` when only the user can resolve it — a choice
between two readings of their request. The coordinator batches those into one
question.

## Return ONLY this report

A few lines of verdict (clean, or N blocking / M minor), then exactly one
fenced JSON block. The coordinator hands it to `review_ledger.py` verbatim,
so it must parse:

```json
{"findings": [
  {"severity": "blocking", "kind": "spec", "where": "REQ-003",
   "summary": "Acceptance does not say what an empty search returns",
   "scenario": "a search with no matches: an empty list, a 404, or an error?",
   "fix": "state the empty-result behaviour and its status code",
   "needs_human": false}
]}
```

- `severity`: `blocking` | `minor`. `kind`: `spec`, or `intent` for a gap
  against the Original Request.
- `where`: a `REQ-###` or a section name — required on every blocking finding.
- A clean pass is `{"findings": []}`.

Never paste the spec back. Never write files.
````

- [ ] **Step 4: Create `agents/code-reviewer.md`**

````markdown
---
name: code-reviewer
description: Reviews ONE SpecDev unit's implementation diff for defects before the Implementation PR is opened, and returns only findings, each with a file:line and a concrete failure scenario. Dispatched fresh on every pass of the pre-PR review loop (up to max_review_iterations), in parallel with intent-reviewer — never the agent that wrote the code. Hunts the bugs a builder's own tests cannot catch. Read-only: it judges, component-builders fix.
tools: Read, Grep, Glob, Bash
model: inherit
---

You review one governed unit's implementation **before** its PR exists,
hunting defects, and report findings tersely. You are one pass of a bounded
loop: builders fix what you find (test-first), QA re-runs, and a *fresh*
reviewer checks the result. You never edit anything.

The code's tests were written by the agent that wrote the code, and they
pass. Assume that is exactly why the bugs you are looking for survived: find
what those tests do not exercise.

## Your input (provided in the prompt)

- The **unit root** and the **base ref** (the default branch). Review
  `git diff <base>...HEAD` — the whole change, not one wave.
- The spec's REQ list, for what the code is *for*.
- The **dismissed list** (id, summary, reason). **Do not re-raise a dismissed
  finding** unless you have evidence its reason did not consider — and then
  say what is new.

## How to review

Read every changed hunk, and enough surrounding code to know how it is
called. Run the unit's tests or a quick probe with Bash when that settles a
question — never modify files to do it. Look for:

- **Logic:** off-by-one, wrong operator or condition, inverted checks,
  unreachable branches, wrong defaults.
- **Edges:** empty, null/None, zero, negative, very large, unicode, duplicate,
  concurrent or repeated calls.
- **Errors:** exceptions swallowed or mis-reported, partial writes, missing
  cleanup, retries that never stop, status codes that lie.
- **Resources and concurrency:** leaks, unclosed handles, races, shared
  mutable state.
- **Security:** injection (SQL, shell, path), missing authorization checks,
  unsafe deserialization, secrets or PII in code or logs.
- **API misuse:** wrong argument order, ignored return values, deprecated or
  misunderstood calls.
- **Tests that do not test:** assertions that cannot fail, mocks that replace
  the thing under test, a `REQ-###` in a test name that the body never
  asserts.

**Every blocking finding needs a `file:line` and a concrete failure
scenario** — "input X in state Y yields Z; expected W". If you cannot state
one, it is a suspicion, not a finding: make it `minor` or drop it. Style,
naming and formatting are `minor` and never block.

## Return ONLY this report

A few lines of verdict (clean, or N blocking / M minor), then exactly one
fenced JSON block, which the coordinator hands to `review_ledger.py`
verbatim:

```json
{"findings": [
  {"severity": "blocking", "kind": "bug", "where": "src/pager.py:42",
   "summary": "page=0 returns the last page",
   "scenario": "list_items(page=0) -> items[-20:]; expected a 400 or the first page",
   "fix": "reject page < 1 before slicing"}
]}
```

- `severity`: `blocking` | `minor`. `kind`: `bug` | `security` | `test`.
- A clean pass is `{"findings": []}`.

Never paste file contents or full test output.
````

- [ ] **Step 5: Create `agents/intent-reviewer.md`**

````markdown
---
name: intent-reviewer
description: Checks ONE SpecDev unit's implementation against what was asked for before the Implementation PR is opened — every REQ delivered end to end, every linked test asserting its Acceptance line, the Original Request honoured, no scope creep — and returns only findings. Dispatched fresh on every pass of the pre-PR review loop (up to max_review_iterations), in parallel with code-reviewer. Read-only: it judges, component-builders fix.
tools: Read, Grep, Glob, Bash
model: inherit
---

You check that the implementation delivers the intent — not merely that it
builds and its tests pass. You are one pass of a bounded loop: builders fix
what you find, QA re-runs, and a *fresh* reviewer checks the result. You
never edit anything.

## Your input (provided in the prompt)

- The **unit root** and the **base ref** (the default branch). The change is
  `git diff <base>...HEAD`.
- The spec at `<unit>/.specdev/spec.md`: its `## Original Request` (the
  user's words, verbatim), Problem, and every `REQ-###` with its
  **Acceptance** line. A spec that predates `## Original Request` is judged
  against Problem + REQs.
- The **dismissed list** (id, summary, reason). **Do not re-raise a dismissed
  finding** unless you have evidence its reason did not consider.

## What to check, per REQ

1. **Delivered end to end.** The behaviour is reachable from the real entry
   point (route, CLI, handler, UI) — not only implemented in a function
   nothing calls, or tested in isolation behind a mock of its own wiring.
2. **Asserted.** Find the test linked to the REQ (its ID in the test name or
   a comment). It must assert the Acceptance line's observable outcome. A
   test that mentions the REQ but asserts something weaker is a blocking
   `test` finding.
3. **Faithful.** The implementation does what the Acceptance line says — its
   numbers, states and error behaviour — not a nearby reading of it.

Then, across the change:

4. **The Original Request is honoured.** Anything the user asked for that the
   implementation fails to do is a blocking `intent` finding even if no REQ
   captured it; say so, because that is a spec gap too.
5. **No scope creep.** Behaviour the spec did not ask for, or put Out of
   Scope, is a finding.

**Blocking** = a REQ not delivered, not asserted, or not faithful; an
Original Request item unmet; scope the spec excluded. Everything else is
`minor`.

## Return ONLY this report

One line per REQ (`REQ-### — delivered | GAP`), then exactly one fenced JSON
block, which the coordinator hands to `review_ledger.py` verbatim:

```json
{"findings": [
  {"severity": "blocking", "kind": "intent", "where": "REQ-004",
   "summary": "export is implemented but no route exposes it",
   "scenario": "GET /export -> 404; the Acceptance line requires a CSV download",
   "fix": "register the export handler in routes.py"}
]}
```

- `severity`: `blocking` | `minor`. `kind`: `intent` | `test`.
- `where`: a `REQ-###` or a `file:line` — required on every blocking finding.
- A clean pass is `{"findings": []}`.

Never paste file contents or full test output.
````

- [ ] **Step 6: Append review-fix mode to `agents/component-builder.md`**

Append at the end of the file:

```markdown

## Review-fix mode

When your contract is a set of **review findings** — ids like `I3-2`, each
with a `where`, a failure scenario and a suggested fix — rather than a
component to build:

1. For each finding, **write a test that reproduces it and watch it fail**
   before changing any code. The scenario is the test.
2. Fix it. The new test and the component's existing tests must pass.
3. If you cannot make it fail — the scenario does not occur — do not change
   the code. Report the finding `not-reproducible` with the evidence (the
   test you wrote and what it showed); the coordinator dismisses it with that
   evidence as the reason.
4. Commit with the usual `Refs: REQ-###` trailers for the REQs the fixes
   touch, and name the finding ids in the commit subject.

Report one line per finding id — `fixed` (+ the test that now passes) or
`not-reproducible` (+ the evidence) — in place of the usual REQ list.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_review_ledger.py tests/test_specdev_ci.py tests/test_pipeline_hardening.py -q`
Expected: PASS (including `test_allowlist_is_a_superset_of_every_vendored_agent_grant`, which now also covers the three new agents).

- [ ] **Step 8: Commit**

```bash
git add agents/spec-reviewer.md agents/code-reviewer.md agents/intent-reviewer.md agents/component-builder.md tests/test_review_ledger.py tests/test_specdev_ci.py
git commit -m "feat(agents): spec-, code- and intent-reviewer; review-fix mode for builders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Templates — Original Request, PR body review sections, BUILD pointer

**Files:**
- Modify: `assets/specdev/spec.md` (insert before `## Mode`)
- Modify: `assets/specdev/PR_BODY.md` (insert after `## Wave ledger`; extend Verification)
- Modify: `assets/specdev/BUILD.md` (insert after the wave-ledger table, before `### Open Items`)
- Test: `tests/test_review_ledger.py` (append)

**Interfaces:**
- Consumes: `review_ledger.render()` output (headings `## Review loop`, `## Unresolved review findings`); `review_ledger.check()`.
- Produces: templates whose `## Review loop` … `## Unresolved review findings` span sits directly before `## Deployment facts` in `PR_BODY.md`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_ledger.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py -q -k "template"`
Expected: FAIL — no `## Original Request`, no review sections, no BUILD pointer. (`test_the_unfilled_template_does_not_hand_anything_off` may already pass; that is fine.)

- [ ] **Step 3: Edit `assets/specdev/spec.md`**

Insert between the `> Gate 1 contract. ...` blockquote and `## Mode`:

```markdown
## Original Request

> The user's request **verbatim** — never paraphrased — then the brainstorm
> answers that shaped it. `spec-reviewer` and `intent-reviewer` judge the
> REQs and the code against this section; it is the only record of what was
> actually asked for.

<the request, exactly as the user wrote it>

**Clarified while brainstorming:**

- <question> → <answer>

```

- [ ] **Step 4: Edit `assets/specdev/PR_BODY.md`**

Insert between the `## Wave ledger` section (ending `<!-- Copy the per-wave verdicts from BUILD.md: what was built, QA green/red. -->`) and `## Deployment facts`:

```markdown
## Review loop

<!-- Paste `review_ledger.py --root <unit> render --phase impl` over this
section and the next: passes run against max_review_iterations, what each
found, and every dismissal with its reason. -->

## Unresolved review findings

<!-- Filled by the same render: "None — the final review pass was clean.", or
every blocking finding still open when the loop reached its cap, by id. The
build asserts that each open id is listed here. -->

```

In `## Verification`, add a line after the `adr-checker` item:

```markdown
- [ ] `review_ledger.py check --phase impl` passes — the last review pass covers this branch's tip
```

- [ ] **Step 5: Edit `assets/specdev/BUILD.md`**

Insert between the wave-ledger table (ending `| 1    |           |              |                   | green/red   |        |`) and `### Open Items`:

```markdown

### Review loop

_The pre-PR review loop's ledger is `review.json`, written only by
`review_ledger.py`. After a compact or a re-dispatch, resume it from
`python .specdev/tools/review_ledger.py --root <unit> status --phase impl` —
it names the next pass, the open findings and the dismissed ones. Never
restart at pass 1._
```

- [ ] **Step 6: Run the tests, then the full suite**

Run: `python -m pytest tests/test_review_ledger.py -q`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: all PASS (the stock-template checks in `test_profiles.py` still see `FEAT-XXX` / `<FEATURE NAME>`).

- [ ] **Step 7: Commit**

```bash
git add assets/specdev/spec.md assets/specdev/PR_BODY.md assets/specdev/BUILD.md tests/test_review_ledger.py
git commit -m "feat(templates): Original Request, PR-body review sections, review resume pointer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Skill and commands drive the loop

**Files:**
- Modify: `skills/specdev/SKILL.md`
- Modify: `commands/build.md`
- Modify: `commands/new-feature.md` (full rewrite — the file is 38 lines)
- Test: `tests/test_review_ledger.py` (append)

**Interfaces:**
- Consumes: the CLI from Tasks 1-2; agent names from Task 4; template sections from Task 5; `required_terminal_state` wording from Task 3.
- Produces: the coordinator instructions. Exact section heading `## Pre-PR review loop (both PRs)` in `SKILL.md`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_ledger.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py -q -k "skill or command or new_feature or both_halves or both_pr"`
Expected: FAIL — none of the text exists yet.

- [ ] **Step 3: Edit `skills/specdev/SKILL.md`**

**(a)** Replace pipeline step 2:

```markdown
2. **Brainstorm.** Socratic Q&A to pin users, problem, scope, out-of-scope.
   Don't write the spec until the problem is unambiguous.
```

with:

```markdown
2. **Brainstorm.** Socratic Q&A to pin users, problem, scope, out-of-scope.
   Don't write the spec until the problem is unambiguous. Record the user's
   request **verbatim** — it becomes the spec's `## Original Request`, the
   one thing the reviewers judge intent against. Never paraphrase it.
```

**(b)** Replace pipeline step 5 (from `5. **Open the Spec PR.**` through `merge. This locks the contract — do not renumber REQs afterward.`) with:

```markdown
5. **Open the Spec PR.** **(`spec_pr: false` → skip this step entirely.)**
   Before opening it, run the **Pre-PR review loop** (below) in the spec
   phase — a fresh `spec-reviewer` every pass until a pass is clean or
   `max_review_iterations` is reached — and then the **`adr-checker`** agent
   must be green (when org governance is configured) — a spec that
   contradicts an applicable org ADR is fixed *before* review, not during.
   Commit `.specdev/review.json` with the spec. Then `spec-validate.yml` runs
   `validate_spec.py` and `review_ledger.py check --phase spec`, and
   `org-adr-check.yml` runs `check_org_adrs.py`, as required checks. Get them
   green, get human approval, merge. This locks the contract — do not
   renumber REQs afterward.
```

**(c)** Replace pipeline step 7 (from `7. **Open the Implementation PR (Gate 2)` through `required status checks. Get review + green, merge.`) with:

```markdown
7. **Open the Implementation PR (Gate 2) — only after the review loop and the
   org-ADR loop are green.** First run the **Pre-PR review loop** (below) in
   the impl phase: fresh `code-reviewer` + `intent-reviewer` every pass,
   test-first fixes by `component-builder`s, `qa-verifier` green, commit —
   until a pass is clean or `max_review_iterations` is reached. Then, with
   org governance configured, run this fully automatic loop (no user prompts
   between iterations): dispatch **`adr-checker`** → on red, dispatch a
   `component-builder` to fix each named violation (or amend the local ADRs
   for a justified deviation — via the **`adr`** skill, so the deviation ADR
   is linted and conflict-checked like any other) → re-run `qa-verifier` →
   re-run `adr-checker` — repeat until green. If that loop changed any code,
   run one more review pass: `review_ledger.py check` fails on code the last
   pass never saw. **Do not open the PR while the checker reports violations
   or `review_ledger.py check --phase impl` fails.** Then `post-dev-qa.yml`
   runs tests, security scan, coverage, and `gen_traceability.py
   --check-gaps` (fails if any REQ has no test), and `org-adr-check.yml`
   deterministically re-proves the manifest against the org index. These are
   required status checks. Get review + green, merge.
```

**(d)** In *Context discipline*, replace:

```markdown
  mapping (`spec-explorer`), component builds (`component-builder`), QA
  (`qa-verifier`), org-ADR verification (`adr-checker`). Only their short
  summaries return to you.
```

with:

```markdown
  mapping (`spec-explorer`), component builds (`component-builder`), QA
  (`qa-verifier`), org-ADR verification (`adr-checker`), pre-PR review
  (`spec-reviewer`, `code-reviewer`, `intent-reviewer`). Only their short
  summaries and finding JSON return to you.
```

**(e)** Insert this new section between the end of *Parallel dispatch protocol* (after `goes through \`component-builder\` so its detail stays out of this thread.`) and `## Terminal state (when a build is actually over)`:

```markdown
## Pre-PR review loop (both PRs)

Nothing else reads a PR for correctness or intent before a human does:
`qa-verifier` runs the tests the builder itself wrote, so a bug the builder
never imagined passes green, and `validate_spec.py` checks a spec's shape, not
whether it says what the user asked for. This loop is that reading. It runs
before **every** Spec PR and Implementation PR, it is recorded by
`review_ledger.py`, and it is **asserted** — `build_outcome.py verify` fails a
prod build without it and `spec-validate.yml` fails a Spec PR without it — so
skipping it is a failed build, not a shortcut.

- **When:** the spec phase when the profile's `spec_pr` is true; the impl
  phase in `prod` mode. `poc` has no PR and runs neither.
- **Cap:** `max_review_iterations` passes per run (`ci.json`, default 10).
- **Resume point:** `python .specdev/tools/review_ledger.py --root <unit>
  status --phase <spec|impl>` — the next pass, the open findings, and the
  dismissed list. Never restart at pass 1.

Each pass:

1. **Dispatch fresh reviewers, in one message.** New `Agent` calls every
   pass — never a continuation of an earlier pass's reviewer, whose blind
   spots are exactly what the next pass is for.
   - spec phase: one **`spec-reviewer`**;
   - impl phase: **`code-reviewer`** and **`intent-reviewer`** in parallel,
     with the base branch.
   Give each the unit root and the **dismissed list** from `status`, labelled
   "already adjudicated — do not re-raise without new evidence". Never give a
   reviewer the builders' summaries.
2. **Record the pass.** Save each reviewer's closing JSON block verbatim to a
   file outside the repo (or pipe one on stdin as `-`) and run
   `review_ledger.py --root <unit> record --phase <p> --findings-json <file>`
   once per pass, repeating `--findings-json` per reviewer. The tool assigns
   the finding ids and stamps what was reviewed — never count or number
   findings yourself. Impl: commit first; it refuses a dirty tree.
3. **Stop** when `status` says `clean` or `cap-reached`. At the cap do **not**
   fix the last pass's findings — that would ship code no reviewer saw —
   hand them off (below).
4. **Fix every open blocking finding, then go to 1.**
   - Impl: dispatch `component-builder`s in review-fix mode — one per
     affected component, in parallel when their files are disjoint — with the
     findings (id, where, scenario) as the contract. Each reproduces its
     finding with a failing test before fixing it. A `not-reproducible`
     comes back with evidence → `review_ledger.py --root <unit> dismiss
     --phase impl --id <id> --reason "<evidence>"`. You may also dismiss a
     finding that contradicts the spec or an accepted ADR, citing the
     section. Then `qa-verifier` green, then commit.
   - Spec: revise the spec yourself and re-run `validate_spec.py --strict`.
     Batch the `needs_human` findings into **one** question to the user per
     pass and fold the answers in. A deferral is dismissed ("deferred by the
     user") and listed in `## Open Questions`.
   Ask the user nothing else between passes — the loop is automatic.

**Hand-off at the cap.** Impl: paste `review_ledger.py --root <unit> render
--phase impl` over `PR_BODY.md`'s `## Review loop` and `## Unresolved review
findings` sections (do this on a clean exit too — it records the passes).
Spec: list every open finding id under the spec's `## Open Questions`. Then
tell the user plainly that the PR carries unresolved findings.

**Before announcing either PR ready,** commit `.specdev/review.json` and run
`review_ledger.py --root <unit> check --phase <p>`. It fails when the loop
never ran, when the code (or the spec) changed after the last pass, when the
loop stopped short of the cap with findings open, or when a capped run's open
findings were not handed off. If something changed after the last pass — the
org-ADR loop touched code, or a human edited the spec on an open PR — run
another pass; if the run is already at its cap, `review_ledger.py new-run`
starts a fresh one, and refuses unless the reviewed state really changed.
```

**(f)** In *Terminal state*, replace:

```markdown
- a **prepared PR body** at `<unit>/.specdev/PR_BODY.md`, filled in;
- a **real checkpoint** at `<unit>/.specdev/BUILD.md`.
```

with:

```markdown
- a **prepared PR body** at `<unit>/.specdev/PR_BODY.md`, filled in;
- a **real checkpoint** at `<unit>/.specdev/BUILD.md`;
- in `prod`, a **review ledger** at `<unit>/.specdev/review.json` whose last
  pass is clean over the branch tip — or at `max_review_iterations` with every
  open finding listed in `PR_BODY.md`.
```

**(g)** In *Guardrails*, add after the `adr-checker` bullet (the one ending `…by content hash.`):

```markdown
- Never announce a Spec or Implementation PR ready while `review_ledger.py
  check` fails, and never hand-edit `.specdev/review.json` — only the tool
  writes it. Reviewers never fix; builders never review their own work.
```

**(h)** In the first *Tools* code block, add after the `adr.py conflicts` line:

```
python .specdev/tools/review_ledger.py status --phase impl   # pre-PR review loop: next pass, open + dismissed findings
python .specdev/tools/review_ledger.py check --phase impl    # the review-loop gate (spec: --phase spec)
```

And in the monorepo block add:

```
python .specdev/tools/review_ledger.py --root <u> check --phase impl  # one unit's review-loop gate
```

- [ ] **Step 4: Edit `commands/build.md`**

**(a)** Replace the opening authorization paragraph's first sentence:

```markdown
**This command dispatches subagents by design. Running it is your authorization
to spawn `component-builder` and `qa-verifier` agents — do it; do not fall back
to building inline.**
```

with:

```markdown
**This command dispatches subagents by design. Running it is your authorization
to spawn `component-builder`, `qa-verifier`, `code-reviewer` and
`intent-reviewer` agents — do it; do not fall back to building or reviewing
inline.**
```

**(b)** In *Terminal state*, replace:

```markdown
- **A real checkpoint** at `<unit>/.specdev/BUILD.md`, not the shipped template.
```

with:

```markdown
- **A real checkpoint** at `<unit>/.specdev/BUILD.md`, not the shipped template.
- **A recorded review loop** (prod) at `<unit>/.specdev/review.json`: the last
  pass clean over the branch tip, or `max_review_iterations` reached with every
  open finding listed in `PR_BODY.md`. `review_ledger.py check --phase impl` is
  the same assertion — run it yourself before you stop.
```

And replace:

```markdown
Interactively (this command, run by a human), the terminal state is the end of
*After the final wave* below: green QA, green org-ADR loop, `PR_BODY.md`
filled in, `BUILD.md` at `qa`, and the user told the PR is ready.
```

with:

```markdown
Interactively (this command, run by a human), the terminal state is the end of
*After the final wave* below: green QA, a complete review loop, green org-ADR
loop, `PR_BODY.md` filled in, `BUILD.md` at `qa`, and the user told the PR is
ready.
```

**(c)** Replace the whole `## After the final wave` section (through step 5, ending `never announce PR readiness while \`qa-verifier\` or \`adr-checker\` is red.`) with:

```markdown
## After the final wave

1. Dispatch `qa-verifier` once more over the fully integrated result as the
   pre-PR dry run. It must be green.
2. Resolve/verify deployment facts if not already done: run `detect_deploy.py`,
   fill any `missing`/`REPLACE_ME` facts in `deploy.profile.json` and
   `BUILD.md` → *Deployment Facts*, then `deploy.py preflight --env staging` and
   `--env production` until green (the `preflight` job blocks the merge
   otherwise).
3. **Pre-PR review loop (prod mode) — the PR is held until it ends.** Follow
   the specdev skill's *Pre-PR review loop*. Start from the pass
   `python .specdev/tools/review_ledger.py --root <unit> status --phase impl`
   names, then per pass:
   - Dispatch a fresh **`code-reviewer`** and a fresh **`intent-reviewer`** in
     one message, with the unit root, the base branch and the dismissed list.
   - Save each one's closing JSON block and record the pass:
     `review_ledger.py --root <unit> record --phase impl --findings-json <a>
     --findings-json <b>`.
   - `clean` → go to step 4. `cap-reached` → stop fixing; the open findings
     are handed off in step 5.
   - Otherwise dispatch `component-builder`s in review-fix mode with the open
     findings as their contract — each reproduces its finding with a failing
     test, then fixes it; dismiss a `not-reproducible` one with its evidence
     (`review_ledger.py --root <unit> dismiss --phase impl --id <id> --reason
     "..."`) — then `qa-verifier` green, commit, and run the next pass.
     **Automatically — do not ask the user between passes.**
4. **Org-ADR compliance loop (when `.specdev/org.json` is configured) — the
   PR is held until this is green.** Dispatch the **`adr-checker`** agent. It
   fetches the org ADR index, verifies every ADR applicable to this repo's
   classification, and writes `.specdev/adr/org-compliance.json`.
   - **Red** → for each named violation dispatch a `component-builder` with
     the violation as its contract (or amend the spec/local ADRs if the fix is
     a documented deviation), then re-run `qa-verifier`, then re-run
     `adr-checker`. Repeat **automatically — do not ask the user between
     iterations** — until green.
   - **Green** → record the verdict in the `BUILD.md` ledger and continue.
   - If this loop changed any code, run one more review pass (step 3) — the
     review check fails on code the last pass never saw.
5. Fill in `.specdev/PR_BODY.md` — the Implementation PR body — from the wave
   ledger: REQs covered and the test asserting each, deployment facts resolved,
   anything deferred. Paste `review_ledger.py --root <unit> render --phase
   impl` over its `## Review loop` and `## Unresolved review findings`
   sections. It is asserted after the run, so the stock template surviving is
   a failed build.
6. Commit `.specdev/review.json`, then run `review_ledger.py --root <unit>
   check --phase impl`. It must pass. If the run reached its cap, tell the
   user which findings are unresolved.
7. Update `BUILD.md` status to `qa`, then tell the user the build is green and
   the next step is to open the Implementation PR (Gate 2) from the pushed
   branch, pasting `PR_BODY.md`. Do **not** open the PR automatically, and
   never announce PR readiness while `qa-verifier`, `adr-checker` or
   `review_ledger.py check` is red.
```

**(d)** In *Guardrails*, add a bullet at the end:

```markdown
- Reviewers never fix and builders never review: every review pass is a fresh
  `code-reviewer` + `intent-reviewer`, and only `review_ledger.py` writes
  `.specdev/review.json`.
```

- [ ] **Step 5: Rewrite `commands/new-feature.md`**

Replace the whole file with:

```markdown
---
description: Start a new SpecDev feature — create a spec/<name> branch and a draft spec from the template.
argument-hint: <feature-name>
---

Start a new SpecDev feature for: **$ARGUMENTS**

Prerequisite: the repo must already contain `.specdev/` (run `/specdev:init`
otherwise).

Do this:

1. Pick the next `FEAT-###` by scanning `.specdev/spec.md`, `.specdev/specs/*.md`,
   and branches for the highest used number; if none, start at `FEAT-001`.
2. Create and switch to a branch `spec/<kebab-name>` from the default branch.
3. **Spec lifecycle (one active feature in `spec.md`):** `.specdev/spec.md` always
   holds the *active* feature; finished features live in `.specdev/specs/`.
   - If `.specdev/spec.md` is the unfilled template, draft the new feature there.
   - If it holds a *previous, completed* feature, first archive it to
     `.specdev/specs/<FEAT-###>-<name>.md` (if not already archived), then draft
     the new feature in `.specdev/spec.md`.
   Keep `spec.md` as the single working spec — do not draft a new feature into a
   `specs/` file directly. Traceability stays whole because
   `gen_traceability.py` scans `spec.md` + every archived `specs/*.md` together.
4. **Brainstorm before writing.** Ask the user the minimum questions needed to
   fill: users, problem, mode (new vs. extend), and the first requirements. For
   an extension, also capture current behavior per requirement. **Record the
   user's request verbatim** — word for word, never paraphrased — plus each
   brainstorm answer that shaped it. They become the spec's `## Original
   Request`, which the reviewers judge intent against.
5. Fill the spec: the `## Original Request`, `FEAT-###`, `Status: draft`,
   sequential `REQ-###` IDs each with a concrete, testable **Acceptance**
   line, and a real **Out of Scope** list.
6. Run `python .specdev/tools/validate_spec.py --strict` and fix what it flags.
7. **Pre-PR review loop (spec phase)** — skip only when
   `python .specdev/tools/profile.py show --unit <unit> --key spec_pr` prints
   `false` (a poc unit has no Spec PR). Follow the specdev skill's *Pre-PR
   review loop*, starting from the pass
   `python .specdev/tools/review_ledger.py --root <unit> status --phase spec`
   names:
   - dispatch a **fresh `spec-reviewer`** each pass with the unit root and the
     dismissed list;
   - record the pass: `review_ledger.py --root <unit> record --phase spec
     --findings-json <file>`;
   - on blocking findings, revise the spec and re-run `validate_spec.py
     --strict`; batch the `needs_human` findings into one question to the
     user per pass; dismiss a deferral (`review_ledger.py dismiss --phase
     spec --id <id> --reason "deferred by the user"`) and list it under
     `## Open Questions`;
   - stop on a clean pass, or at `max_review_iterations` — then list every
     open finding id under `## Open Questions`.
   Commit `.specdev/review.json` with the spec, then
   `review_ledger.py --root <unit> check --phase spec` must pass —
   `spec-validate.yml` runs the same check on the Spec PR.
8. Do **not** open the PR automatically — tell the user the spec is ready
   (and which findings, if any, are unresolved), that the next step is to push
   and open a Spec PR (Gate 1), and remind them not to start the build until
   that PR is merged.

Then for non-trivial architecture, offer to draft an ADR — invoke the `adr`
skill (or `/specdev:adr`) rather than writing the file directly, so the
decision is interviewed, linted, and checked against the ADRs already accepted.
```

- [ ] **Step 6: Run the tests, then the full suite**

Run: `python -m pytest tests/test_review_ledger.py -q`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: all PASS — in particular `test_the_coordinator_is_told_the_terminal_state_the_workflow_asserts`, `test_the_terminal_state_is_phrased_in_the_words_verify_uses` and the `test_profiles.py` SKILL checks still hold.

- [ ] **Step 7: Commit**

```bash
git add skills/specdev/SKILL.md commands/build.md commands/new-feature.md tests/test_review_ledger.py
git commit -m "feat(skill): drive the pre-PR review loop before both PRs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Workflows — Spec PR gate, CI prompts, install smoke

**Files:**
- Modify: `assets/workflows/spec-validate.yml` (`validate` job, after the Gate 1 step)
- Modify: `assets/workflows/specdev-build.yml` (`CONTINUE_PROMPT` env; `agent1` prompt)
- Modify: `.github/workflows/tests.yml` (install-smoke tool run)
- Modify: `tests/test_profiles.py:819-823` (`PROFILE_GATED`)
- Modify: `tests/test_installed_layout.py` (append)
- Test: `tests/test_review_ledger.py` (append)

**Interfaces:**
- Consumes: `review_ledger.py --root <unit> check --phase spec --repo-root .`; profile key `spec_pr` from `discover`'s `profiles` output.
- Produces: a Spec-PR-only gate step; prompts that name `review_ledger.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_ledger.py`:

```python
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
```

In `tests/test_profiles.py`, change the `PROFILE_GATED` entry:

```python
    "spec-validate.yml": ["spec_bar", "spec_pr"],
```

Append to `tests/test_installed_layout.py`:

```python
def test_the_review_ledger_runs_from_its_installed_path(installed):
    """review_ledger.py imports run_manifest as a sibling and resolves the cap
    from the installed ci.json - both only exist in the installed shape."""
    import subprocess
    import sys

    p = subprocess.run(
        [sys.executable,
         str(installed / ".specdev" / "tools" / "review_ledger.py"),
         "--root", ".", "status", "--phase", "impl", "--repo-root", "."],
        cwd=str(installed), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "not-started" in p.stdout and "of 10" in p.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py tests/test_profiles.py tests/test_installed_layout.py -q -k "spec_validate or build_attempt or install_smoke or workflow_gates or installed_path"`
Expected: FAIL for the three workflow tests and `test_workflow_gates_on_its_profile_keys[spec-validate.yml-keys1]`; `test_the_review_ledger_runs_from_its_installed_path` PASSES already (the tool exists) — that is fine, it pins the installed shape.

- [ ] **Step 3: Add the Spec PR gate to `assets/workflows/spec-validate.yml`**

In the `validate` job, after the `Validate spec artifacts (Gate 1)` step, add:

```yaml
      # The pre-PR review loop is part of Gate 1 for a SPEC PR only. Keyed on
      # the head branch so it never fires on an Implementation PR (whose loop
      # the build's terminal state asserts) or on an adopter's unrelated PRs;
      # keyed on spec_pr so a unit with no Spec PR (poc) is never asked for
      # one. The check is content-based (a hash of the spec's substance), so
      # it needs no git history.
      - name: Spec review loop recorded (Gate 1) — ${{ matrix.unit }}
        if: startsWith(github.head_ref, 'spec/') && hashFiles(format('{0}/.specdev/spec.md', matrix.unit)) != '' && fromJSON(needs.discover.outputs.profiles)[matrix.unit].spec_pr
        run: |
          python .specdev/tools/review_ledger.py --root '${{ matrix.unit }}' \
            check --phase spec --repo-root .
```

Also update the header comment's first paragraph — replace:

```yaml
# Gate 1 — validates the spec/architecture artifacts on the Spec PR, per
# governed unit.
```

with:

```yaml
# Gate 1 — validates the spec/architecture artifacts on the Spec PR, per
# governed unit, and (on spec/** heads) that the pre-PR spec review loop ran
# over the spec as it now stands.
```

- [ ] **Step 4: Carry the loop into every build attempt in `assets/workflows/specdev-build.yml`**

In `CONTINUE_PROMPT`, replace:

```yaml
        Commit every wave as it goes green. The terminal state is: the
        implementation branch carries your commits beyond the base branch, and
        <unit>/.specdev/PR_BODY.md is filled in. Do NOT open, review or merge
        a pull request in either mode - a human opens it.
```

with:

```yaml
        Commit every wave as it goes green. The terminal state is: the
        implementation branch carries your commits beyond the base branch,
        <unit>/.specdev/PR_BODY.md is filled in, and - in prod mode -
        `review_ledger.py check --phase impl` passes. If the waves are done,
        continue the pre-PR review loop from
        `python .specdev/tools/review_ledger.py --root <unit> status --phase impl`,
        which names the next pass; never restart it at pass 1. Do NOT open,
        review or merge a pull request in either mode - a human opens it.
```

In the `agent1` step's `prompt`, replace:

```yaml
            Resume from <unit>/.specdev/BUILD.md; if the build is incomplete,
            continue the dependency-wave loop until the terminal state:
              - your commits are on the branch (COMMIT every wave - this job
                pushes them to specdev/impl/<unit>/<FEAT> for you), and
              - <unit>/.specdev/PR_BODY.md is filled in.
```

with:

```yaml
            Resume from <unit>/.specdev/BUILD.md; if the build is incomplete,
            continue the dependency-wave loop until the terminal state:
              - your commits are on the branch (COMMIT every wave - this job
                pushes them to specdev/impl/<unit>/<FEAT> for you),
              - <unit>/.specdev/PR_BODY.md is filled in, and
              - in prod mode, the pre-PR review loop is complete: fresh
                code-reviewer + intent-reviewer passes, fixes committed, until
                a pass is clean or max_review_iterations is reached with the
                open findings in PR_BODY.md -
                `review_ledger.py check --phase impl` passes. Resume it from
                `review_ledger.py --root <unit> status --phase impl`.
```

- [ ] **Step 5: Smoke the installed tool in `.github/workflows/tests.yml`**

In the `install-smoke` job's `The installed tools run from their installed paths` step, add after the `breaker-env` line:

```yaml
          python .specdev/tools/review_ledger.py --root . status --phase impl --repo-root .
```

- [ ] **Step 6: Run the tests, then the full suite**

Run: `python -m pytest tests/ -q`
Expected: all PASS (including `test_every_installed_workflow_parses_and_declares_a_trigger` and the permission-pair checks).

- [ ] **Step 7: Commit**

```bash
git add assets/workflows/spec-validate.yml assets/workflows/specdev-build.yml .github/workflows/tests.yml tests/test_review_ledger.py tests/test_profiles.py tests/test_installed_layout.py
git commit -m "feat(ci): gate Spec PRs on the review loop; carry it into every build attempt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Docs, vendoring list, lint

**Files:**
- Modify: `README.md` (What you get table; Context & parallelism; Pipeline table; Headless builds bullet + `ci.json` table + budget note; Layout)
- Modify: `commands/init.md:28-35` (step 3a2 agent list)
- Modify: `.sdlc/config.json` (lint command)
- Test: `tests/test_review_ledger.py` (append)

**Interfaces:**
- Consumes: names from Tasks 1-7.
- Produces: documentation only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_ledger.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review_ledger.py -q -k "readme or init_vendors or lint_command"`
Expected: FAIL (3 tests).

- [ ] **Step 3: Edit `README.md`**

**(a)** In *What you get*, add after the `adr-checker` row:

```markdown
| Agent `spec-reviewer` | pre-PR review of the spec against the user's original request; read-only, fresh every pass |
| Agent `code-reviewer` | pre-PR defect review of the implementation diff — every finding with a `file:line` and a failure scenario |
| Agent `intent-reviewer` | pre-PR check that every REQ is delivered end to end and genuinely asserted, and the original request honoured |
```

**(b)** In *Context & parallelism*, insert after the paragraph ending `the source of truth.` (before `**Driving pattern ...**`):

```markdown
**Before each PR, a bounded review loop.** `qa-verifier` runs the tests the
builder wrote, so it cannot catch a bug the builder never imagined — which is
what a reviewer like Copilot then finds after the PR is open. So before every
Spec PR and Implementation PR the coordinator runs up to
`max_review_iterations` (default 10) review passes, each with **fresh**
read-only reviewers: `spec-reviewer` for the spec; `code-reviewer` (defects,
each with a `file:line` and a failure scenario) and `intent-reviewer` (every
REQ delivered end to end and genuinely asserted, the Original Request
honoured) for the implementation. Blocking findings go back to
`component-builder`s test-first, QA re-runs, and the next pass starts; the
loop ends on a clean pass or at the cap, where the open findings are listed
in the PR body (or the spec's Open Questions) rather than dropped. Every pass
is recorded in `.specdev/review.json` by `review_ledger.py`, and the loop is
**asserted**: a prod build's terminal state and the Spec PR's `spec-validate`
check both fail if it never ran, stopped early, or reviewed code that has
since changed.
```

**(c)** In the *Pipeline* table, replace the `spec-validate.yml` row with:

```markdown
| `spec-validate.yml` | PR to `spec/**` | Gate 1 — artifact completeness + REQ IDs + the recorded spec review loop |
```

**(d)** In *Headless builds*, replace the first bullet's opening:

```markdown
- **The job is not green because the process exited.** It ends by asserting the
  terminal state: `specdev/impl/<unit>/<FEAT-###>` carries commits beyond the
  base branch, `.specdev/PR_BODY.md` is filled in, and `BUILD.md` is no longer
  the shipped template.
```

with:

```markdown
- **The job is not green because the process exited.** It ends by asserting the
  terminal state: `specdev/impl/<unit>/<FEAT-###>` carries commits beyond the
  base branch, `.specdev/PR_BODY.md` is filled in, `BUILD.md` is no longer
  the shipped template, and — in prod — the pre-PR review loop recorded a
  clean final pass over the branch tip (or reached its cap with every open
  finding in the PR body).
```

**(e)** In the `ci.json` table, add after the `auto_resume` row:

```markdown
| `max_review_iterations` | 10 | — review passes per run before a PR is handed off (≥ 1; `0` does not disable it; not a breaker limit) |
```

**(f)** After the paragraph ending `Keep \`max_wall_minutes\` below \`max_session_minutes\`, or the job timeout fires first.`, add:

```markdown
**The review loop spends inside these limits.** Up to `max_review_iterations`
passes of reviewers, fixers and QA is real work on top of the build itself,
and none of the ceilings above were raised for it. If long features trip
`max_tool_calls`, `max_wall_minutes` or the `continuation_cap_usd` start
gate, raise those (keeping `max_wall_minutes` under `max_session_minutes`) or
lower `max_review_iterations` for the unit — that is your spend decision, not
the kit's. A tripped run is never handed off half-reviewed: the ledger
records the last pass, and the terminal-state assertion fails honestly.
```

**(g)** In *Layout*, add under `agents/` after `adr-checker.md`:

```
  spec-reviewer.md       pre-PR spec review against the original request
  code-reviewer.md       pre-PR defect review of the implementation diff
  intent-reviewer.md     pre-PR check: every REQ delivered and asserted
```

and under `assets/specdev/` after the `tools/units.py` line:

```
    tools/review_ledger.py pre-PR review loop ledger; asserted by verify + spec-validate
```

- [ ] **Step 4: Edit `commands/init.md`**

In step 3a2, replace these two lines:

```markdown
   component-builder / qa-verifier / adr-checker / spec-explorer subagents from
   the repo, so the coordinator can offload work and stay context-bounded.
```

with:

```markdown
   component-builder / qa-verifier / adr-checker / spec-explorer and
   spec-reviewer / code-reviewer / intent-reviewer subagents from the repo, so
   the coordinator can offload work — including the pre-PR review loop — and
   stay context-bounded.
```

- [ ] **Step 5: Edit `.sdlc/config.json`**

Append ` assets/specdev/tools/review_ledger.py` to the end of the `lint` command string (inside the quotes, after `assets/specdev/tools/validate_spec.py`).

- [ ] **Step 6: Run the full suite and the lint command**

Run: `python -m pytest tests/ -q`
Expected: all PASS.

Run: `python -m py_compile assets/specdev/tools/review_ledger.py assets/specdev/tools/build_outcome.py`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add README.md commands/init.md .sdlc/config.json tests/test_review_ledger.py
git commit -m "docs: document the pre-PR review loop, its agents and its budget

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run `python -m pytest tests/ -q` — all pass; count is 536 + the new tests.
- [ ] Simulate the install smoke locally: copy `assets/specdev` → `<tmp>/.specdev` and run `python .specdev/tools/review_ledger.py --root . status --phase impl --repo-root .` from `<tmp>` — prints `not-started - 0 of 10 passes`.
- [ ] `git log --oneline main..HEAD` shows the design commit plus one commit per task.
