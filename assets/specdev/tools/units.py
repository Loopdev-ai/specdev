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


def _ignored(rel: str, patterns) -> bool:
    """True when a unit-relative path is excluded by an ignore glob.

    fnmatch has no notion of path segments: '**/vendor/**' compiles to a regex
    demanding a literal '/' before 'vendor', so a TOP-LEVEL vendor/ would slip
    through and become a governed unit. Each '**/'-prefixed pattern is
    therefore also tried with that prefix stripped."""
    candidates = (rel, f"{rel}/.specdev")
    for pat in patterns:
        alts = [pat, pat[3:]] if pat.startswith("**/") else [pat]
        for a in alts:
            if any(fnmatch.fnmatch(c, a) for c in candidates):
                return True
    return False


def discover(root=".", ignore=None) -> list[str]:
    """Every directory under `root` holding a `.specdev/`, as POSIX-relative
    paths. The repo-root `.specdev/` is infrastructure (it holds the registry
    and the tools), never a unit, so it is excluded."""
    root = Path(root)
    patterns = list(DEFAULT_IGNORE if ignore is None else ignore)
    found = []
    for p in root.rglob(".specdev"):
        if not p.is_dir():
            continue
        rel = p.parent.relative_to(root).as_posix()
        if rel in ("", "."):
            continue
        if _ignored(rel, patterns):
            continue
        found.append(rel)
    return sorted(found)


def cycle_errors(entries) -> list[str]:
    """Depth-first colouring. A cycle is a hard error, never a fixpoint."""
    graph = {e["path"]: e["depends_on"] for e in entries}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    errors: list[str] = []

    def visit(n, stack):
        color[n] = GREY
        for d in graph.get(n, []):
            if d not in color:
                continue  # unknown dep reported separately
            if color[d] == GREY:
                cyc = stack[stack.index(d):] + [d]
                errors.append("depends_on cycle: " + " -> ".join(cyc))
            elif color[d] == WHITE:
                visit(d, stack + [d])
        color[n] = BLACK

    for n in sorted(graph):
        if color[n] == WHITE:
            visit(n, [n])
    return errors


def link_errors(root=".") -> list[str]:
    """The governance link is repo-wide. A unit org.json that redeclares it
    with a different value is a hard error, not a precedence puzzle."""
    reg = load_registry(root)
    if reg is None:
        return []
    link = {k: reg[k] for k in LINK_KEYS if k in reg}
    errors: list[str] = []
    for u in unit_paths(root):
        p = Path(root) / u / ".specdev" / "org.json"
        if not p.exists():
            continue
        org = json.loads(p.read_text(encoding="utf-8-sig"))
        for k in LINK_KEYS:
            if k in org and k in link and org[k] != link[k]:
                errors.append(
                    f"{u}/.specdev/org.json declares {k}={org[k]!r} but "
                    f"{REGISTRY_REL} declares {k}={link[k]!r} — the governance "
                    f"link is repo-wide; remove {k} from the unit's org.json")
    return errors


def check(root=".") -> list[str]:
    """Full registry validation. Empty list means clean. Inert (no errors)
    when there is no registry, so single-root repos are unaffected."""
    reg = load_registry(root)
    if reg is None:
        return []
    errors: list[str] = []

    if reg.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{REGISTRY_REL}: schema_version must be {SCHEMA_VERSION}, "
                      f"got {reg.get('schema_version')!r}")

    entries = unit_entries(root)
    if not entries:
        errors.append(f"{REGISTRY_REL}: 'units' must not be empty")
    names = {e["path"] for e in entries}
    for e in entries:
        for d in e["depends_on"]:
            if d not in names:
                errors.append(f"{REGISTRY_REL}: '{e['path']}' depends_on unknown "
                              f"unit '{d}'")
    errors += cycle_errors(entries)

    registered = set(names)
    found = set(discover(root, reg.get("ignore")))
    for u in sorted(found - registered):
        errors.append(f"{u}/.specdev/ exists but '{u}' is not registered in "
                      f"{REGISTRY_REL} — add it, or exclude it via 'ignore'")
    for u in sorted(registered - found):
        if u == ".":
            continue  # the root unit has no discoverable .specdev/ of its own
        errors.append(f"'{u}' is registered in {REGISTRY_REL} but "
                      f"{u}/.specdev/ does not exist")

    errors += link_errors(root)
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    args = ap.parse_args()

    if args.cmd == "list":
        for u in unit_paths(args.root):
            print(u)
        return 0
    if args.cmd == "check":
        errs = check(args.root)
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        if errs:
            return 1
        print(f"{len(unit_paths(args.root))} unit(s) registered — registry clean.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
