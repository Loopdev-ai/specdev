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
import subprocess
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


def dependents(entries) -> dict:
    """unit -> set of units that TRANSITIVELY depend on it.

    Direction matters: effective classification propagates from a dependent to
    its dependencies, because anything a production system imports is inside
    the production blast radius. Propagating the other way would leave risky
    code relocated into a low-classification unit ungoverned."""
    graph = {e["path"]: e["depends_on"] for e in entries}
    rev = {n: set() for n in graph}
    for n, deps in graph.items():
        for d in deps:
            if d in rev:
                rev[d].add(n)
    out = {}
    for n in graph:
        seen, stack = set(), list(rev[n])
        while stack:
            m = stack.pop()
            if m in seen or m == n:
                continue
            seen.add(m)
            stack.extend(rev.get(m, ()))
        out[n] = seen
    return out


def combine(classifications, axes) -> dict:
    """Ranked axes collapse to the max-rank value; unordered axes union.

    Each input is {axis: set(values)}. For a single classification with
    single-value sets this is the identity, which is what keeps single-unit
    repos bit-identical."""
    out = {}
    for axis, adef in axes.items():
        values = adef.get("values", {})
        vals = set()
        for c in classifications:
            vals |= set(c.get(axis, ()))
        if not vals:
            continue
        if adef.get("ordered"):
            top = max(vals, key=lambda v: values.get(v, {}).get("rank", -1))
            out[axis] = {top}
        else:
            out[axis] = vals
    return out


def effective(entries, axes, declared) -> dict:
    """{unit: {axis: set(values)}} after dependency-ward propagation.

    `declared` is {unit: {axis: set(values)}}, already normalized against the
    org scheme by the caller (check_org_adrs.normalize_classification)."""
    deps = dependents(entries)
    out = {}
    for e in entries:
        n = e["path"]
        sources = [declared.get(n, {})]
        sources += [declared.get(d, {}) for d in sorted(deps.get(n, ()))]
        out[n] = combine(sources, axes)
    return out


def escalations(entries, axes, declared) -> list[str]:
    """Human-readable lines describing where effective exceeded declared, and
    which dependent caused it. Silent escalation is unexplainable at the point
    of failure, so callers print these."""
    deps = dependents(entries)
    eff = effective(entries, axes, declared)
    lines = []
    for e in entries:
        n = e["path"]
        for axis in sorted(eff[n]):
            was = set(declared.get(n, {}).get(axis, ()))
            now = eff[n][axis]
            if was and now != was:
                causes = sorted(
                    d for d in deps.get(n, ())
                    if set(declared.get(d, {}).get(axis, ())) - was)
                lines.append(
                    f"{n}: {axis} {sorted(was)} -> {sorted(now)} "
                    f"(pulled up by dependent(s): {', '.join(causes) or 'n/a'})")
    return lines


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


def _changed_files(root=".", base=None) -> list[str]:
    if not base:
        return []
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=str(root), capture_output=True, text=True, check=True).stdout
    return [f.strip().replace("\\", "/") for f in out.splitlines() if f.strip()]


def _owns(unit: str, path: str) -> bool:
    return unit == "." or path == unit or path.startswith(unit + "/")


def changed_units(root=".", base=None) -> list[str]:
    """Units touched between `base` and HEAD. With no base, every unit."""
    all_units = unit_paths(root)
    if not base:
        return sorted(all_units)
    hit = {u for f in _changed_files(root, base) for u in all_units if _owns(u, f)}
    return sorted(hit)


def out_of_scope(root=".", unit=".", base=None) -> list[str]:
    """Changed files that belong to a DIFFERENT unit, so a branch name cannot
    lie about the scope of its PR.

    Files owned by no unit (shared CI config, top-level docs) are deliberately
    allowed: attributing them to a unit would block every cross-cutting chore."""
    if unit == ".":
        return []
    all_units = unit_paths(root)
    bad = []
    for f in _changed_files(root, base):
        if _owns(unit, f):
            continue
        if any(_owns(u, f) for u in all_units if u != unit and u != "."):
            bad.append(f)
    return sorted(bad)


def write_rollup_index(root, rel_artifact: str, title: str) -> Path | None:
    """Write a rolled-up index that LINKS to each unit's artifact.

    Deliberately an index, never a merge. A Statement of Applicability carries
    a scope statement; concatenating two units' SoAs produces a document that
    is true of neither. Returns None for a single-root repo, which needs no
    index."""
    reg = load_registry(root)
    if reg is None:
        return None
    lines = [f"# {title}", "",
             "Per-unit artifacts. These are deliberately **not** merged — each",
             "carries its own scope. See the monorepo design note, decision 6.",
             ""]
    for u in unit_paths(root):
        target = Path(root) / u / ".specdev" / rel_artifact
        mark = "" if target.exists() else "  _(not generated)_"
        lines.append(f"- [{u}]({u}/.specdev/{rel_artifact}){mark}")
    stem = Path(rel_artifact).stem
    out = Path(root) / ".specdev" / f"{stem}-index.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    pm = sub.add_parser("matrix")
    pm.add_argument("--changed-from", default=None)
    pm.add_argument("--all", action="store_true",
                    help="ignore --changed-from and emit every unit (REQUIRED "
                         "for org-adr-check: its staleness check is driven by "
                         "the upstream ADR index, not the local diff)")
    ps = sub.add_parser("scope-check")
    ps.add_argument("--unit", required=True)
    ps.add_argument("--changed-from", required=True)
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
    if args.cmd == "matrix":
        sel = unit_paths(args.root) if args.all else changed_units(
            args.root, args.changed_from)
        print(json.dumps(sorted(sel)))
        return 0
    if args.cmd == "scope-check":
        bad = out_of_scope(args.root, args.unit, args.changed_from)
        for f in bad:
            print(f"ERROR: {f} is outside unit '{args.unit}'", file=sys.stderr)
        if bad:
            print(f"A branch scoped to '{args.unit}' may not change other units.",
                  file=sys.stderr)
            return 1
        print(f"scope ok — all changes are within '{args.unit}'")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
