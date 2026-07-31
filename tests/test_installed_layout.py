"""Tests that run against the INSTALLED layout, not the template.

Upstream ships `assets/specdev/**` and `assets/workflows/**` and never installs
itself: the only live workflow in this repo is `.github/workflows/adr-index.yml`.
Every other test binds paths under `assets/` and asserts against template text,
so no test has ever seen the pipeline in the shape an adopter runs it.

That has a consequence worth naming: a whole class of defect is structurally
invisible here. A permissions mismatch between two shipped workflow files is
not a property of either file — it only exists in the pair, and only GitHub's
workflow loader ever evaluates the pair. A caller that grants less than the
reusable workflow it calls requests is rejected at workflow INITIALISATION,
before any `if:` runs, so the whole run ends as `startup_failure` with zero
jobs and no logs, in every mode — including the modes where the offending job
would only have been skipped. Text assertions cannot see it, and an adopter
sees it as "the pipeline does not start".

These tests install the template into a scratch tree and check the pairs.
"""
import json
import shutil
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse workflows")

ROOT = Path(__file__).resolve().parents[1]

# none < read < write. `permissions: read-all|write-all` and the `{}` form are
# handled below; anything else is treated as unknown and reported rather than
# guessed at.
_RANK = {None: 0, "none": 0, "read": 1, "write": 2}


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """The template, laid out the way commands/init.md installs it."""
    root = tmp_path_factory.mktemp("adopter")
    shutil.copytree(ROOT / "assets" / "specdev", root / ".specdev")
    (root / ".github" / "workflows").mkdir(parents=True)
    for wf in (ROOT / "assets" / "workflows").glob("*.yml"):
        shutil.copy(wf, root / ".github" / "workflows" / wf.name)
    (root / ".claude").mkdir()
    shutil.copytree(ROOT / "skills", root / ".claude" / "skills")
    shutil.copytree(ROOT / "agents", root / ".claude" / "agents")
    return root


def _workflows(installed):
    out = {}
    for p in sorted((installed / ".github" / "workflows").glob("*.yml")):
        # `on:` is YAML 1.1's boolean true; PyYAML parses it as the key True.
        out[p.name] = yaml.safe_load(p.read_text(encoding="utf-8"))
    return out


def _perms(block):
    """A `permissions:` block as {scope: level}, or None when unspecified."""
    if block is None:
        return None
    if isinstance(block, str):
        if block == "read-all":
            return {"__all__": "read"}
        if block == "write-all":
            return {"__all__": "write"}
        return {}
    if isinstance(block, dict):
        return {k: str(v) for k, v in block.items()}
    return {}


def _required_by(name, docs, seen=None):
    """Every permission a called workflow needs — its own workflow-level block,
    every job-level block inside it, and the same transitively for any local
    reusable workflow it calls in turn. A job-level block inside a CALLED
    workflow is bounded by the caller's grant just as the workflow-level one
    is, so it counts."""
    seen = seen or set()
    if name in seen or name not in docs:
        return {}
    seen.add(name)
    doc = docs[name] or {}
    need = {}

    def merge(block):
        for scope, level in (_perms(block) or {}).items():
            if _RANK.get(level, 99) > _RANK.get(need.get(scope), 0):
                need[scope] = level

    merge(doc.get("permissions"))
    for job in (doc.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        merge(job.get("permissions"))
        uses = str(job.get("uses") or "")
        if uses.startswith("./.github/workflows/"):
            for scope, level in _required_by(Path(uses).name, docs, seen).items():
                if _RANK.get(level, 99) > _RANK.get(need.get(scope), 0):
                    need[scope] = level
    return need


def _granted(doc, job):
    """A calling job's effective permissions: its own block if it has one, else
    the workflow-level block, else None (the repo default, which is not
    knowable from the file)."""
    if job.get("permissions") is not None:
        return _perms(job["permissions"])
    return _perms(doc.get("permissions"))


def _calls(docs):
    for wf_name, doc in docs.items():
        for job_id, job in ((doc or {}).get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            uses = str(job.get("uses") or "")
            if uses.startswith("./.github/workflows/"):
                yield wf_name, job_id, job, Path(uses).name


def test_every_reusable_call_is_granted_what_the_called_workflow_requests(installed):
    """The finding-2 class. A called workflow may not request a permission the
    calling job does not hold; GitHub rejects the run at initialisation, so the
    `if:` that would have skipped the job never gets to run."""
    docs = _workflows(installed)
    problems = []
    for wf_name, job_id, job, callee in _calls(docs):
        assert callee in docs, f"{wf_name}:{job_id} calls missing {callee}"
        need = _required_by(callee, docs)
        grant = _granted(docs[wf_name], job)
        for scope, level in sorted(need.items()):
            if _RANK.get(level, 0) == 0:
                continue
            if grant is None:
                problems.append(
                    f"{wf_name}: job '{job_id}' calls {callee}, which requests "
                    f"'{scope}: {level}', but the job declares no permissions "
                    f"block and neither does the workflow")
                continue
            have = grant.get(scope, grant.get("__all__"))
            if _RANK.get(have, 0) < _RANK.get(level, 99):
                problems.append(
                    f"{wf_name}: job '{job_id}' calls {callee}, which requests "
                    f"'{scope}: {level}', but the job is granted "
                    f"'{scope}: {have or 'none'}' - the run will end as "
                    f"startup_failure before any job starts")
    assert not problems, "\n".join(problems)


def test_the_permission_check_actually_catches_a_mismatch(installed, tmp_path):
    """Paired with the test above so a refactor cannot quietly stop checking:
    an intentionally under-granted caller MUST be reported."""
    docs = _workflows(installed)
    docs["broken-caller.yml"] = {
        "jobs": {"call": {"uses": "./.github/workflows/deploy-poc.yml",
                          "permissions": {"contents": "read"}}}}
    problems = []
    for wf_name, job_id, job, callee in _calls(docs):
        if wf_name != "broken-caller.yml":
            continue
        need = _required_by(callee, docs)
        grant = _granted(docs[wf_name], job)
        for scope, level in need.items():
            have = grant.get(scope, grant.get("__all__"))
            if _RANK.get(have, 0) < _RANK.get(level, 99):
                problems.append(scope)
    assert "contents" in problems, \
        "the superset check no longer detects an under-granted caller"


def test_every_installed_workflow_parses_and_declares_a_trigger(installed):
    for name, doc in _workflows(installed).items():
        assert isinstance(doc, dict), f"{name} is not a YAML mapping"
        # YAML 1.1: the bare key `on` parses as the boolean True.
        assert True in doc or "on" in doc, f"{name} declares no trigger"
        assert doc.get("jobs"), f"{name} declares no jobs"


def test_required_status_checks_are_individually_requirable(installed):
    """Five gates each shipped a job with id `summary` and no `name:`, so all
    five published a status check literally called `summary`. Branch protection
    matches on that name, so requiring one requires all five ambiguously and
    there is no way to require exactly one. Since the matrix legs' names are
    dynamic, the summary job is the only requirable check per workflow — which
    made the documented branch-protection setup unachievable as shipped."""
    names = {}
    for wf_name, doc in _workflows(installed).items():
        for job_id, job in (doc.get("jobs") or {}).items():
            if job_id != "summary" or not isinstance(job, dict):
                continue
            label = job.get("name")
            assert label, (
                f"{wf_name}: the 'summary' job has no `name:`, so its status "
                f"check is called 'summary' - the same as every other gate's")
            assert label not in names, (
                f"{wf_name} and {names[label]} both publish a status check "
                f"named {label!r}; branch protection cannot tell them apart")
            names[label] = wf_name


def test_workflow_headers_name_the_check_they_tell_adopters_to_require(installed):
    """Each gate's header instructs the adopter to require its summary job. The
    instruction has to name the check that actually appears."""
    for wf_name, doc in _workflows(installed).items():
        job = (doc.get("jobs") or {}).get("summary")
        if not isinstance(job, dict) or not job.get("name"):
            continue
        text = (installed / ".github" / "workflows" / wf_name).read_text("utf-8")
        header = text.split("\non:")[0]
        assert job["name"] in header, (
            f"{wf_name}'s header must name the required check exactly "
            f"({job['name']!r}), or the adopter requires the wrong one")


def test_the_installed_tools_resolve_their_limits_from_the_installed_config(installed):
    """The tools are copied to `.specdev/tools/` and the config to
    `.specdev/ci.json`; the defaults must resolve across that pair, not only
    across `assets/`."""
    import importlib.util
    import subprocess
    import sys

    p = subprocess.run(
        [sys.executable, str(installed / ".specdev" / "tools" / "run_manifest.py"),
         "--root", ".", "breaker-env", "--repo-root", "."],
        cwd=str(installed), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    armed = dict(line.split("=", 1) for line in p.stdout.strip().splitlines())
    shipped = json.loads((installed / ".specdev" / "ci.json").read_text("utf-8"))
    spec = importlib.util.spec_from_file_location(
        "rm_installed", installed / ".specdev" / "tools" / "run_manifest.py")
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)
    for key, var in rm.BREAKER_ENV.items():
        assert var in armed, f"{key} is never exported, so it cannot be armed"
        assert float(armed[var]) == float(shipped[key]), (
            f"{var} armed as {armed[var]} but ci.json says {shipped[key]}")
