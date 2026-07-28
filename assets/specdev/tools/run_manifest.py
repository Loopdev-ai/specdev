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
CI_DEFAULTS = {"runner": "ubuntu-latest", "max_session_minutes": 300, "auto_resume": False}


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


def ci_get(key: str, root=".", repo_root=None):
    """Read a ci.json key. A unit's ci.json wins; the repo-root ci.json is the
    fallback so shared runner config is declared once; CI_DEFAULTS is the floor.

    Returns _MISSING for an unknown key so the caller fails loudly instead of
    printing the string 'None' at exit 0 — that value has flowed into an
    inference-metadata record as "model": "None"."""
    cfg = dict(CI_DEFAULTS)
    candidates = []
    if repo_root is not None:
        candidates.append(Path(repo_root) / CI_REL)
    candidates.append(ci_path(root))
    for p in candidates:
        if p.exists():
            cfg.update(json.loads(p.read_text(encoding="utf-8-sig")))
    return cfg.get(key, _MISSING)


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
