#!/usr/bin/env python3
"""In-loop circuit breaker for the headless specdev-build agent.

Why this exists: `max_turns` is a poor bound. A run can accumulate denials and
failures indefinitely without approaching it, and a turn ceiling high enough to
let a real build finish is also high enough to be very expensive to reach. A
bound that only stops runaway spend after the money is gone is not a bound, so
this one runs inside the agent loop, as a Claude Code hook.

Wired via `--settings` (see specdev-build.yml). Runs as a `command` hook on
PreToolUse and PostToolUse, reading the hook payload on stdin and keeping
counters in a state file on the runner.

DESIGN NOTE - deliberate conservatism about hook semantics. Three things are
relied on, in descending order of certainty:

  1. PreToolUse fires before every tool call, with `tool_name` on stdin.
     Certain. Every hard bound here is derived from it.
  2. A hook can end the session by printing {"continue": false} and exiting 0.
     Documented. If a runtime ignores it, the breaker ALSO denies the call, so
     the agent stalls out instead of running free.
  3. Mid-run cost is NOT in the hook payload. It is read best-effort from the
     transcript JSONL at `transcript_path`, whose schema is undocumented and
     which may lag the live conversation. So cost is enforced when readable and
     is never the only bound: `max_wall_minutes` and `max_tool_calls` are
     derived from the clock and the call count, which are always observable.

The failure counter is likewise defensive: rather than depend on one event name
for tool failure, anything whose event name mentions failure/error, or whose
tool output carries an error marker, counts as a failure; a clean PostToolUse
resets the streak.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit(
        "SpecDev tools require Python 3.10+ (found "
        f"{sys.version_info.major}.{sys.version_info.minor}). "
        "On macOS the system python3 is 3.9.x; install a newer Python or use "
        "a virtualenv. In CI, actions/setup-python with python-version '3.x' "
        "satisfies this."
    )

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE_ENV = "SPECDEV_BREAKER_STATE"
DEFAULT_STATE = ".specdev-breaker.json"

_ERROR_MARKERS = re.compile(
    r"permission denied|requested permissions|has not been granted|"
    r"tool use was rejected|is not allowed|error:|exception|traceback",
    re.I)


def state_path() -> Path:
    return Path(os.environ.get(STATE_ENV) or DEFAULT_STATE)


def _num(env: str, default):
    raw = os.environ.get(env, "")
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return None if v <= 0 else v  # <=0 disables a limit


def limits() -> dict:
    return {
        "max_permission_denials": _num("SPECDEV_MAX_DENIALS", 15),
        "max_consecutive_tool_failures": _num("SPECDEV_MAX_CONSECUTIVE_FAILURES", 15),
        "max_cost_usd": _num("SPECDEV_MAX_COST_USD", 10),
        "max_wall_minutes": _num("SPECDEV_MAX_WALL_MINUTES", 0),
        "max_tool_calls": _num("SPECDEV_MAX_TOOL_CALLS", 0),
    }


def load_state() -> dict:
    p = state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "started_at": time.time(),
        "tool_calls": 0,
        "denials": 0,
        "consecutive_failures": 0,
        "max_consecutive_seen": 0,
        "denied_tools": {},
        "cost_usd": 0.0,
        "cost_source": "unavailable",
        "tripped": False,
        "trip_reason": None,
    }


def save_state(st: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def read_cost(transcript: str | None) -> float | None:
    """Best-effort accumulated cost from the transcript JSONL.

    The schema is undocumented, so this hunts for the usual spellings and
    returns None rather than guessing. A None result must never be treated as
    $0 - that would silently disable the cost ceiling."""
    if not transcript:
        return None
    p = Path(transcript)
    if not p.exists():
        return None
    total, running = 0.0, None
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for key in ("total_cost_usd", "totalCostUsd", "total_cost"):
                    if isinstance(obj.get(key), (int, float)):
                        running = float(obj[key])   # cumulative: keep the last
                for key in ("cost_usd", "costUSD"):
                    if isinstance(obj.get(key), (int, float)):
                        total += float(obj[key])    # per-turn: sum
    except OSError:
        return None
    if running is not None:
        return running
    return total if total > 0 else None


def is_failure(payload: dict) -> bool:
    event = str(payload.get("hook_event_name", ""))
    if re.search(r"fail|error|denied|reject", event, re.I):
        return True
    for key in ("tool_output", "tool_response", "tool_result", "error"):
        val = payload.get(key)
        if isinstance(val, dict):
            if val.get("is_error") or val.get("error"):
                return True
            val = json.dumps(val)
        if isinstance(val, str) and _ERROR_MARKERS.search(val):
            return True
    return False


def evaluate(st: dict, lim: dict) -> str | None:
    """The trip reason, or None. Any limit set to 0/absent is disabled."""
    if lim["max_permission_denials"] and st["denials"] >= lim["max_permission_denials"]:
        return (f"permission denials reached {st['denials']} "
                f"(limit {int(lim['max_permission_denials'])})")
    if (lim["max_consecutive_tool_failures"]
            and st["consecutive_failures"] >= lim["max_consecutive_tool_failures"]):
        return (f"{st['consecutive_failures']} consecutive tool failures "
                f"(limit {int(lim['max_consecutive_tool_failures'])})")
    if (lim["max_cost_usd"] and st.get("cost_source") != "unavailable"
            and st["cost_usd"] >= lim["max_cost_usd"]):
        return (f"accumulated cost ${st['cost_usd']:.2f} reached the ceiling "
                f"${lim['max_cost_usd']:.2f}")
    if lim["max_wall_minutes"]:
        mins = (time.time() - st.get("started_at", time.time())) / 60.0
        if mins >= lim["max_wall_minutes"]:
            return (f"wall clock {mins:.0f} min reached the ceiling "
                    f"{int(lim['max_wall_minutes'])} min")
    if lim["max_tool_calls"] and st["tool_calls"] >= lim["max_tool_calls"]:
        return (f"tool calls reached {st['tool_calls']} "
                f"(limit {int(lim['max_tool_calls'])})")
    return None


def _trip_output(reason: str, event: str) -> dict:
    """Stop the session AND deny this call.

    Both, deliberately: `continue: false` is the documented stop, and the deny
    is the belt-and-braces path if a runtime honours only the permission
    decision. Denying without stopping would leave the agent burning turns
    against a wall, which is the failure this whole file exists to prevent."""
    out = {
        "continue": False,
        "stopReason": f"SpecDev circuit breaker: {reason}",
        "systemMessage": (
            f"SpecDev circuit breaker tripped: {reason}. Stop immediately. Do "
            f"not retry. The checkpoint will be pushed and the job will exit "
            f"with a distinct status so a human can re-dispatch."),
    }
    if event == "PreToolUse":
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"circuit breaker: {reason}",
        }
    return out


def handle(payload: dict) -> tuple[dict | None, dict]:
    st = load_state()
    lim = limits()
    event = str(payload.get("hook_event_name") or "PreToolUse")
    tool = str(payload.get("tool_name") or "unknown")

    if event == "PreToolUse":
        st["tool_calls"] += 1
        cost = read_cost(payload.get("transcript_path"))
        if cost is not None:
            st["cost_usd"] = cost
            st["cost_source"] = "transcript"
    elif is_failure(payload):
        st["consecutive_failures"] += 1
        st["max_consecutive_seen"] = max(st["max_consecutive_seen"],
                                         st["consecutive_failures"])
        st["denials"] += 1
        st["denied_tools"][tool] = st["denied_tools"].get(tool, 0) + 1
    else:
        st["consecutive_failures"] = 0

    reason = st["trip_reason"] if st.get("tripped") else evaluate(st, lim)
    out = None
    if reason:
        if not st.get("tripped"):
            st["tripped"] = True
            st["trip_reason"] = reason
        out = _trip_output(reason, event)
    st["limits"] = {k: (int(v) if v and float(v).is_integer() else v)
                    for k, v in lim.items()}
    save_state(st)
    return out, st


def verdict() -> int:
    """Post-run: exit 3 (distinct from a hard failure) if the breaker tripped."""
    st = load_state()
    if not st.get("tripped"):
        print(f"circuit breaker ok - {st.get('tool_calls', 0)} tool calls, "
              f"{st.get('denials', 0)} denials")
        return 0
    print(f"::error title=SpecDev circuit breaker::{st.get('trip_reason')}")
    print(f"The agent was aborted after {st.get('tool_calls', 0)} tool calls "
          f"and {st.get('denials', 0)} denials. This is NOT a code failure - "
          f"re-dispatch the workflow to resume from the pushed checkpoint.",
          file=sys.stderr)
    return 3


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "verdict":
        return verdict()
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        save_state(load_state())
        return 0
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    # A hook that crashes must never take the build with it, nor silently
    # disable the bound: fail open on the call, but leave the state intact.
    try:
        out, _ = handle(payload)
    except Exception as e:  # noqa: BLE001
        print(f"specdev circuit breaker: non-fatal hook error: {e}",
              file=sys.stderr)
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
