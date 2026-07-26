#!/usr/bin/env python3
"""Per-feature CI run manifest (.specdev/run.json) for the SpecDev CI handoff.

run.json records which mode a feature's build/merge belongs to so downstream
workflows can branch: a prod merge takes the standard deploy chain; a poc merge
skips it (the poc build deploys an isolated env itself). Also exposes the mode
to deploy.yml's guard and reads .specdev/ci.json for the build workflow.

Usage:
    run_manifest.py mode                        # print governing mode ('prod' if no file)
    run_manifest.py validate                     # exit nonzero on schema error
    run_manifest.py init --feat FEAT-001 --mode poc [--poc-env poc]
    run_manifest.py ci --get runner              # read a .specdev/ci.json key
"""
import argparse
import json
import re
import sys
from pathlib import Path

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


def ci_get(key: str, root="."):
    cfg = dict(CI_DEFAULTS)
    p = ci_path(root)
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg.get(key, CI_DEFAULTS.get(key))


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
    pc = sub.add_parser("ci")
    pc.add_argument("--get", required=True)
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
        save(doc, args.root)
        print(f"Wrote {run_path(args.root)}")
        return 0
    if args.cmd == "ci":
        print(ci_get(args.get, args.root))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
