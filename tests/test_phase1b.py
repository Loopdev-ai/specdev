"""Phase 1b - lessons from Spec Kit (design §8.1-8.4).

§8.1 the component map is complete before the Spec PR; §8.2 up-front
questions are capped, ranked and carry recommended answers; §8.3 common
defaults are never asked; §8.4 vague Acceptance wording is flagged as advice,
never as an error.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "assets" / "specdev" / "tools"
sys.path.insert(0, str(TOOLS))


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


vs = load_mod(TOOLS / "validate_spec.py", "validate_spec_1b")
rl = load_mod(TOOLS / "review_ledger.py", "review_ledger")
rh = load_mod(TOOLS / "review_hook.py", "review_hook_1b")

SPEC = """# Product Spec - Albums

**Feature ID:** FEAT-004
**Status:** draft

## Original Request

Group photos into albums by date.

## Requirements

### REQ-001 - Group by date

- **Acceptance:** {a1}

### REQ-002 - List albums

- **Acceptance:** {a2}

## Out of Scope

- Sharing albums.
"""

ADR = "# ADR-001 - Storage\n\n**Status:** accepted\n\nSQLite.\n"

GOOD = """| Component | Owns (REQs) | Contract / Interface | Depends on |
|-----------|-------------|----------------------|------------|
| grouper (new) | REQ-001 | `group(photos) -> [Album]` | store |
| `api` | REQ-002 | GET /albums -> JSON list | grouper, Postgres (external) |
| store | | | — |
|  |  |  |  |
"""


def unit(tmp_path, components=GOOD, a1="photos taken on 2026-01-02 land in "
         "album `2026-01-02`", a2="GET /albums returns 200 with every album"):
    root = tmp_path / "u"
    (root / ".specdev" / "adr").mkdir(parents=True)
    (root / ".specdev" / "spec.md").write_text(SPEC.format(a1=a1, a2=a2))
    (root / ".specdev" / "adr" / "ADR-001.md").write_text(ADR)
    if components is not None:
        (root / ".specdev" / "components.md").write_text(components)
    return root


def check(root):
    return vs.components_problems(root, ["REQ-001", "REQ-002"])


def cli(root, *args):
    return subprocess.run([sys.executable, str(TOOLS / "validate_spec.py"),
                           "--root", str(root), *args],
                          capture_output=True, text=True)


# ---- §8.1 the component map ------------------------------------------------

def test_a_complete_map_passes(tmp_path):
    assert check(unit(tmp_path)) == []


def test_status_markers_and_code_formatting_do_not_change_names(tmp_path):
    # "grouper (new)" and "`api`" are depended on as "grouper" and "api".
    assert check(unit(tmp_path, GOOD.replace("grouper, Postgres",
                                             "`grouper (new)`, Postgres"))) \
        == []


@pytest.mark.parametrize("table,needle", [
    (GOOD.replace("| REQ-002 |", "| |"), "REQ-002 is owned by no component"),
    (GOOD.replace("REQ-002", "REQ-002, REQ-009"), "REQ-009, which is not in"),
    (GOOD.replace("GET /albums -> JSON list", ""), "has no Contract"),
    (GOOD.replace("`group(photos) -> [Album]`", "<input → output>"),
     "has no Contract"),
    (GOOD.replace("Postgres (external)", "Postgres"),
     "mark an external system as 'Postgres (external)'"),
    (GOOD.replace("| store | | | — |", "| store | | | api |"),
     "dependency cycle"),
    (GOOD.replace("| store |", "| <name> |"), "template row"),
    (GOOD + "| store | | | |\n", "listed twice"),
    ("# Components\n\nTBD\n", "no component table"),
    ("| Component | Owns (REQs) | Contract / Interface | Depends on |\n"
     "|---|---|---|---|\n|  |  |  |  |\n", "has no components"),
])
def test_each_gap_is_named(tmp_path, table, needle):
    problems = check(unit(tmp_path, table))
    assert any(needle in p for p in problems), problems


def test_a_missing_map_is_an_error_only_when_asked(tmp_path):
    root = unit(tmp_path, components=None)
    assert any("not found" in p for p in check(root))
    assert cli(root, "--strict").returncode == 0, \
        "merged specs without components.md must keep passing the base gate"
    p = cli(root, "--strict", "--components")
    assert p.returncode == 1 and "components.md not found" in p.stdout


def test_cli_components_passes_a_complete_unit(tmp_path):
    p = cli(unit(tmp_path), "--strict", "--components")
    assert p.returncode == 0, p.stdout


def test_spec_pr_gate_runs_the_components_check_on_spec_heads_only():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (ROOT / "assets" / "workflows" / "spec-validate.yml").read_text())
    steps = doc["jobs"]["validate"]["steps"]
    comp = [s for s in steps if "--components" in s.get("run", "")]
    assert len(comp) == 1
    assert "startsWith(github.head_ref, 'spec/')" in comp[0]["if"]
    assert "charter" in comp[0]["if"], "a poc charter has no components"
    base = [s for s in steps if "validate_spec.py --strict" in
            s.get("run", "")]
    assert base and "--components" not in base[0]["run"]


# ---- §8.4 vague wording ----------------------------------------------------

def test_vague_acceptance_is_a_note_never_an_error(tmp_path):
    root = unit(tmp_path, a1="albums load fast and are intuitive")
    p = cli(root, "--strict", "--components")
    assert p.returncode == 0, p.stdout
    assert "NOTE:  REQ-001 acceptance uses 'fast', 'intuitive'" in p.stdout


@pytest.mark.parametrize("line", [
    "albums load in under 200 ms",
    "returns `[]` when there are no photos, fast",
    'shows "No albums yet" quickly',
])
def test_a_concrete_line_is_not_flagged(line):
    reqs = [("REQ-001", "")]
    assert vs.vague_notes(reqs, [f"\n- **Acceptance:** {line}\n"]) == []


def test_vague_words_match_whole_words_only():
    reqs = [("REQ-001", "")]
    assert vs.vague_notes(reqs, ["\n- **Acceptance:** breakfast is "
                                 "goodness\n"]) == []


# ---- §8.2 interrogate mode -------------------------------------------------

def coverage(**over):
    c = {k: "clear" for k in rl.INTERROGATE_CATEGORIES}
    c.update(over)
    return c


def q(n=1, **kw):
    f = {"severity": "blocking", "kind": "intent", "where": "REQ-001",
         "summary": f"question {n}", "scenario": "two readings",
         "needs_human": True, "default": "the usual reading"}
    f.update(kw)
    return f


def test_a_well_formed_interrogate_report_passes():
    rep = {"coverage": coverage(data="partial"), "findings": [q(1), q(2)]}
    assert rl.report_problems(rep, strict=True) == []


@pytest.mark.parametrize("rep,needle", [
    ({"coverage": {"scope": "clear"}, "findings": []}, "rate every category"),
    ({"coverage": coverage(data="unsure"), "findings": []},
     "coverage values"),
    ({"coverage": dict(coverage(), vibes="clear"), "findings": []},
     "unknown coverage"),
    ({"coverage": coverage(), "findings": [q(i) for i in range(6)]},
     "ask at most 5"),
    ({"coverage": coverage(), "findings": [q(default="")]},
     "recommended answer"),
    ({"coverage": coverage(), "findings": [q(needs_human=False)]},
     "only questions"),
    ({"coverage": "all good", "findings": []}, "must be an object"),
])
def test_interrogate_contract_is_enforced(rep, needle):
    problems = rl.report_problems(rep, strict=True)
    assert any(needle in p for p in problems), problems


def msg(obj):
    return f"verdict\n\n```json\n{json.dumps(obj)}\n```\n"


def test_hook_keeps_interrogate_reports_apart_and_record_refuses_them(
        tmp_path):
    good = {"coverage": coverage(), "findings": [q()]}
    pl = {"hook_event_name": "SubagentStop", "agent_type": "spec-reviewer",
          "agent_id": "i1", "last_assistant_message": msg(good)}
    assert rh.handle(pl, tmp_path) is None
    assert (tmp_path / "interrogate" / "spec-reviewer--i1.json").exists()
    assert not (tmp_path / "spec").exists(), \
        "record --from-hook must never take it for a review pass"
    bad = dict(pl, agent_id="i2", last_assistant_message=msg(
        {"coverage": coverage(), "findings": [q(i) for i in range(7)]}))
    assert rh.handle(bad, tmp_path)["decision"] == "block"
    with pytest.raises(rl.LedgerError, match="interrogate"):
        rl.record(tmp_path, "spec", "FEAT-004", [good], repo_root=tmp_path)


# ---- §8.2 / §8.3 / §8.1 procedure text ------------------------------------

@pytest.mark.parametrize("path,needles", [
    ("agents/spec-reviewer.md", [
        "## Interrogate mode", "Ask at most 5 questions",
        "scope > security/privacy", "non_functional", "edge_cases",
        "Never ask about common defaults", "Data retention",
        "validate_spec.py` NOTE lines"]),
    ("commands/new-feature.md", [
        "Fill the component map", "--components", "**Recommended:**",
        '"yes"', "**Coverage:**"]),
    ("commands/build.md", ["--components", "do not stop to"]),
    ("skills/specdev/SKILL.md", ["validate_spec.py --components",
                                 "at most 5 ranked questions"]),
    ("assets/specdev/spec.md", ["**Coverage:**"]),
    ("README.md", ["validate_spec.py --components", "at most 5"]),
])
def test_the_procedure_is_written_where_it_is_followed(path, needles):
    text = (ROOT / path).read_text("utf-8")
    for n in needles:
        assert n in text, f"{path} does not mention {n!r}"


def test_spec_template_coverage_line_is_in_the_reviewed_section():
    text = (ROOT / "assets" / "specdev" / "spec.md").read_text("utf-8")
    assert "**Coverage:**" in rl._section(text, "Original Request")


def test_design_records_the_architectural_decision_as_human_gated():
    text = (ROOT / "docs" / "superpowers" / "specs" /
            "2026-11-05-accuracy-with-fewer-human-stops-design.md"
            ).read_text("utf-8")
    assert "architectural choices stay human-gated" in text
