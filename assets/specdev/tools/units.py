#!/usr/bin/env python3
"""Governed-unit registry for SpecDev repositories.

A *governed unit* is a directory containing a `.specdev/`. A repository holds
one or more. This module is the ONLY place that knows a repo can hold more
than one — every other tool and workflow resolves units through it.

Backward compatibility is a single expression: with no `.specdev/units.json`,
`unit_paths()` returns `["."]` and every caller resolves exactly as it did
before multi-unit support existed.

Usage:
    units.py list  [--root .]                 # one unit path per line
    units.py check [--root .]                 # registry drift + validation
"""
import argparse
import fnmatch
import json
import sys
from pathlib import Path

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCHEMA_VERSION = 1
REGISTRY_REL = ".specdev/units.json"
LINK_KEYS = ("governance_repo", "ref", "path")
DEFAULT_IGNORE = ["**/node_modules/**", "**/vendor/**", "**/.venv/**"]

ROOT_UNIT = {"path": ".", "depends_on": []}


def registry_path(root=".") -> Path:
    return Path(root) / REGISTRY_REL


def load_registry(root=".") -> dict | None:
    p = registry_path(root)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _entry(u) -> dict:
    """Normalize a registry unit entry (bare string or object) to a dict."""
    if isinstance(u, str):
        return {"path": u, "depends_on": []}
    return {"path": u["path"], "depends_on": list(u.get("depends_on", []))}


def unit_entries(root=".") -> list[dict]:
    reg = load_registry(root)
    if reg is None:
        return [dict(ROOT_UNIT)]
    return [_entry(u) for u in reg.get("units", [])]


def unit_paths(root=".") -> list[str]:
    return [e["path"] for e in unit_entries(root)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    args = ap.parse_args()

    if args.cmd == "list":
        for u in unit_paths(args.root):
            print(u)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
