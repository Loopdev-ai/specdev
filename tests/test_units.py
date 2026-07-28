import importlib.util
import json
import re
import shlex
import subprocess
import sys
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


def test_escalations_names_the_causing_dependent():
    entries = [
        {"path": "prod-svc", "depends_on": ["risky"]},
        {"path": "risky", "depends_on": []},
    ]
    declared = {
        "prod-svc": {"maturity": {"prod"}},
        "risky": {"maturity": {"poc"}},
    }
    lines = un.escalations(entries, AXES, declared)
    assert len(lines) == 1
    assert "risky" in lines[0]
    assert "prod-svc" in lines[0]


# ---- check_org_adrs: set-valued matching -------------------------------

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


# ---- check_org_adrs: end to end ----------------------------------------

def _index(adrs, axes=None):
    return {"axes": axes or AXES, "adrs": adrs}


def _write_manifest(root, unit, entries):
    d = root / unit / ".specdev" / "adr"
    d.mkdir(parents=True, exist_ok=True)
    (d / "org-compliance.json").write_text(
        json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def _run_coa(tmp_path, idx_file, *extra):
    return subprocess.run(
        [sys.executable, str(COA_PATH), "--root", str(tmp_path),
         "--index", str(idx_file), *extra],
        capture_output=True, text=True)


def test_check_org_adrs_multi_unit_applies_per_unit(tmp_path):
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
    idx_file = tmp_path / "index.json"
    idx_file.write_text(json.dumps(_index([
        {"id": "ADR-0004", "title": "Terraform", "status": "accepted",
         "applies_to": ["dev+"], "sha256": "abc"}])), encoding="utf-8")

    rc = _run_coa(tmp_path, idx_file)
    assert rc.returncode == 1
    assert "infra" in rc.stdout

    _write_manifest(tmp_path, "infra",
                    [{"id": "ADR-0004", "status": "met", "sha256": "abc"}])
    rc = _run_coa(tmp_path, idx_file)
    assert rc.returncode == 0, rc.stdout + rc.stderr


def test_check_org_adrs_escalated_unit_must_verify(tmp_path):
    """The laundering scenario end-to-end: risky is only reachable as a prod
    dependency, and the gate must demand its verification."""
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
    idx_file = tmp_path / "index.json"
    idx_file.write_text(json.dumps(_index([
        {"id": "ADR-0004", "title": "Terraform", "status": "accepted",
         "applies_to": ["dev+"], "sha256": "abc"}])), encoding="utf-8")
    _write_manifest(tmp_path, "prod-svc",
                    [{"id": "ADR-0004", "status": "met", "sha256": "abc"}])

    rc = _run_coa(tmp_path, idx_file)
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
    idx_file = tmp_path / "index.json"
    idx_file.write_text(json.dumps(_index([
        {"id": "ADR-0004", "title": "Terraform", "status": "accepted",
         "applies_to": ["dev+"], "sha256": "abc"}])), encoding="utf-8")

    rc = _run_coa(tmp_path, idx_file)
    assert rc.returncode == 1

    _write_manifest(tmp_path, ".",
                    [{"id": "ADR-0004", "status": "met", "sha256": "abc"}])
    rc = _run_coa(tmp_path, idx_file)
    assert rc.returncode == 0, rc.stdout + rc.stderr


def test_check_org_adrs_stale_sha_fails(tmp_path):
    """The staleness half: an upstream ADR edit invalidates the verification
    with no local change at all."""
    d = tmp_path / ".specdev"
    d.mkdir()
    (d / "org.json").write_text(json.dumps({
        "governance_repo": "faro/governance", "ref": "main",
        "path": "governance/adr",
        "classification": {"maturity": "prod", "audience": "internal"},
    }), encoding="utf-8")
    _write_manifest(tmp_path, ".",
                    [{"id": "ADR-0004", "status": "met", "sha256": "OLD"}])
    idx_file = tmp_path / "index.json"
    idx_file.write_text(json.dumps(_index([
        {"id": "ADR-0004", "title": "Terraform", "status": "accepted",
         "applies_to": ["dev+"], "sha256": "NEW"}])), encoding="utf-8")

    rc = _run_coa(tmp_path, idx_file)
    assert rc.returncode == 1
    assert "changed upstream" in rc.stdout


def test_check_org_adrs_inert_without_org_json(tmp_path):
    rc = subprocess.run(
        [sys.executable, str(COA_PATH), "--root", str(tmp_path)],
        capture_output=True, text=True)
    assert rc.returncode == 0


# ---- artifact generation is unit-relative ------------------------------

GT_PATH = ROOT / "assets" / "specdev" / "tools" / "gen_traceability.py"


def test_gen_traceability_writes_under_root(tmp_path):
    """Regression pin: the tool joins --out onto --root, so a unit's matrix
    never leaks to the repo root."""
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


def test_gen_traceability_all_units_writes_each_and_an_index(tmp_path):
    for name in ("infra", "demos"):
        d = tmp_path / name / ".specdev"
        d.mkdir(parents=True)
        (d / "spec.md").write_text(
            f"# Spec {name}\n\n**Feature ID:** FEAT-001\n", encoding="utf-8")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra", "demos"]})

    rc = subprocess.run(
        [sys.executable, str(GT_PATH), "--root", str(tmp_path), "--all-units"],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert (tmp_path / "infra" / ".specdev" / "traceability.md").exists()
    assert (tmp_path / "demos" / ".specdev" / "traceability.md").exists()

    idx = tmp_path / ".specdev" / "traceability-index.md"
    assert idx.exists()
    body = idx.read_text(encoding="utf-8")
    assert "infra/.specdev/traceability.md" in body
    assert "demos/.specdev/traceability.md" in body


def test_rollup_index_is_absent_for_single_root(tmp_path):
    assert un.write_rollup_index(tmp_path, "traceability.md", "T") is None


# ---- deploy.py --root --------------------------------------------------

DEPLOY_PATH = ROOT / "assets" / "specdev" / "tools" / "deploy.py"


def test_deploy_accepts_root(tmp_path):
    unit = tmp_path / "infra"
    (unit / ".specdev").mkdir(parents=True)
    (unit / ".specdev" / "deploy.profile.json").write_text(
        json.dumps({"target": "manual", "environments": {}}), encoding="utf-8")
    rc = subprocess.run(
        [sys.executable, str(DEPLOY_PATH), "target", "--root", str(unit)],
        capture_output=True, text=True, cwd=tmp_path)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert "manual" in rc.stdout


def test_deploy_root_isolates_units(tmp_path):
    """Two units, two profiles: each --root must read its own."""
    for name, target in (("a", "manual"), ("b", "script")):
        d = tmp_path / name / ".specdev"
        d.mkdir(parents=True)
        (d / "deploy.profile.json").write_text(
            json.dumps({"target": target, "environments": {}}), encoding="utf-8")
    out = {}
    for name in ("a", "b"):
        rc = subprocess.run(
            [sys.executable, str(DEPLOY_PATH), "target", "--root",
             str(tmp_path / name)],
            capture_output=True, text=True, cwd=tmp_path)
        assert rc.returncode == 0, rc.stdout + rc.stderr
        out[name] = rc.stdout.strip()
    assert out["a"] != out["b"]


# ---- changed units / scope guard ---------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _head(tmp_path):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
                          capture_output=True, text=True).stdout.strip()


def test_changed_units_filters_to_touched_units(tmp_path):
    _repo(tmp_path)
    make_unit(tmp_path, "infra")
    make_unit(tmp_path, "demos")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra", "demos"]})
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = _head(tmp_path)
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
    base = _head(tmp_path)
    (tmp_path / "soc" / "leak.txt").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "touch soc")

    assert un.out_of_scope(tmp_path, "infra", base) == ["soc/leak.txt"]
    assert un.out_of_scope(tmp_path, "soc", base) == []


def test_out_of_scope_allows_shared_files(tmp_path):
    """Files belonging to no unit (shared CI config, docs) are not attributed
    to any unit and must not fail the scope guard."""
    _repo(tmp_path)
    make_unit(tmp_path, "infra")
    write_registry(tmp_path, {"schema_version": 1, "units": ["infra"]})
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = _head(tmp_path)
    (tmp_path / "README.md").write_text("shared", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "touch readme")

    assert un.out_of_scope(tmp_path, "infra", base) == []


def test_out_of_scope_root_unit_is_never_out_of_scope(tmp_path):
    assert un.out_of_scope(tmp_path, ".", "HEAD") == []


# ---- workflow <-> CLI contract -----------------------------------------
# Guards a whole bug class: a workflow invoking a tool with arguments the
# tool's parser rejects. argparse exits 2 on a usage error, so any extracted
# command line that exits 2 means the workflow would fail at runtime with an
# 'unrecognized arguments' error. Caught arch_config.py, whose --root sits on
# the top-level parser and must precede the subcommand.

WORKFLOW_DIR = ROOT / "assets" / "workflows"
TOOLS_DIR = ROOT / "assets" / "specdev" / "tools"

_EXPR = re.compile(r"\$\{\{[^}]*\}\}")
_TOOL_CALL = re.compile(r"python\s+\.specdev/tools/(\w+)\.py([^\n|&;]*)")


def _extract_tool_calls():
    calls = []
    for wf in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        # Join shell line-continuations so a multi-line invocation is seen whole.
        text = re.sub(r"\\\s*\n\s*", " ", text)
        for tool, rest in _TOOL_CALL.findall(text):
            # Workflow expressions and shell vars become a benign literal.
            args = _EXPR.sub("X", rest)
            args = re.sub(r'"\$\w+"|\$\w+|\$\{\w+[^}]*\}', "X", args)
            # Many calls are wrapped in $( ) inside an echo; cut the wrapper off.
            args = re.split(r"[)>]", args, maxsplit=1)[0]
            args = args.replace('"', "").replace("'", "").strip()
            calls.append((wf.name, tool, shlex.split(args)))
    return calls


def test_workflows_reference_only_existing_tools():
    calls = _extract_tool_calls()
    assert calls, "no tool invocations found in assets/workflows — regex broken?"
    for wf, tool, _ in calls:
        assert (TOOLS_DIR / f"{tool}.py").exists(), \
            f"{wf} invokes .specdev/tools/{tool}.py which does not exist"


@pytest.mark.parametrize("wf,tool,args", _extract_tool_calls(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_workflow_tool_invocation_is_accepted_by_the_parser(wf, tool, args, tmp_path):
    """'unrecognized arguments' is the precise signal that a workflow passes a
    flag the tool's parser does not accept — including a flag declared on the
    top-level parser but written after the subcommand.

    Deliberately NOT asserting on exit code 2 generally: placeholder
    substitution turns constrained values into 'X', which argparse rejects as
    an invalid choice. That is an artifact of this test, not a real defect."""
    rc = subprocess.run(
        [sys.executable, str(TOOLS_DIR / f"{tool}.py"), *args],
        capture_output=True, text=True, cwd=tmp_path)
    for signal in ("unrecognized arguments", "expected one argument"):
        assert signal not in rc.stderr, (
            f"{wf} invokes '{tool}.py {' '.join(args)}' but the parser rejects "
            f"it:\n{rc.stderr}")


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


def test_parse_ref_unit_name_with_no_feature_is_not_a_unit():
    """'spec/demos' alone names a feature called demos, not unit demos with an
    empty feature — an empty feature name would be meaningless."""
    assert un.parse_ref("spec/demos", REG) == (".", "demos")


def test_parse_ref_nested_unit_path():
    reg = {"schema_version": 1, "units": ["services/api"]}
    assert un.parse_ref("spec/services/api/add-thing", reg) == \
        ("services/api", "add-thing")


def test_parse_ref_empty_is_root():
    assert un.parse_ref("", REG) == (".", "")


# ---- per-unit release tags ---------------------------------------------

def _wf(name):
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def test_poc_tag_is_namespaced_by_unit():
    """Two units' poc builds on the same day at the same commit must not
    produce the same tag — the second push would fail."""
    t = _wf("deploy-poc.yml")
    assert "slug=" in t and "poc-${slug}-" in t


def test_release_tag_is_namespaced_by_unit():
    t = _wf("deploy-unit.yml")
    assert "v-${SLUG}-" in t


def test_traceability_push_rebases_on_conflict():
    """Per-unit concurrency groups do not stop two unit builds racing to push
    to main; the traceability commit must rebase and retry."""
    t = _wf("traceability.yml")
    assert "--rebase" in t
    assert "--all-units" in t


def test_build_workflow_has_per_unit_concurrency_and_scope_guard():
    t = _wf("specdev-build.yml")
    assert "concurrency:" in t
    assert "scope-check" in t
    assert "resolve-ref" in t


def test_org_adr_check_is_never_path_filtered():
    """Spec decision 7, as a test: the staleness check is driven by the upstream
    ADR index, not the local diff, so a path filter would let verifications rot
    silently while the repo looks green."""
    doc = __import__("yaml").safe_load(_wf("org-adr-check.yml"))
    on = doc[True] if True in doc else doc["on"]
    assert "paths" not in (on.get("pull_request") or {})
    assert "matrix --all" in _wf("org-adr-check.yml")
