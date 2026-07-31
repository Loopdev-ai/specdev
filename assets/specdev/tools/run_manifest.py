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
    "max_consecutive_tool_failures": 15,
    "max_cost_usd": 10,
    "max_wall_minutes": 240,
    "max_tool_calls": 3000,
}

# ci.json key -> the environment variable circuit_breaker.py reads it from.
# Lives here, beside the defaults, so the arming step in specdev-build.yml
# cannot export a subset by hand — which is how max_wall_minutes and
# max_tool_calls came to be implemented, documented as the backstop for an
# unreadable cost, and unreachable in every shipped run.
BREAKER_ENV = {
    "max_permission_denials": "SPECDEV_MAX_DENIALS",
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


def ci_resolve(key: str, root=".", repo_root=None):
    """(value, source) for a ci.json key — the value AND the file it came from.

    The source is not decoration: an adopter who raises a ceiling in the wrong
    file needs to see which file actually won, and the arming step prints it."""
    cfg = dict(CI_DEFAULTS)
    src = {k: CI_DEFAULTS_SOURCE for k in cfg}
    candidates = []
    if repo_root is not None:
        candidates.append(Path(repo_root) / CI_REL)
    candidates.append(ci_path(root))
    for p in candidates:
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
    pc = sub.add_parser("ci")
    pc.add_argument("--get", required=True)
    pc.add_argument("--repo-root", default=None,
                    help="repo root for ci.json fallback when --root is a unit")
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
        doc = {"schema_version": SCHEMA_VERSION, "feat": feat, "mode": args.mode}
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
    if args.cmd == "ci":
        val = ci_get(args.get, args.root, repo_root=args.repo_root)
        if val is _MISSING:
            print(f"ERROR: no such ci.json key: {args.get!r} "
                  f"(known: {', '.join(sorted(CI_DEFAULTS))})", file=sys.stderr)
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
