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

Usage:
    build_outcome.py checkpoint --root <unit>
    build_outcome.py verify --root <unit> --feat FEAT-001 --mode prod
                            [--base main] [--repo owner/name] [--json-out FILE]
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


def _gh_prs(feat: str, repo: str | None = None) -> list[dict]:
    """Every PR mentioning `feat`, via gh. Returns [] if gh is unusable."""
    cmd = ["gh", "pr", "list", "--state", "all", "--limit", "100",
           "--search", feat,
           "--json", "number,title,state,headRefName,baseRefName,url,body"]
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


def implementation_prs(feat: str, unit: str = ".", base: str = "main",
                       repo: str | None = None) -> list[dict]:
    """PRs that look like this unit+FEAT's Implementation PR.

    Deliberately excludes `spec/**` heads: the Spec PR is the INPUT to a prod
    build, so counting it as the terminal state would let a build that did
    nothing at all verify itself against its own trigger."""
    out = []
    for pr in _gh_prs(feat, repo):
        head = pr.get("headRefName", "") or ""
        if head.startswith("spec/"):
            continue
        if pr.get("baseRefName") != base:
            continue
        hay = f"{pr.get('title', '')}\n{pr.get('body', '')}\n{head}"
        if feat not in hay:
            continue
        # In a monorepo the branch or title must name the unit, else unit A's
        # PR would satisfy unit B's terminal state.
        if unit not in (".", "") and unit not in hay:
            continue
        out.append(pr)
    return out


def verify(root=".", feat="", mode="prod", unit=".", base="main",
           repo: str | None = None) -> dict:
    """Assert the mode's terminal state. Returns a record; `ok` is the verdict."""
    problems: list[str] = []
    problems += checkpoint_problems(root)

    prs = implementation_prs(feat, unit=unit, base=base, repo=repo)
    merged = [p for p in prs if (p.get("state") or "").upper() == "MERGED"]

    if not prs:
        problems.append(
            f"no Implementation PR for {feat} against '{base}' was found. "
            f"The build job exited without producing its terminal state.")
    elif mode == "poc" and not merged:
        problems.append(
            f"mode 'poc' requires the Implementation PR for {feat} to be "
            f"MERGED; found {len(prs)} unmerged "
            f"({', '.join('#' + str(p['number']) for p in prs)}).")

    required = ("Implementation PR open against " + base if mode == "prod"
                else "Implementation PR merged into " + base)
    return {
        "ok": not problems,
        "mode": mode,
        "feat": feat,
        "unit": unit,
        "required_terminal_state": required,
        "implementation_prs": [
            {"number": p["number"], "state": p.get("state"),
             "url": p.get("url"), "head": p.get("headRefName")} for p in prs],
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
    lines.append("**Implementation PRs found:** " + (
        ", ".join(f"[#{p['number']}]({p['url']}) ({p['state']})" for p in prs)
        if prs else "_none_"))
    lines.append("")

    if breaker:
        lines += ["### Circuit breaker", ""]
        tripped = breaker.get("tripped")
        lines.append(f"- **Tripped:** {'YES - ' + str(breaker.get('trip_reason')) if tripped else 'no'}")
        lines.append(f"- Permission denials: {breaker.get('denials', 0)}"
                     f" / {breaker.get('max_permission_denials', '-')}")
        lines.append(f"- Consecutive tool failures (high-water): "
                     f"{breaker.get('max_consecutive_seen', 0)}"
                     f" / {breaker.get('max_consecutive_tool_failures', '-')}")
        lines.append(f"- Cost: ${breaker.get('cost_usd', 0)}"
                     f" / ${breaker.get('max_cost_usd', '-')}")
        hist = breaker.get("denied_tools") or {}
        if hist:
            lines += ["", "**Denied tool calls:**", ""]
            lines.append("| tool | denials |")
            lines.append("|---|---|")
            for name, n in sorted(hist.items(), key=lambda kv: -kv[1]):
                lines.append(f"| `{name}` | {n} |")
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
        rec = verify(args.root, args.feat, args.mode, args.unit,
                     args.base, args.repo)
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(rec, indent=2) + "\n", encoding="utf-8")
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
