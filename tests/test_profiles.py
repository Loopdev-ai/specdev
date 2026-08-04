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
