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
