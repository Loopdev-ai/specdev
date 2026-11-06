#!/usr/bin/env python3
"""SubagentStop hook: a reviewer may not stop with a report the ledger cannot
record, and a report that can be recorded is saved - not transcribed.

The pre-PR review loop's ledger is mechanical, but the step that fed it was
not: the coordinator copied each reviewer's closing JSON block into a file by
hand. A reviewer that ended without a parseable block, or a coordinator that
paraphrased one, surfaced only as a refusal from `review_ledger.py record` -
after the reviewer's context was gone, so the only fix was a whole new
dispatch.

This hook runs as each subagent stops. For the three reviewers only
(spec-reviewer, code-reviewer, intent-reviewer):

  * it takes the LAST fenced JSON block of the reviewer's final message and
    checks it with review_ledger.report_problems(strict=True) - the same
    definition `record` uses, plus a scenario on every blocking finding;
  * invalid -> {"decision": "block", "reason": ...}: the reviewer keeps going
    and fixes its own report. At most MAX_BLOCKS times per agent, then it is
    let go (a hook must never trap an agent), and the coordinator sees a
    missing report and re-dispatches;
  * valid -> saved to <hook_dir>/<phase>/<agent>--<agent_id>.json, where
    `review_ledger.py record --from-hook` picks it up. An interrogate-mode
    report (it carries "coverage") goes to <hook_dir>/interrogate/ instead:
    it is the up-front questions, never a review pass.

Every other agent passes through untouched. An internal error FAILS OPEN
(exit 0, a note on stderr): this hook improves the loop's plumbing; the
ledger's `check` is still the gate.

Wiring: CI adds it to the settings file specdev-build.yml passes with
--settings; interactive sessions get it from the plugin's hooks/hooks.json
(`--source plugin`), which stands down when SPECDEV_CI_HOOKS is set so CI
never runs it twice.
"""
import json
import os
import re
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit("SpecDev tools require Python 3.10+")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_ledger  # noqa: E402  (vendored sibling module)

MAX_BLOCKS = 2
AGENT_PHASE = {a: ph for ph, agents in review_ledger.REVIEWERS.items()
               for a in agents}
_FENCE = re.compile(r"```[ \t]*(?:json)?[ \t]*\n(.*?)```", re.S | re.I)


def agent_name(agent_type: str) -> str:
    """'specdev:code-reviewer' (a plugin agent) and 'code-reviewer' (the
    vendored copy) are the same reviewer."""
    return (agent_type or "").rsplit(":", 1)[-1].strip()


def last_json_block(message: str):
    """(parsed, error). The LAST fenced block is the report: the agents are
    told to end with exactly one, and earlier blocks may be quoted input."""
    blocks = _FENCE.findall(message or "")
    if not blocks:
        return None, ("your report has no fenced JSON block. End it with "
                      "exactly one ```json block: {\"findings\": [...]}")
    try:
        return json.loads(blocks[-1]), None
    except json.JSONDecodeError as e:
        return None, f"your closing JSON block does not parse ({e})"


def _blocks_file(d: Path) -> Path:
    return d / "hook-blocks.json"


def _bump(d: Path, agent_id: str) -> int:
    """Count one more block for this agent; return the new count."""
    f = _blocks_file(d)
    try:
        seen = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, json.JSONDecodeError):
        seen = {}
    seen[agent_id] = int(seen.get(agent_id, 0)) + 1
    d.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(seen), encoding="utf-8")
    return seen[agent_id]


def _blocked_so_far(d: Path, agent_id: str) -> int:
    try:
        return int(json.loads(_blocks_file(d).read_text("utf-8"))
                   .get(agent_id, 0))
    except (OSError, ValueError, AttributeError):
        return 0


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "unknown")[:80]


def handle(payload: dict, directory: Path | None = None,
           repo_root=".") -> dict | None:
    """The hook's decision for one SubagentStop payload: a dict to print as
    JSON (a block), or None to let the agent stop."""
    if payload.get("hook_event_name") not in (None, "SubagentStop"):
        return None
    agent = agent_name(payload.get("agent_type", ""))
    phase = AGENT_PHASE.get(agent)
    if phase is None:
        return None
    d = directory or review_ledger.hook_dir(payload.get("cwd") or repo_root)
    agent_id = _safe(str(payload.get("agent_id") or payload.get("session_id")
                         or "unknown"))
    message = payload.get("last_assistant_message")
    if not isinstance(message, str):
        # Nothing to judge (an older Claude Code that does not send it).
        # Fail open: the coordinator still has --findings-json.
        print(f"review_hook: no last_assistant_message for {agent}; not "
              f"validating", file=sys.stderr)
        return None
    report, err = last_json_block(message)
    problems = [err] if err else review_ledger.report_problems(report,
                                                               strict=True)
    if problems:
        if _blocked_so_far(d, agent_id) >= MAX_BLOCKS:
            print(f"review_hook: {agent} {agent_id} still has an invalid "
                  f"report after {MAX_BLOCKS} attempts; letting it stop - "
                  f"no report is saved, so re-dispatch it",
                  file=sys.stderr)
            return None
        _bump(d, agent_id)
        return {"decision": "block",
                "reason": ("Your review report cannot be recorded by "
                           "review_ledger.py: " + "; ".join(problems[:8])
                           + ". Fix the report and end with exactly one "
                             "fenced ```json block. Do not redo the review.")}
    # An interrogate report (spec-reviewer's up-front questions) is kept
    # apart: `record --from-hook` must never take it for a review pass.
    out = d / ("interrogate" if review_ledger.is_interrogate(report)
               else phase)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{agent}--{agent_id}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[-2:] == ["--source", "plugin"] and os.environ.get(
            "SPECDEV_CI_HOOKS"):
        return 0  # CI wires this hook itself; never run it twice
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        decision = handle(payload)
    except Exception as e:  # noqa: BLE001  - fail open, never trap an agent
        print(f"review_hook: internal error, not validating "
              f"({type(e).__name__}: {e})", file=sys.stderr)
        return 0
    if decision:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
