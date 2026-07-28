# Monorepo / Multi-Unit Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one repository hold several independently governed units, each with its own classification, spec, compliance artifacts and build lifecycle, without changing behaviour for existing single-root repos.

**Architecture:** A new `assets/specdev/tools/units.py` is the single module that knows a repo can hold more than one unit; every other tool and workflow calls into it. Registry-driven discovery (`.specdev/units.json`) with `unit_paths(root) -> ["."]` when no registry exists is the entire backward-compatibility mechanism. Classification becomes set-valued internally so effective classification can propagate dependency-ward.

**Tech Stack:** Python 3.10+ (stdlib only — no third-party imports in `assets/specdev/tools/`), pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-28-monorepo-multi-unit-design.md`

## Global Constraints

- **Stdlib only.** Tools under `assets/specdev/tools/` are vendored into user repos and run on a bare `actions/setup-python`. No third-party imports, ever.
- **Python ≥3.10** — the existing tools already use `dict | None` annotations. Match that; do not add a version guard here (it belongs to the separate `fix/adoption-findings` branch).
- **Backward compatibility is a test, not an intention.** With no `.specdev/units.json`, every tool must behave bit-identically to today. Every task that touches an existing tool must keep the existing tests in `tests/test_specdev_ci.py` green.
- **UTF-8 reads.** Use `encoding="utf-8-sig"` when reading JSON (matches `check_org_adrs.load_json`) and `encoding="utf-8"` when writing.
- **Windows-safe paths.** Compare and emit unit paths as POSIX (`Path.as_posix()`). Never emit backslashes into JSON or workflow outputs.
- **`org-adr-check` is never path-filtered.** Hard constraint from the spec. The `sha256` staleness check is driven by the upstream index, not the local diff.
- **Failure is loud.** No tool may print a value and exit 0 when the underlying config is missing or contradictory.

---

## File Structure

| File | Responsibility |
|---|---|
| `assets/specdev/tools/units.py` | **New.** Registry load/validate, discovery, drift check, dependency graph, effective classification, ref parsing, changed-unit computation, migration. The only module with layout knowledge. |
| `assets/specdev/tools/check_org_adrs.py` | Set-valued classification matching; `--all-units`; link resolution via registry. |
| `assets/specdev/tools/gen_traceability.py` | Unit-relative `--out` (bug fix); rolled-up index. |
| `assets/specdev/tools/gen_compliance.py` | Per-unit SoA; rolled-up index. Never merges. |
| `assets/specdev/tools/deploy.py` | Gains `--root`. |
| `assets/specdev/tools/run_manifest.py` | Per-unit `run.json`; `ci.json` unit→root fallback. |
| `assets/specdev/units.json` | **New.** Seed registry, shipped commented-out/inert by `/specdev:init`. |
| `assets/workflows/org-adr-check.yml` | discover → matrix over **all** units → summary. |
| `assets/workflows/spec-validate.yml` | discover → matrix over changed units → summary. |
| `assets/workflows/compliance.yml` | Same shape. |
| `assets/workflows/post-dev-qa.yml` | Same shape; unit-relative `paths-ignore`. |
| `assets/workflows/specdev-sweep.yml` | **New.** Nightly unfiltered all-unit sweep. |
| `assets/workflows/specdev-build.yml` | Unit routing from branch, per-unit concurrency, scope guard. |
| `assets/workflows/deploy.yml`, `deploy-poc.yml` | `--root`, per-unit tags. |
| `agents/adr-checker.md` | Unit root parameter replacing two hardcoded paths. |
| `tests/test_units.py` | **New.** All unit-registry behaviour. |

---

## Phase 1 — Verification

### Task 1: Registry load and `unit_paths` back-compat

**Files:**
- Create: `assets/specdev/tools/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `REGISTRY_REL: str = ".specdev/units.json"`
  - `LINK_KEYS: tuple = ("governance_repo", "ref", "path")`
  - `registry_path(root=".") -> Path`
  - `load_registry(root=".") -> dict | None`
  - `unit_entries(root=".") -> list[dict]` — each `{"path": str, "depends_on": list[str]}`
  - `unit_paths(root=".") -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_units.py`:

```python
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNITS_PATH = ROOT / "assets" / "specdev" / "tools" / "units.py"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


un = load_mod(UNITS_PATH, "units")


def write_registry(root, doc):
    (root / ".specdev").mkdir(parents=True, exist_ok=True)
    (root / ".specdev" / "units.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def make_unit(root, name, classification=None, depends_on=None):
    """Create <root>/<name>/.specdev/org.json. Returns the registry entry."""
    d = root / name / ".specdev"
    d.mkdir(parents=True, exist_ok=True)
    if classification is not None:
        (d / "org.json").write_text(
            json.dumps({"classification": classification}, indent=2) + "\n",
            encoding="utf-8")
    entry = {"path": name}
    if depends_on:
        entry["depends_on"] = depends_on
    return entry


# ---- back-compat: no registry ------------------------------------------

def test_no_registry_yields_single_root_unit(tmp_path):
    assert un.load_registry(tmp_path) is None
    assert un.unit_paths(tmp_path) == ["."]


def test_no_registry_yields_single_root_entry(tmp_path):
    assert un.unit_entries(tmp_path) == [{"path": ".", "depends_on": []}]


# ---- registry parsing --------------------------------------------------

def test_bare_string_units_are_normalized(tmp_path):
    write_registry(tmp_path, {"schema_version": 1, "units": ["a", "b"]})
    assert un.unit_paths(tmp_path) == ["a", "b"]
    assert un.unit_entries(tmp_path) == [
        {"path": "a", "depends_on": []},
        {"path": "b", "depends_on": []},
    ]


def test_object_units_carry_depends_on(tmp_path):
    write_registry(tmp_path, {
        "schema_version": 1,
        "units": ["infra", {"path": "soc", "depends_on": ["infra"]}],
    })
    assert un.unit_paths(tmp_path) == ["infra", "soc"]
    assert un.unit_entries(tmp_path)[1] == {"path": "soc", "depends_on": ["infra"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_units.py -v`
Expected: FAIL — `FileNotFoundError` / `ModuleNotFoundError` for `units.py`.

- [ ] **Step 3: Write the minimal implementation**

Create `assets/specdev/tools/units.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 5: Confirm existing tests still pass**

Run: `python -m pytest tests/ -q`
Expected: all pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add assets/specdev/tools/units.py tests/test_units.py
git commit -m "feat(units): registry load and unit_paths back-compat"
```

---

### Task 2: Discovery and drift check

**Files:**
- Modify: `assets/specdev/tools/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: `unit_paths`, `load_registry`, `_entry`, `DEFAULT_IGNORE`, `REGISTRY_REL`, `SCHEMA_VERSION`, `LINK_KEYS`
- Produces:
  - `discover(root=".", ignore=None) -> list[str]`
  - `cycle_errors(entries) -> list[str]`
  - `link_errors(root=".") -> list[str]`
  - `check(root=".") -> list[str]` — all validation errors; empty means clean

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_units.py`:

```python
# ---- discovery ---------------------------------------------------------

def test_discover_finds_unit_dirs_and_skips_root(tmp_path):
    (tmp_path / ".specdev").mkdir()
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "soc")
    assert un.discover(tmp_path) == ["infra", "soc"]


def test_discover_honours_ignore_globs(tmp_path):
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "vendor/copied")
    assert un.discover(tmp_path, ignore=["**/vendor/**"]) == ["infra"]


def test_discover_finds_nested_units(tmp_path):
    make_unit(tmp_path, "services/api")
    assert un.discover(tmp_path) == ["services/api"]


# ---- drift: both directions fail --------------------------------------

def test_check_fails_on_discovered_but_unregistered(tmp_path):
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "shadow")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra"]})
    errs = un.check(tmp_path)
    assert any("shadow" in e and "not in" in e for e in errs)


def test_check_fails_on_registered_but_missing(tmp_path):
    make_unit(tmp_path, "infra")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra", "ghost"]})
    errs = un.check(tmp_path)
    assert any("ghost" in e and "does not exist" in e for e in errs)


def test_check_clean_registry_has_no_errors(tmp_path):
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "soc")
    write_registry(tmp_path, {
        "schema_version": 1,
        "units": ["infra", {"path": "soc", "depends_on": ["infra"]}],
    })
    assert un.check(tmp_path) == []


def test_check_is_inert_without_registry(tmp_path):
    make_unit(tmp_path, "infra")
    assert un.check(tmp_path) == []


def test_check_rejects_unknown_depends_on(tmp_path):
    make_unit(tmp_path, "soc")
    write_registry(tmp_path, {
        "schema_version": 1,
        "units": [{"path": "soc", "depends_on": ["nope"]}],
    })
    assert any("nope" in e for e in un.check(tmp_path))


def test_check_rejects_wrong_schema_version(tmp_path):
    make_unit(tmp_path, "infra")
    write_registry(tmp_path, {"schema_version": 99, "units": ["infra"]})
    assert any("schema_version" in e for e in un.check(tmp_path))


# ---- cycles ------------------------------------------------------------

def test_cycle_is_a_hard_error(tmp_path):
    make_unit(tmp_path, "a")
    make_unit(tmp_path, "b")
    write_registry(tmp_path, {
        "schema_version": 1,
        "units": [{"path": "a", "depends_on": ["b"]},
                  {"path": "b", "depends_on": ["a"]}],
    })
    assert any("cycle" in e for e in un.check(tmp_path))


def test_self_dependency_is_a_cycle(tmp_path):
    make_unit(tmp_path, "a")
    write_registry(tmp_path, {
        "schema_version": 1,
        "units": [{"path": "a", "depends_on": ["a"]}],
    })
    assert any("cycle" in e for e in un.check(tmp_path))


# ---- governance link disagreement --------------------------------------

def test_unit_org_json_may_not_contradict_registry_link(tmp_path):
    d = tmp_path / "infra" / ".specdev"
    d.mkdir(parents=True)
    (d / "org.json").write_text(json.dumps({
        "governance_repo": "other/repo",
        "classification": {"maturity": "prod"},
    }), encoding="utf-8")
    write_registry(tmp_path, {
        "schema_version": 1,
        "governance_repo": "faro/governance",
        "units": ["infra"],
    })
    errs = un.check(tmp_path)
    assert any("governance_repo" in e and "repo-wide" in e for e in errs)


def test_unit_org_json_without_link_is_fine(tmp_path):
    make_unit(tmp_path, "infra", classification={"maturity": "prod"})
    write_registry(tmp_path, {
        "schema_version": 1,
        "governance_repo": "faro/governance",
        "units": ["infra"],
    })
    assert un.check(tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_units.py -v`
Expected: FAIL — `AttributeError: module 'units' has no attribute 'discover'`.

- [ ] **Step 3: Write the implementation**

Add to `assets/specdev/tools/units.py`, after `unit_paths`:

```python
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
        inner = p.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(inner, pat) or fnmatch.fnmatch(rel, pat)
               for pat in patterns):
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
```

Add the `check` subcommand to `main()`, before `return 0`:

```python
    if args.cmd == "check":
        errs = check(args.root)
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        if errs:
            return 1
        print(f"{len(unit_paths(args.root))} unit(s) registered — registry clean.")
        return 0
```

And register it in the subparsers block, next to `sub.add_parser("list")`:

```python
    sub.add_parser("check")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -v`
Expected: PASS — 17 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/units.py tests/test_units.py
git commit -m "feat(units): discovery, drift check, cycles, link disagreement"
```

---

### Task 3: Effective classification propagates dependency-ward

This is the task that encodes spec decision 4. The laundering regression test must not be deleted.

**Files:**
- Modify: `assets/specdev/tools/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: `unit_entries`
- Produces:
  - `dependents(entries) -> dict[str, set[str]]` — unit → units that **transitively depend on** it
  - `combine(classifications, axes) -> dict[str, set[str]]` — ranked axes collapse to max rank, unordered axes union. `classifications` is a list of `{axis: set[value]}`.
  - `effective(entries, axes, declared) -> dict[str, dict[str, set[str]]]` — `declared` is `{unit: {axis: set[value]}}`, already normalized by the caller.

**Why `effective` takes `declared` rather than reading org.json itself:** normalization (bare-string classification, axis validation) lives in `check_org_adrs.normalize_classification`. Having `units.py` import it would be circular. Keeping `effective` a pure function of already-normalized input avoids that and makes it trivially testable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_units.py`:

```python
# ---- effective classification ------------------------------------------

AXES = {
    "maturity": {
        "ordered": True,
        "values": {"poc": {"rank": 0}, "dev": {"rank": 1}, "prod": {"rank": 2}},
    },
    "audience": {
        "ordered": False,
        "values": {"internal": {}, "customer": {}},
    },
}


def eff(entries, declared):
    return un.effective(entries, AXES, declared)


def test_dependents_is_transitive():
    entries = [
        {"path": "a", "depends_on": ["b"]},
        {"path": "b", "depends_on": ["c"]},
        {"path": "c", "depends_on": []},
    ]
    d = un.dependents(entries)
    assert d["c"] == {"a", "b"}
    assert d["b"] == {"a"}
    assert d["a"] == set()


def test_ranked_axis_takes_the_max():
    got = un.combine([{"maturity": {"poc"}}, {"maturity": {"prod"}}], AXES)
    assert got["maturity"] == {"prod"}


def test_unordered_axis_unions():
    got = un.combine([{"audience": {"internal"}}, {"audience": {"customer"}}], AXES)
    assert got["audience"] == {"internal", "customer"}


def test_single_unit_classification_is_unchanged():
    """Bit-identical behaviour for the degenerate case: every set is size 1."""
    entries = [{"path": ".", "depends_on": []}]
    declared = {".": {"maturity": {"prod"}, "audience": {"internal"}}}
    assert eff(entries, declared) == {
        ".": {"maturity": {"prod"}, "audience": {"internal"}}
    }


def test_laundering_is_blocked_dependency_is_pulled_up():
    """SPEC DECISION 4 — DO NOT DELETE.

    Risky code moved into a poc-declared unit that a prod unit imports must be
    governed as prod. This is the regression test for the whole feature.
    """
    entries = [
        {"path": "prod-svc", "depends_on": ["poc/risky"]},
        {"path": "poc/risky", "depends_on": []},
    ]
    declared = {
        "prod-svc": {"maturity": {"prod"}},
        "poc/risky": {"maturity": {"poc"}},
    }
    got = eff(entries, declared)
    assert got["poc/risky"]["maturity"] == {"prod"}, \
        "a prod unit's dependency must be governed as prod"


def test_escalation_is_transitive():
    entries = [
        {"path": "prod-svc", "depends_on": ["mid"]},
        {"path": "mid", "depends_on": ["leaf"]},
        {"path": "leaf", "depends_on": []},
    ]
    declared = {
        "prod-svc": {"maturity": {"prod"}},
        "mid": {"maturity": {"poc"}},
        "leaf": {"maturity": {"poc"}},
    }
    got = eff(entries, declared)
    assert got["leaf"]["maturity"] == {"prod"}


def test_dependent_direction_does_not_escalate():
    """A poc demo importing a prod library stays poc — reading a production
    library does not make a spike production."""
    entries = [
        {"path": "demos", "depends_on": ["infra"]},
        {"path": "infra", "depends_on": []},
    ]
    declared = {
        "demos": {"maturity": {"poc"}},
        "infra": {"maturity": {"prod"}},
    }
    got = eff(entries, declared)
    assert got["demos"]["maturity"] == {"poc"}
    assert got["infra"]["maturity"] == {"prod"}


def test_independent_units_do_not_affect_each_other():
    entries = [
        {"path": "a", "depends_on": []},
        {"path": "b", "depends_on": []},
    ]
    declared = {"a": {"maturity": {"prod"}}, "b": {"maturity": {"poc"}}}
    got = eff(entries, declared)
    assert got["b"]["maturity"] == {"poc"}


def test_unordered_axis_escalation_unions_across_dependents():
    entries = [
        {"path": "svc", "depends_on": ["lib"]},
        {"path": "lib", "depends_on": []},
    ]
    declared = {
        "svc": {"audience": {"customer"}},
        "lib": {"audience": {"internal"}},
    }
    got = eff(entries, declared)
    assert got["lib"]["audience"] == {"internal", "customer"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_units.py -k "effective or laundering or dependents or combine or escalat or independent or single_unit" -v`
Expected: FAIL — `AttributeError: module 'units' has no attribute 'dependents'`.

- [ ] **Step 3: Write the implementation**

Add to `assets/specdev/tools/units.py`, after `cycle_errors`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -v`
Expected: PASS — 26 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/units.py tests/test_units.py
git commit -m "feat(units): effective classification propagates dependency-ward

Closes the governance-laundering hole: code relocated into a low-classification
unit that a prod unit imports is governed as prod."
```

---

### Task 4: `check_org_adrs.py` — set-valued matching and `--all-units`

**Files:**
- Modify: `assets/specdev/tools/check_org_adrs.py:66-124` (normalize + matching), `:126-215` (main)
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: `units.unit_entries`, `units.effective`, `units.escalations`, `units.check`, `units.load_registry`, `units.LINK_KEYS`
- Produces: `check_org_adrs.normalize_classification(raw, axes) -> tuple[dict | None, list[str]]` now returning `{axis: set[value]}`; `check_unit(root, unit, index, effective_cls) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_units.py`:

```python
COA_PATH = ROOT / "assets" / "specdev" / "tools" / "check_org_adrs.py"
coa = load_mod(COA_PATH, "check_org_adrs")


def test_normalize_returns_sets():
    got, errs = coa.normalize_classification(
        {"maturity": "prod", "audience": "internal"}, AXES)
    assert errs == []
    assert got == {"maturity": {"prod"}, "audience": {"internal"}}


def test_normalize_bare_string_single_axis_scheme():
    axes = {"maturity": AXES["maturity"]}
    got, errs = coa.normalize_classification("prod", axes)
    assert errs == []
    assert got == {"maturity": {"prod"}}


def test_entry_matches_against_a_value_set():
    vmap = coa.build_value_map(AXES)
    cls = {"maturity": {"prod"}, "audience": {"internal", "customer"}}
    assert coa.entry_matches("customer", cls, AXES, vmap) is True
    assert coa.entry_matches("internal", cls, AXES, vmap) is True


def test_entry_matches_ranked_plus_uses_max_rank():
    vmap = coa.build_value_map(AXES)
    cls = {"maturity": {"prod"}, "audience": {"internal"}}
    assert coa.entry_matches("dev+", cls, AXES, vmap) is True
    cls_poc = {"maturity": {"poc"}, "audience": {"internal"}}
    assert coa.entry_matches("dev+", cls_poc, AXES, vmap) is False


def test_entry_matches_unknown_value_fails_safe():
    vmap = coa.build_value_map(AXES)
    cls = {"maturity": {"prod"}}
    assert coa.entry_matches("nonsense", cls, AXES, vmap) is False


def test_and_conditions_still_and():
    vmap = coa.build_value_map(AXES)
    cls = {"maturity": {"prod"}, "audience": {"customer"}}
    assert coa.entry_matches("customer & dev+", cls, AXES, vmap) is True
    cls2 = {"maturity": {"prod"}, "audience": {"internal"}}
    assert coa.entry_matches("customer & dev+", cls2, AXES, vmap) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_units.py -k "normalize or entry_matches or and_conditions" -v`
Expected: FAIL — `normalize_classification` returns `{"maturity": "prod"}` not `{"maturity": {"prod"}}`.

- [ ] **Step 3: Implement set-valued classification**

In `assets/specdev/tools/check_org_adrs.py`, replace the return at the end of `normalize_classification` (currently `return (raw, errors) if not errors else (None, errors)`) with:

```python
    if errors:
        return None, errors
    return {axis: {value} for axis, value in raw.items()}, []
```

Replace the body of `entry_matches` (lines 96-117) with:

```python
def entry_matches(entry: str, classification: dict, axes: dict, vmap: dict) -> bool:
    """'all', or '&'-joined conditions ANDed; '<value>+' = that rank and above
    on the value's (ordered) axis. List entries are OR — handled by applies().

    `classification` maps each axis to a SET of values: a unit may hold several
    values on an unordered axis once effective classification has been pulled
    up from its dependents."""
    if entry == "all":
        return True
    for cond in (c.strip() for c in entry.split("&")):
        plus = cond.endswith("+")
        val = cond[:-1] if plus else cond
        axis = vmap.get(val)
        if axis is None:
            return False  # unknown value: fail safe, never widen applicability
        repo_vals = classification.get(axis)
        if not repo_vals:
            return False
        if plus:
            values = axes[axis]["values"]
            top = max(values.get(v, {}).get("rank", -1) for v in repo_vals)
            if top < values[val].get("rank", 0):
                return False
        elif val not in repo_vals:
            return False
    return True
```

Add the import at the top of the file, after `from pathlib import Path`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402  (vendored sibling module)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -k "normalize or entry_matches or and_conditions" -v`
Expected: PASS — 6 passed.

- [ ] **Step 5: Refactor `main()` into a per-unit function plus a driver**

Replace `main()` in `check_org_adrs.py` from `cls_str = ...` onward. Extract the per-unit body into:

```python
def check_unit(root: Path, unit: str, index: dict, classification: dict) -> list[str]:
    """Verify one unit's manifest against the applicable org ADRs. Returns a
    list of error strings; empty means green."""
    axes = index.get("axes", {})
    vmap = build_value_map(axes)
    manifest_path = root / unit / ".specdev" / "adr" / "org-compliance.json"
    cls_str = ", ".join(f"{a}: {'|'.join(sorted(classification[a]))}"
                        for a in sorted(classification))
    errors: list[str] = []

    applicable = [a for a in index.get("adrs", [])
                  if applies(a, classification, axes, vmap)]

    entries: dict[str, dict] = {}
    if manifest_path.exists():
        entries = {e.get("id"): e for e in load_json(manifest_path).get("entries", [])}
    elif applicable:
        return [f"{manifest_path} not found but {len(applicable)} org ADR(s) apply — "
                "run the adr-checker agent to verify and write the manifest"]

    for adr in applicable:
        e = entries.get(adr["id"])
        if e is None:
            errors.append(f"{adr['id']} ({adr['title']}) applies to {unit} ({cls_str}) "
                          "but has no manifest entry — not yet verified")
            continue
        status = e.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{adr['id']}: manifest status '{status}' not in "
                          f"{sorted(ALLOWED_STATUSES)}")
            continue
        if status != "met" and not (e.get("justification") or "").strip():
            errors.append(f"{adr['id']}: status '{status}' requires a justification")
        if e.get("sha256") != adr.get("sha256"):
            errors.append(f"{adr['id']}: recorded sha256 does not match the org index — "
                          "the ADR changed upstream; re-run the adr-checker agent")
    return errors
```

> **Note for the implementer:** the loop body above must preserve whatever
> checks the current `main()` performs after the `status not in ALLOWED_STATUSES`
> branch. Read `check_org_adrs.py:186-215` before writing this and carry every
> existing check across verbatim — this refactor must not silently drop a rule.

Then the new driver:

```python
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--index", help="local index.json (skip fetching)")
    ap.add_argument("--unit", help="check a single unit (default: all)")
    ap.add_argument("--all-units", action="store_true",
                    help="check every registered unit (default when a registry exists)")
    args = ap.parse_args()

    root = Path(args.root)

    reg_errors = units.check(root)
    if reg_errors:
        for e in reg_errors:
            print(f"ERROR: {e}")
        return 1

    entries = units.unit_entries(root)
    if args.unit:
        entries = [e for e in entries if e["path"] == args.unit]
        if not entries:
            print(f"ERROR: unit '{args.unit}' is not registered")
            return 1

    # Resolve the repo-wide governance link: the registry when multi-unit,
    # else the single root org.json.
    reg = units.load_registry(root)
    link = None
    if reg is not None:
        link = {k: reg[k] for k in units.LINK_KEYS if k in reg}
    else:
        org_path = root / ".specdev" / "org.json"
        if not org_path.exists():
            print("org.json not found — org ADR governance not adopted; skipping (OK).")
            return 0
        link = load_json(org_path)

    if not link or any("REPLACE_ME" in json.dumps(link.get(k, ""))
                       for k in ("governance_repo",)):
        print("governance link still has REPLACE_ME values — org ADR governance "
              "not configured; skipping (OK).")
        return 0

    index = load_json(Path(args.index)) if args.index else fetch_index(link)
    axes = index.get("axes", {})

    declared: dict[str, dict] = {}
    for e in units.unit_entries(root):
        p = root / e["path"] / ".specdev" / "org.json"
        if not p.exists():
            continue
        raw = load_json(p).get("classification")
        if raw is None or "REPLACE_ME" in json.dumps(raw):
            continue
        norm, errs = normalize_classification(raw, axes)
        if errs:
            for err in errs:
                print(f"ERROR: {e['path']}: {err}")
            return 1
        declared[e["path"]] = norm

    if not declared:
        print("no unit declares a classification — org ADR governance not "
              "configured; skipping (OK).")
        return 0

    all_entries = units.unit_entries(root)
    eff = units.effective(all_entries, axes, declared)
    for line in units.escalations(all_entries, axes, declared):
        print(f"NOTE: effective classification raised — {line}")

    failed = False
    for e in entries:
        unit = e["path"]
        if unit not in declared:
            continue
        errs = check_unit(root, unit, index, eff[unit])
        label = unit if unit != "." else "repo"
        if errs:
            failed = True
            for err in errs:
                print(f"ERROR [{label}]: {err}")
        else:
            print(f"OK [{label}]: all applicable org ADRs verified and current.")
    return 1 if failed else 0
```

- [ ] **Step 6: Write the integration test**

Append to `tests/test_units.py`:

```python
def _index(adrs, axes=None):
    return {"axes": axes or AXES, "adrs": adrs}


def _write_manifest(root, unit, entries):
    d = root / unit / ".specdev" / "adr"
    d.mkdir(parents=True, exist_ok=True)
    (d / "org-compliance.json").write_text(
        json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def test_check_org_adrs_multi_unit_applies_per_unit(tmp_path, capsys):
    make_unit(tmp_path, "infra", classification={"maturity": "prod",
                                                 "audience": "internal"})
    make_unit(tmp_path, "demos", classification={"maturity": "poc",
                                                 "audience": "internal"})
    write_registry(tmp_path, {
        "schema_version": 1,
        "governance_repo": "faro/governance",
        "ref": "main",
        "path": "governance/adr",
        "units": ["infra", "demos"],
    })
    idx = _index([{"id": "ADR-0004", "title": "Terraform", "status": "accepted",
                   "applies_to": ["dev+"], "sha256": "abc"}])
    idx_file = tmp_path / "index.json"
    idx_file.write_text(json.dumps(idx), encoding="utf-8")

    # infra is prod -> ADR applies -> unverified -> fail
    rc = subprocess.run(
        ["python", str(COA_PATH), "--root", str(tmp_path),
         "--index", str(idx_file)],
        capture_output=True, text=True)
    assert rc.returncode == 1
    assert "infra" in rc.stdout

    # verify infra, leave demos untouched -> green (ADR does not apply to poc)
    _write_manifest(tmp_path, "infra",
                    [{"id": "ADR-0004", "status": "met", "sha256": "abc"}])
    rc = subprocess.run(
        ["python", str(COA_PATH), "--root", str(tmp_path),
         "--index", str(idx_file)],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr


def test_check_org_adrs_escalated_unit_must_verify(tmp_path):
    """The laundering scenario end-to-end: poc/risky is only reachable as a
    prod dependency, and the gate must demand its verification."""
    make_unit(tmp_path, "prod-svc", classification={"maturity": "prod",
                                                    "audience": "internal"})
    make_unit(tmp_path, "risky", classification={"maturity": "poc",
                                                 "audience": "internal"})
    write_registry(tmp_path, {
        "schema_version": 1,
        "governance_repo": "faro/governance",
        "ref": "main",
        "path": "governance/adr",
        "units": [{"path": "prod-svc", "depends_on": ["risky"]}, "risky"],
    })
    idx = _index([{"id": "ADR-0004", "title": "Terraform", "status": "accepted",
                   "applies_to": ["dev+"], "sha256": "abc"}])
    idx_file = tmp_path / "index.json"
    idx_file.write_text(json.dumps(idx), encoding="utf-8")
    _write_manifest(tmp_path, "prod-svc",
                    [{"id": "ADR-0004", "status": "met", "sha256": "abc"}])

    rc = subprocess.run(
        ["python", str(COA_PATH), "--root", str(tmp_path),
         "--index", str(idx_file)],
        capture_output=True, text=True)
    assert rc.returncode == 1
    assert "risky" in rc.stdout
    assert "effective classification raised" in rc.stdout


def test_check_org_adrs_single_root_unchanged(tmp_path):
    """No registry: behaves exactly as before multi-unit support."""
    d = tmp_path / ".specdev"
    d.mkdir()
    (d / "org.json").write_text(json.dumps({
        "governance_repo": "faro/governance", "ref": "main",
        "path": "governance/adr",
        "classification": {"maturity": "prod", "audience": "internal"},
    }), encoding="utf-8")
    idx = _index([{"id": "ADR-0004", "title": "Terraform", "status": "accepted",
                   "applies_to": ["dev+"], "sha256": "abc"}])
    idx_file = tmp_path / "index.json"
    idx_file.write_text(json.dumps(idx), encoding="utf-8")

    rc = subprocess.run(
        ["python", str(COA_PATH), "--root", str(tmp_path),
         "--index", str(idx_file)],
        capture_output=True, text=True)
    assert rc.returncode == 1  # unverified

    _write_manifest(tmp_path, ".",
                    [{"id": "ADR-0004", "status": "met", "sha256": "abc"}])
    rc = subprocess.run(
        ["python", str(COA_PATH), "--root", str(tmp_path),
         "--index", str(idx_file)],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr


def test_check_org_adrs_inert_without_org_json(tmp_path):
    rc = subprocess.run(
        ["python", str(COA_PATH), "--root", str(tmp_path)],
        capture_output=True, text=True)
    assert rc.returncode == 0
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: PASS — all tests including the pre-existing `tests/test_specdev_ci.py`.

- [ ] **Step 8: Commit**

```bash
git add assets/specdev/tools/check_org_adrs.py tests/test_units.py
git commit -m "feat(org-adr): per-unit checking with set-valued classification"
```

---

### Task 5: Unit-relative artifact generation

**Files:**
- Modify: `assets/specdev/tools/gen_traceability.py:141-160`
- Modify: `assets/specdev/tools/gen_compliance.py:396-420`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: `units.unit_paths`
- Produces: both tools gain `--all-units`; `gen_traceability` writes `<root>/<unit>/.specdev/traceability.md`

> **CORRECTED DURING IMPLEMENTATION.** This task originally opened with a
> bug fix: `gen_traceability.py:144`'s `--out` default was said to be
> root-absolute, making the tool ignore `--root`. **That was wrong.** Line 204
> is `out = root / args.out`, which joins the default onto the root. Verified
> empirically — `--root infra` writes `infra/.specdev/traceability.md`.
> `gen_compliance.py:406` is likewise already root-relative. The bug-fix steps
> have been removed; only the `--all-units` work below is real.
>
> The genuinely root-absolute path is in `traceability.yml:34`
> (`git add .specdev/traceability.md`) and is fixed in Task 14.

- [ ] **Step 1: Add a regression test pinning the (already correct) behaviour**

```python
GT_PATH = ROOT / "assets" / "specdev" / "tools" / "gen_traceability.py"


def test_gen_traceability_writes_under_root(tmp_path):
    unit = tmp_path / "infra"
    (unit / ".specdev").mkdir(parents=True)
    (unit / ".specdev" / "spec.md").write_text(
        "# Spec\n\n**Feature ID:** FEAT-001\n", encoding="utf-8")

    rc = subprocess.run(
        [sys.executable, str(GT_PATH), "--root", str(unit)],
        capture_output=True, text=True, cwd=tmp_path)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert (unit / ".specdev" / "traceability.md").exists()
    assert not (tmp_path / ".specdev" / "traceability.md").exists()
```

- [ ] **Step 2: Add `--all-units` to both generators**

Add to both `gen_traceability.py` and `gen_compliance.py`, in `main()` after parsing:

```python
    if args.all_units:
        rc = 0
        for unit in units.unit_paths(args.root):
            sub = argparse.Namespace(**vars(args))
            sub.all_units = False
            sub.root = str(Path(args.root) / unit)
            rc |= run_one(sub)
        return rc
```

with the existing single-unit body extracted into `run_one(args) -> int`, and the flag registered as:

```python
    ap.add_argument("--all-units", action="store_true",
                    help="generate for every registered unit")
```

Both need the same sibling import as Task 4:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402
```

**Do not merge outputs.** Each unit writes its own file. A rolled-up
`<root>/.specdev/traceability-index.md` links to each unit's file:

```python
def write_index(root, unit_paths_list, filename, title):
    lines = [f"# {title}", "",
             "Per-unit artifacts. These are deliberately NOT merged — see",
             "docs/superpowers/specs/2026-07-28-monorepo-multi-unit-design.md",
             "decision 6.", ""]
    for u in unit_paths_list:
        lines.append(f"- [{u}]({u}/.specdev/{filename})")
    (Path(root) / ".specdev" / f"{Path(filename).stem}-index.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest tests/ -q`
Expected: all pass.

```bash
git add assets/specdev/tools/gen_traceability.py assets/specdev/tools/gen_compliance.py tests/test_units.py
git commit -m "fix(traceability): honour --root for --out; add per-unit generation

gen_traceability's --out defaulted to the root-absolute path, so it ignored
--root entirely and wrote to the repo root. Compliance and traceability are
generated per-unit and never merged: an SoA carries a scope statement, and
merging two units' SoAs produces a document true of neither."
```

---

### Task 6: `deploy.py --root`

**Files:**
- Modify: `assets/specdev/tools/deploy.py:250-270`
- Test: `tests/test_units.py`

**Interfaces:**
- Produces: `deploy.py` accepts `--root` on every subcommand; the deploy profile resolves to `<root>/.specdev/deploy.profile.json`

- [ ] **Step 1: Write the failing test**

```python
DEPLOY_PATH = ROOT / "assets" / "specdev" / "tools" / "deploy.py"


def test_deploy_accepts_root(tmp_path):
    unit = tmp_path / "infra"
    (unit / ".specdev").mkdir(parents=True)
    (unit / ".specdev" / "deploy.profile.json").write_text(
        json.dumps({"platform": "none", "environments": {}}), encoding="utf-8")
    rc = subprocess.run(
        ["python", str(DEPLOY_PATH), "target", "--root", str(unit)],
        capture_output=True, text=True, cwd=tmp_path)
    assert rc.returncode == 0, rc.stdout + rc.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_units.py::test_deploy_accepts_root -v`
Expected: FAIL — `error: unrecognized arguments: --root`.

- [ ] **Step 3: Add `--root`**

In `deploy.py`'s `main()`, add to the top-level parser before the subparsers:

```python
    ap.add_argument("--root", default=".")
```

Then thread `args.root` into wherever the profile path is currently resolved. Read `deploy.py` fully first: locate the module-level constant or function that builds the profile path and give it a `root="."` parameter, exactly as `run_manifest.run_path` does.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_units.py::test_deploy_accepts_root -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/deploy.py tests/test_units.py
git commit -m "feat(deploy): add --root so deploy is unit-relative"
```

---

### Task 7: Matrix helper — changed units and scope guard

**Files:**
- Modify: `assets/specdev/tools/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Produces:
  - `changed_units(root=".", base=None) -> list[str]`
  - `out_of_scope(root=".", unit=".", base=None) -> list[str]`
  - CLI: `units.py matrix [--changed-from REF] [--all]` printing a JSON array
  - CLI: `units.py scope-check --unit U --changed-from REF`

- [ ] **Step 1: Write the failing tests**

```python
def _git(tmp_path, *args):
    subprocess.run(["git", *args], cwd=tmp_path, check=True,
                   capture_output=True, text=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def test_changed_units_filters_to_touched_units(tmp_path):
    _repo(tmp_path)
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "demos")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra", "demos"]})
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()
    (tmp_path / "demos" / "note.txt").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "touch demos")

    assert un.changed_units(tmp_path, base) == ["demos"]


def test_changed_units_without_base_returns_all(tmp_path):
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "demos")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra", "demos"]})
    assert un.changed_units(tmp_path, None) == ["demos", "infra"]


def test_out_of_scope_detects_cross_unit_changes(tmp_path):
    _repo(tmp_path)
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "soc")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra", "soc"]})
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()
    (tmp_path / "soc" / "leak.txt").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "touch soc")

    assert un.out_of_scope(tmp_path, "infra", base) == ["soc/leak.txt"]
    assert un.out_of_scope(tmp_path, "soc", base) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_units.py -k "changed_units or out_of_scope" -v`
Expected: FAIL — `AttributeError: module 'units' has no attribute 'changed_units'`.

- [ ] **Step 3: Implement**

Add to `units.py` (and add `import subprocess` at the top):

```python
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
    """Changed files that do NOT belong to `unit` — so a branch name cannot lie
    about the scope of its PR. Files outside every registered unit (shared CI
    config, docs) are not attributed to any unit and are allowed."""
    if unit == ".":
        return []
    all_units = unit_paths(root)
    bad = []
    for f in _changed_files(root, base):
        if _owns(unit, f):
            continue
        if any(_owns(u, f) for u in all_units if u != unit):
            bad.append(f)
    return sorted(bad)
```

Add the CLI subcommands in `main()`:

```python
    pm = sub.add_parser("matrix")
    pm.add_argument("--changed-from", default=None)
    pm.add_argument("--all", action="store_true",
                    help="ignore --changed-from and emit every unit "
                         "(required for org-adr-check: the staleness check is "
                         "not diff-driven)")
    ps = sub.add_parser("scope-check")
    ps.add_argument("--unit", required=True)
    ps.add_argument("--changed-from", required=True)
```

and the handlers:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -k "changed_units or out_of_scope" -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/units.py tests/test_units.py
git commit -m "feat(units): changed-unit matrix and cross-unit scope guard"
```

---

### Task 8: Matrix the verification workflows

**Files:**
- Modify: `assets/workflows/org-adr-check.yml`
- Modify: `assets/workflows/spec-validate.yml`
- Modify: `assets/workflows/compliance.yml`
- Modify: `assets/workflows/post-dev-qa.yml`

**Interfaces:**
- Consumes: `units.py matrix`, `units.py check`

**Critical:** `org-adr-check` uses `--all` and has **no** `paths:` filter. The
`sha256` staleness check is driven by the upstream index, not the local diff;
filtering it by changed paths would let verifications rot silently. This is a
correctness constraint, not a performance trade-off.

- [ ] **Step 1: Rewrite `org-adr-check.yml`**

```yaml
name: org-adr-check

# Deterministic org-ADR governance gate, per governed unit.
#
# NO PATH FILTER, BY DESIGN. The staleness half of check_org_adrs.py is not
# diff-driven: an upstream ADR edit invalidates every unit's recorded sha256
# with no local change at all. Filtering this workflow by changed paths would
# yield a repo that looks green for months while its verifications rot.
#
# Mark the 'summary' job — not the matrix legs — a required status check.
# Matrix legs produce dynamic check names that branch protection cannot
# require, so a newly added unit would otherwise add a silently unrequired check.
on:
  pull_request:
  merge_group:

permissions:
  contents: read

jobs:
  discover:
    runs-on: ubuntu-latest
    outputs:
      units: ${{ steps.m.outputs.units }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Validate the unit registry
        run: python .specdev/tools/units.py check
      - id: m
        name: Enumerate ALL units (never filtered — see header)
        run: echo "units=$(python .specdev/tools/units.py matrix --all)" >> "$GITHUB_OUTPUT"

  check:
    needs: discover
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        unit: ${{ fromJSON(needs.discover.outputs.units) }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Org ADR compliance — ${{ matrix.unit }}
        if: hashFiles(format('{0}/.specdev/org.json', matrix.unit)) != '' || hashFiles('.specdev/org.json') != ''
        env:
          # For a PRIVATE governance repo the default GITHUB_TOKEN cannot read
          # across repos — create a read-only PAT, save it as the
          # GOVERNANCE_TOKEN secret, and it will be picked up here.
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GOVERNANCE_TOKEN: ${{ secrets.GOVERNANCE_TOKEN }}
        run: python .specdev/tools/check_org_adrs.py --unit '${{ matrix.unit }}'

  summary:
    needs: [discover, check]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Per-unit verdicts
        run: |
          {
            echo "## org-adr-check"
            echo ""
            echo "| unit | verdict |"
            echo "| --- | --- |"
          } >> "$GITHUB_STEP_SUMMARY"
          for u in $(echo '${{ needs.discover.outputs.units }}' | python -c 'import json,sys; print(" ".join(json.load(sys.stdin)))'); do
            echo "| \`$u\` | ${{ needs.check.result }} |" >> "$GITHUB_STEP_SUMMARY"
          done
      - name: Fail if any unit failed
        if: needs.check.result != 'success' || needs.discover.result != 'success'
        run: |
          echo "One or more units failed org-adr-check — see the matrix legs."
          exit 1
```

- [ ] **Step 2: Rewrite `spec-validate.yml` with changed-unit filtering**

```yaml
name: spec-validate

# Gate 1 — validates the spec/architecture artifacts on the Spec PR, per unit.
# Unlike org-adr-check this IS filtered to changed units: spec validation is
# purely local, so an untouched unit's verdict cannot change.
# Make the 'summary' job a required status check on branches matching spec/**.
on:
  pull_request:
  merge_group:

permissions:
  contents: read

jobs:
  discover:
    runs-on: ubuntu-latest
    outputs:
      units: ${{ steps.m.outputs.units }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Validate the unit registry
        run: python .specdev/tools/units.py check
      - id: m
        name: Enumerate changed units
        run: |
          base='${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}'
          echo "units=$(python .specdev/tools/units.py matrix --changed-from "$base")" >> "$GITHUB_OUTPUT"

  validate:
    needs: discover
    if: needs.discover.outputs.units != '[]'
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        unit: ${{ fromJSON(needs.discover.outputs.units) }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Validate spec artifacts (Gate 1) — ${{ matrix.unit }}
        run: python .specdev/tools/validate_spec.py --strict --root '${{ matrix.unit }}'

  summary:
    needs: [discover, validate]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Per-unit verdicts
        run: |
          {
            echo "## spec-validate"
            echo ""
            echo "units: \`${{ needs.discover.outputs.units }}\`"
            echo ""
            echo "result: **${{ needs.validate.result }}**"
          } >> "$GITHUB_STEP_SUMMARY"
      - name: Fail if any unit failed
        if: needs.discover.result != 'success' || needs.validate.result == 'failure'
        run: exit 1
```

- [ ] **Step 3: Apply the same discover/matrix/summary shape to `compliance.yml` and `post-dev-qa.yml`**

For both: replace the root-absolute `paths-ignore` literals with unit-relative
globs, and pass `--root '${{ matrix.unit }}'` to every tool invocation.

In `compliance.yml`, the existing guard

```yaml
        if: hashFiles('.specdev/compliance/compliance.config.json') != ''
```

becomes

```yaml
        if: hashFiles(format('{0}/.specdev/compliance/compliance.config.json', matrix.unit)) != ''
```

and the artifact upload path becomes `${{ matrix.unit }}/.specdev/compliance/*.md`
with `name: compliance-${{ matrix.unit }}` so per-unit artifacts do not collide.

In `post-dev-qa.yml`, `paths-ignore: ['.specdev/**']` becomes `['**/.specdev/**']`.

- [ ] **Step 4: Validate the workflow YAML parses**

Run:
```bash
python -c "import json,sys;
try:
    import yaml
except ImportError:
    print('PyYAML not installed - install with: pip install pyyaml'); sys.exit(1)
import glob
for f in glob.glob('assets/workflows/*.yml'):
    yaml.safe_load(open(f, encoding='utf-8'))
    print('ok', f)"
```
Expected: `ok` for every workflow file. (PyYAML is a dev-only dependency for
this check — it is never imported by the vendored tools.)

- [ ] **Step 5: Commit**

```bash
git add assets/workflows/
git commit -m "feat(ci): matrix verification gates over governed units

org-adr-check matrixes over ALL units and is never path-filtered — its
staleness check is driven by the upstream ADR index, not the local diff. The
aggregate summary job is the required status check, because matrix legs
produce dynamic check names branch protection cannot require."
```

---

### Task 9: Nightly full sweep

**Files:**
- Create: `assets/workflows/specdev-sweep.yml`

**Interfaces:**
- Consumes: `units.py matrix --all`, `check_org_adrs.py`

**Why:** `ref` may be a moving branch, so governance can change with zero repo
activity. A repo with no PR traffic would never notice.

- [ ] **Step 1: Create the workflow**

```yaml
name: specdev-sweep

# Nightly unfiltered sweep across every governed unit.
#
# Required because org.json's `ref` may be a moving branch: an upstream ADR can
# change with no local commit, invalidating recorded sha256 verifications in a
# repo that sees no PR activity for weeks. PR-triggered gates cannot catch that.
on:
  schedule:
    - cron: '17 3 * * *'
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  discover:
    runs-on: ubuntu-latest
    outputs:
      units: ${{ steps.m.outputs.units }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - id: m
        run: echo "units=$(python .specdev/tools/units.py matrix --all)" >> "$GITHUB_OUTPUT"

  sweep:
    needs: discover
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        unit: ${{ fromJSON(needs.discover.outputs.units) }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Org ADR compliance — ${{ matrix.unit }}
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GOVERNANCE_TOKEN: ${{ secrets.GOVERNANCE_TOKEN }}
        run: python .specdev/tools/check_org_adrs.py --unit '${{ matrix.unit }}'

  report:
    needs: [discover, sweep]
    if: always() && needs.sweep.result != 'success'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            const title = 'specdev-sweep: org ADR verifications are stale or failing';
            const body = [
              'The nightly SpecDev sweep failed.',
              '',
              'This usually means an org ADR changed upstream and one or more',
              'units\' recorded `sha256` verifications are now stale. Re-run the',
              '`adr-checker` agent for the affected units.',
              '',
              `Units swept: \`${{ needs.discover.outputs.units }}\``,
              '',
              `Run: ${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`,
            ].join('\n');
            const existing = await github.rest.issues.listForRepo({
              owner: context.repo.owner, repo: context.repo.repo,
              state: 'open', labels: 'specdev-sweep',
            });
            if (existing.data.length) {
              await github.rest.issues.createComment({
                owner: context.repo.owner, repo: context.repo.repo,
                issue_number: existing.data[0].number, body,
              });
            } else {
              await github.rest.issues.create({
                owner: context.repo.owner, repo: context.repo.repo,
                title, body, labels: ['specdev-sweep'],
              });
            }
```

- [ ] **Step 2: Validate the YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('assets/workflows/specdev-sweep.yml', encoding='utf-8')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add assets/workflows/specdev-sweep.yml
git commit -m "feat(ci): nightly all-unit sweep for upstream ADR staleness"
```

---

### Task 10: `adr-checker` agent takes a unit root

**Files:**
- Modify: `agents/adr-checker.md:15`, `:51`, and every other `.specdev/` literal

- [ ] **Step 1: Read the agent file end to end**

Run: `cat agents/adr-checker.md`

Identify every hardcoded `.specdev/` path. Task 4's implementation changed the
manifest location to `<unit>/.specdev/adr/org-compliance.json`.

- [ ] **Step 2: Add a unit parameter to the procedure**

Insert a new step before the current step 1:

```markdown
0. **Establish your unit root.** The coordinator gives you a **unit root** —
   a repo-relative directory, `.` for a single-unit repo. Every path below is
   relative to it: `<unit>/.specdev/org.json`,
   `<unit>/.specdev/adr/org-compliance.json`, `<unit>/.specdev/adr/`. If no
   unit root was given, use `.`.

   Run `python .specdev/tools/units.py list` to see the repo's units. The
   **tools** always live at the repo root (`.specdev/tools/`); only the
   governed artifacts are per-unit.

   Your verdict covers YOUR unit only. Never read or write another unit's
   manifest.
```

- [ ] **Step 3: Replace the hardcoded paths**

Rewrite step 1's `Load `.specdev/org.json`` as `Load `<unit>/.specdev/org.json`.
If it declares no `classification`, and a `.specdev/units.json` exists, the
governance link comes from the registry — read it there.`

Rewrite step 4's and step 6's `.specdev/adr/org-compliance.json` as
`<unit>/.specdev/adr/org-compliance.json`, and step 5's local `.specdev/adr/`
as `<unit>/.specdev/adr/`.

Update the `check_org_adrs.py` invocation in step 3 to:

```
python .specdev/tools/check_org_adrs.py --unit <unit> --index <file>
```

- [ ] **Step 4: Add the effective-classification note**

Append to step 3:

```markdown
   **Effective classification may exceed what the unit declares.** If a
   higher-classified unit depends on yours, yours is governed at the higher
   level — `check_org_adrs.py` prints `NOTE: effective classification raised`
   with the causing dependent. Judge against the EFFECTIVE classification the
   tool reports, never the raw value in `org.json`.
```

- [ ] **Step 5: Commit**

```bash
git add agents/adr-checker.md
git commit -m "feat(adr-checker): operate on a unit root instead of hardcoded paths"
```

---

## Phase 2 — Build and Deploy

### Task 11: Branch ref parsing

**Files:**
- Modify: `assets/specdev/tools/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Produces: `parse_ref(ref, registry) -> tuple[str, str]` returning `(unit, feat)`

- [ ] **Step 1: Write the failing tests**

```python
# ---- ref parsing -------------------------------------------------------

REG = {"schema_version": 1, "units": ["infrastructure", "demos"]}


def test_parse_ref_single_root_no_registry():
    assert un.parse_ref("spec/add-retry-logic", None) == (".", "add-retry-logic")


def test_parse_ref_named_unit():
    assert un.parse_ref("spec/infrastructure/vpc-peering", REG) == \
        ("infrastructure", "vpc-peering")


def test_parse_ref_poc_prefix():
    assert un.parse_ref("poc/demos/rag-spike", REG) == ("demos", "rag-spike")


def test_parse_ref_unknown_segment_is_part_of_the_feature_name():
    """spec/foo/bar with no unit 'foo' must NOT silently become unit 'foo'."""
    assert un.parse_ref("spec/foo/bar", REG) == (".", "foo/bar")


def test_parse_ref_strips_refs_heads_prefix():
    assert un.parse_ref("refs/heads/spec/demos/x", REG) == ("demos", "x")


def test_parse_ref_non_specdev_branch():
    assert un.parse_ref("main", REG) == (".", "main")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_units.py -k parse_ref -v`
Expected: FAIL — `AttributeError: module 'units' has no attribute 'parse_ref'`.

- [ ] **Step 3: Implement**

```python
BUILD_PREFIXES = ("spec/", "poc/")


def parse_ref(ref: str, registry) -> tuple:
    """('spec/infrastructure/vpc', reg) -> ('infrastructure', 'vpc').

    A path segment is read as a unit ONLY when a registry exists and the
    segment names a registered unit — so today's `spec/<name>` on a single-root
    repo keeps resolving to the root unit, and `spec/foo/bar` with no unit
    'foo' keeps 'foo/bar' as the feature name rather than inventing a unit."""
    ref = ref or ""
    if ref.startswith("refs/heads/"):
        ref = ref[len("refs/heads/"):]
    for prefix in BUILD_PREFIXES:
        if ref.startswith(prefix):
            rest = ref[len(prefix):]
            break
    else:
        return (".", ref)
    if registry:
        names = {_entry(u)["path"] for u in registry.get("units", [])}
        head, sep, tail = rest.partition("/")
        if sep and tail and head in names:
            return (head, tail)
    return (".", rest)
```

Add the CLI handler:

```python
    pr = sub.add_parser("resolve-ref")
    pr.add_argument("--ref", required=True)
```

```python
    if args.cmd == "resolve-ref":
        unit, feat = parse_ref(args.ref, load_registry(args.root))
        print(f"unit={unit}")
        print(f"feat={feat}")
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -k parse_ref -v`
Expected: PASS — 6 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/units.py tests/test_units.py
git commit -m "feat(units): parse spec/<unit>/<name> refs with back-compat"
```

---

### Task 12: Per-unit `run.json` and `ci.json` fallback

**Files:**
- Modify: `assets/specdev/tools/run_manifest.py`
- Test: `tests/test_specdev_ci.py`

**Interfaces:**
- Consumes: `units.parse_ref`, `units.load_registry`
- Produces: `run_manifest.py` gains `--unit`; `ci_get(key, root, repo_root=None)` falls back to the repo-root `ci.json`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_specdev_ci.py`:

```python
def test_run_json_is_per_unit(tmp_path):
    (tmp_path / "infra" / ".specdev").mkdir(parents=True)
    (tmp_path / "demos" / ".specdev").mkdir(parents=True)
    rm.save({"schema_version": 1, "feat": "FEAT-001", "mode": "prod"},
            tmp_path / "infra")
    rm.save({"schema_version": 1, "feat": "FEAT-002", "mode": "poc",
             "poc_environment": "poc"}, tmp_path / "demos")
    assert rm.mode_of(tmp_path / "infra") == "prod"
    assert rm.mode_of(tmp_path / "demos") == "poc"
    assert rm.load(tmp_path / "infra")["feat"] == "FEAT-001"


def test_ci_get_falls_back_to_repo_root(tmp_path):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "ci.json").write_text(
        json.dumps({"runner": "self-hosted"}), encoding="utf-8")
    (tmp_path / "infra" / ".specdev").mkdir(parents=True)
    assert rm.ci_get("runner", tmp_path / "infra", repo_root=tmp_path) == "self-hosted"


def test_ci_get_unit_overrides_repo_root(tmp_path):
    (tmp_path / ".specdev").mkdir()
    (tmp_path / ".specdev" / "ci.json").write_text(
        json.dumps({"runner": "self-hosted"}), encoding="utf-8")
    (tmp_path / "infra" / ".specdev").mkdir(parents=True)
    (tmp_path / "infra" / ".specdev" / "ci.json").write_text(
        json.dumps({"runner": "ubuntu-24.04"}), encoding="utf-8")
    assert rm.ci_get("runner", tmp_path / "infra", repo_root=tmp_path) == "ubuntu-24.04"


def test_ci_get_missing_key_fails_loudly(tmp_path):
    """A missing key must not print the string 'None' at exit 0."""
    rc = subprocess.run(
        [sys.executable, str(RM_PATH), "--root", str(tmp_path), "ci",
         "--get", "no_such_key"],
        capture_output=True, text=True)
    assert rc.returncode != 0
    assert "None" not in rc.stdout
```

> **Note:** `test_ci_get_missing_key_fails_loudly` overlaps with finding 4 on
> the separate `fix/adoption-findings` branch. Implement it here — whichever
> branch merges second will have a no-op for that finding, which is fine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_specdev_ci.py -k "per_unit or ci_get" -v`
Expected: FAIL — `ci_get()` takes no `repo_root` keyword; the missing-key test exits 0.

- [ ] **Step 3: Implement**

Replace `ci_get` in `run_manifest.py`:

```python
_MISSING = object()


def ci_get(key: str, root=".", repo_root=None):
    """Read a ci.json key. A unit's ci.json wins; the repo-root ci.json is the
    fallback; CI_DEFAULTS is the floor. Returns _MISSING for an unknown key so
    the caller can fail loudly rather than emit the string 'None'."""
    cfg = dict(CI_DEFAULTS)
    for candidate in ([Path(repo_root) / CI_REL] if repo_root else []) + [ci_path(root)]:
        if candidate.exists():
            cfg.update(json.loads(candidate.read_text(encoding="utf-8-sig")))
    return cfg.get(key, _MISSING)
```

Replace the `ci` handler in `main()`:

```python
    if args.cmd == "ci":
        val = ci_get(args.get, args.root, repo_root=args.repo_root)
        if val is _MISSING:
            print(f"ERROR: no such ci.json key: {args.get!r} "
                  f"(known: {', '.join(sorted(CI_DEFAULTS))})", file=sys.stderr)
            return 1
        print(val)
        return 0
```

Add to the `ci` subparser and the top-level parser:

```python
    ap.add_argument("--repo-root", default=None,
                    help="repo root for ci.json fallback when --root is a unit")
```

Add unit resolution to `init`:

```python
    pi.add_argument("--unit", default=None,
                    help="governed unit (default: resolved from --ref, else '.')")
    pi.add_argument("--ref", default=None,
                    help="branch ref to resolve the unit and feature from")
```

and in the `init` handler, before building `doc`:

```python
        unit = args.unit
        if unit is None and args.ref:
            unit, _ = units.parse_ref(args.ref, units.load_registry(args.root))
        unit = unit or "."
        target = str(Path(args.root) / unit)
```

then `save(doc, target)` and `print(f"Wrote {run_path(target)}")`.

Add the sibling import at the top of `run_manifest.py`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: PASS — all tests, including pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/run_manifest.py tests/test_specdev_ci.py
git commit -m "feat(run): per-unit run.json, ci.json fallback, loud missing key"
```

---

### Task 13: Unit-aware build workflow

**Files:**
- Modify: `assets/workflows/specdev-build.yml`

**Interfaces:**
- Consumes: `units.py resolve-ref`, `units.py scope-check`, `run_manifest.py --unit`

- [ ] **Step 1: Resolve the unit in the `setup` job**

Add to `setup`'s outputs:

```yaml
      unit: ${{ steps.unit.outputs.unit }}
```

and the step (before the existing feature-detection step):

```yaml
      - id: unit
        name: Resolve the governed unit from the branch
        run: |
          python .specdev/tools/units.py resolve-ref \
            --ref '${{ github.event.pull_request.head.ref || github.ref }}' \
            >> "$GITHUB_OUTPUT"
```

- [ ] **Step 2: Make feature detection unit-relative**

Replace the existing fallback at `specdev-build.yml:59-60`:

```yaml
          if [ -z "$feat" ] && [ -f "${unit}/.specdev/spec.md" ]; then
            feat=$(grep -oE 'FEAT-[0-9]+' "${unit}/.specdev/spec.md" | head -1 || true)
          fi
```

with `unit` read from the previous step's output.

- [ ] **Step 3: Add the scope guard**

New job, before `build`:

```yaml
  scope:
    needs: setup
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: A branch scoped to one unit may not change another
        run: |
          python .specdev/tools/units.py scope-check \
            --unit '${{ needs.setup.outputs.unit }}' \
            --changed-from '${{ github.event.pull_request.base.sha || github.event.before }}'
```

and add `scope` to `build`'s `needs`.

- [ ] **Step 4: Per-unit concurrency**

Add at the top level of the workflow, after `permissions`:

```yaml
# Parallel across units, serialised within one. This replaces the previous
# one-build-per-repo invariant.
concurrency:
  group: specdev-build-${{ github.event.pull_request.head.ref || github.ref }}
  cancel-in-progress: false
```

- [ ] **Step 5: Thread the unit through `run_manifest init` and the agent prompt**

Change the `run_manifest.py init` invocation to add:

```yaml
            --unit '${{ needs.setup.outputs.unit }}' \
```

and the `ci` reads to add `--root '${{ needs.setup.outputs.unit }}' --repo-root .`.

Add to the agent prompt, after the `Mode:` line:

```
            Unit:    ${{ needs.setup.outputs.unit }}

            You are building ONE governed unit. Every .specdev/ path in your
            work is relative to that unit root — <unit>/.specdev/spec.md,
            <unit>/.specdev/BUILD.md, <unit>/.specdev/run.json. The TOOLS stay
            at the repo root (.specdev/tools/). Never modify files outside your
            unit: a scope check fails the build if you do.
```

and change the two `Resume from .specdev/BUILD.md` references to
`<unit>/.specdev/BUILD.md`.

- [ ] **Step 6: Validate the YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('assets/workflows/specdev-build.yml', encoding='utf-8')); print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add assets/workflows/specdev-build.yml
git commit -m "feat(build): route builds to a governed unit with a scope guard

Per-unit concurrency groups replace the one-build-per-repo invariant: builds
in different units now run in parallel and serialise only within a unit."
```

---

### Task 14: Per-unit deploy tags and profiles

**Files:**
- Modify: `assets/workflows/deploy.yml`
- Modify: `assets/workflows/deploy-poc.yml`

- [ ] **Step 1: Namespace the poc tag**

In `deploy-poc.yml`, add a `unit` input:

```yaml
      unit:
        required: false
        type: string
        default: '.'
```

and change the tag computation (currently `tag=poc-$(date +%Y%m%d)-${GITHUB_SHA::7}`):

```yaml
      - id: meta
        name: Compute poc release tag
        run: |
          slug=$(echo '${{ inputs.unit }}' | tr '/.' '--' | sed 's/^-*//')
          if [ -z "$slug" ]; then
            echo "tag=poc-$(date +%Y%m%d)-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"
          else
            echo "tag=poc-${slug}-$(date +%Y%m%d)-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"
          fi
```

Without this, two units' poc builds on the same day and commit produce the
same tag and the second push fails.

- [ ] **Step 2: Thread `--root` into every `deploy.py` call**

In both workflows, every `python .specdev/tools/deploy.py <cmd>` gains
`--root '${{ inputs.unit }}'` (deploy-poc) or `--root '${{ needs.setup.outputs.unit }}'`
(deploy).

- [ ] **Step 3: Pass the unit from `specdev-build.yml`**

In the `deploy-poc` job of `specdev-build.yml`:

```yaml
    with:
      environment: poc
      unit: ${{ needs.setup.outputs.unit }}
```

- [ ] **Step 4: Guard the traceability push against concurrent units**

In `traceability.yml`, replace the commit step with a rebase-retry loop, because
two units' builds can now push to `main` concurrently:

```yaml
      - name: Commit matrix
        run: |
          git config user.name "specdev-bot"
          git config user.email "bot@specdev.local"
          git add '**/.specdev/traceability.md' .specdev/traceability.md 2>/dev/null || true
          git diff --cached --quiet && exit 0
          git commit -m "chore(traceability): update matrix [skip ci]"
          for i in 1 2 3 4 5; do
            if git push; then exit 0; fi
            echo "push rejected (concurrent unit build?) — rebasing, attempt $i"
            git pull --rebase --autostash origin main || exit 1
          done
          echo "could not push traceability after 5 attempts"
          exit 1
```

- [ ] **Step 5: Validate the YAML parses and commit**

Run: `python -c "import yaml,glob; [yaml.safe_load(open(f, encoding='utf-8')) for f in glob.glob('assets/workflows/*.yml')]; print('ok')"`
Expected: `ok`

```bash
git add assets/workflows/
git commit -m "feat(deploy): per-unit tags, profiles, and rebase-retry pushes"
```

---

### Task 15: Migration tool

**Files:**
- Modify: `assets/specdev/tools/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Produces: `migrate(root, unit) -> list[str]` (moved paths); CLI `units.py migrate --unit <path>`

**Unit-scoped artifacts** (moved): `spec.md`, `components.md`, `traceability.md`,
`BUILD.md`, `run.json`, `org.json`, `architecture-config.json`,
`deploy.profile.json`, `adr/`, `compliance/`, `specs/`.
**Repo-scoped** (left at root): `tools/`, `units.json`, `ci.json`.

- [ ] **Step 1: Write the failing test**

```python
def test_migrate_moves_unit_scoped_artifacts(tmp_path):
    d = tmp_path / ".specdev"
    (d / "tools").mkdir(parents=True)
    (d / "adr").mkdir()
    (d / "spec.md").write_text("# Spec\n", encoding="utf-8")
    (d / "adr" / "ADR-001.md").write_text("# ADR\n", encoding="utf-8")
    (d / "tools" / "units.py").write_text("# tool\n", encoding="utf-8")
    (d / "ci.json").write_text('{"runner": "ubuntu-latest"}', encoding="utf-8")
    (d / "org.json").write_text(json.dumps({
        "governance_repo": "faro/governance", "ref": "main",
        "path": "governance/adr",
        "classification": {"maturity": "prod", "audience": "internal"},
    }), encoding="utf-8")

    un.migrate(tmp_path, "infra")

    assert (tmp_path / "infra" / ".specdev" / "spec.md").exists()
    assert (tmp_path / "infra" / ".specdev" / "adr" / "ADR-001.md").exists()
    assert not (d / "spec.md").exists()
    # repo-scoped things stay put
    assert (d / "tools" / "units.py").exists()
    assert (d / "ci.json").exists()
    # registry written with the link extracted
    reg = json.loads((d / "units.json").read_text(encoding="utf-8"))
    assert reg["governance_repo"] == "faro/governance"
    assert reg["units"] == ["infra"]
    # the unit's org.json keeps ONLY the classification
    org = json.loads(
        (tmp_path / "infra" / ".specdev" / "org.json").read_text(encoding="utf-8"))
    assert org == {"classification": {"maturity": "prod", "audience": "internal"}}


def test_migrate_refuses_when_registry_exists(tmp_path):
    (tmp_path / ".specdev").mkdir()
    write_registry(tmp_path, {"schema_version": 1, "units": ["a"]})
    with pytest.raises(SystemExit):
        un.migrate(tmp_path, "infra")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_units.py -k migrate -v`
Expected: FAIL — `AttributeError: module 'units' has no attribute 'migrate'`.

- [ ] **Step 3: Implement**

```python
UNIT_SCOPED = ("spec.md", "components.md", "traceability.md", "BUILD.md",
               "run.json", "org.json", "architecture-config.json",
               "deploy.profile.json", "adr", "compliance", "specs")
REPO_SCOPED = ("tools", "units.json", "ci.json")


def migrate(root=".", unit="") -> list[str]:
    """Move a single-root .specdev/ into <unit>/.specdev/ and write the
    registry, splitting org.json into repo-wide link + per-unit classification.

    Without this, every adopter hand-rolls the move differently."""
    import shutil

    root = Path(root)
    if not unit or unit == ".":
        print("ERROR: --unit must name a subdirectory", file=sys.stderr)
        raise SystemExit(1)
    src = root / ".specdev"
    if not src.is_dir():
        print(f"ERROR: {src} does not exist", file=sys.stderr)
        raise SystemExit(1)
    if registry_path(root).exists():
        print(f"ERROR: {REGISTRY_REL} already exists — this repo is already "
              "multi-unit; add the unit to the registry by hand", file=sys.stderr)
        raise SystemExit(1)

    dst = root / unit / ".specdev"
    dst.mkdir(parents=True, exist_ok=True)
    moved = []
    for name in UNIT_SCOPED:
        s = src / name
        if s.exists():
            shutil.move(str(s), str(dst / name))
            moved.append(f"{unit}/.specdev/{name}")

    link = {}
    org_p = dst / "org.json"
    if org_p.exists():
        org = json.loads(org_p.read_text(encoding="utf-8-sig"))
        link = {k: org[k] for k in LINK_KEYS if k in org}
        rest = {k: v for k, v in org.items() if k not in LINK_KEYS}
        org_p.write_text(json.dumps(rest, indent=2) + "\n", encoding="utf-8")

    reg = {"schema_version": SCHEMA_VERSION}
    reg.update(link)
    reg["units"] = [unit]
    registry_path(root).write_text(
        json.dumps(reg, indent=2) + "\n", encoding="utf-8")
    moved.append(REGISTRY_REL)
    return moved
```

CLI:

```python
    pmg = sub.add_parser("migrate")
    pmg.add_argument("--unit", required=True)
```

```python
    if args.cmd == "migrate":
        for p in migrate(args.root, args.unit):
            print(f"  {p}")
        print(f"\nMigrated to a multi-unit layout. Next: move the unit's source "
              f"files under {args.unit}/, then run "
              f"'python .specdev/tools/units.py check'.")
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -k migrate -v`
Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/units.py tests/test_units.py
git commit -m "feat(units): migration from single-root to multi-unit layout"
```

---

### Task 16: Seed asset, init wiring, and docs

**Files:**
- Create: `assets/specdev/units.json`
- Modify: `skills/specdev/SKILL.md`
- Modify: `skills/init` handling in `commands/init.md`
- Modify: `README.md`

- [ ] **Step 1: Create the seed registry**

`assets/specdev/units.json` is **not** copied by `init` for single-root repos.
It is a documented template only:

```json
{
  "$comment": "OPTIONAL. Present only in multi-unit (monorepo) repositories. A governed unit is a directory containing a .specdev/. Without this file the repository is a single governed unit rooted at '.', which is the default and needs no configuration. 'governance_repo'/'ref'/'path' are repo-wide and live ONLY here; each unit's .specdev/org.json carries just its 'classification'. 'depends_on' drives effective classification: a unit is governed at the level of the highest-classified unit that depends on it, because anything a production system imports is inside the production blast radius. Create this file with: python .specdev/tools/units.py migrate --unit <path>",
  "schema_version": 1,
  "governance_repo": "REPLACE_ME_OWNER/REPLACE_ME_REPO",
  "ref": "main",
  "path": "governance/adr",
  "ignore": ["**/node_modules/**", "**/vendor/**"],
  "units": [
    "REPLACE_ME_UNIT_PATH"
  ]
}
```

- [ ] **Step 2: Copy `units.py` in init**

Find the init file list in `commands/init.md` / `skills/specdev/SKILL.md` that
enumerates `assets/specdev/tools/*.py` and confirm `units.py` is included. If
the list is a glob, no change is needed — verify by reading it. If it is an
explicit list, add `units.py`.

- [ ] **Step 3: Document the monorepo layout in README.md**

Add a section after the existing layout description:

```markdown
### Monorepos: multiple governed units

By default a repository is one governed unit. A repo holding several projects
at different maturities can declare each as its own **governed unit** — a
directory containing a `.specdev/` — so a spike and a production service in the
same repo are classified, gated, and built independently.

Create `.specdev/units.json` (or run
`python .specdev/tools/units.py migrate --unit <path>`):

```json
{
  "schema_version": 1,
  "governance_repo": "your-org/governance",
  "ref": "main",
  "path": "governance/adr",
  "units": [
    "demos",
    "infrastructure",
    {"path": "soc-automation", "depends_on": ["infrastructure"]}
  ]
}
```

- The governance link is **repo-wide** and lives only in the registry. Each
  unit's `.specdev/org.json` carries just its `classification`.
- `depends_on` drives **effective classification**: a unit is governed at the
  level of the highest-classified unit that depends on it. Moving risky code
  into a `poc` unit that a `prod` unit imports does not escape governance.
- Feature branches carry the unit: `spec/<unit>/<name>`, `poc/<unit>/<name>`.
  Builds run in parallel across units and serialise within one.
- Validate the registry with `python .specdev/tools/units.py check`.
- Mark each gate's **`summary`** job the required status check, not the matrix
  legs — leg names are dynamic and branch protection cannot require them.

Single-root repos need none of this and are entirely unaffected.
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/units.json skills/ commands/ README.md
git commit -m "docs: seed registry, init wiring, and monorepo README section"
```

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
|---|---|
| Governed unit definition, `["."]` back-compat | 1 |
| Registry, `--check` both directions, `ignore` | 2 |
| Repo-wide vs per-unit split, link disagreement error | 2 (validation), 4 (resolution), 15 (migration) |
| Effective classification, laundering blocked, cycles, escalation printing | 3 |
| Set-valued matching, `--all-units` | 4 |
| Per-unit artifacts never merged, rolled-up index | 5 |
| `gen_traceability --out` bug | 5 |
| `deploy.py --root` | 6 |
| `org-adr-check` never filtered | 8 |
| Summary job is the required check | 8 |
| Nightly sweep | 9 |
| `adr-checker` unit root | 10 |
| Branch routing + back-compat table | 11 |
| Per-unit `run.json` | 12 |
| Scope guard, per-unit concurrency | 13 |
| Per-unit tags, push race mitigation | 14 |
| Migration tool | 15 |
| Seed asset, init, docs | 16 |

**Type consistency:** `unit_entries` returns `list[dict]` with keys `path` /
`depends_on` everywhere (Tasks 1, 3, 11). `effective`/`combine`/`declared` all
use `{axis: set[value]}` consistently (Tasks 3, 4). `parse_ref` returns
`(unit, feat)` in Tasks 11, 12, 13.

**Known overlap:** Task 12's `ci_get` loud-failure test also covers finding 4
from the separate `fix/adoption-findings` branch. Flagged inline in Task 12.

**Deferred, deliberately:** the other five findings from the adoption report
(agent tool surface, gitleaks licence, `secrets` in `steps.if`, TODO QA gates,
Python version guard) are out of scope here per the spec's "Out of scope"
section and land on `fix/adoption-findings`.
