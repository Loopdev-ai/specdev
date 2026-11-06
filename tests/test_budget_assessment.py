"""Out of budget brings a person in, with an assessment (design §8.6).

A build that stops for budget short of its terminal state gets facts
(assess_budget.py), a read-only budget-assessor's verdict, and an
ASSESSMENT.md with the resume command. A person decides. A must-only resume
defers untested should/could REQs visibly; a must is never deferred.
"""
import importlib.util
import json
import re
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


gt = load_mod(TOOLS / "gen_traceability.py", "gen_traceability")
bo = load_mod(TOOLS / "build_outcome.py", "build_outcome")
ab = load_mod(TOOLS / "assess_budget.py", "assess_budget_t")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=str(root), check=True,
                          capture_output=True, text=True).stdout.strip()


SPEC = """# Product Spec - Albums

**Feature ID:** FEAT-002

## Requirements

### REQ-001 - Group by date

- **Acceptance:** photos from one day share an album
- **Priority:** must

### REQ-002 - Rename albums

- **Acceptance:** a renamed album keeps its photos
- **Priority:** should

### REQ-003 - Album covers

- **Acceptance:** the first photo is the cover
- **Priority:** could

### REQ-004 - Export

- **Acceptance:** export writes a zip

## Out of Scope

- Sharing.
"""

BREAKER_COST = {"tripped": True, "cost_usd": 10.4,
                "cost_source": "transcript",
                "trip_reason": "accumulated cost $10.40 reached the ceiling "
                               "$10.00"}


@pytest.fixture
def build(tmp_path):
    """A prod build stopped mid-way: REQ-001 tested, the rest not."""
    root = tmp_path / "repo"
    (root / ".specdev").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / ".specdev" / "spec.md").write_text(SPEC)
    (root / "README").write_text("x")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    git(root, "checkout", "-q", "-b", "feat")
    (root / "tests").mkdir()
    (root / "tests" / "test_group.py").write_text(
        "def test_req_001_groups_by_day():  # REQ-001\n    assert True\n")
    (root / ".specdev" / "BUILD.md").write_text("# Build\n\nwave 1 green\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "wave 1")
    return root


def facts(root, **kw):
    args = dict(breaker=BREAKER_COST, attempts_used=1, max_attempts=3,
                spent=[6.0, 4.4], cap=25.0, stop_blocks=0, max_stop_blocks=5)
    args.update(kw)
    args.setdefault("unit", ".")
    return ab.facts(str(root), "FEAT-002", "prod", "main", str(root), **args)


# ---- what counts as a budget stop ------------------------------------------

@pytest.mark.parametrize("kw,needle", [
    ({}, "circuit breaker: accumulated cost"),
    ({"breaker": {"tripped": True, "trip_reason": "wall clock 241 min "
                                                  "reached the ceiling 240 "
                                                  "min"}}, "wall clock"),
    ({"breaker": {"tripped": True, "trip_reason": "tool calls reached 3000 "
                                                  "(limit 3000)"}},
     "tool calls"),
    ({"breaker": {}, "attempts_used": 3}, "all 3 continuation attempts"),
    ({"breaker": {}, "spent": [20.0, 6.0]}, "continuation cap"),
    ({"breaker": {}, "stop_blocks": 5}, "max_stop_blocks"),
])
def test_budget_stops_are_assessed(build, kw, needle):
    f = facts(build, **kw)
    assert f["assess"] is True
    assert any(needle in r for r in f["budget_reasons"]), f["budget_reasons"]


@pytest.mark.parametrize("why", [
    "16 permission denials in 40 tool calls (40% of calls) (floor 15, rate "
    "ceiling 10%)",
    "15 consecutive tool failures (limit 15)",
])
def test_a_broken_run_is_not_a_budget_stop(build, why):
    f = facts(build, breaker={"tripped": True, "trip_reason": why})
    assert f["assess"] is False
    assert "not for budget" in f["not_assessed_because"]
    assert f["other_reasons"]


def test_classification_matches_the_breakers_own_wording():
    """Classify the reasons circuit_breaker.evaluate() actually produces,
    so a change to its wording cannot silently turn a budget stop into a
    non-budget one (or the reverse)."""
    import time
    cb = load_mod(TOOLS / "circuit_breaker.py", "circuit_breaker_ab")
    lim = {"max_permission_denials": 15, "max_denial_rate": 0.1,
           "max_consecutive_tool_failures": 15, "max_cost_usd": 10,
           "max_wall_minutes": 240, "max_tool_calls": 3000}

    def reason(**over):
        st = cb.fresh_state()
        st.update(over)
        return cb.evaluate(st, lim)

    budget = [reason(cost_usd=10.5, cost_source="transcript"),
              reason(started_at=time.time() - 241 * 60),
              reason(tool_calls=3000)]
    broken = [reason(denials=20, tool_calls=40),
              reason(consecutive_failures=15)]
    assert all(budget) and all(broken)
    for why in budget:
        b, o = ab.stop_reasons({"tripped": True, "trip_reason": why},
                               1, 3, 0, 25, 0, 5)
        assert b and not o, why
    for why in broken:
        b, o = ab.stop_reasons({"tripped": True, "trip_reason": why},
                               1, 3, 0, 25, 0, 5)
        assert o and not b, why


def test_no_limit_reached_is_not_assessed(build):
    f = facts(build, breaker={})
    assert f["assess"] is False


def test_a_deliberate_stop_already_needs_a_person(build):
    (build / ".specdev" / "BUILD.md").write_text(
        "# Build\n\n## Stopped\n\nREQ-002 contract conflicts with ADR-003.\n")
    f = facts(build)
    assert f["assess"] is False and "deliberate" in f["not_assessed_because"]


def test_a_finished_build_is_not_assessed(build, monkeypatch):
    monkeypatch.setattr(bo, "local_terminal_problems", lambda *a, **k: [])
    assert facts(build)["assess"] is False


def test_facts_count_progress_by_priority_and_spend(build):
    f = facts(build)
    pri = f["progress"]["by_priority"]
    assert pri["must"] == {"total": 2, "tested": 1, "deferred": 0}, \
        "REQ-004 has no priority, so it counts as a must"
    assert pri["should"]["total"] == 1 and pri["could"]["total"] == 1
    assert f["reqs_done"] == 1 and f["reqs_left"] == 3
    assert f["musts_left"] == 1
    assert f["spend"]["spent_usd"] == 10.4
    assert f["spend"]["rough_cost_to_finish_usd"] == 31.2
    assert f["commits_ahead"] == 1


@pytest.mark.parametrize("bpp,trend", [
    ([], "not-started"), ([3, 0], "clean"), ([4], "converging"),
    ([6, 3, 1], "converging"), ([4, 4, 4], "stalled"), ([2, 5], "diverging"),
    ([5, 2, 3], "diverging"), ([5, 3, 3, 3], "stalled"),
])
def test_review_convergence(bpp, trend):
    assert ab.convergence(bpp) == trend


# ---- the assessor's verdict ------------------------------------------------

VERDICT = {"recommendation": "must-only", "extra_budget_usd": 8,
           "summary": "Musts are close.", "reasoning": "Converging 5-2-1.",
           "remaining": ["REQ-002, REQ-003 unbuilt"], "risks": []}


def fence(obj):
    return f"Recommend must-only.\n\n```json\n{json.dumps(obj)}\n```\n"


@pytest.mark.parametrize("shape", ["array", "lines", "assistant"])
def test_extract_reads_every_execution_file_shape(tmp_path, shape):
    result = {"type": "result", "result": fence(VERDICT)}
    asst = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": fence(VERDICT)}]}}
    f = tmp_path / "exec.json"
    if shape == "array":
        f.write_text(json.dumps([{"type": "system"}, asst, result]))
    elif shape == "lines":
        f.write_text("\n".join(json.dumps(m) for m in
                               [{"type": "system"}, result]))
    else:
        f.write_text(json.dumps([{"type": "system"}, asst]))
    a, problems = ab.extract(f)
    assert problems == [] and a["recommendation"] == "must-only"


@pytest.mark.parametrize("bad,needle", [
    (dict(VERDICT, recommendation="maybe"), "recommendation"),
    (dict(VERDICT, extra_budget_usd=0), "positive number"),
    (dict(VERDICT, extra_budget_usd="lots"), "positive number"),
    (dict(VERDICT, summary=""), "summary"),
    (dict(VERDICT, risks="none"), "risks must be a list"),
])
def test_an_invalid_verdict_is_rejected(tmp_path, bad, needle):
    f = tmp_path / "exec.json"
    f.write_text(json.dumps([{"type": "result", "result": fence(bad)}]))
    a, problems = ab.extract(f)
    assert a is None and any(needle in p for p in problems), problems


def test_stop_needs_no_budget():
    assert ab.assessment_problems(dict(VERDICT, recommendation="stop",
                                       extra_budget_usd=0)) == []


def test_extract_cli_writes_errors_for_render(tmp_path):
    f = tmp_path / "exec.json"
    f.write_text(json.dumps([{"type": "result", "result": "no json here"}]))
    errs = tmp_path / "errs.txt"
    p = subprocess.run([sys.executable, str(TOOLS / "assess_budget.py"),
                        "extract", "--execution-file", str(f), "--out",
                        str(tmp_path / "a.json"), "--errors-out", str(errs)],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "fenced JSON" in errs.read_text()


# ---- what the person reads -------------------------------------------------

def test_render_with_a_verdict_gives_the_case_and_both_resume_commands(build):
    md = ab.render(facts(build), VERDICT, "Loopdev-ai/specdev", "main")
    assert "**Recommendation: must-only** with up to **$8** more" in md
    assert "Converging 5-2-1." in md
    assert "-f resume_budget_usd=8 -f scope=all" in md
    assert "-f resume_budget_usd=8 -f scope=must-only" in md
    assert "Nothing resumes on its own" in md
    assert "| must | 2 | 1 | 0 |" in md
    assert "https://github.com/Loopdev-ai/specdev/actions/workflows/" in md


def test_render_without_a_verdict_still_gives_the_facts(build):
    md = ab.render(facts(build), None, "", "main", ["the assessor did not "
                                                   "run"])
    assert "**No recommendation**" in md and "did not run" in md
    assert "resume_budget_usd=<USD>" in md and "## Facts" in md


def test_render_cli_treats_a_missing_assessment_as_none(build, tmp_path):
    ff = tmp_path / "f.json"
    ff.write_text(json.dumps(facts(build)))
    errs = tmp_path / "e.txt"
    errs.write_text("the assessor did not run\n")
    out = tmp_path / "ASSESSMENT.md"
    p = subprocess.run([sys.executable, str(TOOLS / "assess_budget.py"),
                        "--root", str(build), "render", "--facts", str(ff),
                        "--assessment", str(tmp_path / "missing.json"),
                        "--assessment-errors-file", str(errs),
                        "--out", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "No recommendation" in out.read_text()


def test_unit_appears_in_the_resume_command_only_when_not_root(build):
    f = facts(build)
    assert "-f unit=" not in ab.resume_command(f, "all", 5, "main")
    f["unit"] = "svc/api"
    assert "-f unit=svc/api" in ab.resume_command(f, "all", 5, "main")


# ---- must-only: deferral ---------------------------------------------------

def test_defer_unbuilt_defers_only_untested_should_and_could(build):
    ids = ab.defer_unbuilt(build, "approved by alex (run 1)")
    assert ids == ["REQ-002", "REQ-003"]
    text = (build / ".specdev" / "spec.md").read_text()
    deferred = gt.deferred_reqs(text)
    assert set(deferred) == {"REQ-002", "REQ-003"}
    assert "REQ-001" not in deferred, "tested musts are untouched"
    assert "REQ-004" not in deferred, "no priority means must: never deferred"
    blocks = gt.req_blocks(text)
    assert "**Deferred:** approved by alex" in blocks["REQ-002"]
    assert "**Deferred:**" not in blocks["REQ-004"]
    assert text.count("## Out of Scope") == 1
    assert ab.defer_unbuilt(build, "again") == [], "idempotent"


def test_defer_needs_a_reason(build):
    with pytest.raises(SystemExit):
        ab.defer_unbuilt(build, " ")


def test_gap_check_reports_deferred_reqs_and_still_fails_real_gaps(build):
    def gaps():
        return subprocess.run([sys.executable, str(TOOLS /
                                                   "gen_traceability.py"),
                               "--root", str(build), "--check-gaps"],
                              cwd=str(build), capture_output=True, text=True)
    ab.defer_unbuilt(build, "approved by alex")
    p = gaps()
    assert p.returncode == 1, "REQ-004 is a must with no test: a real gap"
    assert "Deferred (not a gap): REQ-002" in p.stdout
    assert "REQ-004" in p.stdout.split("gap — requirements")[1]
    (build / "tests" / "test_export.py").write_text(
        "def test_req_004():  # REQ-004\n    assert True\n")
    p = gaps()
    assert p.returncode == 0, p.stdout
    assert "(2 deferred)" in p.stdout


def test_verify_requires_deferred_reqs_in_the_pr_body(build):
    ab.defer_unbuilt(build, "approved by alex")
    body = build / ".specdev" / "PR_BODY.md"
    body.write_text("# FEAT-002 - Albums\n\n## Deferred requirements\n\n"
                    "- REQ-002 (should)\n")
    probs = bo.pr_body_problems(str(build))
    assert probs and "REQ-003" in probs[0] and "REQ-002" not in probs[0]
    body.write_text("# FEAT-002 - Albums\n\n## Deferred requirements\n\n"
                    "- REQ-002, REQ-003: deferred by alex\n\n## Notes\n")
    assert bo.pr_body_problems(str(build)) == []


def test_nothing_deferred_needs_no_section(build):
    (build / ".specdev" / "PR_BODY.md").write_text("# FEAT-002 - Albums\n")
    assert bo.pr_body_problems(str(build)) == []


# ---- the workflow and the agent -------------------------------------------

def _wf():
    yaml = pytest.importorskip("yaml")
    text = (ROOT / "assets" / "workflows" / "specdev-build.yml").read_text()
    return yaml.safe_load(text), text


def _steps():
    doc, _ = _wf()
    return {s.get("id") or s.get("name"): s
            for s in doc["jobs"]["build"]["steps"]}


def test_dispatch_inputs_carry_the_persons_decision():
    doc, _ = _wf()
    inputs = doc[True]["workflow_dispatch"]["inputs"]
    assert inputs["resume_budget_usd"]["required"] is False
    assert inputs["scope"]["options"] == ["all", "must-only"]
    assert inputs["scope"]["default"] == "all"


def test_a_resume_starts_from_the_impl_branch_before_the_start_sha():
    doc, _ = _wf()
    names = [s.get("id") or s.get("name") for s in
             doc["jobs"]["build"]["steps"]]
    resume = "Resume from the implementation branch (approved budget)"
    assert names.index(resume) < names.index("start"), \
        "the scope check must measure only this run's changes"
    run = _steps()[resume]["run"]
    assert "build_outcome.py refs" in run and "git checkout" in run
    assert "positive number" in run


def test_the_approved_budget_replaces_the_jobs_ceiling_and_cap():
    run = _steps()["Arm the circuit breaker"]["run"]
    assert 'echo "SPECDEV_MAX_COST_USD=${RESUME_BUDGET}"' in run
    assert 'CAP="${RESUME_BUDGET}"' in run
    assert run.index('CAP="${RESUME_BUDGET}"') < run.index(
        'echo "CONTINUATION_CAP_USD=${CAP}"')


def test_must_only_defers_before_the_coordinator_runs():
    doc, _ = _wf()
    names = [s.get("id") or s.get("name") for s in
             doc["jobs"]["build"]["steps"]]
    step = "Defer untested should/could REQs (must-only resume)"
    assert names.index(step) < names.index("agent1")
    assert "must-only" in _steps()[step]["if"]
    assert "defer-unbuilt" in _steps()[step]["run"]


def test_the_assessor_is_read_only_capped_and_off_the_breaker():
    st = _steps()
    a = st["assessor"]
    args = a["with"]["claude_args"]
    assert "--settings" not in args, \
        "a tripped breaker would deny every call the assessor makes"
    assert "--max-budget-usd" in args
    m = re.search(r'--allowedTools "([^"]+)"', args)
    allowed = set(m.group(1).split(","))
    assert not allowed & {"Write", "Edit", "Bash", "Agent", "Task"}
    assert "Write" in args.split("--disallowedTools")[1]
    assert a.get("continue-on-error") is True
    assert "assess == 'true'" in a["if"]
    assert "budget-assessor.md" in a["with"]["prompt"]


def test_assessment_runs_after_the_impl_push_and_before_the_checkpoint():
    doc, _ = _wf()
    names = [s.get("id") or s.get("name") for s in
             doc["jobs"]["build"]["steps"]]
    assert names.index("impl") < names.index("assess_facts") < \
        names.index("assessor") < names.index("assessment") < \
        names.index("checkpoint")
    assert "ASSESSMENT.md" in _steps()["assessment"]["run"]


def test_both_prompts_tell_the_coordinator_about_an_approved_resume():
    _, text = _wf()
    assert "${{ env.RESUME_NOTE }}" in text
    assert "ASSESSMENT.md says what they approved" in text


def test_new_ci_keys():
    ci = json.loads((ROOT / "assets" / "specdev" / "ci.json").read_text())
    assert ci["assess_budget_stops"] is True
    assert ci["assessment_budget_usd"] == 1


def test_the_assessor_agent_judges_and_never_acts():
    text = (ROOT / "agents" / "budget-assessor.md").read_text()
    fm = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
    tools = {t.strip() for t in re.search(r"^tools:\s*(.+)$", fm,
                                          re.M).group(1).split(",")}
    assert not tools & {"Write", "Edit", "NotebookEdit", "Agent"}
    for needle in ("continue", "must-only", "stop", "A person",
                   "never made by a build", '"extra_budget_usd"'):
        assert needle in text, needle
    block = re.findall(r"```json\n(.*?)\n```", text, re.S)[-1]
    assert ab.assessment_problems(json.loads(block)) == []


@pytest.mark.parametrize("path,needles", [
    ("skills/specdev/SKILL.md", ["budget-assessor", "ASSESSMENT.md",
                                 "## Deferred", "defer-unbuilt"]),
    ("commands/build.md", ["ASSESSMENT.md", "Never defer a REQ yourself"]),
    ("agents/intent-reviewer.md", ["**Deferred:**"]),
    ("assets/specdev/PR_BODY.md", ["## Deferred requirements"]),
    ("README.md", ["budget-assessor", "resume_budget_usd",
                   "assess_budget_stops"]),
])
def test_the_procedure_is_written_where_it_is_followed(path, needles):
    text = (ROOT / path).read_text("utf-8")
    for n in needles:
        assert n in text, f"{path} does not mention {n!r}"
