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
                     [--reviewed SHA]
    review_ledger.py --root <unit> dismiss --phase impl --id I2-1 --reason TEXT
    review_ledger.py --root <unit> new-run --phase impl
    review_ledger.py --root <unit> status  --phase impl [--json]
    review_ledger.py --root <unit> render  --phase impl
    review_ledger.py --root <unit> check   --phase impl [--feat FEAT-###]
    review_ledger.py --root <unit> stats   [--phase impl] [--json]
    review_ledger.py --root <unit> record  --phase impl --from-hook [...]
Every subcommand takes --repo-root (ci.json fallback and git working tree).

`--from-hook` records the reports review_hook.py (a SubagentStop hook)
validated and saved as each reviewer finished, so nothing is transcribed by
hand. They live under hook_dir(): inside the git directory, never tracked.
"""
import argparse
import hashlib
import json
import math
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

# Which reviewer serves which phase. review_hook.py validates only these
# agents' reports, and `record --from-hook` expects one report from each
# reviewer of the phase.
REVIEWERS = {"spec": ("spec-reviewer",),
             "impl": ("code-reviewer", "intent-reviewer")}
HOOK_DIR_ENV = "SPECDEV_REVIEW_DIR"

# Interrogate mode (spec-reviewer over a first draft, before the loop): a
# coverage rating per ambiguity category, adapted from Spec Kit's `clarify`,
# and at most INTERROGATE_MAX questions, each with a recommended answer. An
# interrogate report is NOT a review pass and is never recorded as one.
INTERROGATE_CATEGORIES = ("scope", "data", "interaction", "non_functional",
                          "integrations", "edge_cases", "constraints",
                          "terminology", "done", "placeholders")
COVERAGE_VALUES = ("clear", "partial", "missing")
INTERROGATE_MAX = 5


def is_interrogate(report) -> bool:
    return isinstance(report, dict) and "coverage" in report


def interrogate_problems(report) -> list[str]:
    """Why an interrogate report breaks its contract. Empty = it is fine."""
    problems = []
    cov = report.get("coverage")
    if not isinstance(cov, dict):
        return ["coverage must be an object: category -> clear | partial | "
                "missing"]
    missing = [c for c in INTERROGATE_CATEGORIES if c not in cov]
    if missing:
        problems.append(f"coverage is missing {', '.join(missing)} - rate "
                        f"every category")
    bad = [f"{k}={v!r}" for k, v in cov.items()
           if k in INTERROGATE_CATEGORIES and v not in COVERAGE_VALUES]
    if bad:
        problems.append(f"coverage values must be one of {COVERAGE_VALUES}: "
                        f"{', '.join(bad)}")
    unknown = [k for k in cov if k not in INTERROGATE_CATEGORIES]
    if unknown:
        problems.append(f"unknown coverage categories: {', '.join(unknown)}")
    findings = report.get("findings")
    if not isinstance(findings, list):
        return problems + ['an interrogate report needs "findings": [...]']
    if len(findings) > INTERROGATE_MAX:
        problems.append(f"{len(findings)} questions - ask at most "
                        f"{INTERROGATE_MAX}, ranked scope > security/privacy "
                        f"> UX > technical; turn the rest into assumptions "
                        f"(a default) instead")
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        if not f.get("needs_human"):
            problems.append(f"finding {i + 1}: interrogate mode reports only "
                            f"questions for the user (needs_human: true)")
        if not str(f.get("default") or "").strip():
            problems.append(f"finding {i + 1}: every question needs a "
                            f"recommended answer in default - the user can "
                            f"then just say yes")
    return problems
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


def parse_ledger(text: str, label: str = "review.json") -> dict:
    """A ledger document from its JSON text. Shared by load() and by
    loop_stats.py, which reads past versions out of git history."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise LedgerError(f"{label} is not valid JSON: {e}") from e
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise LedgerError(f"{label} is not a schema_version "
                          f"{SCHEMA_VERSION} review ledger")
    phases = doc.setdefault("phases", {})
    for ph in PHASES:
        phases.setdefault(ph, {"runs": [], "dismissed": []})
    return doc


def load(root=".") -> dict | None:
    p = ledger_path(root)
    if not p.exists():
        return None
    return parse_ledger(p.read_text(encoding="utf-8-sig"), p.as_posix())


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
            or (isinstance(val, float) and not math.isfinite(val))
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


def _code_changes(a: str, b: str, repo_root=".") -> list[str] | None:
    """Paths outside .specdev/ that differ between commits a and b, or None
    when git cannot say."""
    rc, out = _git(["diff", "--name-only", a, b, "--", ".", NOT_CODE],
                   repo_root)
    if rc != 0:
        return None
    return [ln for ln in out.splitlines() if ln.strip()]


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


def finding_problems(raw, n: int) -> list[str]:
    """Why one finding cannot be recorded. Empty = it can.

    The one definition of a well-formed finding, shared by record() and by
    review_hook.py - which checks a reviewer's report as the reviewer stops,
    so a malformed report is sent back to the reviewer to fix instead of
    surfacing later as a refusal the coordinator has to work around."""
    if not isinstance(raw, dict):
        return [f"finding {n} is not an object"]
    problems = []
    sev = str(raw.get("severity", "")).strip().lower()
    if sev not in SEVERITIES:
        problems.append(f"finding {n}: severity must be one of {SEVERITIES}, "
                        f"got {raw.get('severity')!r}")
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in KINDS:
        problems.append(f"finding {n}: kind must be one of {KINDS}, got "
                        f"{raw.get('kind')!r}")
    if not str(raw.get("summary", "")).strip():
        problems.append(f"finding {n}: summary is required")
    if sev == "blocking" and not str(raw.get("where", "")).strip():
        problems.append(f"finding {n}: a blocking finding must say where "
                        f"(file:line, REQ-###, or a section)")
    if "default" in raw and not isinstance(raw.get("default"), (str, type(None))):
        problems.append(f"finding {n}: default must be a string (the reading "
                        f"to assume if nobody answers)")
    return problems


def report_problems(report, strict=False) -> list[str]:
    """Why a reviewer report cannot be recorded. Empty = it can.

    `strict` adds what the review hook demands beyond what record() refuses:
    a concrete failure scenario on every blocking finding. The agents are
    told to give one; the ledger stays lenient so a report recorded before
    the hook existed still records."""
    try:
        raw = _findings_of(report)
    except LedgerError as e:
        return [str(e)]
    problems = interrogate_problems(report) if is_interrogate(report) else []
    for i, f in enumerate(raw):
        problems += finding_problems(f, i + 1)
        if (strict and isinstance(f, dict)
                and str(f.get("severity", "")).strip().lower() == "blocking"
                and not str(f.get("scenario", "")).strip()):
            problems.append(f"finding {i + 1}: a blocking finding needs a "
                            f"concrete scenario (input X in state Y yields "
                            f"Z; expected W) - without one it is a "
                            f"suspicion: make it minor or drop it")
    return problems


def _normalize(raw, n: int, pass_no: int, phase: str) -> dict:
    problems = finding_problems(raw, n)
    if problems:
        raise LedgerError(problems[0])
    sev = str(raw.get("severity", "")).strip().lower()
    entry = {"id": f"{ID_PREFIX[phase]}{pass_no}-{n}", "severity": sev,
             "kind": str(raw.get("kind", "")).strip().lower(),
             "where": str(raw.get("where", "")).strip(),
             "summary": str(raw.get("summary", "")).strip(),
             "scenario": str(raw.get("scenario", "")).strip(),
             "fix": str(raw.get("fix", "")).strip(),
             "needs_human": bool(raw.get("needs_human", False))}
    default = str(raw.get("default") or "").strip()
    if default:
        # Assume-and-record: the reading to apply when nobody is asked.
        entry["default"] = default
    return entry


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
           head="HEAD", reviewed=None) -> dict:
    """Append the next pass to the current run. Returns the pass entry.

    `reviewed` (impl): the commit HEAD pointed at when the reviewers were
    dispatched. Reviewers have Bash and run tests; whatever that changed in
    tracked files was seen by no reviewer, so it must be discarded - never
    committed to get past the dirty-tree check - and a pass is stamped with
    the commit the reviewers actually read, not whatever HEAD is by now."""
    _phase(phase)
    if reviewed is not None and phase == "spec":
        raise LedgerError("--reviewed applies to the impl phase only; the "
                          "spec phase stamps the spec's hash")
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
                f"{' ...' if len(dirty) > 5 else ''}). A pass records the "
                f"code the reviewers saw: if a reviewer's tests or tools "
                f"changed these files, discard those changes (git checkout "
                f"-- <paths>). Never commit them to get past this check - "
                f"that would stamp code no reviewer read.")
        stamp = _commit(head, repo_root)
        if not stamp:
            raise LedgerError(f"cannot resolve {head!r} to a commit in "
                              f"{Path(repo_root).as_posix()}")
        if reviewed is not None:
            r = _commit(reviewed, repo_root)
            if not r:
                raise LedgerError(f"cannot resolve --reviewed {reviewed!r} "
                                  f"to a commit in "
                                  f"{Path(repo_root).as_posix()}")
            if r != stamp:
                changed = _code_changes(r, stamp, repo_root)
                if changed is None or changed:
                    paths = (", ".join(changed[:5])
                             + (" ..." if len(changed) > 5 else "")
                             if changed else "git could not diff them")
                    raise LedgerError(
                        f"code changed between dispatch ({r[:12]}) and "
                        f"record ({stamp[:12]}): {paths}. The reviewers saw "
                        f"{r[:12]}: discard their side effects, or commit "
                        f"the changes and re-run the pass over them.")
            stamp = r
        reviewed = stamp
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
    if any(is_interrogate(r) for r in reports):
        raise LedgerError("an interrogate-mode report (it has 'coverage') is "
                          "the up-front questions, not a review pass - do not "
                          "record it; dispatch a normal spec-reviewer")
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
    stale, why = False, ""
    if n > 0:
        # The resume point must not say "clean" about code or a spec that
        # changed after the last pass; only check() used to notice.
        try:
            stale, why = _staleness(root, phase, ph, "HEAD", repo_root)
        except LedgerError as e:
            stale, why = None, str(e)
    if n == 0:
        state = "not-started"
    elif stale is True or stale is None:
        state = "stale" if n < cap else "stale-at-cap"
    elif not open_:
        state = "clean"
    elif n >= cap:
        state = "cap-reached"
    else:
        state = "needs-fixes"
    return {"phase": phase, "feat": doc.get("feat") if doc else None,
            "cap": cap, "run": len(ph["runs"]), "passes_in_run": n,
            "next_pass_in_run": n + 1 if n < cap else None, "state": state,
            "stale_reason": why if state.startswith("stale") else "",
            "stale_unverified": stale is None,
            "open_blocking": open_, "dismissed": ph["dismissed"]}


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
    changed = _code_changes(a, b, repo_root)
    if changed is None:
        return None, f"git diff {a[:12]}..{b[:12]} failed"
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
    # An unverifiable reviewed state (rewritten history) is a reason to
    # re-review; only a state verified unchanged is refused.
    if stale is False:
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
    handed to the human. Fails CLOSED: it now runs inside the build's
    terminal-state step, where a traceback would drop every problem from
    verify.json, so an unexpected error becomes a problem, never a pass."""
    try:
        return _check(root, phase, feat, head, repo_root)
    except LedgerError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001
        return [f"the review ledger could not be checked ({type(e).__name__}: "
                f"{e}) - failing closed"]


def _check(root, phase, feat, head, repo_root) -> list[str]:
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
    elif run and run["passes"] and any(
            f["severity"] == "blocking" for f in run["passes"][-1]["findings"]):
        lines.append("None — every blocking finding in the final pass was "
                     "dismissed (reasons above).")
    else:
        lines.append("None — the final review pass was clean.")
    return "\n".join(lines) + "\n"


def hook_dir(repo_root=".") -> Path:
    """Where review_hook.py saves validated reports, and where `record
    --from-hook` reads them.

    SPECDEV_REVIEW_DIR when set; otherwise `specdev-review/` inside the git
    common directory. That is deterministic for the hook and the coordinator
    alike (both run in the repository), shared by every worktree of it, and
    never tracked - so a saved report can never be committed by accident."""
    import os  # noqa: PLC0415
    env = os.environ.get(HOOK_DIR_ENV, "").strip()
    if env:
        return Path(env)
    rc, out = _git(["rev-parse", "--path-format=absolute",
                    "--git-common-dir"], repo_root)
    if rc != 0 or not out.strip():
        raise LedgerError(f"{Path(repo_root).as_posix()} is not a git "
                          f"repository, so there is no hook directory - set "
                          f"{HOOK_DIR_ENV} or pass --findings-json")
    return Path(out.strip()) / "specdev-review"


def hook_reports(phase: str, repo_root=".",
                 directory: Path | None = None) -> list[Path]:
    """The pending reports for one pass: exactly one per reviewer of the
    phase. Refuses a missing reviewer (the pass would silently record half a
    review) and a duplicate (two dispatches of one reviewer - which of them
    is this pass is a judgment the tool will not make)."""
    _phase(phase)
    d = (directory or hook_dir(repo_root)) / phase
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    problems = []
    chosen = []
    for agent in REVIEWERS[phase]:
        mine = [f for f in files if f.name.startswith(f"{agent}--")]
        if not mine:
            problems.append(f"no saved report from {agent} in "
                            f"{d.as_posix()} - was it dispatched, and did "
                            f"its report pass the review hook?")
        elif len(mine) > 1:
            problems.append(f"{len(mine)} saved reports from {agent} in "
                            f"{d.as_posix()} ({', '.join(f.name for f in mine)})"
                            f" - delete the ones that are not this pass's")
        else:
            chosen.append(mine[0])
    if problems:
        raise LedgerError("; ".join(problems))
    return chosen


def consume_hook_reports(paths) -> None:
    """Move recorded reports out of the way so the next pass cannot reuse
    them. Kept (under recorded/), not deleted: they are the raw evidence."""
    for p in paths:
        done = p.parent / "recorded"
        done.mkdir(parents=True, exist_ok=True)
        p.replace(done / p.name)


def stats(root=".", phase=None, feat=None) -> dict:
    """What the review loop cost and found, per phase, from the ledger.

    Read-only. This is the yardstick for any change to the loop: passes to
    clean or to the cap, what kinds of finding recur, how often findings were
    dismissed or re-raised. Without it "the loop is expensive" and "accuracy
    improved" are both opinions."""
    return stats_of(_ledger_for(root, feat), phase)


def stats_of(doc: dict | None, phase=None) -> dict:
    """stats() for a ledger document already in hand."""
    phases = [_phase(phase)] if phase else list(PHASES)
    out = {"feat": doc.get("feat") if doc else None, "phases": {}}
    for ph_name in phases:
        ph = doc["phases"][ph_name] if doc else {"runs": [], "dismissed": []}
        passes = [p for r in ph["runs"] for p in r["passes"]]
        findings = [f for p in passes for f in p["findings"]]
        by_kind: dict = {}
        for f in findings:
            k = by_kind.setdefault(f["kind"], {"blocking": 0, "minor": 0})
            k[f["severity"]] += 1
        dismissed = ph["dismissed"]
        reasons = [d["reason"].lower() for d in dismissed]
        dismissed_summaries = {d["summary"].strip().lower()
                               for d in dismissed}
        # A re-raise: a later finding with a dismissed finding's summary.
        # Summaries are reviewer prose, so this undercounts - it is a floor.
        dismissed_at = {d["id"]: d["summary"].strip().lower()
                        for d in dismissed}
        reraised = 0
        for p in passes:
            for f in p["findings"]:
                s = f["summary"].strip().lower()
                if s in dismissed_summaries and f["id"] not in dismissed_at:
                    reraised += 1
        blocking = sum(f["severity"] == "blocking" for f in findings)
        out["phases"][ph_name] = {
            "runs": len(ph["runs"]),
            "passes": len(passes),
            "passes_per_run": [len(r["passes"]) for r in ph["runs"]],
            "blocking_per_pass": [sum(f["severity"] == "blocking"
                                      for f in p["findings"])
                                  for p in passes],
            "findings": {"blocking": blocking,
                         "minor": len(findings) - blocking},
            "by_kind": by_kind,
            "needs_human": sum(f.get("needs_human", False)
                               for f in findings),
            "dismissed": len(dismissed),
            "dismissal_rate": (round(len(dismissed) / blocking, 3)
                               if blocking else 0.0),
            "not_reproducible": sum("not-reproducible" in r
                                    or "not reproducible" in r
                                    for r in reasons),
            "assumed": sum(r.startswith("assumed") for r in reasons),
            "reraised_after_dismissal": reraised,
            "final_pass_clean": bool(passes) and not any(
                f["severity"] == "blocking"
                and f["id"] not in dismissed_at
                for f in passes[-1]["findings"]),
        }
    return out


def _stats_text(st: dict) -> str:
    lines = [f"review loop stats for {st['feat'] or '(no ledger yet)'}"]
    for name, ph in st["phases"].items():
        if not ph["passes"]:
            lines.append(f"  {name}: no passes recorded")
            continue
        kinds = ", ".join(f"{k} {v['blocking']}b/{v['minor']}m"
                          for k, v in sorted(ph["by_kind"].items()))
        lines.append(
            f"  {name}: {ph['passes']} pass(es) over {ph['runs']} run(s) "
            f"{ph['passes_per_run']}; blocking per pass "
            f"{ph['blocking_per_pass']}; final pass "
            f"{'clean' if ph['final_pass_clean'] else 'NOT clean'}")
        lines.append(
            f"      findings {ph['findings']['blocking']} blocking / "
            f"{ph['findings']['minor']} minor ({kinds or 'none'}); "
            f"dismissed {ph['dismissed']} (rate {ph['dismissal_rate']}, "
            f"not-reproducible {ph['not_reproducible']}, assumed "
            f"{ph['assumed']}); re-raised after dismissal "
            f"{ph['reraised_after_dismissal']}; needs_human "
            f"{ph['needs_human']}")
    return "\n".join(lines)


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
    elif st["state"] in ("stale", "stale-at-cap"):
        what = ("the last reviewed state cannot be verified"
                if st.get("stale_unverified") else
                "the reviewed state changed")
        if st["open_blocking"]:
            lines.append(f"{len(st['open_blocking'])} blocking finding(s) "
                         f"from the last pass are still open - fix or "
                         f"dismiss each before the next pass")
        if st["state"] == "stale":
            lines.append(f"next: pass {st['next_pass_in_run']} of "
                         f"{st['cap']} - {what} ({st['stale_reason']})")
        else:
            lines.append(f"the run is at its cap and {what} "
                         f"({st['stale_reason']}): run 'review_ledger.py --root "
                     f"<unit> new-run --phase {st['phase']}', then record "
                     f"pass 1 of the new run")
    for f in st["open_blocking"]:
        lines.append(f"OPEN {f['id']} [{f['kind']}] {f['where']} - "
                     f"{f['summary']}"
                     + (" (needs the user" + (f"; default: {f['default']}"
                                              if f.get("default") else "")
                        + ")" if f.get("needs_human") else ""))
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
    p.add_argument("--reviewed", default=None, metavar="SHA",
                   help="the commit HEAD pointed at when the reviewers were "
                        "dispatched (impl); refuses if code changed since")
    p.add_argument("--findings-json", action="append", default=None,
                   help="one reviewer's closing JSON block; '-' reads stdin; "
                        "repeat once per reviewer")
    p.add_argument("--from-hook", action="store_true",
                   help="record the reports review_hook.py saved for this "
                        "phase (one per reviewer) instead of --findings-json")
    p.add_argument("--hook-dir", default=None,
                   help="where the hook saved them (default: hook_dir())")
    p = add("dismiss", "adjudicate a finding as not acted on, with the reason")
    p.add_argument("--id", required=True)
    p.add_argument("--reason", required=True)
    p = add("status", "the resume point: next pass, open and dismissed "
                      "findings")
    p.add_argument("--feat", default=None)
    p.add_argument("--json", action="store_true")
    add("new-run", "start a fresh run after the reviewed state changed")
    p = add("render", "the PR body's Review loop + Unresolved sections")
    p.add_argument("--feat", default=None)
    p = sub.add_parser("stats", help="what the loop cost and found, per "
                                     "phase (read-only)")
    p.add_argument("--phase", default=None, choices=PHASES)
    p.add_argument("--feat", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--repo-root", default=".")
    p = add("check", "assert the loop is complete (the gate)")
    p.add_argument("--feat", default=None)
    p.add_argument("--head", default="HEAD",
                   help="the commit that would be handed off (impl)")
    return ap


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.cmd == "record":
            if bool(args.findings_json) == bool(args.from_hook):
                raise LedgerError("give exactly one of --findings-json "
                                  "(repeatable) or --from-hook")
            saved = []
            if args.from_hook:
                saved = hook_reports(args.phase, args.repo_root,
                                     Path(args.hook_dir) if args.hook_dir
                                     else None)
                reports = _read_reports([str(f) for f in saved])
            else:
                reports = _read_reports(args.findings_json)
            entry = record(args.root, args.phase, args.feat, reports,
                           args.repo_root, reviewed=args.reviewed)
            consume_hook_reports(saved)
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
        if args.cmd == "stats":
            st = stats(args.root, args.phase, args.feat)
            print(json.dumps(st, indent=2) if args.json else _stats_text(st))
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
    except LedgerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
