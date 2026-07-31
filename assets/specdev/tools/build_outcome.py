#!/usr/bin/env python3
"""Post-run verification and reporting for a specdev-build run.

A build job's exit code says the agent process ended, NOT that it built
anything. An agent can exit cleanly having produced no branch, no commit, no PR
and a still-stock BUILD.md — and while `deploy-poc` was gated on that exit
code, such a build still triggered a deployment.

This tool replaces "the process exited 0" with "the mode's terminal state was
actually reached":

    prod: an Implementation PR for this unit+FEAT exists against the base branch
    poc:  that PR exists AND was merged

plus a cheap independent check that the run left a real checkpoint behind
rather than the shipped template.

PROVENANCE IS THE WHOLE POINT. The invariant is not "a PR mentioning FEAT-002
exists" — it is "THIS RUN produced one". Any artifact this run did not create
is not evidence about this run, whoever made it: a human's PR that discusses
the feature, a PR merged weeks earlier, a neighbouring unit's PR, the Spec PR
that triggered the build. Text matching alone lets a third party satisfy the
assertion, which replaces one unreliable signal with another. So `--since` and
`--author` are hard filters applied BEFORE any text match, everything they
reject is reported with its reason, and running without them emits a warning
rather than implying a certainty this tool does not have.

Usage:
    build_outcome.py checkpoint --root <unit>
    build_outcome.py verify --root <unit> --feat FEAT-001 --mode prod
                            [--base main] [--repo owner/name] [--json-out FILE]
                            [--since 2026-07-31T09:00:00Z] [--author bot,...]
    build_outcome.py report --root <unit> --feat FEAT-001 --mode prod
                            [--verify-json FILE] [--breaker-json FILE]
                            [--out FILE] [--append-build-md]
"""
import argparse
import json
import os
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

BUILD_REL = ".specdev/BUILD.md"

# Markers present in the shipped assets/specdev/BUILD.md template. If any
# survives the run, the coordinator never wrote a real checkpoint — which is
# both a failed build and an unresumable one.
STOCK_MARKERS = ("FEAT-XXX", "<FEATURE NAME>")


def checkpoint_path(root=".") -> Path:
    return Path(root) / BUILD_REL


def checkpoint_problems(root=".") -> list[str]:
    """Reasons the checkpoint is not a real one. Empty list means it is."""
    p = checkpoint_path(root)
    if not p.exists():
        return [f"{p.as_posix()} does not exist - the run wrote no checkpoint."]
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        return [f"{p.as_posix()} is empty."]
    found = [m for m in STOCK_MARKERS if m in text]
    if found:
        return [f"{p.as_posix()} is still the stock template (contains "
                f"{', '.join(repr(m) for m in found)}). The run did not "
                f"check point any real state, so it is not resumable."]
    return []


GH_PR_FIELDS = ("number,title,state,headRefName,baseRefName,url,body,"
                "createdAt,author")


def _gh_prs(feat: str, repo: str | None = None) -> list[dict]:
    """Every PR mentioning `feat`, via gh. Returns [] if gh is unusable."""
    cmd = ["gh", "pr", "list", "--state", "all", "--limit", "100",
           "--search", feat, "--json", GH_PR_FIELDS]
    if repo:
        cmd += ["--repo", repo]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if p.returncode != 0 or not p.stdout.strip():
        return []
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return []


def _token_re(value: str) -> re.Pattern:
    """`value` as a whole token, not a substring.

    Both text matches here used to be raw `in` tests, so "FEAT-002" matched
    "FEAT-0021" and a unit named "api" matched a branch named "rapid-sync".
    Alphanumerics either side break the match; separators (/ - _ . space) do
    not, because a unit path and a branch name are made of segments."""
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])")


def _parse_ts(value):
    """An ISO-8601 instant, or None if it is not one. GitHub emits Z-suffixed
    UTC; `datetime.fromisoformat` only learned Z in 3.11."""
    if not value:
        return None
    s = str(value).strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _author_login(pr: dict) -> str:
    a = pr.get("author")
    if isinstance(a, dict):
        return str(a.get("login") or "")
    return str(a or "")


def _same_actor(login: str, wanted: str) -> bool:
    """`github-actions[bot]`, `app/github-actions` and `github-actions` are the
    same identity spelled three ways depending on which API answered."""
    def norm(s):
        s = str(s or "").strip().lower()
        s = s.removeprefix("app/")
        return s.removesuffix("[bot]")
    return bool(norm(login)) and norm(login) == norm(wanted)


def implementation_prs(feat: str, unit: str = ".", base: str = "main",
                       repo: str | None = None, since: str | None = None,
                       author: str | list | None = None
                       ) -> tuple[list[dict], list[dict]]:
    """(matches, rejected) for this unit+FEAT's Implementation PR.

    `since` and `author` are PROVENANCE filters and run first: a PR this run
    could not have created is rejected before its text is ever considered.
    Without them the terminal-state assertion is satisfiable by anyone who
    mentions the FEAT id — which is the failure this module exists to prevent.

    Both stay optional so the tool remains usable by hand; `verify` warns when
    neither was supplied. Everything rejected comes back with a reason so a
    reader can tell "this run produced no PR" from "the filters were off and
    this is a text match" without re-deriving it."""
    wanted = ([author] if isinstance(author, str) else list(author or []))
    wanted = [w.strip() for w in wanted if str(w).strip()]
    started = _parse_ts(since)
    feat_re, unit_re = _token_re(feat), _token_re(unit)

    matches, rejected = [], []

    def reject(pr, reason):
        rejected.append({"number": pr.get("number"), "url": pr.get("url"),
                         "title": pr.get("title"), "state": pr.get("state"),
                         "author": _author_login(pr), "reason": reason})

    for pr in _gh_prs(feat, repo):
        head = pr.get("headRefName", "") or ""

        # ---- provenance: was this artifact produced by THIS run? ----
        if wanted:
            login = _author_login(pr)
            if not any(_same_actor(login, w) for w in wanted):
                reject(pr, f"authored by {login or '(unknown)'}, not this "
                           f"run's builder ({', '.join(wanted)})")
                continue
        if started is not None:
            created = _parse_ts(pr.get("createdAt"))
            if created is None:
                reject(pr, "createdAt is missing or unparseable, so it cannot "
                           "be attributed to this run")
                continue
            if created < started:
                reject(pr, f"created {created.isoformat()}, before this run "
                           f"started {started.isoformat()}")
                continue

        # ---- shape: is it the Implementation PR for this unit+FEAT? ----
        if head.startswith("spec/"):
            # The Spec PR is the INPUT to a prod build; counting it would let a
            # build that did nothing verify itself against its own trigger.
            reject(pr, f"head '{head}' is a spec/** branch - the Spec PR is "
                       f"this build's trigger, not its output")
            continue
        if pr.get("baseRefName") != base:
            reject(pr, f"targets '{pr.get('baseRefName')}', not '{base}'")
            continue
        hay = f"{pr.get('title', '')}\n{pr.get('body', '')}\n{head}"
        if not feat_re.search(hay):
            reject(pr, f"does not name {feat} as a whole id")
            continue
        # In a monorepo the branch or title must name the unit, else unit A's
        # PR would satisfy unit B's terminal state.
        if unit not in (".", "") and not unit_re.search(hay):
            reject(pr, f"does not name unit '{unit}'")
            continue
        matches.append(pr)
    return matches, rejected


def verify(root=".", feat="", mode="prod", unit=".", base="main",
           repo: str | None = None, since: str | None = None,
           author: str | list | None = None) -> dict:
    """Assert the mode's terminal state. Returns a record; `ok` is the verdict."""
    problems: list[str] = []
    warnings: list[str] = []
    problems += checkpoint_problems(root)

    authors = ([author] if isinstance(author, str) else list(author or []))
    authors = [a.strip() for a in authors if str(a).strip()]
    if since and _parse_ts(since) is None:
        warnings.append(
            f"--since {since!r} is not an ISO-8601 instant and was IGNORED; "
            f"PRs predating this run can satisfy the assertion.")
        since = None
    if not since and not authors:
        warnings.append(
            "no provenance filter (--since/--author) was supplied, so this "
            "check cannot tell a PR this run created from a pre-existing one "
            "that merely mentions " + (feat or "the FEAT id") + ". The verdict "
            "below is a TEXT MATCH, not proof that this run produced anything.")

    prs, rejected = implementation_prs(feat, unit=unit, base=base, repo=repo,
                                       since=since, author=authors)
    merged = [p for p in prs if (p.get("state") or "").upper() == "MERGED"]

    attributed = bool(since or authors)
    numbers = ", ".join("#" + str(p["number"]) for p in prs)

    if not prs:
        problems.append(
            f"no Implementation PR for {feat} against '{base}' was found. "
            f"The build job exited without producing its terminal state.")
    elif mode == "poc" and not merged:
        # A check that cannot attribute its evidence must not state a reason
        # that presupposes attribution. Unfiltered, `prs` may be anybody's —
        # so "the Implementation PR ... found 1 unmerged (#101)" told a reader
        # that THIS BUILD opened a PR and failed to merge it, and sent them to
        # a stranger's PR to find out why. The verdict was right; the reason
        # was false, and the reason is the part a reader acts on.
        if attributed:
            problems.append(
                f"mode 'poc' requires the Implementation PR for {feat} to be "
                f"MERGED; found {len(prs)} unmerged ({numbers}).")
        else:
            problems.append(
                f"mode 'poc' requires an Implementation PR for {feat} that "
                f"THIS RUN merged, and no PR here can be attributed to this "
                f"run - no provenance filter (--since/--author) was supplied. "
                f"{len(prs)} PR(s) mention {feat} and none is merged "
                f"({numbers}); they may belong to anyone.")

    required = ("Implementation PR open against " + base if mode == "prod"
                else "Implementation PR merged into " + base)
    return {
        "ok": not problems,
        "mode": mode,
        "feat": feat,
        "unit": unit,
        "required_terminal_state": required,
        "provenance_filters": {
            "since": since,
            "authors": authors,
            "applied": bool(since or authors),
        },
        "implementation_prs": [
            {"number": p["number"], "state": p.get("state"),
             "url": p.get("url"), "head": p.get("headRefName"),
             "author": _author_login(p), "created_at": p.get("createdAt")}
            for p in prs],
        "rejected_prs": rejected,
        "warnings": warnings,
        "problems": problems,
    }


def _md_report(rec: dict, breaker: dict | None) -> str:
    """The post-run record. Written even when the run failed - especially then:
    the failing run is the one whose denial histogram is worth having."""
    ok = rec.get("ok")
    lines = [
        f"## SpecDev build outcome - {rec.get('feat')} ({rec.get('mode')})",
        "",
        f"**Verdict:** {'PASS' if ok else 'FAIL'}",
        f"**Unit:** `{rec.get('unit')}`",
        f"**Required terminal state:** {rec.get('required_terminal_state')}",
        "",
    ]
    prs = rec.get("implementation_prs") or []
    filt = rec.get("provenance_filters") or {}
    # Unattributed matches are not "Implementation PRs found" — calling them
    # that is the claim the filters were never able to support.
    label = ("**Implementation PRs found:**" if filt.get("applied")
             else "**PRs matching on text only (NOT attributed to this run):**")
    lines.append(label + " " + (
        ", ".join(f"[#{p['number']}]({p['url']}) ({p['state']})" for p in prs)
        if prs else "_none_"))
    lines.append("")

    # What the check EXCLUDED is as much the record as what it matched: without
    # it, "no PR was produced" and "the filters were off and this is a text
    # match" read identically to whoever opens this after a failure.
    if filt:
        lines.append("**Provenance filters:** " + (
            f"created at/after `{filt.get('since')}`, "
            f"authored by `{', '.join(filt.get('authors') or []) or 'anyone'}`"
            if filt.get("applied") else
            "_none applied — the match below is a TEXT match, not proof this "
            "run produced anything_"))
        lines.append("")
    rejected = rec.get("rejected_prs") or []
    if rejected:
        lines += [f"**Other PR(s) mentioning {rec.get('feat')} that are not "
                  f"this run's:** {len(rejected)}", ""]
        lines += ["| PR | author | reason it is not this run's |", "|---|---|---|"]
        for r in rejected:
            url = r.get("url")
            ref = f"[#{r.get('number')}]({url})" if url else f"#{r.get('number')}"
            lines.append(f"| {ref} | `{r.get('author') or '?'}` | {r.get('reason')} |")
        lines.append("")

    if breaker:
        lines += ["### Circuit breaker", ""]
        tripped = breaker.get("tripped")
        lim = breaker.get("limits") or {}

        def _lim(name):
            return breaker.get(name, lim.get(name, "-"))

        lines.append(f"- **Tripped:** {'YES - ' + str(breaker.get('trip_reason')) if tripped else 'no'}")
        lines.append(f"- Permission denials: {breaker.get('denials', 0)}"
                     f" / {_lim('max_permission_denials')}")
        lines.append(f"- Consecutive tool failures (high-water): "
                     f"{breaker.get('max_consecutive_seen', 0)}"
                     f" / {_lim('max_consecutive_tool_failures')}")
        # `$0.0 / $10` is not a reading, it is the absence of one — and in the
        # one artifact whose purpose is being read after a failure it says
        # "this run was free". Say what was actually true instead.
        source = breaker.get("cost_source", "unavailable")
        ceiling = _lim("max_cost_usd")
        if source == "unavailable":
            lines.append(
                f"- Cost: **not measured** — the ${ceiling} ceiling was "
                f"INACTIVE for this run. Mid-run cost is read best-effort from "
                f"the agent transcript and was never readable here; this is "
                f"NOT $0 spent. The real figure is the build action's own "
                f"`total_cost_usd` output.")
        else:
            lines.append(f"- Cost: ${breaker.get('cost_usd', 0)} / ${ceiling}"
                         f" (source: {source})")
        lines.append(f"- Wall clock ceiling: {_lim('max_wall_minutes')} min"
                     f" · tool calls: {breaker.get('tool_calls', 0)}"
                     f" / {_lim('max_tool_calls')}")
        for name in breaker.get("unarmed_limits") or []:
            lines.append(f"- ⚠️ `{name}` was 0/unset — that bound never applied.")
        if breaker.get("limits_source"):
            lines.append(f"- Limits resolved from: `{breaker['limits_source']}`")
        hist = breaker.get("denied_tools") or {}
        if hist:
            lines += ["", "**Denied tool calls:**", ""]
            lines.append("| tool | denials |")
            lines.append("|---|---|")
            for name, n in sorted(hist.items(), key=lambda kv: -kv[1]):
                lines.append(f"| `{name}` | {n} |")
        lines.append("")

    if rec.get("warnings"):
        lines += ["### What this check could not establish", ""]
        lines += [f"- {w}" for w in rec["warnings"]]
        lines.append("")
    if rec.get("problems"):
        lines += ["### Why this run did not reach its terminal state", ""]
        lines += [f"- {p}" for p in rec["problems"]]
        lines.append("")
    if rec.get("checkpoint_ref"):
        lines += [f"**Resume from:** `{rec['checkpoint_ref']}`", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("checkpoint")

    pv = sub.add_parser("verify")
    pv.add_argument("--feat", required=True)
    pv.add_argument("--mode", required=True, choices=("prod", "poc"))
    pv.add_argument("--unit", default=".")
    pv.add_argument("--base", default="main")
    pv.add_argument("--repo", default=None)
    pv.add_argument("--since", default=None,
                    help="ISO-8601 instant this run started; PRs created "
                         "earlier are not this run's output")
    pv.add_argument("--author", default=None,
                    help="comma-separated login(s) this run's PRs are opened "
                         "by; PRs by anyone else are not this run's output")
    pv.add_argument("--json-out", default=None)

    pr = sub.add_parser("report")
    pr.add_argument("--feat", required=True)
    pr.add_argument("--mode", required=True, choices=("prod", "poc"))
    pr.add_argument("--verify-json", default=None)
    pr.add_argument("--breaker-json", default=None)
    pr.add_argument("--checkpoint-ref", default=None)
    pr.add_argument("--out", default=None)
    pr.add_argument("--append-build-md", action="store_true")

    args = ap.parse_args()

    if args.cmd == "checkpoint":
        probs = checkpoint_problems(args.root)
        for p in probs:
            print(f"ERROR: {p}", file=sys.stderr)
        if probs:
            return 1
        print(f"checkpoint ok - {checkpoint_path(args.root).as_posix()} "
              f"carries real state")
        return 0

    if args.cmd == "verify":
        authors = [a.strip() for a in (args.author or "").split(",") if a.strip()]
        rec = verify(args.root, args.feat, args.mode, args.unit,
                     args.base, args.repo, since=args.since, author=authors)
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        for w in rec.get("warnings") or []:
            print(f"::warning title=Terminal-state check is unqualified::{w}")
        rejected = rec.get("rejected_prs") or []
        if rejected:
            print(f"saw {len(rejected)} other PR(s) mentioning {args.feat} "
                  f"that are not this run's:")
            for r in rejected:
                print(f"  #{r.get('number')} by {r.get('author') or '?'} "
                      f"({r.get('reason')})")
        for p in rec["problems"]:
            print(f"ERROR: {p}", file=sys.stderr)
        if not rec["ok"]:
            print("A green job here would mean 'the agent process exited', not "
                  "'the build produced anything'. Failing so no downstream "
                  "deploy treats this as a successful build.", file=sys.stderr)
            return 1
        print(f"terminal state reached - {rec['required_terminal_state']}")
        return 0

    if args.cmd == "report":
        rec = {"feat": args.feat, "mode": args.mode, "ok": None,
               "problems": [], "implementation_prs": []}
        if args.verify_json and Path(args.verify_json).exists():
            rec.update(json.loads(Path(args.verify_json).read_text("utf-8")))
        breaker = None
        if args.breaker_json and Path(args.breaker_json).exists():
            breaker = json.loads(Path(args.breaker_json).read_text("utf-8"))
        if args.checkpoint_ref:
            rec["checkpoint_ref"] = args.checkpoint_ref
        md = _md_report(rec, breaker)
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(md)
        if args.append_build_md:
            bp = checkpoint_path(args.root)
            if bp.exists():
                with open(bp, "a", encoding="utf-8") as fh:
                    fh.write("\n---\n\n" + md)
        sys.stdout.write(md)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
