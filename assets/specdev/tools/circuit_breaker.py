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
     which may lag the live conversation. In practice the cumulative cost field
     appears in the TERMINAL result record, so a hook reading that transcript
     mid-run usually has nothing to sum and the cost limb never arms at all.
     Treat the cost ceiling as opportunistic. The bounds that actually hold are
     `max_wall_minutes` and `max_tool_calls`, derived from the clock and the
     call count, which are always observable — they are configured in ci.json
     and armed on every run, not left at zero as "covered by cost".

Every limit's default comes from the bundled ci.json via run_manifest, so
there is exactly one place to change one. Whether cost was ever measured is
recorded in the state file as `cost_source` and reported after the run: a
ceiling that could not engage is stated, never rendered as $0.

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


def _load_defaults() -> tuple[dict, str, dict]:
    """Limit defaults + the env-var mapping, from run_manifest — the single
    source of truth. This file used to carry its own literals, which is how a
    repo whose arming step silently failed ran on constants nobody configured
    while the report showed the ceiling it had asked for.

    Imported defensively: a hook that cannot import must not take the build
    down, and must not pretend to a default it does not have."""
    fallback_env = {
        "max_permission_denials": "SPECDEV_MAX_DENIALS",
        "max_consecutive_tool_failures": "SPECDEV_MAX_CONSECUTIVE_FAILURES",
        "max_cost_usd": "SPECDEV_MAX_COST_USD",
        "max_wall_minutes": "SPECDEV_MAX_WALL_MINUTES",
        "max_tool_calls": "SPECDEV_MAX_TOOL_CALLS",
    }
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import run_manifest  # noqa: PLC0415  (vendored sibling module)
        return (dict(run_manifest.CI_DEFAULTS),
                run_manifest.CI_DEFAULTS_SOURCE,
                dict(run_manifest.BREAKER_ENV))
    except Exception as e:  # noqa: BLE001
        return {}, f"unavailable (run_manifest not importable: {e})", fallback_env


DEFAULTS, DEFAULTS_SOURCE, ENV_FOR = _load_defaults()


def _num(env: str, default):
    """The env var, else `default`. A value <= 0, or no value at all, disables
    the limit — and `limits_armed()` says so afterwards rather than leaving a
    reader to infer it from a zero."""
    raw = os.environ.get(env, "")
    v = None
    if raw is not None and str(raw).strip() != "":
        try:
            v = float(raw)
        except ValueError:
            v = None
    if v is None:
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v <= 0 else v  # <=0 disables a limit


def limits() -> dict:
    return {key: _num(var, DEFAULTS.get(key))
            for key, var in ENV_FOR.items()}


def unarmed(lim: dict) -> list[str]:
    """Which bounds are OFF for this run. Reported, never assumed."""
    return [k for k, v in lim.items() if not v]


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

    # Read on BOTH events, not just PreToolUse: the cumulative cost field tends
    # to land late in the transcript, so every extra look is a chance the limb
    # arms at all. It still may never promote — which `cost_source` records.
    cost = read_cost(payload.get("transcript_path"))
    if cost is not None:
        st["cost_usd"] = cost
        st["cost_source"] = "transcript"

    if event == "PreToolUse":
        st["tool_calls"] += 1
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
    st["limits_source"] = DEFAULTS_SOURCE
    st["unarmed_limits"] = unarmed(lim)
    save_state(st)
    return out, st


def _report_what_was_not_measured(st: dict) -> None:
    """A bound that could not engage is a fact about the run, and belongs in
    the log of that run — not in a reader's head. Silence here is what let a
    configured $10 ceiling sit inactive for an entire multi-wave build while
    the outcome report rendered it as `$0.0 / $10`."""
    lim = st.get("limits") or {}
    ceiling = lim.get("max_cost_usd")
    if ceiling and st.get("cost_source", "unavailable") == "unavailable":
        print(f"::warning title=Cost ceiling inactive::Mid-run cost was never "
              f"readable from the agent transcript, so the ${ceiling} ceiling "
              f"could not engage at any point in this run. This is NOT $0 "
              f"spent - see the build job's total_cost_usd output for the "
              f"actual figure. The wall-clock and tool-call ceilings are the "
              f"bounds that held.")
    for name in st.get("unarmed_limits") or []:
        print(f"::warning title=Bound disabled::{name} was 0/unset for this "
              f"run, so that bound never applied.")


def verdict() -> int:
    """Post-run: exit 3 (distinct from a hard failure) if the breaker tripped."""
    st = load_state()
    _report_what_was_not_measured(st)
    if not st.get("tripped"):
        print(f"circuit breaker ok - {st.get('tool_calls', 0)} tool calls, "
              f"{st.get('denials', 0)} denials, cost "
              f"{'$%.2f' % st['cost_usd'] if st.get('cost_source', 'unavailable') != 'unavailable' else 'not measured'}")
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
