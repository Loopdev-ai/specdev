import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


def test_cli_index_before_subcommand_exits_nonzero(tmp_path):
    """Regression test for argparse clobber: --index on top-level parser was
    being overwritten by subparser's None default. With the fix, --index must
    come AFTER the subcommand or argparse exits non-zero."""
    e = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    write_registry(tmp_path, [e])
    idx = write_index(tmp_path)
    # --index BEFORE subcommand should fail: argparse will either reject it as
    # an unrecognized argument or treat the file path as an invalid subcommand.
    r = subprocess.run(
        [sys.executable, str(PROFILE_PATH), "--root", str(tmp_path),
         "--index", str(idx), "show", "--unit", "spike"],
        capture_output=True, text=True)
    assert r.returncode != 0, f"Expected exit code != 0, got {r.returncode}: {r.stderr}"
    assert "error:" in r.stderr


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


def test_promotion_check_cli_refuses_empty_changed_from(tmp_path):
    """The CLI boundary rejects empty --changed-from to prevent vacuous success."""
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "spike"}])
    set_maturity(tmp_path, "spike", "poc")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    idx = write_index(tmp_path)

    # Empty --changed-from should exit 2 and print error
    r = run_cli(tmp_path, "promotion-check", "--changed-from", "",
                "--index", str(idx))
    assert r.returncode == 2, (
        f"Expected exit 2 for empty --changed-from, got {r.returncode}: {r.stderr}")
    assert "no base ref given" in r.stderr or "base" in r.stderr.lower()
    assert "compares nothing" in r.stderr or "verifying nothing" in r.stderr.lower()


def test_poc_tag_does_not_match_unit_name_prefixes(tmp_path):
    """Regression test: poc-spike-* should not match poc-spike-two-*.
    Only spike-two should be detected as poc-built."""
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "spike"}, {"path": "spike-two"}])
    set_maturity(tmp_path, "spike", "poc")
    set_maturity(tmp_path, "spike-two", "poc")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    # Tag only spike-two as poc-built
    git(tmp_path, "tag", "poc-spike-two-20260804-abc1234")

    # spike should not have poc history (no tag matches poc-spike-[0-9]*)
    assert _pf().has_poc_history(tmp_path, "spike") is False
    # spike-two should have poc history (tag matches poc-spike-two-[0-9]*)
    assert _pf().has_poc_history(tmp_path, "spike-two") is True


def test_root_unit_poc_tag_does_not_match_named_unit_tags(tmp_path):
    """Regression test: root unit (.) pattern poc-[0-9]* should not match
    named unit tags like poc-api-20260804-sha7. Only the root unit's own tag
    should register as poc history."""
    init_repo(tmp_path)
    write_registry(tmp_path, [{"path": "."}, {"path": "api"}])
    set_maturity(tmp_path, ".", "prod")
    set_maturity(tmp_path, "api", "poc")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    # Tag only the api unit as poc-built
    git(tmp_path, "tag", "poc-api-20260804-abc1234")

    # Root unit (.) should not have poc history (no tag matches poc-[0-9]*)
    assert _pf().has_poc_history(tmp_path, ".") is False
    # api should have poc history (tag matches poc-api-[0-9]*)
    assert _pf().has_poc_history(tmp_path, "api") is True


WF = ROOT / "assets" / "workflows" / "org-adr-check.yml"


def test_org_adr_check_runs_the_promotion_check():
    """Promotion check must be in the discover job as a real, enabled step."""
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = doc["jobs"]["discover"]["steps"]
    # Find the step by name or by content
    promo_step = None
    for s in steps:
        if s.get("name", "").startswith("Reject in-place promotion"):
            promo_step = s
            break
    assert promo_step is not None, (
        "org-adr-check discover job must have a 'Reject in-place promotion' step")
    # Verify it's a real step with a run command
    assert "run" in promo_step, "promotion check step must have a 'run' command"
    assert "profile.py promotion-check" in promo_step["run"], (
        "promotion check step must invoke profile.py promotion-check")
    # Verify it's not disabled
    assert "if" not in promo_step or not promo_step["if"], (
        "promotion check step must not be disabled with an if condition")


def test_org_adr_check_discover_has_full_history():
    """git show <base>:... cannot resolve in a shallow clone, so the promotion
    check would pass vacuously without fetch-depth: 0."""
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = doc["jobs"]["discover"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0


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


def test_charter_mode_accepts_comparison_operators_in_prose(tmp_path):
    """Regression: angle-bracket comparison operators (< 50k/sec) must not
    false-positive as placeholders. This is the exact false-positive from the
    Important finding: 'Throughput must stay < 50k/sec and > 10k/sec.'"""
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    charter = GOOD_CHARTER.replace(
        "Three working days, ending 2026-08-07.",
        "Three working days, ending 2026-08-07. Throughput must stay < 50k/sec and > 10k/sec.")
    (d / "CHARTER.md").write_text(charter, encoding="utf-8")
    r = run_validate(tmp_path, "--strict", "--profile", "charter")
    assert r.returncode == 0, f"Expected pass but got: {r.stdout}{r.stderr}"


def test_charter_mode_accepts_email_addresses_in_angle_brackets(tmp_path):
    """Regression: email addresses in angle brackets (< team@example.com >) must
    not false-positive as placeholders."""
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    charter = GOOD_CHARTER.replace(
        "Throughput below 20k/sec after two days of tuning.",
        "Throughput below 20k/sec after two days of tuning (contact <team@example.com> for questions).")
    (d / "CHARTER.md").write_text(charter, encoding="utf-8")
    r = run_validate(tmp_path, "--strict", "--profile", "charter")
    assert r.returncode == 0, f"Expected pass but got: {r.stdout}{r.stderr}"


def test_charter_mode_rejects_literal_tbd_placeholder(tmp_path):
    """Regression: bare TBD as a whole word (case-insensitive) must still be
    caught as a placeholder, even though angle-bracket false-positives are fixed."""
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    charter = GOOD_CHARTER.replace(
        "Throughput below 20k/sec after two days of tuning.",
        "TBD")
    (d / "CHARTER.md").write_text(charter, encoding="utf-8")
    r = run_validate(tmp_path, "--strict", "--profile", "charter")
    assert r.returncode == 1, f"Expected fail but got: {r.stdout}{r.stderr}"


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


# ---- Task 8: profile-conditional gate workflows ------------------------

WORKFLOWS = ROOT / "assets" / "workflows"

# traceability.yml is deliberately EXCLUDED here: it has no `discover` job and
# no per-unit matrix (a single job over --all-units, to avoid multiplying the
# push race the "Commit matrix" step already rebase-retries around). Its
# profile filtering lives inside gen_traceability.py instead — see the
# gen_traceability tests below.
PROFILE_GATED = {
    "post-dev-qa.yml": ["coverage_gate", "traceability"],
    "spec-validate.yml": ["spec_bar"],
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


def test_traceability_workflow_has_no_discover_job():
    """Regression pin: traceability.yml is a single job over --all-units, not a
    per-unit matrix. Splitting it would multiply the concurrent-push race its
    'Commit matrix' step rebase-retries around. The profile filter lives in
    the tool instead (see the gen_traceability tests below)."""
    doc = yaml.safe_load((WORKFLOWS / "traceability.yml").read_text(encoding="utf-8"))
    assert "discover" not in doc["jobs"]


# ---- traceability profile filtering lives in the tool, not the workflow ---

GT_PATH = ROOT / "assets" / "specdev" / "tools" / "gen_traceability.py"


def load_gt():
    return load_mod(GT_PATH, "specdev_gen_traceability")


def write_unit_spec(root, name):
    d = root / name / ".specdev"
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.md").write_text(
        f"# Spec {name}\n\n**Feature ID:** FEAT-001\n", encoding="utf-8")


def test_gen_traceability_skips_a_unit_whose_profile_disables_traceability(
        tmp_path, monkeypatch):
    gt = load_gt()
    a = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    b = make_unit(tmp_path, "api", {"maturity": "prod", "audience": "customer"})
    write_registry(tmp_path, [a, b])
    write_unit_spec(tmp_path, "spike")
    write_unit_spec(tmp_path, "api")
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))

    # Load the profile module and monkeypatch its resolve_all
    profile_mod = _pf()
    real_resolve_all = profile_mod.resolve_all
    monkeypatch.setattr(profile_mod, "resolve_all",
                        lambda root: real_resolve_all(root, index=idx))
    # Monkeypatch _load_profile_module to return our patched module
    monkeypatch.setattr(gt, "_load_profile_module",
                        lambda: profile_mod)
    monkeypatch.setattr(sys, "argv",
                        ["gen_traceability.py", "--root", str(tmp_path), "--all-units"])

    assert gt.main() == 0
    assert not (tmp_path / "spike" / ".specdev" / "traceability.md").exists(), \
        "poc unit has traceability: false in its resolved profile — must be skipped"
    assert (tmp_path / "api" / ".specdev" / "traceability.md").exists(), \
        "prod unit has traceability: true — must still be generated"


def test_gen_traceability_generates_for_a_unit_whose_profile_enables_it(
        tmp_path, monkeypatch):
    gt = load_gt()
    e = make_unit(tmp_path, "api", {"maturity": "prod", "audience": "customer"})
    write_registry(tmp_path, [e])
    write_unit_spec(tmp_path, "api")
    idx = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))

    # Load the profile module and monkeypatch its resolve_all
    profile_mod = _pf()
    real_resolve_all = profile_mod.resolve_all
    monkeypatch.setattr(profile_mod, "resolve_all",
                        lambda root: real_resolve_all(root, index=idx))
    # Monkeypatch _load_profile_module to return our patched module
    monkeypatch.setattr(gt, "_load_profile_module",
                        lambda: profile_mod)
    monkeypatch.setattr(sys, "argv",
                        ["gen_traceability.py", "--root", str(tmp_path), "--all-units"])

    assert gt.main() == 0
    out = tmp_path / "api" / ".specdev" / "traceability.md"
    assert out.exists()
    assert "REQ" in out.read_text(encoding="utf-8") or "requirements" in out.read_text(
        encoding="utf-8").lower()


def test_gen_traceability_fails_safe_when_profile_resolution_raises(
        tmp_path, monkeypatch, capsys):
    """A unit must never be silently skipped because governance itself broke.
    If profile.resolve_all() raises, every unit still gets traceability
    generated -- the strict behaviour -- never the loose one."""
    gt = load_gt()
    e = make_unit(tmp_path, "spike", {"maturity": "poc", "audience": "internal"})
    write_registry(tmp_path, [e])
    write_unit_spec(tmp_path, "spike")

    def boom(root):
        raise RuntimeError("network is unreachable")

    # Monkeypatch _load_profile_module to raise, simulating profile load failure
    def load_broken():
        boom(None)  # Will raise RuntimeError
    monkeypatch.setattr(gt, "_load_profile_module", load_broken)
    monkeypatch.setattr(sys, "argv",
                        ["gen_traceability.py", "--root", str(tmp_path), "--all-units"])

    assert gt.main() == 0
    assert (tmp_path / "spike" / ".specdev" / "traceability.md").exists(), \
        "profile resolution failure must fail SAFE (generate), never skip"
    assert "NOTE" in capsys.readouterr().err


# ---- Task 9: Findings as the poc terminal state ------------------------
#
# The original plan had the assertion live as a shell block appended to the
# `verify` step's `run:` in specdev-build.yml. It does not: that step only
# dispatches to `python .specdev/tools/build_outcome.py verify`, which
# assembles the record whose `ok` field becomes `terminal_ok` — the value
# `deploy-poc` is gated on. So the assertion lives in build_outcome.py's
# findings_problems(), wired into verify() only for mode="poc".

BUILD_TEMPLATE = ROOT / "assets" / "specdev" / "BUILD.md"
BO_PATH = ROOT / "assets" / "specdev" / "tools" / "build_outcome.py"


def test_build_template_has_a_findings_section():
    text = BUILD_TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"^##\s+Findings\s*$", text, re.M), \
        "BUILD.md must carry a Findings section for the poc terminal state"


def _bo():
    return load_mod(BO_PATH, "specdev_build_outcome_findings")


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True,
                   capture_output=True, text=True)


def _findings_repo(tmp_path, findings_block):
    """A repo where a build otherwise reached its terminal state: a real
    checkpoint, a prepared PR body, and the implementation branch pushed
    with a commit beyond base. `findings_block` is appended verbatim to
    BUILD.md so each test varies only the Findings section."""
    root = tmp_path / "repo"
    (root / ".specdev").mkdir(parents=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / ".specdev" / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-900\n\nWave 1 green.\n"
        + findings_block, encoding="utf-8")
    (root / ".specdev" / "PR_BODY.md").write_text(
        "# FEAT-900 - Widget\n\nImplements REQ-001.\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed")

    bo = _bo()
    ref = bo.implementation_ref(".", "FEAT-900")
    _git(root, "checkout", "-q", "-B", ref, "main")
    (root / "widget.py").write_text("widget", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat(widget): REQ-001")
    _git(root, "checkout", "-q", "main")
    return root, bo


def _verify(root, bo, mode, monkeypatch):
    monkeypatch.setattr(bo, "_gh_prs", lambda *a, **k: [])
    return bo.verify(root, "FEAT-900", mode, ".", "main", None, repo_dir=root)


def test_findings_assertion_gates_the_poc_verdict_when_missing(tmp_path, monkeypatch):
    """No '## Findings' section at all -> poc verify() fails, and the reason
    ends up in `problems` (what _md_report surfaces to explain the FAIL)."""
    root, bo = _findings_repo(tmp_path, "")
    rec = _verify(root, bo, "poc", monkeypatch)
    assert rec["ok"] is False
    assert any("Findings" in p for p in rec["problems"])


def test_findings_assertion_gates_the_poc_verdict_when_only_placeholders(
        tmp_path, monkeypatch):
    block = ("\n## Findings\n\n"
             "- **What we learned:** <the answer>\n")
    root, bo = _findings_repo(tmp_path, block)
    rec = _verify(root, bo, "poc", monkeypatch)
    assert rec["ok"] is False
    assert any("Findings" in p for p in rec["problems"])


def test_findings_assertion_passes_the_poc_verdict_when_filled_in(tmp_path, monkeypatch):
    block = "\n## Findings\n\nA single consumer sustained 62k events/sec.\n"
    root, bo = _findings_repo(tmp_path, block)
    rec = _verify(root, bo, "poc", monkeypatch)
    assert rec["ok"] is True, rec["problems"]


def test_prod_verify_is_unaffected_by_an_absent_findings_section(tmp_path, monkeypatch):
    """A prod build must be completely unaffected by Task 9 -- it has no
    Findings requirement at all."""
    root, bo = _findings_repo(tmp_path, "")  # no Findings section
    rec = _verify(root, bo, "prod", monkeypatch)
    assert rec["ok"] is True, rec["problems"]
    assert not any("Findings" in p for p in rec["problems"])


def test_shipped_build_template_findings_is_not_counted_as_filled_in(tmp_path):
    """The shipped template's own Findings section is all blockquote intro
    and <...> placeholders -- the same discipline CHARTER.md's template gets
    -- so validating it as-is must still report a problem."""
    bo = _bo()
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "BUILD.md").write_text(
        BUILD_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    probs = bo.findings_problems(tmp_path)
    assert probs, "the stock template's Findings section must not pass"


def test_checkpoint_problems_still_flags_the_stock_template(tmp_path):
    """Regression: the Findings section added for Task 9 must not disturb
    STOCK_MARKERS detection -- the stock template (now with an unfilled
    Findings section too) must still be rejected as a checkpoint."""
    bo = _bo()
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "BUILD.md").write_text(
        BUILD_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    probs = bo.checkpoint_problems(tmp_path)
    assert probs and "stock template" in probs[0]


def test_checkpoint_problems_still_accepts_a_real_edited_build_md(tmp_path):
    """A genuinely edited BUILD.md (no stock markers) still passes
    checkpoint_problems -- unaffected by the new Findings section."""
    bo = _bo()
    d = tmp_path / ".specdev"
    d.mkdir(parents=True)
    (d / "BUILD.md").write_text(
        "# Build Plan - Widget\n\n**Feature ID:** FEAT-900\n\nWave 1 green.\n"
        "\n## Findings\n\nThroughput held at 62k events/sec.\n",
        encoding="utf-8")
    assert bo.checkpoint_problems(tmp_path) == []


def test_gen_traceability_does_not_shadow_stdlib_profile():
    """Regression: gen_traceability must not register our sibling profile.py as
    sys.modules['profile'], shadowing the Python standard library's profile
    module (the deterministic profiler). The fix loads the sibling under an
    unambiguous name (_specdev_profile) instead."""
    gt = load_gt()

    # After loading gen_traceability, sys.modules['profile'] should either
    # not exist, or should be the stdlib module (no resolve_all attribute)
    cached_profile = sys.modules.get("profile")
    if cached_profile is not None:
        assert not hasattr(cached_profile, "resolve_all"), \
            "sys.modules['profile'] is our custom module (has resolve_all)"


# ---- Task 10: Agent contracts — smoke mode and charter mode -----

AGENTS = ROOT / "agents"


def test_qa_verifier_documents_smoke_mode():
    text = (AGENTS / "qa-verifier.md").read_text(encoding="utf-8")
    assert "smoke mode" in text.lower()
    assert "at least one test" in text.lower() or "≥1 test" in text
    for floor in ("gitleaks", "secret"):
        assert floor in text.lower(), f"smoke mode must still run {floor}"


def test_qa_verifier_smoke_mode_keeps_the_credential_floor():
    text = (AGENTS / "qa-verifier.md").read_text(encoding="utf-8")
    # Extract the smoke-mode section specifically (from ## Smoke mode to next ## or EOF)
    # The header has parameters in parentheses, so account for the full line
    match = re.search(r"^##\s+Smoke mode.*?\n(.*?)(?=^##|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, "smoke mode section not found"
    smoke_section = match.group(1).lower()

    # Verify the smoke-mode contract:
    # 1. gitleaks/secret scan is unconditional and not disableable
    assert "gitleaks" in smoke_section or "secret scan" in smoke_section, \
        "smoke mode must describe gitleaks/secret scan"
    assert "unconditional" in smoke_section, \
        "smoke mode must state gitleaks is unconditional"
    assert "profile cannot switch it off" in smoke_section or "cannot" in smoke_section, \
        "smoke mode must state gitleaks is not disableable by profile"

    # 2. coverage and --check-gaps are described as skipped
    assert "skip" in smoke_section, \
        "smoke mode must describe what is skipped"
    assert "coverage" in smoke_section, \
        "smoke mode must mention coverage is skipped"
    assert "check-gaps" in smoke_section, \
        "smoke mode must mention --check-gaps is skipped"

    # 3. A zero-test run is a RED verdict
    assert "zero tests" in smoke_section or "collects zero" in smoke_section or \
           ("red" in smoke_section and "test" in smoke_section), \
        "smoke mode must state that zero tests result in RED verdict"


def test_component_builder_documents_charter_mode():
    text = (AGENTS / "component-builder.md").read_text(encoding="utf-8")
    assert "charter" in text.lower()
    assert "smoke test" in text.lower()


# ---- Task 11: SKILL.md — resolve and honour the governance profile ----

SKILL = ROOT / "skills" / "specdev" / "SKILL.md"


def test_skill_resolves_the_profile_before_the_pipeline():
    text = SKILL.read_text(encoding="utf-8")
    assert "profile.py show" in text
    i_unit_first = text.index("## Establish the governed unit FIRST")
    i_profile = text.index("profile.py show")
    i_pipeline = text.index("## The pipeline")
    assert i_unit_first < i_profile < i_pipeline, \
        "the profile section must appear between 'Establish the governed unit FIRST' and 'The pipeline'"


@pytest.mark.parametrize("key", ["spec_bar", "spec_pr", "adrs",
                                 "per_wave_qa", "traceability"])
def test_skill_documents_each_profile_branch(key):
    assert key in SKILL.read_text(encoding="utf-8")


def test_skill_states_the_floor_is_not_negotiable():
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "floor" in text
    assert "findings" in text
