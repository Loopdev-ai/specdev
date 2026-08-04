# Governance Profiles (poc fast path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a unit's declared `maturity` actually scale how much governance runs, so a `poc` spike skips ceremony (spec bar, ADRs, Spec PR, per-wave QA, coverage, traceability) while credential protection and containment stay full-strength.

**Architecture:** A per-maturity `profile` table is declared in `governance/classification.json`. It rides to product repos inside `governance/adr/index.json`, which `gen_adr_index.py` already emits with the classification scheme embedded verbatim — so there is **no new distribution plumbing**. A new `assets/specdev/tools/profile.py` resolves a unit's profile from its **effective** classification (reusing `units.effective()` and `check_org_adrs.fetch_index`), applies a non-overridable floor, and fails closed to full production governance. CI workflows resolve all profiles once in their existing `discover` job and gate steps with `fromJSON(...)`.

**Tech Stack:** Python 3.10+ (stdlib only — `argparse`, `json`, `urllib`, `subprocess`, `pathlib`), pytest, GitHub Actions.

## Global Constraints

- **Python 3.10+ floor.** Every new tool module starts with the same `sys.version_info < (3, 10)` guard and `sys.stdout.reconfigure(encoding="utf-8")` block used by `units.py:36-49`. Copy it verbatim.
- **Read JSON with `encoding="utf-8-sig"`.** Every existing tool does; a BOM otherwise breaks parsing on Windows-authored files.
- **POSIX-normalise paths in messages.** Use `.as_posix()` — messages appear in CI logs that must read identically on every platform.
- **Fail closed, always.** Any uncertainty in profile resolution (missing config, unreachable index, unparseable value, unknown key) resolves to the **strictest** profile. "Inert" means strict here, the opposite of the ADR gate's inert-means-skip.
- **The floor is never overridable.** `secret_scan`, `scope_check`, `sast`, `findings`, `smoke_test`, `org_adr_check` are applied *after* the table and cannot be turned off by it.
- **Single-unit repos must stay bit-identical** when governance is unconfigured. `units.py` back-compat rule: no registry → `unit_paths()` is `["."]`.
- **Tests:** `python -m pytest tests/ -q` from the repo root. New tests follow `tests/test_units.py`'s `load_mod()` + `tmp_path` fixture pattern — no network in tests, always pass `--index`/`index=`.

## Planning deviation from the design doc

The design put the no-promotion rule in `units.py check`. **It moves to `profile.py`, wired into `org-adr-check.yml`** (Tasks 5–6). Reason: `units.py` runs offline and has no access to the org index, so it would need hardcoded maturity ranks — and if an org renamed its maturity values the rule would silently go inert, which fails *open* on exactly the rule meant to prevent promotion. `org-adr-check.yml` already fetches the index, already runs on every PR, and is deliberately never path-filtered. Same "no new workflow" property, correct ranks. Task 12 updates the design doc to match.

---

### Task 1: Declare the profile table and prove it rides the index

**Files:**
- Modify: `governance/classification.json`
- Test: `tests/test_profiles.py` (create)

**Interfaces:**
- Produces: each `maturity` value in `governance/classification.json` gains a `profile` object with keys `spec_bar` (`"charter"|"full"`), `spec_pr`, `adrs`, `per_wave_qa`, `coverage_gate`, `traceability`, `compliance`, `prod_promotion` (all bool). Consumed by every later task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_profiles.py`:

```python
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "assets" / "specdev" / "tools" / "profile.py"
SCHEME_PATH = ROOT / "governance" / "classification.json"
GEN_INDEX = ROOT / "governance" / "tools" / "gen_adr_index.py"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


PROFILE_KEYS = {"spec_bar", "spec_pr", "adrs", "per_wave_qa",
                "coverage_gate", "traceability", "compliance",
                "prod_promotion"}


def test_every_maturity_value_declares_a_full_profile():
    scheme = json.loads(SCHEME_PATH.read_text(encoding="utf-8-sig"))
    values = scheme["axes"]["maturity"]["values"]
    for name, vdef in values.items():
        assert "profile" in vdef, f"maturity '{name}' declares no profile"
        assert set(vdef["profile"]) == PROFILE_KEYS, (
            f"maturity '{name}' profile keys {sorted(vdef['profile'])} "
            f"!= {sorted(PROFILE_KEYS)}")


def test_poc_is_the_loosest_and_prod_the_strictest():
    scheme = json.loads(SCHEME_PATH.read_text(encoding="utf-8-sig"))
    values = scheme["axes"]["maturity"]["values"]
    poc, prod = values["poc"]["profile"], values["prod"]["profile"]
    assert poc["spec_bar"] == "charter" and prod["spec_bar"] == "full"
    for k in PROFILE_KEYS - {"spec_bar"}:
        assert poc[k] is False, f"poc.{k} should be False"
        assert prod[k] is True, f"prod.{k} should be True"


def test_profile_rides_the_generated_index(tmp_path):
    """gen_adr_index.py embeds the scheme verbatim, so the profile reaches
    product repos with no new distribution plumbing. This is the load-bearing
    assumption of the whole design."""
    out = tmp_path / "index.json"
    subprocess.run(
        [sys.executable, str(GEN_INDEX), "--root", str(ROOT), "--out", str(out)],
        check=True, capture_output=True, text=True)
    index = json.loads(out.read_text(encoding="utf-8-sig"))
    assert index["axes"]["maturity"]["values"]["poc"]["profile"]["adrs"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: FAIL — `KeyError: 'profile'` / `AssertionError: maturity 'poc' declares no profile`.

If `test_profile_rides_the_generated_index` fails on the `--root`/`--out` flags rather than on the assertion, run `python governance/tools/gen_adr_index.py --help` and adjust the invocation to the real flags before continuing — the assertion is what matters.

- [ ] **Step 3: Add the profile table**

In `governance/classification.json`, replace the `maturity` axis with:

```json
    "maturity": {
      "ordered": true,
      "$comment": "'profile' scales how much of the SpecDev pipeline runs at this maturity. Resolved by .specdev/tools/profile.py from a unit's EFFECTIVE classification. It can only remove ceremony: the floor (secret scan, scope check, SAST, findings, smoke test, poc-scoped org ADRs) is applied after this table and cannot be disabled here.",
      "values": {
        "poc": {
          "rank": 0,
          "description": "Proof of concept / spike. Disposable code; minimal governance.",
          "profile": {
            "spec_bar": "charter",
            "spec_pr": false,
            "adrs": false,
            "per_wave_qa": false,
            "coverage_gate": false,
            "traceability": false,
            "compliance": false,
            "prod_promotion": false
          }
        },
        "dev": {
          "rank": 1,
          "description": "Active development, not yet serving production traffic or real data.",
          "profile": {
            "spec_bar": "full",
            "spec_pr": true,
            "adrs": true,
            "per_wave_qa": true,
            "coverage_gate": true,
            "traceability": true,
            "compliance": false,
            "prod_promotion": true
          }
        },
        "prod": {
          "rank": 2,
          "description": "Production: serves real users or handles real data. Full governance applies.",
          "profile": {
            "spec_bar": "full",
            "spec_pr": true,
            "adrs": true,
            "per_wave_qa": true,
            "coverage_gate": true,
            "traceability": true,
            "compliance": true,
            "prod_promotion": true
          }
        }
      }
    },
```

Note `dev.compliance` is `false` while every other `dev` key is `true`; `test_poc_is_the_loosest_and_prod_the_strictest` only constrains `poc` and `prod`, so this passes.

- [ ] **Step 4: Regenerate the index and run the tests**

Run:
```bash
python governance/tools/gen_adr_index.py
python -m pytest tests/test_profiles.py -q
```
Expected: index regenerated, 3 passed.

- [ ] **Step 5: Commit**

```bash
git add governance/classification.json governance/adr/index.json governance/adr/INDEX.md tests/test_profiles.py
git commit -m "feat(governance): per-maturity profile table in the classification scheme"
```

---

### Task 2: `profile.py` — pure composition (floor, strictest-wins, sanitising)

**Files:**
- Create: `assets/specdev/tools/profile.py`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Produces: `STRICTEST: dict`, `FLOOR: dict`, `_strictest(key, a, b)`, `_sanitize(tbl) -> dict`, `compose(values: set[str], axes: dict) -> dict`. `compose` returns a dict holding **both** the 8 profile keys and the 6 floor keys.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
pf = None  # bound after the module exists


def _pf():
    global pf
    if pf is None:
        pf = load_mod(PROFILE_PATH, "specdev_profile")
    return pf


AXES = {
    "maturity": {
        "ordered": True,
        "values": {
            "poc":  {"rank": 0, "profile": {"spec_bar": "charter", "spec_pr": False,
                                            "adrs": False, "per_wave_qa": False,
                                            "coverage_gate": False, "traceability": False,
                                            "compliance": False, "prod_promotion": False}},
            "prod": {"rank": 2, "profile": {"spec_bar": "full", "spec_pr": True,
                                            "adrs": True, "per_wave_qa": True,
                                            "coverage_gate": True, "traceability": True,
                                            "compliance": True, "prod_promotion": True}},
        },
    },
    "audience": {"ordered": False, "values": {"internal": {}, "customer": {}}},
}


def test_compose_single_value_returns_that_profile():
    p = _pf().compose({"poc"}, AXES)
    assert p["spec_bar"] == "charter"
    assert p["per_wave_qa"] is False


def test_compose_strictest_wins_across_a_set():
    p = _pf().compose({"poc", "prod"}, AXES)
    assert p["spec_bar"] == "full"
    assert p["per_wave_qa"] is True
    assert p["prod_promotion"] is True


def test_compose_includes_the_floor():
    p = _pf().compose({"poc"}, AXES)
    for k in ("secret_scan", "scope_check", "sast", "findings",
              "smoke_test", "org_adr_check"):
        assert p[k] is True, f"floor key {k} must be True even for poc"


def test_floor_keys_cannot_be_disabled_by_the_table(capsys):
    axes = json.loads(json.dumps(AXES))
    axes["maturity"]["values"]["poc"]["profile"]["secret_scan"] = False
    p = _pf().compose({"poc"}, axes)
    assert p["secret_scan"] is True
    assert "secret_scan" in capsys.readouterr().err


def test_unknown_profile_key_is_ignored_with_a_warning(capsys):
    axes = json.loads(json.dumps(AXES))
    axes["maturity"]["values"]["poc"]["profile"]["nonsense"] = True
    p = _pf().compose({"poc"}, axes)
    assert "nonsense" not in p
    assert "nonsense" in capsys.readouterr().err


def test_missing_profile_falls_back_to_strictest():
    axes = json.loads(json.dumps(AXES))
    del axes["maturity"]["values"]["poc"]["profile"]
    p = _pf().compose({"poc"}, axes)
    assert p == _pf().compose(set(), axes) == {**_pf().STRICTEST, **_pf().FLOOR}


def test_invalid_spec_bar_value_falls_back_to_strictest():
    axes = json.loads(json.dumps(AXES))
    axes["maturity"]["values"]["poc"]["profile"]["spec_bar"] = "sloppy"
    assert _pf().compose({"poc"}, axes)["spec_bar"] == "full"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: FAIL — `FileNotFoundError` / `ModuleNotFoundError` for `profile.py`.

- [ ] **Step 3: Create `assets/specdev/tools/profile.py`**

```python
#!/usr/bin/env python3
"""Governance profile resolution for SpecDev product repos.

A *profile* scales how much of the pipeline runs at a unit's maturity, so a
disposable spike skips ceremony a production service must clear. The table is
declared per maturity value in the org's governance/classification.json and
travels to product repos inside governance/adr/index.json, which already
embeds the classification scheme verbatim.

Three rules, in order, and all three are load-bearing:

  1. Resolve from EFFECTIVE classification, never declared. units.effective()
     pulls a unit up when a higher-classified unit depends on it. Keying off
     the DECLARED value would let anyone park risky code in a poc unit, import
     it from prod, and skip QA, coverage and traceability along with the org
     ADRs -- a wider hole than the one multi-unit support exists to close.
  2. Strictest wins across a set. effective() returns a SET per axis;
     resolution is monotone, so a higher rank can never yield a looser profile.
  3. Fail closed. Unconfigured governance, an unreachable index, a missing or
     unparseable profile -> full production governance. "Inert" means STRICT
     here, the opposite of the org-ADR gate's inert-means-skip: undeclared
     governance is never a discount.

The FLOOR is applied after the table and cannot be disabled by it.

Usage:
    profile.py show   [--root .] [--unit .] [--index FILE] [--key KEY]
    profile.py matrix [--root .] [--index FILE]
"""
import argparse
import json
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

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402  (vendored sibling module)
import check_org_adrs as cch  # noqa: E402

MATURITY_AXIS = "maturity"

# The strictest value of every profile key. These ARE the defaults: any key the
# table omits, mistypes, or that we cannot resolve lands here.
STRICTEST = {
    "spec_bar": "full",
    "spec_pr": True,
    "adrs": True,
    "per_wave_qa": True,
    "coverage_gate": True,
    "traceability": True,
    "compliance": True,
    "prod_promotion": True,
}

# Non-overridable. Credential protection and containment are not ceremony, so
# the table may not switch them off. Applied AFTER the table.
FLOOR = {
    "secret_scan": True,
    "scope_check": True,
    "sast": True,
    "findings": True,
    "smoke_test": True,
    "org_adr_check": True,
}

_SPEC_BAR_RANK = {"charter": 0, "full": 1}


def _strictest(key: str, a, b):
    """Fold two values for one key, keeping the more governed one."""
    if key == "spec_bar":
        return max((a, b), key=lambda v: _SPEC_BAR_RANK.get(v, 1))
    return bool(a) or bool(b)


def _sanitize(tbl) -> dict:
    """One declared profile -> a complete, type-checked profile.

    Anything unrecognised is dropped with a warning and the strictest default
    kept, so a typo in the org's table can never quietly widen the fast path."""
    out = dict(STRICTEST)
    if not isinstance(tbl, dict):
        return out
    for k, v in tbl.items():
        if k in FLOOR:
            print(f"WARNING: profile key '{k}' is part of the non-overridable "
                  f"floor — ignoring the table's value", file=sys.stderr)
            continue
        if k not in STRICTEST:
            print(f"WARNING: unknown profile key '{k}' — ignoring", file=sys.stderr)
            continue
        if k == "spec_bar":
            if v not in _SPEC_BAR_RANK:
                print(f"WARNING: spec_bar '{v}' is not one of "
                      f"{sorted(_SPEC_BAR_RANK)} — using '{STRICTEST[k]}'",
                      file=sys.stderr)
                continue
            out[k] = v
        else:
            if not isinstance(v, bool):
                print(f"WARNING: profile key '{k}' must be a boolean, got "
                      f"{v!r} — using {STRICTEST[k]}", file=sys.stderr)
                continue
            out[k] = v
    return out


def compose(values, axes: dict) -> dict:
    """Fold the profiles of every maturity value in `values`, strictest wins.

    An empty set means we could not determine a maturity, which is a
    fail-closed case, not an empty fold."""
    vdefs = axes.get(MATURITY_AXIS, {}).get("values", {})
    out = None
    for v in sorted(values):
        p = _sanitize(vdefs.get(v, {}).get("profile"))
        out = p if out is None else {k: _strictest(k, out[k], p[k]) for k in STRICTEST}
    if out is None:
        out = dict(STRICTEST)
    out.update(FLOOR)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/profile.py tests/test_profiles.py
git commit -m "feat(profile): profile composition — strictest wins, floor non-overridable"
```

---

### Task 3: `profile.py` — resolve from effective classification, fail closed

**Files:**
- Modify: `assets/specdev/tools/profile.py`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `compose()`, `STRICTEST`, `FLOOR` from Task 2.
- Produces: `resolve_all(root=".", index=None) -> dict[str, dict]` mapping unit path → profile; `resolve(root=".", unit=".", index=None) -> dict`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
def write_index(tmp_path, axes=None):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"axes": axes or AXES, "adrs": []}), encoding="utf-8")
    return idx


def make_unit(root, name, classification=None, depends_on=None):
    d = root / name / ".specdev"
    d.mkdir(parents=True, exist_ok=True)
    if classification is not None:
        (d / "org.json").write_text(
            json.dumps({"classification": classification}), encoding="utf-8")
    entry = {"path": name}
    if depends_on:
        entry["depends_on"] = depends_on
    return entry


def write_registry(root, units_list):
    (root / ".specdev").mkdir(parents=True, exist_ok=True)
    (root / ".specdev" / "units.json").write_text(json.dumps({
        "schema_version": 1,
        "governance_repo": "org/governance",
        "ref": "main",
        "path": "governance/adr",
        "units": units_list,
    }), encoding="utf-8")


def test_poc_unit_resolves_to_the_poc_profile(tmp_path):
    e = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    write_registry(tmp_path, [e])
    p = _pf().resolve(tmp_path, "spike", index=json.loads(
        write_index(tmp_path).read_text(encoding="utf-8")))
    assert p["per_wave_qa"] is False
    assert p["spec_bar"] == "charter"


def test_poc_unit_a_prod_unit_depends_on_resolves_to_prod(tmp_path):
    """THE escape-hatch regression. Moving risky code into a poc unit that a
    prod unit imports must NOT buy a lighter pipeline."""
    a = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    b = make_unit(tmp_path, "api", {"maturity": "prod", "audience": "customer"},
                  depends_on=["spike"])
    write_registry(tmp_path, [a, b])
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    p = _pf().resolve(tmp_path, "spike", index=idx)
    assert p["per_wave_qa"] is True
    assert p["coverage_gate"] is True
    assert p["traceability"] is True
    assert p["spec_bar"] == "full"


def test_prod_unit_depending_on_poc_is_not_lowered(tmp_path):
    a = make_unit(tmp_path, "lib", {"maturity": "prod", "audience": "internal"})
    b = make_unit(tmp_path, "demo", {"maturity": "poc", "audience": "internal"},
                  depends_on=["lib"])
    write_registry(tmp_path, [a, b])
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert _pf().resolve(tmp_path, "demo", index=idx)["per_wave_qa"] is False
    assert _pf().resolve(tmp_path, "lib", index=idx)["per_wave_qa"] is True


def test_unconfigured_repo_fails_closed(tmp_path):
    p = _pf().resolve(tmp_path, ".")
    assert p == {**_pf().STRICTEST, **_pf().FLOOR}


def test_replace_me_classification_fails_closed(tmp_path):
    e = make_unit(tmp_path, "u", {"maturity": "REPLACE_ME (poc | dev | prod)",
                                  "audience": "internal"})
    write_registry(tmp_path, [e])
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert _pf().resolve(tmp_path, "u", index=idx)["per_wave_qa"] is True


def test_unparseable_classification_fails_closed(tmp_path):
    e = make_unit(tmp_path, "u", {"maturity": "nonsense", "audience": "internal"})
    write_registry(tmp_path, [e])
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert _pf().resolve(tmp_path, "u", index=idx)["per_wave_qa"] is True


def test_resolve_all_covers_every_registered_unit(tmp_path):
    a = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    b = make_unit(tmp_path, "api", {"maturity": "prod", "audience": "customer"})
    write_registry(tmp_path, [a, b])
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    allp = _pf().resolve_all(tmp_path, index=idx)
    assert set(allp) == {"spike", "api"}
    assert allp["spike"]["coverage_gate"] is False
    assert allp["api"]["coverage_gate"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: FAIL — `AttributeError: module 'specdev_profile' has no attribute 'resolve'`.

- [ ] **Step 3: Append the resolver to `assets/specdev/tools/profile.py`**

```python
def _fallback(reason: str) -> dict:
    """Fail closed, and say why. A silent strict fallback is indistinguishable
    from a correctly-resolved prod profile at the point of failure."""
    print(f"NOTE: falling back to full production governance — {reason}",
          file=sys.stderr)
    return {**STRICTEST, **FLOOR}


def _load_index(root: Path):
    """Reuse check_org_adrs' single fetcher rather than adding a second one, so
    exactly one code path decides which ref the org's policy is read at.
    fetch_index() exits the process on failure; for profiles that must be a
    fail-closed fallback instead, so SystemExit is caught."""
    link = cch.resolve_link(root)
    if link is None:
        return None, "governance not adopted (no org.json / registry link)"
    if "REPLACE_ME" in json.dumps(link.get("governance_repo", "")):
        return None, "governance link still holds REPLACE_ME"
    try:
        return cch.fetch_index(link), None
    except SystemExit:
        return None, "org ADR index could not be fetched"


def _declared(root: Path, entries, axes) -> dict:
    """{unit: {axis: set(values)}} for every unit with a usable classification.
    A unit whose classification is absent, REPLACE_ME, or invalid is simply
    omitted, which lands it in the fail-closed path."""
    out = {}
    for e in entries:
        p = root / e["path"] / ".specdev" / "org.json"
        if not p.exists():
            continue
        raw = cch.load_json(p).get("classification")
        if raw is None or "REPLACE_ME" in json.dumps(raw):
            continue
        norm, errors = cch.normalize_classification(raw, axes)
        if errors:
            for err in errors:
                print(f"WARNING: {e['path']}: {err}", file=sys.stderr)
            continue
        out[e["path"]] = norm
    return out


def resolve_all(root=".", index=None) -> dict:
    """{unit path: profile} for every registered unit."""
    root = Path(root)
    entries = units.unit_entries(root)
    if index is None:
        index, why = _load_index(root)
        if index is None:
            return {e["path"]: _fallback(why) for e in entries}
    axes = index.get("axes", {})
    declared = _declared(root, entries, axes)
    if not declared:
        return {e["path"]: _fallback("no unit declares a classification")
                for e in entries}
    eff = units.effective(entries, axes, declared)
    out = {}
    for e in entries:
        u = e["path"]
        if u not in declared:
            out[u] = _fallback(f"unit '{u}' declares no usable classification")
            continue
        values = eff.get(u, {}).get(MATURITY_AXIS) or set()
        out[u] = compose(values, axes) if values else _fallback(
            f"unit '{u}' resolves to no '{MATURITY_AXIS}' value")
    return out


def resolve(root=".", unit=".", index=None) -> dict:
    """One unit's profile. Unknown units fail closed rather than KeyError."""
    allp = resolve_all(root, index)
    if unit not in allp:
        return _fallback(f"unit '{unit}' is not registered")
    return allp[unit]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: 17 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/profile.py tests/test_profiles.py
git commit -m "feat(profile): resolve from effective classification, fail closed"
```

---

### Task 4: `profile.py` — CLI (`show`, `matrix`)

**Files:**
- Modify: `assets/specdev/tools/profile.py`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `resolve()`, `resolve_all()` from Task 3.
- Produces: `profile.py show --unit U [--key K]` prints the whole profile as JSON, or one key as lowercase `true`/`false`/string. `profile.py matrix` prints `{"unit": {...}}` on one line for a GitHub Actions job output.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
def run_cli(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(PROFILE_PATH), "--root", str(tmp_path), *args],
        capture_output=True, text=True)


def test_cli_show_key_prints_a_yaml_truthy_literal(tmp_path):
    e = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    write_registry(tmp_path, [e])
    idx = write_index(tmp_path)
    r = run_cli(tmp_path, "show", "--unit", "spike",
                "--index", str(idx), "--key", "coverage_gate")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "false"


def test_cli_show_key_prints_string_values_bare(tmp_path):
    e = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    write_registry(tmp_path, [e])
    idx = write_index(tmp_path)
    r = run_cli(tmp_path, "show", "--unit", "spike",
                "--index", str(idx), "--key", "spec_bar")
    assert r.stdout.strip() == "charter"


def test_cli_matrix_emits_one_json_line_for_every_unit(tmp_path):
    a = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    b = make_unit(tmp_path, "api", {"maturity": "prod", "audience": "customer"})
    write_registry(tmp_path, [a, b])
    idx = write_index(tmp_path)
    r = run_cli(tmp_path, "matrix", "--index", str(idx))
    assert r.returncode == 0, r.stderr
    assert len(r.stdout.strip().splitlines()) == 1, "must be one line for $GITHUB_OUTPUT"
    doc = json.loads(r.stdout)
    assert doc["spike"]["traceability"] is False
    assert doc["api"]["traceability"] is True


def test_cli_unknown_key_exits_nonzero(tmp_path):
    e = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    write_registry(tmp_path, [e])
    r = run_cli(tmp_path, "show", "--unit", "spike",
                "--index", str(write_index(tmp_path)), "--key", "bogus")
    assert r.returncode != 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k cli`
Expected: FAIL — argparse errors on the unknown `show` subcommand (non-zero exit, empty stdout).

- [ ] **Step 3: Append the CLI to `assets/specdev/tools/profile.py`**

```python
def _emit(value) -> str:
    """Render one key for shell/YAML consumption: JSON booleans lowercase,
    strings bare (no quotes), so `if:` expressions read naturally."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--index", help="local index.json (skip fetching; offline/tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("show", help="one unit's resolved profile")
    ps.add_argument("--unit", default=".")
    ps.add_argument("--key", help="print just this key")
    sub.add_parser("matrix", help="{unit: profile} for every unit, one JSON line")
    args = ap.parse_args()

    index = cch.load_json(Path(args.index)) if args.index else None

    if args.cmd == "matrix":
        print(json.dumps(resolve_all(args.root, index), sort_keys=True))
        return 0

    prof = resolve(args.root, args.unit, index)
    if args.key:
        if args.key not in prof:
            print(f"ERROR: unknown profile key '{args.key}' — one of "
                  f"{sorted(prof)}", file=sys.stderr)
            return 1
        print(_emit(prof[args.key]))
        return 0
    print(json.dumps(prof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/profile.py tests/test_profiles.py
git commit -m "feat(profile): show/matrix CLI for skill and CI consumption"
```

---

### Task 5: No promotion in place

**Files:**
- Modify: `assets/specdev/tools/profile.py`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `units.unit_paths()`, `cch.load_json()`.
- Produces: `maturity_at(root, unit, ref) -> str | None`, `has_poc_history(root, unit) -> bool`, `promotion_errors(root=".", base=None, index=None) -> list[str]`, and a `promotion-check --changed-from REF` subcommand exiting 1 on any error.

Ranks come from the org index, not a hardcoded map — see the planning deviation note at the top.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
def git(tmp_path, *args):
    return subprocess.run(["git", *args], cwd=str(tmp_path),
                          capture_output=True, text=True, check=True)


def init_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")


def set_maturity(tmp_path, unit, maturity):
    p = tmp_path / unit / ".specdev" / "org.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"classification": {"maturity": maturity, "audience": "internal"}}),
        encoding="utf-8")


def test_promoting_a_unit_with_poc_history_is_rejected(tmp_path):
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "spike"}])
    set_maturity(tmp_path, "spike", "poc")
    (tmp_path / "spike" / ".specdev" / "run.json").write_text(
        json.dumps({"mode": "poc", "feat": "FEAT-001"}), encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "poc")
    base = git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    set_maturity(tmp_path, "spike", "prod")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "promote")

    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    errs = _pf().promotion_errors(tmp_path, base=base, index=idx)
    assert len(errs) == 1
    assert "spike" in errs[0]
    assert "spec-explorer" in errs[0]


def test_promoting_a_unit_without_poc_history_is_allowed(tmp_path):
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "svc"}])
    set_maturity(tmp_path, "svc", "poc")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    base = git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    set_maturity(tmp_path, "svc", "prod")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "promote")

    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert _pf().promotion_errors(tmp_path, base=base, index=idx) == []


def test_demotion_is_allowed(tmp_path):
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "spike"}])
    set_maturity(tmp_path, "spike", "prod")
    (tmp_path / "spike" / ".specdev" / "run.json").write_text(
        json.dumps({"mode": "poc"}), encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    base = git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    set_maturity(tmp_path, "spike", "poc")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "demote")

    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert _pf().promotion_errors(tmp_path, base=base, index=idx) == []


def test_poc_release_tag_counts_as_poc_history(tmp_path):
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "spike"}])
    set_maturity(tmp_path, "spike", "poc")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    git(tmp_path, "tag", "poc-spike-20260804-abc1234")
    base = git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    set_maturity(tmp_path, "spike", "dev")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "promote")

    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert len(_pf().promotion_errors(tmp_path, base=base, index=idx)) == 1


def test_promotion_check_with_no_base_is_inert(tmp_path):
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "spike"}])
    set_maturity(tmp_path, "spike", "poc")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert _pf().promotion_errors(tmp_path, base=None, index=idx) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k promot`
Expected: FAIL — `AttributeError: ... has no attribute 'promotion_errors'`.

- [ ] **Step 3: Append to `assets/specdev/tools/profile.py`**

Add `import subprocess` to the imports at the top of the file, then append:

```python
def maturity_at(root, unit: str, ref: str) -> str | None:
    """This unit's declared maturity at a git ref, or None if unreadable.

    Reads via `git show` rather than the worktree so the BASE side of the
    comparison is the committed state, not whatever is checked out."""
    rel = ".specdev/org.json" if unit == "." else f"{unit}/.specdev/org.json"
    r = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=str(root),
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        cls = json.loads(r.stdout).get("classification")
    except json.JSONDecodeError:
        return None
    if isinstance(cls, str):
        return cls
    if isinstance(cls, dict):
        return cls.get(MATURITY_AXIS)
    return None


def has_poc_history(root, unit: str) -> bool:
    """True when this unit has actually been BUILT under the poc lane.

    Two independent signals, either sufficient: run.json recording mode 'poc'
    (written by run_manifest.py), and a poc release tag (written by
    deploy-poc.yml, namespaced by unit slug the same way)."""
    rm = Path(root) / unit / ".specdev" / "run.json"
    if rm.exists():
        try:
            if cch.load_json(rm).get("mode") == "poc":
                return True
        except json.JSONDecodeError:
            pass
    slug = unit.replace("/", "-").replace(".", "-").strip("-")
    pattern = f"poc-{slug}-*" if slug else "poc-*"
    r = subprocess.run(["git", "tag", "--list", pattern], cwd=str(root),
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def promotion_errors(root=".", base=None, index=None) -> list[str]:
    """poc units are never promoted in place; they are reverse-mapped and
    rebuilt. Ranks come from the org index, so an org that renames its maturity
    values keeps a working rule instead of a silently inert one.

    Inert with no base: this is a diff-driven rule, and there is nothing to
    compare against. The staleness half of org-adr-check is what stays
    unfiltered, not this."""
    if not base:
        return []
    root = Path(root)
    if index is None:
        index, why = _load_index(root)
        if index is None:
            print(f"NOTE: promotion check skipped — {why}", file=sys.stderr)
            return []
    ranks = {v: d.get("rank", -1) for v, d in
             index.get("axes", {}).get(MATURITY_AXIS, {}).get("values", {}).items()}
    errors = []
    for unit in units.unit_paths(root):
        was = maturity_at(root, unit, base)
        now = maturity_at(root, unit, "HEAD")
        if not was or not now or was == now:
            continue
        if ranks.get(now, -1) <= ranks.get(was, -1):
            continue  # demotion or lateral move
        if not has_poc_history(root, unit):
            continue
        errors.append(
            f"unit '{unit}' was built under the poc lane and this change "
            f"promotes it {was} -> {now}. poc units are not promoted in "
            f"place: reverse-map it with the spec-explorer agent and rebuild "
            f"it as a new unit through the full pipeline.")
    return errors
```

Then add the subcommand to `main()`. Insert after the `matrix` parser is declared:

```python
    pp = sub.add_parser("promotion-check",
                        help="reject in-place promotion of a poc-built unit")
    pp.add_argument("--changed-from", required=True)
```

and insert this branch immediately before `prof = resolve(...)`:

```python
    if args.cmd == "promotion-check":
        errs = promotion_errors(args.root, args.changed_from, index)
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        if errs:
            return 1
        print("promotion check ok — no poc-built unit is being promoted in place.")
        return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/profile.py tests/test_profiles.py
git commit -m "feat(profile): reject in-place promotion of a poc-built unit"
```

---

### Task 6: Wire the promotion check into `org-adr-check.yml`

**Files:**
- Modify: `assets/workflows/org-adr-check.yml`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `profile.py promotion-check --changed-from REF` from Task 5.

`org-adr-check.yml` is chosen because it already fetches the index, already runs on every PR, and is deliberately never path-filtered. The `discover` job's `actions/checkout@v4` currently has **no** `fetch-depth: 0`; the promotion check diffs against the PR base and needs it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
WF = ROOT / "assets" / "workflows" / "org-adr-check.yml"


def test_org_adr_check_runs_the_promotion_check():
    text = WF.read_text(encoding="utf-8")
    assert "profile.py promotion-check" in text, (
        "org-adr-check must enforce the no-in-place-promotion rule")


def test_org_adr_check_discover_has_full_history():
    """git show <base>:... cannot resolve in a shallow clone, so the promotion
    check would pass vacuously without fetch-depth: 0."""
    import yaml
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = doc["jobs"]["discover"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k org_adr_check`
Expected: FAIL on both — the string is absent and `fetch-depth` is unset.

- [ ] **Step 3: Edit `assets/workflows/org-adr-check.yml`**

In the `discover` job, replace:

```yaml
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Validate the unit registry
        run: python .specdev/tools/units.py check
```

with:

```yaml
      - uses: actions/checkout@v4
        with:
          # The promotion check diffs org.json against the PR base via
          # `git show <base>:...`, which cannot resolve in a shallow clone.
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Validate the unit registry
        run: python .specdev/tools/units.py check
      # A poc unit is never promoted in place — it is reverse-mapped and
      # rebuilt. This lives here, not in units.py, because the maturity RANKS
      # come from the org index: an org that renames its maturity values keeps
      # a working rule instead of a silently inert one.
      - name: Reject in-place promotion of a poc-built unit
        env:
          BASE: ${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}
        run: python .specdev/tools/profile.py promotion-check --changed-from "$BASE"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -q && python -m pytest tests/ -q`
Expected: all green. If `test_specdev_ci.py` or `test_pipeline_hardening.py` assert on this workflow's step count or shape, update those assertions to include the new step.

- [ ] **Step 5: Commit**

```bash
git add assets/workflows/org-adr-check.yml tests/test_profiles.py
git commit -m "feat(ci): enforce no-in-place-promotion in org-adr-check"
```

---

### Task 7: Charter spec bar — `validate_spec.py --profile charter` + template

**Files:**
- Create: `assets/specdev/CHARTER.md`
- Modify: `assets/specdev/tools/validate_spec.py`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Produces: `validate_spec.py --profile charter|full` (default `full`). Under `charter` it validates `<root>/.specdev/CHARTER.md` for four required sections and no placeholders, and does not look at `spec.md`, REQ IDs, or ADRs.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
VALIDATE = ROOT / "assets" / "specdev" / "tools" / "validate_spec.py"
CHARTER_TEMPLATE = ROOT / "assets" / "specdev" / "CHARTER.md"


def run_validate(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(VALIDATE), "--root", str(tmp_path), *args],
        capture_output=True, text=True)


GOOD_CHARTER = """# Spike Charter — streaming ingest

## Goal

Find out whether a single Kafka consumer can sustain 50k events/sec on the
current instance size.

## Questions this spike must answer

- Does one consumer saturate before 50k/sec?
- Where does back-pressure first appear?

## Timebox

Three working days, ending 2026-08-07.

## Abandon criteria

- Throughput below 20k/sec after two days of tuning.
"""


def test_charter_mode_accepts_a_complete_charter(tmp_path):
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "CHARTER.md").write_text(GOOD_CHARTER, encoding="utf-8")
    r = run_validate(tmp_path, "--strict", "--profile", "charter")
    assert r.returncode == 0, r.stdout + r.stderr


def test_charter_mode_rejects_a_missing_section(tmp_path):
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "CHARTER.md").write_text(
        GOOD_CHARTER.replace("## Timebox", "## Notes"), encoding="utf-8")
    r = run_validate(tmp_path, "--strict", "--profile", "charter")
    assert r.returncode == 1
    assert "Timebox" in r.stdout


def test_charter_mode_rejects_placeholders(tmp_path):
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "CHARTER.md").write_text(
        GOOD_CHARTER.replace("Three working days, ending 2026-08-07.", "TBD"),
        encoding="utf-8")
    r = run_validate(tmp_path, "--strict", "--profile", "charter")
    assert r.returncode == 1


def test_charter_mode_does_not_require_a_spec(tmp_path):
    """The whole point: no spec.md, no REQ IDs, no ADRs."""
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "CHARTER.md").write_text(GOOD_CHARTER, encoding="utf-8")
    assert not (d / "spec.md").exists()
    assert run_validate(tmp_path, "--profile", "charter").returncode == 0


def test_shipped_charter_template_is_a_template_not_a_pass(tmp_path):
    """The template ships with placeholders, so validating it must FAIL —
    the same discipline the four post-dev-qa stubs ship with."""
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "CHARTER.md").write_text(
        CHARTER_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    assert run_validate(tmp_path, "--strict", "--profile", "charter").returncode == 1


def test_full_profile_is_the_default_and_still_requires_a_spec(tmp_path):
    (tmp_path / ".specdev").mkdir(parents=True)
    assert run_validate(tmp_path).returncode == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k charter`
Expected: FAIL — argparse rejects `--profile`.

- [ ] **Step 3: Create `assets/specdev/CHARTER.md`**

```markdown
# Spike Charter — <name>

> Replace every `<...>` placeholder. This file is the `poc` profile's Gate 1
> artifact, standing in for `spec.md`: a spike answers questions, it does not
> deliver requirements. Validate with
> `python .specdev/tools/validate_spec.py --strict --profile charter`.

## Goal

<One paragraph: what we want to find out, and why it matters now.>

## Questions this spike must answer

- <A question with a knowable answer.>
- <Another.>

## Timebox

<Concrete duration and an end date. A spike without one is just untracked work.>

## Abandon criteria

- <What result would make us stop and not pursue this.>
```

- [ ] **Step 4: Modify `assets/specdev/tools/validate_spec.py`**

Add the argument, immediately after the `--strict` line:

```python
    ap.add_argument("--profile", choices=("full", "charter"), default="full",
                    help="'charter' validates .specdev/CHARTER.md for a poc "
                         "unit instead of the full spec.md bar (see "
                         "profile.py). Default 'full'.")
```

Then insert this block immediately after `root = Path(args.root)` and before `spec = root / ".specdev" / "spec.md"`:

```python
    if args.profile == "charter":
        return _validate_charter(root, args.strict)
```

Add this function above `main()`:

```python
CHARTER_SECTIONS = ("Goal", "Questions this spike must answer", "Timebox",
                    "Abandon criteria")


def _validate_charter(root: Path, strict: bool) -> int:
    """Gate 1 for a `poc` unit. A spike answers questions; it does not deliver
    requirements, so there are no REQ IDs, acceptance lines or ADRs to check.
    What must NOT be skipped is that the charter is actually filled in — an
    unedited template is how a spike becomes untracked work."""
    charter = root / ".specdev" / "CHARTER.md"
    if not charter.exists():
        print(f"ERROR: {charter.as_posix()} not found "
              "(the poc profile's Gate 1 artifact)")
        return 1
    text = charter.read_text(encoding="utf-8")
    errors: list[str] = []
    warnings: list[str] = []

    for name in CHARTER_SECTIONS:
        m = re.search(rf"^##\s+{re.escape(name)}\s*$(.*?)(^##\s|\Z)",
                      text, re.S | re.M)
        if not m:
            errors.append(f"missing '## {name}' section")
            continue
        body = m.group(1).strip()
        if not body:
            errors.append(f"'## {name}' is empty")
        elif PLACEHOLDER.search(body):
            errors.append(f"'## {name}' still holds a placeholder")

    for w in warnings:
        print(f"WARN:  {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors or (strict and warnings):
        print(f"\nGate 1 (charter) FAILED: {len(errors)} error(s), "
              f"{len(warnings)} warning(s)")
        return 1
    print(f"\nGate 1 (charter) passed ({len(CHARTER_SECTIONS)} sections).")
    return 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: 32 passed.

- [ ] **Step 6: Commit**

```bash
git add assets/specdev/CHARTER.md assets/specdev/tools/validate_spec.py tests/test_profiles.py
git commit -m "feat(spec): charter spec bar for poc units"
```

---

### Task 8: Profile-conditional gate workflows

**Files:**
- Modify: `assets/workflows/post-dev-qa.yml`
- Modify: `assets/workflows/spec-validate.yml`
- Modify: `assets/workflows/traceability.yml`
- Modify: `assets/workflows/compliance.yml`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `profile.py matrix` from Task 4.
- Produces: each workflow's `discover` job gains a `profiles` output; legs gate on `fromJSON(needs.discover.outputs.profiles)[matrix.unit].<key>`.

One fetch per workflow run, in the job that already exists. No per-leg network, no cached file on disk to tamper with.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
import yaml

WORKFLOWS = ROOT / "assets" / "workflows"

PROFILE_GATED = {
    "post-dev-qa.yml": ["coverage_gate", "traceability"],
    "spec-validate.yml": ["spec_bar"],
    "traceability.yml": ["traceability"],
    "compliance.yml": ["compliance"],
}


@pytest.mark.parametrize("wf", sorted(PROFILE_GATED))
def test_discover_emits_the_profiles_output(wf):
    doc = yaml.safe_load((WORKFLOWS / wf).read_text(encoding="utf-8"))
    outputs = doc["jobs"]["discover"].get("outputs", {})
    assert "profiles" in outputs, f"{wf}: discover must emit 'profiles'"
    steps = doc["jobs"]["discover"]["steps"]
    assert any("profile.py matrix" in str(s.get("run", "")) for s in steps), \
        f"{wf}: discover must run 'profile.py matrix'"


@pytest.mark.parametrize("wf,keys", sorted(PROFILE_GATED.items()))
def test_workflow_gates_on_its_profile_keys(wf, keys):
    text = (WORKFLOWS / wf).read_text(encoding="utf-8")
    for k in keys:
        assert f"profiles)[matrix.unit].{k}" in text, \
            f"{wf}: nothing gates on profile key '{k}'"


def test_secret_scan_and_sast_are_never_profile_gated():
    """The credential floor is not subject to the profile. If either of these
    ever acquires an `if:` referencing profiles, the floor has been breached."""
    doc = yaml.safe_load((WORKFLOWS / "post-dev-qa.yml").read_text(encoding="utf-8"))
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            name = str(step.get("name", ""))
            if "Secret scan" in name or "SAST" in name or "gitleaks" in name:
                assert "profiles" not in str(step.get("if", "")), \
                    f"floor step '{name}' must not be profile-gated"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k "discover_emits or workflow_gates"`
Expected: FAIL — no `profiles` output in any of the four workflows.

- [ ] **Step 3: Add the `profiles` output to each `discover` job**

In **all four** workflows, in the `discover` job, add to `outputs:`:

```yaml
      profiles: ${{ steps.p.outputs.profiles }}
```

and add this step at the end of the `discover` job's `steps:`:

```yaml
      # Resolve every unit's governance profile ONCE per run; legs read their
      # own from this output. Keeps it to one index fetch and leaves no
      # resolved file on disk for a later step to tamper with.
      - id: p
        name: Resolve governance profiles
        run: echo "profiles=$(python .specdev/tools/profile.py matrix)" >> "$GITHUB_OUTPUT"
```

`traceability.yml` and `compliance.yml` may not have a `discover` job with `outputs`. If a workflow lacks one, copy the `discover` job wholesale from `spec-validate.yml` (registry check + `units.py matrix --all` + the step above) and make the existing job `needs: discover` with a `matrix.unit` strategy. Read the file first and match its existing trigger and matrix shape.

- [ ] **Step 4: Gate the steps**

In `post-dev-qa.yml`, add to the **coverage** step and the **trace-gap** step (leave `Install gitleaks`, `Secret scan`, and the SAST step untouched):

```yaml
        if: fromJSON(needs.discover.outputs.profiles)[matrix.unit].coverage_gate
```

```yaml
        if: fromJSON(needs.discover.outputs.profiles)[matrix.unit].traceability
```

In `spec-validate.yml`, replace the validate step's `run:` so it passes the resolved bar through:

```yaml
      - name: Validate spec artifacts (Gate 1) — ${{ matrix.unit }}
        if: hashFiles(format('{0}/.specdev/spec.md', matrix.unit)) != '' || fromJSON(needs.discover.outputs.profiles)[matrix.unit].spec_bar == 'charter'
        run: |
          python .specdev/tools/validate_spec.py --strict \
            --root '${{ matrix.unit }}' \
            --profile '${{ fromJSON(needs.discover.outputs.profiles)[matrix.unit].spec_bar }}'
```

In `traceability.yml`, gate the generate/commit job or step with:

```yaml
        if: fromJSON(needs.discover.outputs.profiles)[matrix.unit].traceability
```

In `compliance.yml`, gate its check step with:

```yaml
        if: fromJSON(needs.discover.outputs.profiles)[matrix.unit].compliance
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all green. Existing workflow-shape assertions in `test_specdev_ci.py` / `test_pipeline_hardening.py` may need updating for the new `discover` step — update them, do not delete them.

- [ ] **Step 6: Commit**

```bash
git add assets/workflows/post-dev-qa.yml assets/workflows/spec-validate.yml assets/workflows/traceability.yml assets/workflows/compliance.yml tests/test_profiles.py
git commit -m "feat(ci): gate coverage, traceability and compliance on the resolved profile"
```

---

### Task 9: Findings as the poc terminal state

**Files:**
- Modify: `assets/workflows/specdev-build.yml`
- Modify: `assets/specdev/BUILD.md`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Extends the existing `verify` step (`specdev-build.yml:624-625`) that feeds `terminal_ok`, which `deploy-poc` is gated on. A poc build that leaves no Findings must not deploy.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
BUILD_WF = WORKFLOWS / "specdev-build.yml"
BUILD_TEMPLATE = ROOT / "assets" / "specdev" / "BUILD.md"


def test_build_template_has_a_findings_section():
    text = BUILD_TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"^##\s+Findings\s*$", text, re.M), \
        "BUILD.md must carry a Findings section for the poc terminal state"


def test_verify_step_asserts_findings_for_poc():
    text = BUILD_WF.read_text(encoding="utf-8")
    assert "Findings" in text, \
        "the terminal-state assertion must require Findings in poc mode"


def test_findings_assertion_is_inside_the_verify_step():
    """It must feed terminal_ok, which deploy-poc gates on — not be a separate
    advisory step that a failed assertion cannot block a deployment from."""
    doc = yaml.safe_load(BUILD_WF.read_text(encoding="utf-8"))
    steps = doc["jobs"]["build"]["steps"]
    verify = next(s for s in steps if s.get("id") == "verify")
    assert "Findings" in str(verify.get("run", ""))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k findings`
Expected: FAIL on all three.

- [ ] **Step 3: Add a Findings section to `assets/specdev/BUILD.md`**

Append to the template:

```markdown
## Findings

> **Required to finish a `poc` build.** A spike's deliverable is what it taught
> you, because the code itself is reverse-mapped and rebuilt rather than
> promoted. An empty section fails the build's terminal-state assertion, so
> `deploy-poc` never runs on a spike that left nothing behind.

- **What we set out to answer:** <from CHARTER.md>
- **What we learned:** <the answer, including "it doesn't work" — that is a
  result, not a failure>
- **What surprised us:** <anything the real spec must account for>
- **What the rebuild must do differently:** <concrete guidance for the
  dev/prod unit that replaces this>
```

- [ ] **Step 4: Extend the `verify` step in `assets/workflows/specdev-build.yml`**

Locate the `- id: verify` step (around line 624) and add this to the **end** of its `run:` block, keeping the step's existing `terminal_ok` output logic intact:

```bash
          # A poc's deliverable is its Findings — the code is reverse-mapped
          # and rebuilt, never promoted. A spike that deploys but records
          # nothing has produced nothing, so this is part of the TERMINAL
          # STATE, not a separate advisory check: deploy-poc gates on
          # terminal_ok, and an advisory step could not stop it.
          if [ "$MODE" = "poc" ]; then
            findings=$(awk '/^## Findings[[:space:]]*$/{f=1;next} /^## /{f=0} f' \
                       "$UNIT/.specdev/BUILD.md" 2>/dev/null \
                       | grep -v '^[[:space:]]*>' \
                       | grep -v '^[[:space:]]*$' \
                       | grep -v '<[^>]*>' || true)
            if [ -z "$findings" ]; then
              echo "TERMINAL STATE NOT REACHED: $UNIT/.specdev/BUILD.md has no" \
                   "filled-in '## Findings' section. A poc build's deliverable" \
                   "is what it taught you; record it before finishing."
              echo "ok=false" >> "$GITHUB_OUTPUT"
              exit 1
            fi
          fi
```

Check the surrounding step for the exact variable names it already uses for the mode and unit (`$MODE`, `$UNIT`, or `${{ needs.setup.outputs.* }}`) and match them — do not introduce new ones.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add assets/workflows/specdev-build.yml assets/specdev/BUILD.md tests/test_profiles.py
git commit -m "feat(build): require Findings to reach the poc terminal state"
```

---

### Task 10: Agent contracts — smoke mode and charter mode

**Files:**
- Modify: `agents/qa-verifier.md`
- Modify: `agents/component-builder.md`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: the profile dict the coordinator passes in each agent's dispatch prompt (Task 11 wires that).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
AGENTS = ROOT / "agents"


def test_qa_verifier_documents_smoke_mode():
    text = (AGENTS / "qa-verifier.md").read_text(encoding="utf-8")
    assert "smoke mode" in text.lower()
    assert "at least one test" in text.lower() or "≥1 test" in text
    for floor in ("gitleaks", "secret"):
        assert floor in text.lower(), f"smoke mode must still run {floor}"


def test_qa_verifier_smoke_mode_keeps_the_credential_floor():
    text = (AGENTS / "qa-verifier.md").read_text(encoding="utf-8").lower()
    assert "coverage" in text and "check-gaps" in text


def test_component_builder_documents_charter_mode():
    text = (AGENTS / "component-builder.md").read_text(encoding="utf-8")
    assert "charter" in text.lower()
    assert "smoke test" in text.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k "qa_verifier or component_builder"`
Expected: FAIL — neither file mentions smoke or charter mode.

- [ ] **Step 3: Add smoke mode to `agents/qa-verifier.md`**

Append a section:

```markdown
## Smoke mode (`per_wave_qa: false` / `coverage_gate: false`)

The coordinator passes the unit's resolved governance profile with the
dispatch. When `coverage_gate` is false you are in **smoke mode**, which runs
once at the end of the build rather than after every wave.

In smoke mode:

- **Run the test suite** and assert **at least one test was collected and
  passed**. Report the count. A suite that collects zero tests is a RED
  verdict, not a green one — "no tests ran" is exactly how a poc gets reported
  as working when it never executed.
- **Run the gitleaks secret scan.** Unconditional; it is part of the floor and
  the profile cannot switch it off.
- **Skip** the coverage threshold and `gen_traceability.py --check-gaps`. A
  charter-bar unit has no `REQ-###` IDs for `--check-gaps` to join on, so
  running it would fail on absence rather than on a real gap.

Everything else about your contract is unchanged: return only actionable
failures, never full test output.
```

- [ ] **Step 4: Add charter mode to `agents/component-builder.md`**

Append a section:

```markdown
## Charter mode (`spec_bar: charter`)

The coordinator passes the unit's resolved governance profile with the
dispatch. When `spec_bar` is `charter` you are building a **spike**: there is
no `spec.md` and no `REQ-###` IDs, only `.specdev/CHARTER.md` with the
questions the spike must answer.

In charter mode:

- **Write one smoke test first, then build.** Not red-green per requirement —
  there are no requirements to drive it. The smoke test proves the thing runs.
- **Omit `Refs: REQ-###` commit trailers.** There is no REQ to reference and
  no traceability matrix being generated.
- **Report against the charter's questions,** not against acceptance criteria:
  say what the spike answered and what it did not.

The code is disposable — it is reverse-mapped and rebuilt, never promoted — so
prefer the shortest path to an answer over structure you expect to keep.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_profiles.py -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add agents/qa-verifier.md agents/component-builder.md tests/test_profiles.py
git commit -m "feat(agents): smoke mode for qa-verifier, charter mode for component-builder"
```

---

### Task 11: `SKILL.md` — resolve the profile and branch the pipeline

**Files:**
- Modify: `skills/specdev/SKILL.md`
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `profile.py show` (Task 4), `CHARTER.md` (Task 7), the agent modes (Task 10).

This is the task that closes the gap the design opens on: a spike driven **locally** currently gets the entire production pipeline, because `SKILL.md` has no profile awareness at all.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profiles.py`:

```python
SKILL = ROOT / "skills" / "specdev" / "SKILL.md"


def test_skill_resolves_the_profile_before_the_pipeline():
    text = SKILL.read_text(encoding="utf-8")
    assert "profile.py show" in text
    i_profile = text.index("profile.py show")
    i_pipeline = text.index("## The pipeline")
    assert i_profile < i_pipeline, \
        "the profile must be resolved before the pipeline steps, not inside them"


@pytest.mark.parametrize("key", ["spec_bar", "spec_pr", "adrs",
                                 "per_wave_qa", "traceability"])
def test_skill_documents_each_profile_branch(key):
    assert key in SKILL.read_text(encoding="utf-8")


def test_skill_states_the_floor_is_not_negotiable():
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "floor" in text
    assert "findings" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_profiles.py -q -k skill`
Expected: FAIL — `profile.py show` is absent.

- [ ] **Step 3: Add profile resolution to `SKILL.md`**

Insert this section immediately **after** the `## Establish the governed unit FIRST` section and **before** `## The pipeline (do these in order)`:

```markdown
## Then resolve the governance profile

Run `python .specdev/tools/profile.py show --unit <unit>` (omit `--unit` for a
single-root repo). It prints the unit's resolved profile — how much of the
pipeline actually runs. Hold it alongside the spec and the component DAG, and
record it in `BUILD.md`.

The profile is resolved from the unit's **effective** classification, so a
`poc` unit that a `prod` unit depends on resolves to the `prod` profile. You do
not get to infer it from the branch name or from `org.json` — run the tool.

| Key | When false / `charter` |
|---|---|
| `spec_bar` | `charter` → write `.specdev/CHARTER.md`, not `spec.md`; no `REQ-###` IDs |
| `spec_pr` | skip the Spec PR entirely; the charter commits onto the `poc/**` branch |
| `adrs` | do not invoke the `adr` skill; author no ADRs |
| `per_wave_qa` | no `qa-verifier` between waves — one smoke-mode run at the end |
| `coverage_gate` | no coverage threshold |
| `traceability` | no matrix, no `--check-gaps`, no `Refs: REQ-###` trailers |
| `compliance` | no control-framework evidence |
| `prod_promotion` | `deploy-poc` only; never staging→prod |

**The floor is not negotiable and is not in the table.** Whatever the profile
says, these always apply: the unit scope check, the gitleaks secret scan, SAST,
`arch_config.py validate`'s `secret_ref` rule, org ADRs that target `poc`, at
least one passing smoke test, and a filled-in `## Findings` section in
`BUILD.md` before a poc build is finished. Credential protection and
containment are not ceremony.

**Pass the profile to every subagent you dispatch.** `component-builder` needs
`spec_bar` to know whether to write REQ-driven tests or a smoke test;
`qa-verifier` needs `coverage_gate` to know whether it is in smoke mode.

**A poc is never promoted in place.** When a spike proves out, reverse-map it
with the `spec-explorer` agent and rebuild it as a new `dev`/`prod` unit
through the full pipeline. `org-adr-check` rejects a PR that raises a
poc-built unit's maturity.
```

- [ ] **Step 4: Mark the conditional pipeline steps**

In `## The pipeline (do these in order)`, prefix the affected steps so the reduction is visible where the work happens. Add to step 3: `**(`spec_bar: charter` → write `.specdev/CHARTER.md` instead; skip the rest of this step.)**`. Add to step 4: `**(`adrs: false` → skip; still dispatch `adr-checker`, which is floor.)**`. Add to step 5: `**(`spec_pr: false` → skip this step entirely.)**`. In step 6's per-wave QA bullet add: `**(`per_wave_qa: false` → one smoke-mode `qa-verifier` at the end instead.)**`. In step 9 add: `**(`traceability: false` → skip.)**`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add skills/specdev/SKILL.md tests/test_profiles.py
git commit -m "feat(skill): resolve and honour the governance profile"
```

---

### Task 12: Documentation and design-doc reconciliation

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-03-poc-governance-profile-design.md`
- Modify: `commands/init.md`

- [ ] **Step 1: Add a README section**

Insert after the `## Org ADR governance (architectural repo of record)` section:

```markdown
## Governance profiles (the poc fast path)

Classification does more than scope org ADRs: each `maturity` value in
[governance/classification.json](governance/classification.json) carries a
**profile** declaring how much of the pipeline runs. A `poc` unit writes a
`CHARTER.md` instead of a REQ-level spec, skips the Spec PR, authors no ADRs,
runs one smoke-mode QA at the end instead of per-wave gates, and never
promotes to prod. A `prod` unit runs everything.

The table lives in the **governance repo** and travels inside `adr/index.json`,
so a product repo cannot grant itself a discount — the dial belongs to the org.
Resolve a unit's profile with:

```
python .specdev/tools/profile.py show --unit <unit>
```

Three properties keep the fast path from becoming an escape hatch:

- **Resolution uses *effective* classification.** A `poc` unit that a `prod`
  unit depends on resolves to the **prod** profile. Moving risky code into a
  spike unit buys nothing.
- **It fails closed.** Unconfigured governance, an unreachable index or an
  unparseable value all resolve to full production governance. Undeclared
  governance is never a discount.
- **The floor is non-overridable.** The scope check, gitleaks, SAST,
  `arch_config.py`'s `secret_ref` rule, poc-scoped org ADRs, one passing smoke
  test, and a filled-in `## Findings` section are applied *after* the table and
  cannot be switched off by it. Credential protection is not ceremony.

**A poc is never promoted in place.** It is reverse-mapped with `spec-explorer`
and rebuilt as a new unit; `org-adr-check` rejects a PR that raises a poc-built
unit's maturity.
```

- [ ] **Step 2: Reconcile the design doc**

In `docs/superpowers/specs/2026-08-03-poc-governance-profile-design.md`, replace the `## No promotion in place` section's first paragraph with:

```markdown
`profile.py` gains a `promotion-check` subcommand, wired into
`org-adr-check.yml`'s `discover` job: if a unit's maturity rank **increased**
versus the merge base **and** that unit has poc build history (a `poc-*`
release tag, or `run.json` recording mode `poc`), it fails hard:
```

and replace the closing paragraph with:

```markdown
`org-adr-check.yml` is the right host: it already fetches the org index, it
already runs on every PR, and it is deliberately never path-filtered. It needs
no new workflow — only `fetch-depth: 0` on its `discover` checkout, since
`git show <base>:...` cannot resolve in a shallow clone.

**This moved from `units.py check`, where the design first put it.** `units.py`
runs offline and has no access to the org index, so it would need hardcoded
maturity ranks — and an org that renamed its maturity values would get a
silently inert rule, failing *open* on exactly the check meant to prevent
promotion. Ranks now come from the index.
```

- [ ] **Step 3: Note the charter artifact in `commands/init.md`**

In the post-init wiring list, add:

```markdown
- A unit classified `maturity: poc` uses `.specdev/CHARTER.md` as its Gate 1
  artifact instead of `spec.md`, and runs the reduced pipeline its profile
  declares. Check what you get with
  `python .specdev/tools/profile.py show --unit <unit>`.
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-08-03-poc-governance-profile-design.md commands/init.md
git commit -m "docs: governance profiles, and reconcile the design's promotion-rule host"
```

---

## Self-Review

**Spec coverage:** Data model → Tasks 1–3. Floor → Task 2 (`FLOOR`, non-overridable test) + Task 8 (`test_secret_scan_and_sast_are_never_profile_gated`). Effective-classification rule → Task 3 (`test_poc_unit_a_prod_unit_depends_on_resolves_to_prod`). Fail-closed → Task 3. Strictest-wins → Task 2. CI distribution → Tasks 4, 8. Pipeline changes → Tasks 7–11. Findings terminal state → Task 9. Agents → Task 10. No-promotion → Tasks 5–6. Testing section → covered across all tasks. Docs → Task 12.

**Deviation recorded:** the no-promotion rule's host moved from `units.py` to `profile.py`/`org-adr-check.yml`, stated at the top and reconciled into the design doc in Task 12.

**Type consistency:** `compose(values, axes)` (Task 2) is called by `resolve_all` (Task 3) with a `set`; `resolve_all(root, index)` → `dict[str, dict]` is called by `resolve` and by the CLI (Task 4) and by nothing else. `_load_index` returns `(index | None, reason | None)` and is used by both `resolve_all` and `promotion_errors` (Task 5). `PROFILE_KEYS` in the tests matches `STRICTEST`'s keys exactly. Floor keys appear in `FLOOR` only, never in `STRICTEST` — `compose` merges them last, which is what makes `test_missing_profile_falls_back_to_strictest`'s `{**STRICTEST, **FLOOR}` equality hold.

**Known follow-up for the implementer:** Tasks 6, 8 and 9 touch workflows that `tests/test_specdev_ci.py` and `tests/test_pipeline_hardening.py` already assert on. Expect to update those assertions — update, never delete; they exist because these gates have silently reported green before.
