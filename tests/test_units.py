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
    assert any("shadow" in e and "not registered" in e for e in errs)


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
