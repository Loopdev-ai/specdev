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
    import shutil
    shutil.copytree(ROOT / "governance", tmp_path / "governance")
    subprocess.run(
        [sys.executable, str(GEN_INDEX), "--root", str(tmp_path)],
        check=True, capture_output=True, text=True)
    index = json.loads(
        (tmp_path / "governance" / "adr" / "index.json").read_text(encoding="utf-8-sig"))
    assert index["axes"]["maturity"]["values"]["poc"]["profile"]["adrs"] is False


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


def test_invalid_classification_still_propagates_strictness_to_dependencies(tmp_path):
    """THE escape-hatch regression via a broken file, not a declared value.
    A prod unit with a corrupt (present-but-invalid) classification must still
    pull the poc unit it depends on up to strict, exactly as a valid prod
    declaration would -- a corrupt org.json must not be a lighter-touch
    stand-in for one."""
    a = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    b = make_unit(tmp_path, "api", {"maturity": "nonsense", "audience": "bogus"},
                  depends_on=["spike"])
    write_registry(tmp_path, [a, b])
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    p = _pf().resolve(tmp_path, "spike", index=idx)
    assert p["per_wave_qa"] is True
    assert p["coverage_gate"] is True
    assert p["traceability"] is True
    assert p["spec_bar"] == "full"
    # api itself still fails closed on its own broken classification.
    assert _pf().resolve(tmp_path, "api", index=idx)["per_wave_qa"] is True


def test_resolve_all_covers_every_registered_unit(tmp_path):
    a = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    b = make_unit(tmp_path, "api", {"maturity": "prod", "audience": "customer"})
    write_registry(tmp_path, [a, b])
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    allp = _pf().resolve_all(tmp_path, index=idx)
    assert set(allp) == {"spike", "api"}
    assert allp["spike"]["coverage_gate"] is False
    assert allp["api"]["coverage_gate"] is True


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
