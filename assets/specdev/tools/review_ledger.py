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
