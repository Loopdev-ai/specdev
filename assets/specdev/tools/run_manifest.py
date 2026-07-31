#!/usr/bin/env python3
"""Per-feature CI run manifest (<unit>/.specdev/run.json) for the SpecDev CI handoff.

run.json records which mode a feature's build/merge belongs to so downstream
workflows can branch: a prod merge takes the standard deploy chain; a poc merge
skips it (the poc build deploys an isolated env itself). Also exposes the mode
to deploy.yml's guard and reads ci.json for the build workflow.

The manifest is PER GOVERNED UNIT: a monorepo runs one in-flight build per unit
rather than one per repo, so unrelated projects do not serialise against each
other. `--root` names the unit; `init --ref` resolves it from the branch.

ci.json resolves unit-first with a repo-root fallback, so shared runner config
is declared once at the root and a unit overrides only what it needs.

Usage:
    run_manifest.py --root <unit> mode           # governing mode ('prod' if no file)
    run_manifest.py --root <unit> validate       # exit nonzero on schema error
    run_manifest.py init --feat FEAT-001 --mode poc [--poc-env poc]
                         [--unit <path> | --ref spec/<unit>/<name>]
    run_manifest.py --root <unit> ci --get runner [--repo-root .]
    run_manifest.py --root <unit> breaker-env [--repo-root .] [--summary-out F]
"""
import argparse
import json
import re
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402  (vendored sibling module)

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCHEMA_VERSION = 1
RUN_REL = ".specdev/run.json"
CI_REL = ".specdev/ci.json"
MODES = ("prod", "poc")
FEAT_RE = re.compile(r"^FEAT-\d{3,}$")

# The ONLY fields a resume carries forward from the checkpoint's run.json.
#
# `auto_resume` makes a re-dispatch a continuation of the same LOGICAL build,
# so anything identifying that build has to survive the attempt boundary —
# `started_at` above all, because the terminal-state check anchors its
# provenance window to it. Anchoring to the attempt instead made a build that
# HAD opened its PR unable to ever verify: the PR only gets older, so every
# subsequent dispatch rejects it for predating the job, terminally.
#
# Everything else must NOT be carried. The checkpoint was written by a previous
# attempt and restoring it wholesale silently overrode the current dispatch's
# own inputs: re-dispatching the same unit+FEAT in a different mode left
# run.json saying one thing and `needs.setup.outputs.mode` another.
RESUME_CARRIES = ("started_at",)

# The bundled ci.json — the sibling of this tools/ directory, i.e.
# assets/specdev/ci.json upstream and .specdev/ci.json once installed.
BUNDLED_CI = Path(__file__).resolve().parent.parent / "ci.json"

# ONE source of truth for every limit default: the bundled ci.json above.
#
# These used to exist in three uncoordinated copies — ci.json, this dict, and
# circuit_breaker.py's own literals — which made drift silent AND asymmetric: a
# repo that omitted a key got this dict, a repo whose arming step failed got
# circuit_breaker's literals, and only a repo with a complete ci.json got what
# the shipped config actually said. Raising a ceiling in two of the three
# places aborted builds below the cost of an honest multi-wave run while
# looking like a runaway trip.
#
# _FALLBACK_DEFAULTS exists only for the case where the bundled file is missing
# or malformed (an adopter edits it; it is their config file too). It is not a
# second opinion: tests/test_pipeline_hardening.py asserts it agrees with
# ci.json key for key, so drift fails CI rather than a build.
_FALLBACK_DEFAULTS = {
    "runner": "ubuntu-latest",
    "max_session_minutes": 300,
    "auto_resume": True,
    "max_permission_denials": 15,
    "max_denial_rate": 0.1,
    "max_consecutive_tool_failures": 15,
    "max_cost_usd": 10,
    "max_wall_minutes": 240,
    "max_tool_calls": 3000,
    # U17 bounded continuation - NOT breaker limits, so deliberately not in
    # BREAKER_ENV. The cap is a START GATE: it bounds whether the next attempt
    # begins, not what it spends, so true exposure is "cap plus one attempt".
    "max_build_attempts": 3,
    "continuation_cap_usd": 25,
}

# ci.json key -> the environment variable circuit_breaker.py reads it from.
# Lives here, beside the defaults, so the arming step in specdev-build.yml
# cannot export a subset by hand — which is how max_wall_minutes and
# max_tool_calls came to be implemented, documented as the backstop for an
# unreadable cost, and unreachable in every shipped run.
BREAKER_ENV = {
    "max_permission_denials": "SPECDEV_MAX_DENIALS",
    "max_denial_rate": "SPECDEV_MAX_DENIAL_RATE",
    "max_consecutive_tool_failures": "SPECDEV_MAX_CONSECUTIVE_FAILURES",
    "max_cost_usd": "SPECDEV_MAX_COST_USD",
    "max_wall_minutes": "SPECDEV_MAX_WALL_MINUTES",
    "max_tool_calls": "SPECDEV_MAX_TOOL_CALLS",
}


def _load_bundled_defaults() -> tuple[dict, str]:
    cfg = dict(_FALLBACK_DEFAULTS)
    try:
        doc = json.loads(BUNDLED_CI.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        return cfg, f"built-in fallback ({BUNDLED_CI.name} unreadable: {e})"
    if not isinstance(doc, dict):
        return cfg, f"built-in fallback ({BUNDLED_CI.name} is not an object)"
    doc.pop("schema_version", None)
    cfg.update(doc)
    return cfg, BUNDLED_CI.as_posix()


CI_DEFAULTS, CI_DEFAULTS_SOURCE = _load_bundled_defaults()


def run_path(root=".") -> Path:
    return Path(root) / RUN_REL


def ci_path(root=".") -> Path:
    return Path(root) / CI_REL


def load(root="."):
    p = run_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(doc: dict, root=".") -> None:
    p = run_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def started_at(root=".") -> str:
    """This LOGICAL build's start instant — stamped by `init` and carried
    across a resume, so it identifies the build rather than the attempt."""
    return str((load(root) or {}).get("started_at") or "")


def resume_from(prev: dict, root=".") -> dict:
    """Merge a checkpoint's run.json into the current one, carrying ONLY
    RESUME_CARRIES. Returns the document written."""
    doc = load(root) or {}
    for key in RESUME_CARRIES:
        if prev.get(key):
            doc[key] = prev[key]
    save(doc, root)
    return doc


def validate(doc: dict) -> list:
    errs = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version must be {SCHEMA_VERSION}")
    mode = doc.get("mode")
    if mode not in MODES:
        errs.append(f"mode must be one of {MODES}, got {mode!r}")
    feat = doc.get("feat")
    if feat is not None and not FEAT_RE.match(str(feat)):
        errs.append(f"feat must match FEAT-### or be null, got {feat!r}")
    if mode == "poc" and not doc.get("poc_environment"):
        errs.append("mode 'poc' requires a non-empty poc_environment")
    return errs


def mode_of(root=".") -> str:
    """Mode governing the current tree — 'prod' when no manifest (safe default:
    the standard deploy chain runs)."""
    doc = load(root)
    m = (doc or {}).get("mode")
    return m if m in MODES else "prod"


def prod_chain_should_run(doc) -> bool:
    """deploy.yml guard: the standard staging->prod chain runs for everything
    except a poc merge."""
    return (doc or {}).get("mode") != "poc"


_MISSING = object()


def ci_supplied(root=".", repo_root=None) -> set:
    """The keys an actual ci.json in THIS repo supplied, as opposed to ones
    inherited from the bundled defaults.

    Loading CI_DEFAULTS from the bundled ci.json fixed the three-copies drift
    trap, but it made every shipped key resolvable in every repo whether or not
    the adopter set it — so a control of the form "this must be a deliberate,
    committed choice, never inherited" became unwritable, and would fail open
    and silently. Membership here answers that question directly.

    It is deliberately NOT expressed by comparing sources against
    CI_DEFAULTS_SOURCE: in an installed tree BUNDLED_CI and `<root>/.specdev/
    ci.json` are the SAME file, so such a comparison either aliases the bundle
    to the adopter's own pin or turns on whether the path arrived relative or
    absolute. Which candidate files exist and what they contain does not."""
    supplied = set()
    for p in _ci_candidates(root, repo_root):
        if p.exists():
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
            if isinstance(doc, dict):
                supplied.update(doc)
    return supplied


def ci_explicit(key: str, root=".", repo_root=None) -> bool:
    """True when a ci.json in this repo sets `key` — not when the tool's own
    default happens to supply a value for it."""
    return key in ci_supplied(root, repo_root)


def _ci_candidates(root=".", repo_root=None) -> list:
    """Lowest precedence first: the repo-root ci.json declares shared config
    once, and a unit's own overrides only what it needs."""
    candidates = []
    if repo_root is not None:
        candidates.append(Path(repo_root) / CI_REL)
    candidates.append(ci_path(root))
    return candidates


def ci_resolve(key: str, root=".", repo_root=None):
    """(value, source) for a ci.json key — the value AND the file it came from.

    The source is not decoration: an adopter who raises a ceiling in the wrong
    file needs to see which file actually won, and the arming step prints it.
    Use `ci_explicit` to ask whether the repo configured the key at all; the
    source cannot answer that (see the note there)."""
    cfg = dict(CI_DEFAULTS)
    src = {k: CI_DEFAULTS_SOURCE for k in cfg}
    for p in _ci_candidates(root, repo_root):
        if not p.exists():
            continue
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
        cfg.update(doc)
        src.update({k: p.as_posix() for k in doc})
    if key not in cfg:
        return _MISSING, None
    return cfg[key], src.get(key)


def ci_get(key: str, root=".", repo_root=None):
    """Read a ci.json key. A unit's ci.json wins; the repo-root ci.json is the
    fallback so shared runner config is declared once; CI_DEFAULTS is the floor.

    Returns _MISSING for an unknown key so the caller fails loudly instead of
    printing the string 'None' at exit 0 — that value has flowed into an
    inference-metadata record as "model": "None"."""
    return ci_resolve(key, root, repo_root)[0]


def breaker_env(root=".", repo_root=None) -> tuple[dict, dict, list[str]]:
    """Resolve every circuit-breaker limit: (env vars, sources, errors).

    All of them, together, from the one mapping above. The workflow used to
    inline three `$(...)` substitutions inside an `echo`, where a nonzero exit
    does NOT trip `set -e` (the compound command's status is echo's), so any
    run_manifest failure wrote an empty value, circuit_breaker fell through to
    its own literals, and the run presented as configured. Errors are returned
    so the caller can fail the step instead of arming a guess."""
    env, sources, errors = {}, {}, []
    for key, var in BREAKER_ENV.items():
        try:
            val, src = ci_resolve(key, root, repo_root)
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{key}: could not read ci.json ({e})")
            continue
        if val is _MISSING:
            errors.append(f"{key}: no such ci.json key")
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            errors.append(f"{key}: {val!r} is not a number")
            continue
        env[var] = int(num) if num.is_integer() else num
        sources[var] = src
    return env, sources, errors


def _breaker_summary(env: dict, sources: dict) -> str:
    lines = ["### SpecDev circuit breaker — limits armed for this run", "",
             "| limit | value | resolved from |", "|---|---|---|"]
    for key, var in BREAKER_ENV.items():
        val = env.get(var)
        shown = "**not armed**" if val in (None, 0) else f"`{val}`"
        lines.append(f"| `{key}` | {shown} | `{sources.get(var, '-')}` |")
    lines += ["", "A limit of `0` is disabled. Cost is best-effort: it is "
              "enforced only when the mid-run transcript exposes it, which is "
              "why the wall-clock and tool-call ceilings exist.", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mode")
    sub.add_parser("validate")
    pi = sub.add_parser("init")
    pi.add_argument("--feat", required=True)
    pi.add_argument("--mode", required=True, choices=MODES)
    pi.add_argument("--poc-env", default="poc")
    pi.add_argument("--unit", default=None,
                    help="governed unit (default: resolved from --ref, else '.')")
    pi.add_argument("--ref", default=None,
                    help="branch ref to resolve the unit from")
    sub.add_parser("started-at")
    pr = sub.add_parser("resume")
    pr.add_argument("--from", dest="prev", required=True,
                    help="the checkpoint's run.json; only RESUME_CARRIES is "
                         "taken from it, never the whole document")
    pc = sub.add_parser("ci")
    pc.add_argument("--get", required=True)
    pc.add_argument("--repo-root", default=None,
                    help="repo root for ci.json fallback when --root is a unit")
    pc.add_argument("--require-explicit", action="store_true",
                    help="fail unless a ci.json in THIS repo sets the key; a "
                         "value inherited from the bundled defaults is not a "
                         "deliberate choice and does not satisfy this")
    pb = sub.add_parser("breaker-env")
    pb.add_argument("--repo-root", default=None,
                    help="repo root for ci.json fallback when --root is a unit")
    pb.add_argument("--summary-out", default=None,
                    help="append the resolved-limits table here (e.g. "
                         "$GITHUB_STEP_SUMMARY)")
    args = ap.parse_args()

    if args.cmd == "mode":
        print(mode_of(args.root))
        return 0
    if args.cmd == "validate":
        doc = load(args.root)
        if doc is None:
            print("no run.json (inert) — ok")
            return 0
        errs = validate(doc)
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1 if errs else 0
    if args.cmd == "init":
        feat = (args.feat or "").strip() or None
        # started_at is the LOGICAL build's clock. A resume carries it forward,
        # so the terminal-state check's provenance window covers every attempt
        # at this unit+FEAT rather than only the current job.
        doc = {"schema_version": SCHEMA_VERSION, "feat": feat, "mode": args.mode,
               "started_at": datetime.now(timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ")}
        if args.mode == "poc":
            doc["poc_environment"] = args.poc_env
        errs = validate(doc)
        if errs:
            for e in errs:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        unit = args.unit
        if unit is None and args.ref:
            unit, _ = units.parse_ref(args.ref, units.load_registry(args.root))
        unit = unit or "."
        target = str(Path(args.root) / unit)
        save(doc, target)
        print(f"Wrote {run_path(target)}")
        return 0
    if args.cmd == "started-at":
        print(started_at(args.root))
        return 0
    if args.cmd == "resume":
        p = Path(args.prev)
        if not p.exists():
            print(f"no checkpoint manifest at {p} — nothing to carry forward.")
            return 0
        try:
            prev = json.loads(p.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            print(f"::warning title=Unusable checkpoint manifest::{p}: {e}. "
                  f"This attempt's own run.json is kept as-is.", file=sys.stderr)
            return 0
        if not isinstance(prev, dict):
            return 0
        doc = resume_from(prev, args.root)
        carried = {k: doc.get(k) for k in RESUME_CARRIES if prev.get(k)}
        print(f"carried forward from the checkpoint: {carried or '(nothing)'}; "
              f"this dispatch's feat={doc.get('feat')} mode={doc.get('mode')} "
              f"are kept.")
        return 0
    if args.cmd == "ci":
        val = ci_get(args.get, args.root, repo_root=args.repo_root)
        if val is _MISSING:
            print(f"ERROR: no such ci.json key: {args.get!r} "
                  f"(known: {', '.join(sorted(CI_DEFAULTS))})", file=sys.stderr)
            return 1
        if args.require_explicit and not ci_explicit(args.get, args.root,
                                                     args.repo_root):
            print(f"::error title=Unconfigured key::{args.get!r} must be set "
                  f"explicitly in this repo's ci.json. It currently resolves to "
                  f"{val!r} inherited from {CI_DEFAULTS_SOURCE}, which is a "
                  f"default, not a decision.", file=sys.stderr)
            return 1
        print(val)
        return 0
    if args.cmd == "breaker-env":
        env, sources, errors = breaker_env(args.root, args.repo_root)
        if errors:
            for e in errors:
                print(f"ERROR: {e}", file=sys.stderr)
            print("::error title=Circuit breaker not armed::Could not resolve "
                  "every limit from ci.json: " + "; ".join(errors) +
                  ". Refusing to arm the breaker with library constants the "
                  "adopter never configured.", file=sys.stderr)
            return 1
        for var, val in env.items():
            print(f"{var}={val}")
        if args.summary_out:
            with open(args.summary_out, "a", encoding="utf-8") as fh:
                fh.write(_breaker_summary(env, sources))
        for key, var in BREAKER_ENV.items():
            if not env.get(var):
                print(f"::warning title=Limit disabled::ci.json sets {key} to "
                      f"{env.get(var)} — that bound is OFF for this run.",
                      file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
