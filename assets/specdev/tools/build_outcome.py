#!/usr/bin/env python3
"""Post-run verification and reporting for a specdev-build run.

A build job's exit code says the agent process ended, NOT that it built
anything. An agent can exit cleanly having produced no branch, no commit, no PR
and a still-stock BUILD.md — and while `deploy-poc` was gated on that exit
code, such a build still triggered a deployment.

This tool replaces "the process exited 0" with "the mode's terminal state was
actually reached":

    prod: an implementation BRANCH for this unit+FEAT was pushed by this run,
          carrying commits beyond the base, with a prepared PR body
    poc:  the same branch — the poc environment deploys from it directly

plus a cheap independent check that the run left a real checkpoint behind
rather than the shipped template.

WHY A BRANCH AND NOT A PR. Asserting "a PR exists" required the repository to
enable *Allow GitHub Actions to create and approve pull requests* — one switch
that grants create and approve together. With it off, the coordinator cannot
open its PR (403) and the terminal state is unreachable: a completed build with
nowhere to put its result. With it on, Actions can APPROVE pull requests, which
GitHub's own documentation describes as enabling an unreviewed and potentially
malicious PR that bypasses branch protections. No setting satisfies both,
because the hazard and the capability are the same switch — so the assertion,
not the adopter's settings, had to change.

A branch is also better evidence, not merely safer:

  * More attributable. A ref and its commits are checkable against this run's
    identity and start instant. A PR is an object anyone can create, which is
    the entire subject of the provenance work this module already does.
  * Reachable with the switch off. `contents: write` pushes a branch, so the
    dangerous capability is never needed.
  * A human then opens the PR — and a human-opened PR fires `on: pull_request`,
    so the required checks actually run. A bot-authored PR receives none, so
    the old terminal state asserted a PR the merge gate could not process.

The build's job is to produce reviewable work; opening the PR is the review
gate, and it belongs to a person.

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
PR_BODY_REL = ".specdev/PR_BODY.md"

# Markers present in the shipped assets/specdev/BUILD.md template. If any
# survives the run, the coordinator never wrote a real checkpoint — which is
# both a failed build and an unresumable one.
STOCK_MARKERS = ("FEAT-XXX", "<FEATURE NAME>")

# The branch this run pushes its work to. Deterministic and bot-owned, keyed
# on unit+FEAT exactly like the checkpoint ref — so verifying it is a lookup,
# not a search over branch names that happen to mention the feature.
IMPL_REF_PREFIX = "specdev/impl"
CHECKPOINT_REF_PREFIX = "specdev/checkpoint"

# A checkpoint commit carries [skip ci] so that pushing it does not fire CI on
# every checkpoint. That marker is correct on the checkpoint ref and poisonous
# at the head of a work branch: GitHub skips EVERY workflow on a pull request
# whose head commit carries it — no error, no skipped-run entry, just a PR
# with zero checks that looks like CI has not started yet.
SKIP_CI_MARKERS = ("[skip ci]", "[ci skip]", "[no ci]", "***NO_CI***")


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


def pr_body_problems(root=".") -> list[str]:
    """The prepared PR body — the other half of "reviewable work".

    A branch with no description is not something a human can open a PR from
    without reconstructing what the build did. The coordinator writes this;
    the human opening the PR pastes it."""
    p = Path(root) / PR_BODY_REL
    if not p.exists():
        return [f"{p.as_posix()} does not exist - the run prepared no PR body, "
                f"so its branch is not reviewable work yet."]
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        return [f"{p.as_posix()} is empty."]
    found = [m for m in STOCK_MARKERS if m in text]
    if found:
        return [f"{p.as_posix()} is still the stock template (contains "
                f"{', '.join(repr(m) for m in found)})."]
    return []


def ref_unit(unit: str = ".") -> str:
    """A unit as a legal git ref COMPONENT.

    The single-root unit is `.`, and `refs/heads/specdev/checkpoint/./FEAT-001`
    is not a valid ref name — git rejects any path containing `/./`. Both ref
    families were built by interpolating the unit directly, so in a
    single-root repo (the default, and the common case) the checkpoint push
    failed every time. It failed into the workflow's `||` branch, which prints
    a warning, so it never failed the job: `auto_resume` had nothing to
    restore and no run was ever actually resumable."""
    u = (unit or ".").strip().strip("/")
    parts = [p for p in u.split("/") if p not in ("", ".", "..")]
    return "/".join(parts) if parts else "root"


def implementation_ref(unit: str = ".", feat: str = "") -> str:
    return f"{IMPL_REF_PREFIX}/{ref_unit(unit)}/{feat}"


def checkpoint_ref(unit: str = ".", feat: str = "") -> str:
    return f"{CHECKPOINT_REF_PREFIX}/{ref_unit(unit)}/{feat}"


def _git(args: list, cwd=".") -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd),
                           capture_output=True, text=True)
    except (FileNotFoundError, OSError, NotADirectoryError):
        return 1, ""
    return p.returncode, p.stdout


def _resolve(rev: str, cwd=".") -> str:
    rc, out = _git(["rev-parse", "--verify", "--quiet", rev + "^{commit}"], cwd)
    return out.strip() if rc == 0 else ""


def branch_evidence(repo_dir=".", feat="", unit=".", base="main",
                    since: str | None = None,
                    author: str | list | None = None,
                    remote: str = "origin") -> dict:
    """Did THIS RUN push an implementation branch carrying real work?

    Checked against git rather than an API: a ref and its commits are the most
    directly attributable artifact a build produces."""
    ref = implementation_ref(unit, feat)
    rec = {"ref": ref, "sha": None, "commits_ahead": 0, "tip": {},
           "problems": [], "warnings": []}

    sha = ""
    for spelling in (f"refs/remotes/{remote}/{ref}", f"refs/heads/{ref}", ref):
        sha = _resolve(spelling, repo_dir)
        if sha:
            rec["resolved_from"] = spelling
            break
    if not sha:
        rec["problems"].append(
            f"no implementation branch at '{ref}'. The run produced no "
            f"reviewable work: nothing was pushed for a human to open a PR "
            f"from.")
        return rec
    rec["sha"] = sha

    base_sha = ""
    for spelling in (f"refs/remotes/{remote}/{base}", f"refs/heads/{base}", base):
        base_sha = _resolve(spelling, repo_dir)
        if base_sha:
            break
    if not base_sha:
        rec["warnings"].append(
            f"base '{base}' could not be resolved here, so 'commits beyond the "
            f"base' was NOT checked. The branch exists; whether it carries "
            f"work is unverified.")
    else:
        rc, out = _git(["rev-list", "--count", f"{base_sha}..{sha}"], repo_dir)
        rec["commits_ahead"] = int(out.strip() or 0) if rc == 0 else 0
        if rec["commits_ahead"] == 0:
            rec["problems"].append(
                f"'{ref}' carries no commits beyond '{base}'. The branch "
                f"exists but the run committed nothing to it.")

    rc, out = _git(["show", "-s", "--format=%cI%n%ae%n%an%n%s", sha], repo_dir)
    if rc == 0 and out.strip():
        parts = (out.strip().split("\n") + ["", "", "", ""])[:4]
        rec["tip"] = {"committed_at": parts[0], "email": parts[1],
                      "name": parts[2], "subject": parts[3]}

    tip = rec["tip"]
    # A [skip ci] head means every workflow on the resulting PR is skipped —
    # silently. Refuse to call that reviewable work.
    if any(m.lower() in (tip.get("subject") or "").lower()
           for m in SKIP_CI_MARKERS):
        rec["problems"].append(
            f"the tip of '{ref}' is a [skip ci] commit "
            f"({tip.get('subject')!r}). GitHub skips EVERY workflow on a pull "
            f"request whose head carries that marker, so this branch would "
            f"produce a PR with no checks at all and no indication why. The "
            f"checkpoint commit must never be the head of the work branch.")

    started = _parse_ts(since)
    if started is not None and tip.get("committed_at"):
        tip_at = _parse_ts(tip["committed_at"])
        if tip_at is None:
            rec["warnings"].append("the branch tip has no parseable commit date.")
        elif tip_at < started:
            rec["problems"].append(
                f"'{ref}' was last committed {tip_at.isoformat()}, before this "
                f"build started {started.isoformat()} - it is a leftover from "
                f"an earlier build, not this one's output.")

    wanted = ([author] if isinstance(author, str) else list(author or []))
    wanted = [w.strip() for w in wanted if str(w).strip()]
    if wanted and tip:
        who = f"{tip.get('name', '')} <{tip.get('email', '')}>"
        if not any(_same_actor(tip.get("name", ""), w)
                   or w.lower() in (tip.get("email", "") or "").lower()
                   or w.lower() in (tip.get("name", "") or "").lower()
                   for w in wanted):
            rec["warnings"].append(
                f"'{ref}' tip was committed by {who}, which does not match "
                f"this run's builder ({', '.join(wanted)}). The branch is "
                f"accepted on its ref and timing; treat authorship as "
                f"unconfirmed.")
    return rec


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
           author: str | list | None = None, repo_dir=".") -> dict:
    """Assert the mode's terminal state. Returns a record; `ok` is the verdict.

    The terminal state is REVIEWABLE WORK, not a pull request — see the module
    docstring for why. Existing PRs are still looked up and reported, because
    "a PR already exists for this branch" is useful to whoever reads this, but
    no PR is required and none can satisfy the assertion on its own."""
    problems: list[str] = []
    warnings: list[str] = []
    problems += checkpoint_problems(root)
    problems += pr_body_problems(root)

    authors = ([author] if isinstance(author, str) else list(author or []))
    authors = [a.strip() for a in authors if str(a).strip()]
    if since and _parse_ts(since) is None:
        warnings.append(
            f"--since {since!r} is not an ISO-8601 instant and was IGNORED; "
            f"work predating this build cannot be told from its output.")
        since = None
    if not since and not authors:
        warnings.append(
            "no provenance filter (--since/--author) was supplied, so a "
            "branch left by an earlier build cannot be told from this one's "
            "output, and any PR listed below is a TEXT MATCH rather than "
            "evidence about this run.")

    branch = branch_evidence(repo_dir, feat=feat, unit=unit, base=base,
                             since=since, author=authors)
    problems += branch["problems"]
    warnings += branch["warnings"]

    # Supplementary only. A PR is neither necessary nor sufficient here.
    prs, rejected = implementation_prs(feat, unit=unit, base=base, repo=repo,
                                       since=since, author=authors)

    required = (f"implementation branch '{branch['ref']}' pushed with commits "
                f"beyond '{base}', and a prepared PR body — a human opens the "
                f"PR" + (" (poc deploys from this branch directly)"
                         if mode == "poc" else ""))
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
        "implementation_branch": branch,
        "implementation_prs": [
            {"number": p["number"], "state": p.get("state"),
             "url": p.get("url"), "head": p.get("headRefName"),
             "author": _author_login(p), "created_at": p.get("createdAt")}
            for p in prs],
        "rejected_prs": rejected,
        "warnings": warnings,
        "problems": problems,
    }


def continuation_gate(rec: dict, attempt: int, max_attempts, spent, cap,
                      head_before: str = "", head_now: str = "") -> dict:
    """Should the build get another attempt in this same job?

    A coordinator that ends its turn ends the run — headlessly, that is the
    whole run, and it happened at turn 35 of 200 with five waves untouched, no
    error and no breaker trip. Sharpening the prompt did not fix it: the next
    run stopped after 6 turns with the corrected wording in place. What built
    the feature was a second invocation. A fix that depends on the model
    complying is not a fix; where a mechanical control and prompt language
    both address a failure, the mechanical one is the fix.

    Every condition must hold, and each is a reason NOT to continue when it
    does not. The last one is what makes the loop safe: an attempt that
    achieved nothing does not buy another."""
    reasons = []

    if rec.get("ok"):
        reasons.append("the terminal state was reached")

    try:
        max_attempts = int(max_attempts)
    except (TypeError, ValueError):
        max_attempts = 1
    if attempt >= max_attempts:
        reasons.append(f"attempt {attempt} of {max_attempts} - no attempts left")

    # An unreadable cost stops the loop. The breaker's figure reads
    # "unavailable" on hosted runners by design, so this must come from the
    # action's own total_cost_usd — and treating unknown as zero is exactly
    # the mistake the cost reporting already refuses to make elsewhere.
    try:
        spent_f = float(str(spent).strip())
    except (TypeError, ValueError):
        spent_f = None
    try:
        cap_f = float(str(cap).strip())
    except (TypeError, ValueError):
        cap_f = None
    if cap_f is None or cap_f <= 0:
        reasons.append("no continuation spend cap is configured")
    elif spent_f is None:
        reasons.append(
            "this attempt's cost could not be read from the build action, so "
            "the next attempt would be unbudgeted - stopping rather than "
            "treating unknown as $0")
    elif spent_f >= cap_f:
        reasons.append(f"${spent_f:.2f} spent reaches the ${cap_f:.2f} "
                       f"continuation cap")

    if head_before and head_now and head_before == head_now:
        reasons.append(
            "the last attempt committed nothing, so another would resume from "
            "the same place with the same context and no new information")

    go = not reasons
    return {
        "go": go,
        "attempt": attempt,
        "next_attempt": attempt + 1 if go else attempt,
        "max_attempts": max_attempts,
        "spent_usd": spent_f,
        "cap_usd": cap_f,
        "reasons": reasons,
        # A cap gates whether the NEXT attempt starts; it does not bound what
        # that attempt spends. Real exposure is "cap plus one attempt".
        "cap_is_a_start_gate": True,
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
    br = rec.get("implementation_branch") or {}
    if br:
        tip = br.get("tip") or {}
        if br.get("sha"):
            lines.append(
                f"**Implementation branch:** `{br['ref']}` @ `{br['sha'][:9]}` "
                f"— {br.get('commits_ahead', 0)} commit(s) beyond the base"
                + (f", tip {tip.get('committed_at')}" if tip.get("committed_at")
                   else ""))
            lines.append("")
            lines.append(f"> Open the PR from `{br['ref']}` yourself — a "
                         f"human-opened PR is what fires the required checks. "
                         f"The body is prepared in `{PR_BODY_REL}`.")
        else:
            lines.append(f"**Implementation branch:** `{br['ref']}` — _not "
                         f"pushed_")
        lines.append("")

    prs = rec.get("implementation_prs") or []
    filt = rec.get("provenance_filters") or {}
    # Unattributed matches are not "Implementation PRs found" — calling them
    # that is the claim the filters were never able to support.
    label = ("**Existing PRs for this feature** (informational — not the "
             "terminal state):" if filt.get("applied")
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
        # The denial ceiling is a conjunction, so the report has to show both
        # halves — a reader who sees only "12 / 15" cannot tell a run that was
        # nowhere near tripping from one held back solely by the rate.
        denials = breaker.get("denials", 0)
        calls = breaker.get("tool_calls", 0)
        rate = f"{denials / calls:.0%}" if calls else "n/a"
        rate_ceiling = _lim("max_denial_rate")
        rate_shown = (f"{rate_ceiling:.0%}"
                      if isinstance(rate_ceiling, (int, float)) else rate_ceiling)
        lines.append(f"- Permission denials (harness refusals only): {denials}"
                     f" / {_lim('max_permission_denials')}"
                     f" · {rate} of {calls} tool calls / {rate_shown}"
                     f" — trips only when BOTH are exceeded")
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
            lines += ["", "**Denied tool calls** (refused by the harness):", ""]
            lines.append("| tool | denials |")
            lines.append("|---|---|")
            for name, n in sorted(hist.items(), key=lambda kv: -kv[1]):
                lines.append(f"| `{name}` | {n} |")
        # A harness may refuse a call without surfacing it to either hook, and
        # then the table above is empty and an allowlist fix has nothing to
        # work from. What was ATTEMPTED comes from PreToolUse, which always
        # fires, so this record survives an unobservable refusal.
        attempted = breaker.get("attempted_tools") or {}
        if attempted:
            lines += ["", "**Tools attempted** (every PreToolUse):", ""]
            lines.append("| tool | calls | denied |")
            lines.append("|---|---|---|")
            for name, n in sorted(attempted.items(), key=lambda kv: -kv[1]):
                lines.append(f"| `{name}` | {n} | {hist.get(name, 0)} |")
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

    pr_ = sub.add_parser("refs", help="the ref names for this unit+FEAT, as "
                                      "KEY=VALUE lines for $GITHUB_OUTPUT")
    pr_.add_argument("--unit", default=".")
    pr_.add_argument("--feat", required=True)

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
    pv.add_argument("--repo-dir", default=".",
                    help="the git working tree to read branch evidence from "
                         "(default: cwd)")
    pv.add_argument("--json-out", default=None)

    pg = sub.add_parser("continue-gate")
    pg.add_argument("--feat", required=True)
    pg.add_argument("--mode", required=True, choices=("prod", "poc"))
    pg.add_argument("--unit", default=".")
    pg.add_argument("--base", default="main")
    pg.add_argument("--repo", default=None)
    pg.add_argument("--repo-dir", default=".")
    pg.add_argument("--since", default=None)
    pg.add_argument("--author", default=None)
    pg.add_argument("--attempt", type=int, required=True)
    pg.add_argument("--max-attempts", default="1")
    pg.add_argument("--spent", default="")
    pg.add_argument("--cap", default="")
    pg.add_argument("--head-before", default="")
    pg.add_argument("--head-now", default="")

    pr = sub.add_parser("report")
    pr.add_argument("--feat", required=True)
    pr.add_argument("--mode", required=True, choices=("prod", "poc"))
    pr.add_argument("--verify-json", default=None)
    pr.add_argument("--breaker-json", default=None)
    pr.add_argument("--checkpoint-ref", default=None)
    pr.add_argument("--out", default=None)
    pr.add_argument("--append-build-md", action="store_true")

    args = ap.parse_args()

    if args.cmd == "refs":
        print(f"impl={implementation_ref(args.unit, args.feat)}")
        print(f"checkpoint={checkpoint_ref(args.unit, args.feat)}")
        return 0

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
                     args.base, args.repo, since=args.since, author=authors,
                     repo_dir=args.repo_dir)
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

    if args.cmd == "continue-gate":
        authors = [a.strip() for a in (args.author or "").split(",") if a.strip()]
        rec = verify(args.root, args.feat, args.mode, args.unit, args.base,
                     args.repo, since=args.since, author=authors,
                     repo_dir=args.repo_dir)
        gate = continuation_gate(rec, args.attempt, args.max_attempts,
                                 args.spent, args.cap,
                                 args.head_before, args.head_now)
        print(f"go={'true' if gate['go'] else 'false'}")
        if gate["go"]:
            print(f"::notice title=Continuing::Attempt {gate['next_attempt']} of "
                  f"{gate['max_attempts']} — the terminal state is not reached "
                  f"and the last attempt committed work. Spend so far "
                  f"${gate['spent_usd']:.2f} of the ${gate['cap_usd']:.2f} "
                  f"start-gate (which bounds whether the next attempt BEGINS, "
                  f"not what it spends).", file=sys.stderr)
        else:
            print(f"::notice title=Not continuing::"
                  f"{'; '.join(gate['reasons'])}", file=sys.stderr)
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
