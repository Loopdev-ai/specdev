#!/usr/bin/env python3
"""When a build stops for BUDGET, bring a person in with the case for and
against continuing - never just stop, never continue on its own.

A CI build can run out of budget before its terminal state: the circuit
breaker trips on cost, wall clock or tool calls; the bounded continuation
runs out of attempts or reaches its spend cap; or the Stop hook uses up
max_stop_blocks. Until now that was simply a failed build, and whoever
re-dispatched it did so blind. Now:

  1. `facts` (this tool, mechanical) decides whether the stop was a budget
     stop and gathers what a decision needs: why it stopped, progress by
     priority, whether the review loop is converging, what was spent, and a
     rough estimate of what finishing would cost.
  2. The `budget-assessor` agent - read-only, and never the coordinator that
     spent the budget - judges those facts and recommends `continue`,
     `must-only` or `stop`, with an extra budget and its reasons.
     `extract` validates its verdict.
  3. `render` writes <unit>/.specdev/ASSESSMENT.md (committed with the
     checkpoint) and the run summary: the facts, the verdict, and the exact
     command that resumes the build. A PERSON decides. Nothing here resumes
     anything; if nobody answers, the build stays stopped.
  4. A resume the person approves with scope `must-only` runs
     `defer-unbuilt`: every should/could REQ with no linked test is marked
     Deferred in the spec - visibly, in the PR diff - so the build finishes
     the musts. A `must` REQ is never deferred.

Usage:
    assess_budget.py --root <unit> facts --feat F --mode M [--base main]
        [--breaker-json F] [--attempts-used N] [--max-attempts N]
        [--spent a,b,c] [--cap USD] [--stop-state F] [--max-stop-blocks N]
        [--out facts.json] [--github-output]
    assess_budget.py extract --execution-file F --out assessment.json
    assess_budget.py --root <unit> render --facts F [--assessment A]
        --repo owner/name --workflow-ref main --out ASSESSMENT.md
    assess_budget.py --root <unit> defer-unbuilt --reason TEXT
"""
import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit("SpecDev tools require Python 3.10+")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_outcome  # noqa: E402  (vendored sibling modules)
import gen_traceability  # noqa: E402
import review_ledger  # noqa: E402

SPEC_REL = ".specdev/spec.md"
ASSESSMENT_REL = ".specdev/ASSESSMENT.md"
RECOMMENDATIONS = ("continue", "must-only", "stop")
# Breaker trips that mean "out of budget" - matched on how
# circuit_breaker.evaluate() words each one, from the start of the reason.
# Denials and consecutive failures mean the run is broken, not short of
# money: more budget would not help. (A denial trip's reason also mentions
# "tool calls", which is why a loose word match is not enough.)
_BUDGET_TRIP = re.compile(r"^\s*(accumulated cost |wall clock |tool calls "
                          r"reached )", re.I)


# ---- facts ----------------------------------------------------------------

def _floats(value) -> list[float]:
    out = []
    for part in str(value or "").split(","):
        try:
            f = float(part.strip())
        except ValueError:
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def _first(value, default=0.0) -> float:
    vals = _floats(value)
    return vals[0] if vals else default


def _load_json(path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def stop_reasons(breaker: dict, attempts_used: int, max_attempts: int,
                 spent: float, cap: float, stop_blocks: int,
                 max_stop_blocks: int) -> tuple[list[str], list[str]]:
    """(budget reasons, other reasons) the build stopped."""
    budget, other = [], []
    if breaker.get("tripped"):
        why = str(breaker.get("trip_reason") or "unknown")
        (budget if _BUDGET_TRIP.search(why) else other).append(
            f"circuit breaker: {why}")
    if max_attempts and attempts_used >= max_attempts:
        budget.append(f"all {max_attempts} continuation attempts used")
    if cap and spent >= cap:
        budget.append(f"${spent:.2f} spent reaches the ${cap:.2f} "
                      f"continuation cap")
    if max_stop_blocks and stop_blocks >= max_stop_blocks:
        budget.append(f"the Stop hook kept the coordinator going "
                      f"{stop_blocks} times (max_stop_blocks)")
    return budget, other


def req_progress(root=".") -> dict:
    """Every REQ in the active spec with its priority, whether a test is
    linked to it, and whether it is deferred."""
    spec = Path(root) / SPEC_REL
    text = spec.read_text(encoding="utf-8") if spec.exists() else ""
    blocks = gen_traceability.req_blocks(text)
    deferred = gen_traceability.deferred_reqs(text)
    tested = gen_traceability.find_tests(Path(root))
    reqs = []
    for rid, block in blocks.items():
        reqs.append({"id": rid,
                     "priority": gen_traceability.req_priority(block),
                     "tested": rid in tested,
                     "deferred": rid in deferred})
    summary = {}
    for pri in ("must", "should", "could"):
        mine = [r for r in reqs if r["priority"] == pri]
        summary[pri] = {"total": len(mine),
                        "tested": sum(r["tested"] for r in mine),
                        "deferred": sum(r["deferred"] for r in mine)}
    return {"reqs": reqs, "by_priority": summary}


def convergence(blocking_per_pass: list[int]) -> str:
    """How the review loop is going, from blocking findings per pass:
    not-started, clean, converging, stalled or diverging."""
    b = blocking_per_pass
    if not b:
        return "not-started"
    if b[-1] == 0:
        return "clean"
    if len(b) < 2:
        return "converging"  # one pass with findings: nothing to compare
    if b[-1] > b[-2]:
        return "diverging"
    if len(b) >= 3 and b[-1] >= b[-3]:
        return "stalled"
    return "converging" if b[-1] < b[0] else "stalled"


def facts(root=".", feat="", mode="prod", base="main", repo_dir=".",
          breaker=None, attempts_used=1, max_attempts=0, spent=(),
          cap=0.0, stop_blocks=0, max_stop_blocks=0, unit=None) -> dict:
    breaker = breaker or {}
    spent_total = round(sum(spent), 2)
    budget, other = stop_reasons(breaker, attempts_used, max_attempts,
                                 spent_total, cap, stop_blocks,
                                 max_stop_blocks)
    problems = build_outcome.local_terminal_problems(root, feat, mode, base,
                                                     repo_dir)
    deliberate = build_outcome.deliberate_stop(root)
    progress = req_progress(root)
    try:
        st = review_ledger.stats(root, "impl", feat or None)["phases"]["impl"]
    except Exception:  # noqa: BLE001  - measurement only
        st = None
    bpp = st["blocking_per_pass"] if st else []
    rc, out = build_outcome._git(["rev-list", "--count", f"{base}..HEAD"],
                                 repo_dir)
    commits = int(out.strip()) if rc == 0 and out.strip().isdigit() else None

    pri = progress["by_priority"]
    done = sum(v["tested"] for v in pri.values())
    left = sum(v["total"] - v["tested"] - v["deferred"] for v in pri.values())
    left_must = pri["must"]["total"] - pri["must"]["tested"]
    estimate = (round(spent_total / done * left, 2)
                if spent_total and done and left else None)
    assess = bool(budget) and bool(problems) and not deliberate
    why_not = ("" if assess else
               "the terminal state was reached" if not problems else
               "BUILD.md records a deliberate stop - a person is already "
               "needed" if deliberate else
               "the stop was not for budget ("
               + ("; ".join(other) or "no budget limit was reached") + ")")
    return {
        "feat": feat, "mode": mode, "unit": unit or str(root),
        "assess": assess, "not_assessed_because": why_not,
        "budget_reasons": budget, "other_reasons": other,
        "terminal_problems": problems,
        "progress": progress,
        "reqs_done": done, "reqs_left": left, "musts_left": left_must,
        "review": {"blocking_per_pass": bpp,
                   "trend": convergence(bpp),
                   "reraised": st["reraised_after_dismissal"] if st else 0,
                   "dismissed": st["dismissed"] if st else 0},
        "commits_ahead": commits,
        "spend": {"spent_usd": spent_total, "per_attempt": list(spent),
                  "attempts_used": attempts_used,
                  "max_attempts": max_attempts, "cap_usd": cap,
                  "breaker_cost_usd": breaker.get("cost_usd"),
                  "breaker_cost_source": breaker.get("cost_source"),
                  "rough_cost_to_finish_usd": estimate},
    }


# ---- the assessor's verdict ----------------------------------------------

def _final_text(execution_file) -> str:
    """The assessor's final message from claude-code-action's execution
    file: a JSON array (or JSON lines) of SDK messages."""
    raw = Path(execution_file).read_text(encoding="utf-8")
    try:
        msgs = json.loads(raw)
        if isinstance(msgs, dict):
            msgs = [msgs]
    except ValueError:
        msgs = []
        for line in raw.splitlines():
            try:
                msgs.append(json.loads(line))
            except ValueError:
                continue
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("type") == "result" and \
                isinstance(m.get("result"), str):
            return m["result"]
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("type") != "assistant":
            continue
        content = (m.get("message") or {}).get("content") or []
        texts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        if any(texts):
            return "\n".join(texts)
    return ""


def assessment_problems(a) -> list[str]:
    if not isinstance(a, dict):
        return ["the assessment is not a JSON object"]
    p = []
    if a.get("recommendation") not in RECOMMENDATIONS:
        p.append(f"recommendation must be one of {RECOMMENDATIONS}")
    extra = a.get("extra_budget_usd")
    if a.get("recommendation") in ("continue", "must-only"):
        if (isinstance(extra, bool) or not isinstance(extra, (int, float))
                or not math.isfinite(extra) or extra <= 0):
            p.append("extra_budget_usd must be a positive number when "
                     "recommending a resume")
    for key in ("summary", "reasoning"):
        if not str(a.get(key) or "").strip():
            p.append(f"{key} is required")
    for key in ("remaining", "risks"):
        if not isinstance(a.get(key, []), list):
            p.append(f"{key} must be a list")
    return p


def extract(execution_file) -> tuple[dict | None, list[str]]:
    import review_hook  # noqa: PLC0415  (reuse the fenced-block reader)
    try:
        text = _final_text(execution_file)
    except OSError as e:
        return None, [f"cannot read {execution_file}: {e}"]
    if not text:
        return None, ["the assessor left no final message"]
    a, err = review_hook.last_json_block(text)
    if err:
        return None, [err]
    problems = assessment_problems(a)
    return (a if not problems else None), problems


# ---- render ----------------------------------------------------------------

def resume_command(f: dict, scope: str, budget, workflow_ref: str) -> str:
    unit = f.get("unit") or "."
    return (f"gh workflow run specdev-build.yml --ref {workflow_ref} "
            f"-f feat={f.get('feat')} -f mode={f.get('mode')} "
            + (f"-f unit={unit} " if unit != "." else "")
            + f"-f resume_budget_usd={budget} -f scope={scope}")


def render(f: dict, a: dict | None, repo: str, workflow_ref: str,
           assess_errors=()) -> str:
    sp, rv, pri = f["spend"], f["review"], f["progress"]["by_priority"]
    lines = [f"# Budget assessment — {f['feat']}", ""]
    lines += ["The build stopped for budget before its terminal state:", ""]
    lines += [f"- {r}" for r in f["budget_reasons"]] + [""]
    if a:
        rec = a["recommendation"]
        lines += [f"**Recommendation: {rec}**"
                  + (f" with up to **${a['extra_budget_usd']}** more"
                     if rec != "stop" else ""), "",
                  a["summary"], "", "**Why:** " + a["reasoning"], ""]
        if a.get("remaining"):
            lines += ["**What is left:**", ""] + [f"- {x}" for x in
                                                   a["remaining"]] + [""]
        if a.get("risks"):
            lines += ["**Risks:**", ""] + [f"- {x}" for x in a["risks"]] + [""]
    else:
        lines += ["**No recommendation**: the assessor did not produce a "
                  "valid verdict"
                  + (f" ({'; '.join(assess_errors)})" if assess_errors else "")
                  + ". The facts below are complete; decide from them.", ""]
    lines += ["## Facts", "",
              "| Priority | REQs | Tested | Deferred |", "|---|---|---|---|"]
    for p in ("must", "should", "could"):
        v = pri[p]
        lines.append(f"| {p} | {v['total']} | {v['tested']} | "
                     f"{v['deferred']} |")
    est = sp["rough_cost_to_finish_usd"]
    lines += ["",
              f"- **Spent:** ${sp['spent_usd']:.2f} over {sp['attempts_used']}"
              f" attempt(s) {sp['per_attempt']}; continuation cap "
              f"${sp['cap_usd']:.2f}.",
              f"- **Rough cost to finish:** "
              + (f"${est:.2f} (spend so far per tested REQ × REQs left — a "
                 f"crude extrapolation)" if est is not None else "n/a"),
              f"- **Review loop:** {rv['trend']}; blocking findings per pass "
              f"{rv['blocking_per_pass'] or '—'}; {rv['dismissed']} "
              f"dismissed, {rv['reraised']} re-raised after dismissal.",
              f"- **Commits beyond the base:** {f['commits_ahead']}",
              f"- **Still missing for the terminal state:** "
              + ("; ".join(f["terminal_problems"][:5]) or "nothing"), ""]
    if f["other_reasons"]:
        lines += [f"- **Also:** {'; '.join(f['other_reasons'])}", ""]
    budget = (a or {}).get("extra_budget_usd") or "<USD>"
    lines += ["## Your decision", "",
              "Nothing resumes on its own; if you do nothing the build stays "
              "stopped. Every resume restarts from the pushed implementation "
              "branch under the budget you approve.", "",
              "- **Continue, full scope:**", "",
              f"  `{resume_command(f, 'all', budget, workflow_ref)}`", "",
              "- **Continue, musts only** (every should/could REQ not yet "
              "tested is marked Deferred in the spec, visibly in the PR):", "",
              f"  `{resume_command(f, 'must-only', budget, workflow_ref)}`",
              "",
              "- **Stop:** do nothing, or open the PR from the pushed branch "
              "and decide there.", ""]
    if repo:
        lines += [f"Or from the web: https://github.com/{repo}/actions/"
                  f"workflows/specdev-build.yml → *Run workflow*, with the "
                  f"same inputs.", ""]
    return "\n".join(lines)


# ---- defer -----------------------------------------------------------------

def defer_unbuilt(root=".", reason="") -> list[str]:
    """Mark every should/could REQ with no linked test as Deferred, in its
    own block of the spec. Returns the ids deferred now. Musts are never
    touched; already-deferred REQs are left as they are."""
    reason = (reason or "").strip()
    if not reason:
        raise SystemExit("a deferral needs a reason (who approved it, where)")
    spec = Path(root) / SPEC_REL
    text = spec.read_text(encoding="utf-8")
    progress = req_progress(root)
    targets = [r["id"] for r in progress["reqs"]
               if r["priority"] in ("should", "could")
               and not r["tested"] and not r["deferred"]]
    blocks = gen_traceability.req_blocks(text)
    for rid in targets:
        block = blocks[rid]
        body = block.rstrip("\n")
        new = body + f"\n- **Deferred:** {reason}\n" + block[len(body) + 1:]
        text = text.replace(block, new, 1)
    if targets:
        spec.write_text(text, encoding="utf-8")
    return targets


# ---- CLI -------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("facts")
    pf.add_argument("--feat", required=True)
    pf.add_argument("--mode", default="prod", choices=("prod", "poc"))
    pf.add_argument("--base", default="main")
    pf.add_argument("--repo-dir", default=".")
    pf.add_argument("--breaker-json", default=None)
    pf.add_argument("--attempts-used", type=int, default=1)
    pf.add_argument("--max-attempts", default="0")
    pf.add_argument("--spent", default="",
                    help="each attempt's cost, comma-separated")
    pf.add_argument("--cap", default="0")
    pf.add_argument("--stop-state", default=None)
    pf.add_argument("--max-stop-blocks", default="0")
    pf.add_argument("--unit", default=None,
                    help="the unit as the resume command names it "
                         "(default: --root)")
    pf.add_argument("--out", default=None)
    pf.add_argument("--github-output", action="store_true",
                    help="also print assess=true|false for $GITHUB_OUTPUT")

    pe = sub.add_parser("extract")
    pe.add_argument("--execution-file", required=True)
    pe.add_argument("--out", required=True)
    pe.add_argument("--errors-out", default=None,
                    help="write why there is no valid verdict here (for "
                         "render); emptied when the verdict is valid")

    pr = sub.add_parser("render")
    pr.add_argument("--facts", required=True)
    pr.add_argument("--assessment", default=None)
    pr.add_argument("--assessment-errors", default="")
    pr.add_argument("--assessment-errors-file", default=None)
    pr.add_argument("--repo", default="")
    pr.add_argument("--workflow-ref", default="main")
    pr.add_argument("--out", default=None)

    pd = sub.add_parser("defer-unbuilt")
    pd.add_argument("--reason", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "facts":
        stop = _load_json(args.stop_state) if args.stop_state else {}
        f = facts(args.root, args.feat, args.mode, args.base, args.repo_dir,
                  _load_json(args.breaker_json) if args.breaker_json else {},
                  args.attempts_used, int(_first(args.max_attempts)),
                  _floats(args.spent), _first(args.cap),
                  int(stop.get("blocks", 0) or 0),
                  int(_first(args.max_stop_blocks)), args.unit)
        if args.out:
            Path(args.out).write_text(json.dumps(f, indent=2) + "\n",
                                      encoding="utf-8")
        if args.github_output:
            print(f"assess={'true' if f['assess'] else 'false'}")
        else:
            print(json.dumps(f, indent=2))
        if not f["assess"]:
            print(f"not assessing: {f['not_assessed_because']}",
                  file=sys.stderr)
        return 0
    if args.cmd == "extract":
        a, problems = extract(args.execution_file)
        if args.errors_out:
            Path(args.errors_out).write_text(
                "\n".join(problems) + ("\n" if problems else ""),
                encoding="utf-8")
        if a is None:
            for p in problems:
                print(f"ERROR: {p}", file=sys.stderr)
            return 1
        Path(args.out).write_text(json.dumps(a, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"assessment: {a['recommendation']}")
        return 0
    if args.cmd == "render":
        f = _load_json(args.facts)
        a = _load_json(args.assessment) if args.assessment else None
        if a is not None and assessment_problems(a):
            a = None
        raw_errs = args.assessment_errors
        if args.assessment_errors_file:
            try:
                raw_errs += "\n" + Path(args.assessment_errors_file).read_text(
                    encoding="utf-8")
            except OSError:
                pass
        errs = [e for e in raw_errs.split("\n") if e.strip()] if a is None \
            else []
        md = render(f, a, args.repo, args.workflow_ref, errs)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
        sys.stdout.write(md)
        return 0
    if args.cmd == "defer-unbuilt":
        ids = defer_unbuilt(args.root, args.reason)
        print("deferred: " + (", ".join(ids) if ids else "none"))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
