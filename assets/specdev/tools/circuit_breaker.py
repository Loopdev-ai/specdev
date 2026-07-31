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

TWO PREDICATES, DELIBERATELY. `is_denial` is narrow — only a harness REFUSAL —
and is the sole numerator of the cumulative `max_permission_denials` ceiling.
`is_failure` stays broad, includes denials, and feeds only the consecutive
streak, which resets on the first success. One predicate fed both, and since
`error:`/`exception`/`traceback` are what a failing command prints and this
pipeline is TDD, every red test spent a ceiling named for permission denials.

The denial ceiling is also a CONJUNCTION, not a count: it trips only when the
denials clear `max_permission_denials` AND exceed `max_denial_rate` of tool
calls. A bare count is scale-dependent — 16 denials in 900 calls is noise in a
long healthy build, 20 in 40 is a broken one — and the floor doubles as the
minimum-sample guard the rate needs.
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

# Text that LOOKS like a refusal. Tallied as a hint, never as a denial.
#
# Text cannot answer the only question a cumulative denial ceiling exists to
# answer — "is --allowedTools wrong?" — because the same words arrive from
# things that have nothing to do with the allowlist. A real run hit
# `403: GitHub Actions is not permitted to create or approve pull requests`,
# wrote it into BUILD.md, and then scored a fresh "denial" on every subsequent
# Read of that file. A remote service refusing an API call, and a checkpoint
# quoting an earlier error, are both indistinguishable from a harness refusal
# at the level of a regex — and any pattern is one string away from the next
# instance of this, whichever string it is.
#
# So the hints are kept (a runtime may report refusals only as output, which
# is the case the old behaviour was right about) and demoted: they are counted
# per tool, rendered in the outcome record, and count only as a resetting
# failure. Many hints with zero structured denials is itself the datum — it
# says this runtime is text-only.
_DENIAL_MARKERS = re.compile(
    r"permission denied|requested permissions|has not been granted|"
    r"tool use was rejected|is not allowed",
    re.I)

# A transient upstream condition: throttling, an overloaded or unavailable
# service, a timeout, a dropped connection. The OPPOSITE of a stuck agent —
# and it arrives precisely when the coordinator fans out to subagents, so an
# unclassified 429 makes the consecutive-failure bound fire hardest exactly
# when nothing is wrong.
_TRANSIENT_MARKERS = re.compile(
    r"\b429\b|too many requests|rate.?limit|overloaded|\b50[234]\b|"
    r"service unavailable|timed? ?out|ETIMEDOUT|ECONNRESET|EAI_AGAIN|"
    r"connection reset|temporarily unavailable",
    re.I)

# ANY tool failure, refusals included. `error:`, `exception` and `traceback`
# are what a failing COMMAND prints — and this pipeline is TDD, so a red test
# run is the normal first half of every component and prints all three. They
# belong to the streak counter, which resets on the first success, and must
# never reach the cumulative ceiling: a control that fires on correct
# behaviour is the same class of bug as the allowlist that denied the agent
# its own first instruction.
_ERROR_MARKERS = re.compile(
    _DENIAL_MARKERS.pattern + r"|error:|exception|traceback", re.I)

# A refusal may arrive as a structured decision rather than as text, on either
# hook event depending on the runtime.
_DECISION_KEYS = ("permissionDecision", "permission_decision", "decision",
                  "behavior", "behaviour")
_DENY_VALUES = {"deny", "denied", "block", "blocked", "reject", "rejected"}
_DECISION_CONTAINERS = ("hookSpecificOutput", "tool_response", "tool_result",
                        "tool_output", "permission", "error")


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
        "max_denial_rate": "SPECDEV_MAX_DENIAL_RATE",
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


# Keys that are FRACTIONS, not ceilings. `_num` ends in `None if v <= 0`,
# which is the right rule for a ceiling ("0 disables it") and exactly the
# wrong one for a rate: a configured rate of 0 means "no rate constraint, the
# count alone trips" — stricter, not disabled. Reading it through `_num` would
# silently turn the strictest setting into the loosest.
RATE_KEYS = ("max_denial_rate",)


def _rate(env: str, default):
    """A fraction in [0, 1]. Returns None only when there is no value at all."""
    raw = os.environ.get(env, "")
    v = None
    if str(raw).strip() != "":
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
    return max(0.0, v)


def limits() -> dict:
    return {key: (_rate(var, DEFAULTS.get(key)) if key in RATE_KEYS
                  else _num(var, DEFAULTS.get(key)))
            for key, var in ENV_FOR.items()}


def unarmed(lim: dict) -> list[str]:
    """Which bounds are OFF for this run. Reported, never assumed.

    A rate of 0 is not "off" — it is the strictest setting, so it is never
    reported as a disabled bound."""
    return [k for k, v in lim.items() if not v and k not in RATE_KEYS]


def fresh_state() -> dict:
    return {
        "started_at": time.time(),
        "tool_calls": 0,
        "denials": 0,
        "consecutive_failures": 0,
        "max_consecutive_seen": 0,
        "denied_tools": {},
        "attempted_tools": {},
        "denial_text_hints": {},
        "transient_tools": {},
        "transient_events": 0,
        "cost_usd": 0.0,
        "cost_source": "unavailable",
        "tripped": False,
        "trip_reason": None,
    }


def load_state() -> dict:
    """Always a COMPLETE state. Starting from `fresh_state` and layering the
    file over it means a state written by an older version — or a hand-built
    one in a test — cannot KeyError a hook that must never crash."""
    st = fresh_state()
    p = state_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                st.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return st


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


def _denial_decision(payload: dict) -> bool:
    """A structured deny/block anywhere the runtime plausibly puts one."""
    containers = [payload]
    for key in _DECISION_CONTAINERS:
        val = payload.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for c in containers:
        for k in _DECISION_KEYS:
            v = c.get(k)
            if isinstance(v, str) and v.strip().lower() in _DENY_VALUES:
                return True
    return False


def _payload_text(payload: dict) -> str:
    out = []
    for key in ("tool_output", "tool_response", "tool_result", "error"):
        val = payload.get(key)
        if isinstance(val, dict):
            val = json.dumps(val)
        if isinstance(val, str):
            out.append(val)
    return "\n".join(out)


def is_denial(payload: dict) -> bool:
    """True only for an AUTHORITATIVE refusal: a structured permission
    decision of deny/block, or a hook event that names denial. Never text.

    This is the numerator of the permission-denial ceiling, so it has to mean
    "the harness refused this call" and nothing else. It was `is_failure`,
    which spent the ceiling on every red test in a TDD build; narrowing it to
    refusal TEXT was still wrong, because a 403 from a remote API, or a
    checkpoint file quoting one, reads identically to a harness refusal and
    says nothing about whether --allowedTools is right. A count of those
    aborted a six-wave build that had finished its work, at 15 denials in 865
    calls.

    Checked on BOTH hook events: which one carries a refusal is
    runtime-dependent."""
    if _denial_decision(payload):
        return True
    return bool(re.search(r"denied|reject|blocked",
                          str(payload.get("hook_event_name", "")), re.I))


def denial_text_hint(payload: dict) -> bool:
    """Refusal-shaped TEXT with no structured decision behind it. Counted and
    reported so the uncertain case is measurable rather than guessed at."""
    return bool(_DENIAL_MARKERS.search(_payload_text(payload)))


def is_transient(payload: dict) -> bool:
    """A throttle or an upstream blip. Counted and reported, but it must
    neither extend the consecutive-failure streak nor reset it: a 429
    sprinkled through a genuine failure run tells you nothing in either
    direction, so it must not hide a streak any more than it may manufacture
    one."""
    if _TRANSIENT_MARKERS.search(_payload_text(payload)):
        return True
    for key in ("tool_output", "tool_response", "tool_result", "error"):
        val = payload.get(key)
        if isinstance(val, dict):
            for field in ("status", "status_code", "statusCode", "code"):
                if str(val.get(field, "")).strip() in ("429", "502", "503", "504"):
                    return True
    return False


def is_failure(payload: dict) -> bool:
    """Any tool failure — refusals and refusal-shaped text included. Feeds
    `consecutive_failures`, which resets on the first success, so a red-test
    streak stays bounded without becoming permanent. Deliberately still broad:
    a streak of ANY failure is a sound thrash bound, and it is only the
    CUMULATIVE counter that needed a narrow, authoritative predicate. A red
    pytest stays a failure — that is precisely the streak's job."""
    if is_denial(payload) or denial_text_hint(payload):
        return True
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


def denial_rate(st: dict) -> float | None:
    """Denials as a fraction of tool calls, or None when there are no calls to
    divide by. Denials can be counted on either hook event while `tool_calls`
    counts only PreToolUse, so treat this as bounded at ~1.0 rather than as a
    strict subset ratio."""
    calls = st.get("tool_calls") or 0
    if calls <= 0:
        return None
    return st.get("denials", 0) / calls


def evaluate(st: dict, lim: dict) -> str | None:
    """The trip reason, or None. Any limit set to 0/absent is disabled."""
    # A bare count means opposite things at different run lengths: 16 denials
    # in 900 calls is scattered noise in a long healthy build, while 20 in 40
    # is a run that is clearly broken. Lowering the number makes the first
    # worse; raising it delays catching the second. So trip only when BOTH
    # hold — a substantial number of refusals, AND refusals being a
    # substantial fraction of what this run is doing. The floor doubles as the
    # minimum-sample guard, without which the rate alone would fire at the
    # start of every run (2 denials in the first 5 calls is 40%).
    floor = lim.get("max_permission_denials")
    rate_ceiling = lim.get("max_denial_rate")
    if floor and st["denials"] >= floor:
        rate = denial_rate(st)
        if rate is None:
            pass  # no calls to divide by; the rate limb cannot be evaluated
        elif rate_ceiling is None or rate >= rate_ceiling:
            bound = (f"floor {int(floor)}"
                     + (f", rate ceiling {rate_ceiling:.0%}"
                        if rate_ceiling is not None
                        else ", no rate ceiling armed"))
            return (f"{st['denials']} permission denials in "
                    f"{st['tool_calls']} tool calls ({rate:.0%} of calls) "
                    f"({bound})")
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

    # Only an AUTHORITATIVE refusal reaches the cumulative ceiling.
    # `denied_tools` is what someone reads to fix an allowlist, so it must
    # contain refusals and nothing else; refusal-shaped text is tallied
    # separately, and a transient upstream condition separately again.
    denied = is_denial(payload)
    transient = (not denied) and is_transient(payload)
    hint = (not denied) and (not transient) and denial_text_hint(payload)

    if denied:
        st["denials"] += 1
        st["denied_tools"][tool] = st["denied_tools"].get(tool, 0) + 1
    elif transient:
        st["transient_events"] += 1
        st["transient_tools"][tool] = st["transient_tools"].get(tool, 0) + 1
    elif hint:
        st["denial_text_hints"][tool] = st["denial_text_hints"].get(tool, 0) + 1

    def _streak_up():
        st["consecutive_failures"] += 1
        st["max_consecutive_seen"] = max(st["max_consecutive_seen"],
                                         st["consecutive_failures"])

    if event == "PreToolUse":
        st["tool_calls"] += 1
        # Every tool ATTEMPTED, from the one signal the design notes call
        # certain. A harness that refuses a call outright may surface it to
        # neither hook, and then `denied_tools` is empty and there is nothing
        # to fix an allowlist from. This leaves a record either way.
        st["attempted_tools"][tool] = st["attempted_tools"].get(tool, 0) + 1
        if denied:
            _streak_up()
    elif transient:
        pass  # neither extends nor resets the streak - see is_transient()
    elif is_failure(payload):
        _streak_up()
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
