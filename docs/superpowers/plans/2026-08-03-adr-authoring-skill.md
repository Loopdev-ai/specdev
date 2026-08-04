# ADR Authoring Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SpecDev skill that interviews the user (or Claude) into a good ADR — local or org — and refuses to write one that is thin or conflicts with an already-accepted decision.

**Architecture:** A new stdlib-only CLI `assets/specdev/tools/adr.py` does the decidable half (id allocation, structural lint, supersession-chain integrity, and an *overlap shortlist*); a new skill `skills/adr/SKILL.md` runs the interview and judges semantic conflict against that shortlist only. Mode (`local` = `.specdev/adr/`, `org` = `governance/adr/`) is auto-detected. Local ADRs gain YAML frontmatter while keeping their existing prose `**Status:**` / `Relates to:` lines, so `validate_spec.py` and `gen_traceability.py` are untouched.

**Tech Stack:** Python 3.10+ stdlib only (argparse, re, json, pathlib, itertools); pytest for tests; Claude Code plugin skill + slash command markdown.

**Spec:** [docs/superpowers/specs/2026-08-03-adr-authoring-skill-design.md](../specs/2026-08-03-adr-authoring-skill-design.md)

## Global Constraints

- **Python 3.10+ stdlib only.** No third-party imports in `adr.py`. Copy the
  existing version guard verbatim from `assets/specdev/tools/arch_config.py:29-36`.
- **Never modify** `validate_spec.py`, `gen_traceability.py`, `check_org_adrs.py`,
  or `governance/tools/gen_adr_index.py`. Back-compat with installed `.specdev/`
  directories is the whole reason local ADRs keep their prose lines.
- **Exit codes:** `0` = pass, `1` = failure. Every failure line is printed as
  `ERROR: <message>`; advisory lines as `WARN: <message>`. This matches every
  other tool in `assets/specdev/tools/`.
- **Paths in output use `Path.as_posix()`** so CI logs read identically on
  Windows and Linux (see `check_org_adrs.py:176`).
- **Read files with `encoding="utf-8-sig"`** to tolerate a Windows-editor BOM;
  write with `encoding="utf-8"`.
- **`--root` is the repo root, `--unit` is the governed unit** (default `.`),
  matching every other SpecDev tool's monorepo convention.
- Commit after every task. Do not squash tasks together.

---

### Task 1: `adr.py` — parsing, mode detection, `next-id`

**Files:**
- Create: `assets/specdev/tools/adr.py`
- Test: `tests/test_adr.py`

**Interfaces:**
- Consumes: `check_org_adrs.entry_matches`, `check_org_adrs.build_value_map`
  (sibling modules in the same `tools/` directory, in both the product-repo and
  governance-repo layouts) — used in Task 4.
- Produces:
  - `parse_frontmatter(text: str) -> dict | None`
  - `as_list(v) -> list[str]`
  - `sections(body: str) -> dict[str, str]` — `'## Heading'` lowercased → body
  - `load_adr(path: Path) -> dict` — the ADR record used by every other function
  - `load_adrs(directory: Path, skip_templates: bool = True) -> list[dict]`
  - `detect_mode(root: Path, unit: str) -> str` — `"local"` | `"org"`
  - `adr_dir(root: Path, unit: str, mode: str) -> Path`
  - `next_id(adrs: list[dict], mode: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_adr.py`:

```python
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "assets" / "specdev" / "tools" / "adr.py"


def load_mod():
    spec = importlib.util.spec_from_file_location("adr", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


adr = load_mod()


GOOD_LOCAL = """---
id: ADR-003
title: Sessions are stateless JWTs
status: accepted
date: 2026-08-03
relates_to: [REQ-002, REQ-005]
scopes: [auth, session]
---

# ADR-003 — Sessions are stateless JWTs

**Status:** accepted
**Relates to:** REQ-002, REQ-005

## Context

Two services must both authenticate the same caller without a shared store.

## Options

1. **Server-side sessions in Redis**
   - Pros: instant revocation
   - Cons: a second stateful dependency to run and back up
2. **Stateless signed JWTs**
   - Pros: no shared store; either service verifies alone
   - Cons: revocation is only as fast as the token lifetime

## Decision

Stateless signed JWTs, with a 15-minute access token lifetime.

## Consequences

- Positive: no Redis to operate; either service verifies a caller alone.
- Negative / risks: a stolen token stays valid for up to 15 minutes.
- Follow-ups: revisit if we ever need instant revocation.
"""

PROSE_ONLY_LOCAL = """# ADR-001 — Postgres is the primary store

**Status:** accepted
**Relates to:** REQ-001

## Context

We need durable relational storage.

## Decision

Postgres 16.
"""


def write_local(root: Path, name: str, text: str) -> Path:
    d = root / ".specdev" / "adr"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


def write_org_repo(root: Path) -> Path:
    d = root / "governance" / "adr"
    d.mkdir(parents=True, exist_ok=True)
    (root / "governance" / "classification.json").write_text(
        '{"axes": {"maturity": {"ordered": true, "values": '
        '{"poc": {"rank": 0}, "dev": {"rank": 1}, "prod": {"rank": 2}}}, '
        '"audience": {"ordered": false, "values": '
        '{"internal": {}, "customer": {}}}}}',
        encoding="utf-8",
    )
    return d


def test_parse_frontmatter_reads_scalars_and_inline_lists():
    fm = adr.parse_frontmatter(GOOD_LOCAL)
    assert fm["id"] == "ADR-003"
    assert fm["status"] == "accepted"
    assert fm["relates_to"] == ["REQ-002", "REQ-005"]
    assert fm["scopes"] == ["auth", "session"]


def test_parse_frontmatter_returns_none_without_a_block():
    assert adr.parse_frontmatter(PROSE_ONLY_LOCAL) is None


def test_sections_splits_on_h2_only():
    secs = adr.sections(GOOD_LOCAL)
    assert set(secs) >= {"context", "options", "decision", "consequences"}
    assert "Stateless signed JWTs" in secs["decision"]


def test_load_adr_reads_frontmatter(tmp_path):
    p = write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    a = adr.load_adr(p)
    assert a["id"] == "ADR-003"
    assert a["status"] == "accepted"
    assert a["relates_to"] == ["REQ-002", "REQ-005"]
    assert a["scopes"] == ["auth", "session"]
    assert a["has_fm"] is True


def test_load_adr_falls_back_to_prose(tmp_path):
    p = write_local(tmp_path, "ADR-001.md", PROSE_ONLY_LOCAL)
    a = adr.load_adr(p)
    assert a["id"] == "ADR-001"
    assert a["title"] == "Postgres is the primary store"
    assert a["status"] == "accepted"
    assert a["relates_to"] == ["REQ-001"]
    assert a["has_fm"] is False


def test_load_adrs_skips_unfilled_templates(tmp_path):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    write_local(
        tmp_path,
        "ADR-999.md",
        "# ADR-999 — <decision title>\n\n"
        "**Status:** proposed | accepted | superseded\n",
    )
    ids = [a["id"] for a in adr.load_adrs(tmp_path / ".specdev" / "adr")]
    assert ids == ["ADR-003"]


def test_detect_mode(tmp_path):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    assert adr.detect_mode(tmp_path, ".") == "local"

    org_root = tmp_path / "gov"
    write_org_repo(org_root)
    assert adr.detect_mode(org_root, ".") == "org"


def test_detect_mode_is_ambiguous_when_both_layers_exist(tmp_path):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    write_org_repo(tmp_path)
    with pytest.raises(SystemExit):
        adr.detect_mode(tmp_path, ".")


@pytest.mark.parametrize(
    "existing, mode, expected",
    [
        ([], "local", "ADR-001"),
        (["ADR-001", "ADR-002"], "local", "ADR-003"),
        (["ADR-001", "ADR-007"], "local", "ADR-008"),   # gaps are not reused
        ([], "org", "ADR-0001"),
        (["ADR-0001", "ADR-0002"], "org", "ADR-0003"),
    ],
)
def test_next_id(existing, mode, expected):
    adrs = [{"id": i} for i in existing]
    assert adr.next_id(adrs, mode) == expected


def test_next_id_cli(tmp_path, capsys):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    rc = adr.main(["next-id", "--root", str(tmp_path)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "ADR-004"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_adr.py -v`
Expected: collection error — `FileNotFoundError` / `spec_from_file_location` on
the missing `assets/specdev/tools/adr.py`.

- [ ] **Step 3: Write `adr.py`**

Create `assets/specdev/tools/adr.py`:

```python
#!/usr/bin/env python3
"""SpecDev ADR authoring support: id allocation, quality lint, conflict checks.

Serves both ADR layers:

  local  <unit>/.specdev/adr/ADR-###.md     — product decisions, linked to REQs
  org    governance/adr/ADR-####-<slug>.md  — org-wide decisions, w/ Conformance

This tool decides only what is decidable: id collisions, supersession-chain
integrity, structural quality, and which accepted ADRs *could* overlap the one
being written. Whether two decisions actually contradict each other is
judgment — made by the `adr` skill against the shortlist printed here, so the
skill reads a handful of ADRs instead of a directory.

Local ADRs carry BOTH YAML frontmatter and the older prose '**Status:**' /
'Relates to:' lines. That redundancy is deliberate: gen_traceability.py and
validate_spec.py scrape the prose, so dropping it would break every installed
.specdev/. `lint` fails on drift between the two, which turns the duplication
into a checked invariant.

Usage:
    python .specdev/tools/adr.py next-id   [--root .] [--unit .] [--mode auto]
    python .specdev/tools/adr.py lint      [--root .] [--unit .] [--file F]
    python .specdev/tools/adr.py conflicts [--root .] [--unit .] [--file F] [--json]
"""
import argparse
import re
import sys
from pathlib import Path

# SpecDev tools use PEP 604 unions (`dict | None`) in annotations, which are
# evaluated at def time and raise TypeError on Python 3.9. macOS ships 3.9.x as
# the system python3, so without this guard every tool dies with an opaque
# "unsupported operand type(s) for |". The message is deliberately pure ASCII.
if sys.version_info < (3, 10):
    raise SystemExit(
        "SpecDev tools require Python 3.10+ (found "
        f"{sys.version_info.major}.{sys.version_info.minor}). "
        "On macOS the system python3 is 3.9.x; install a newer Python or use "
        "a virtualenv. In CI, actions/setup-python with python-version '3.x' "
        "satisfies this."
    )

STATUSES = {"proposed", "accepted", "superseded"}
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
# An unedited template still carries the literal choice line — same probe
# validate_spec.py uses, so both tools agree on what "not a real ADR" means.
TEMPLATE_STATUS = re.compile(r"\*\*Status:\*\*\s*proposed \| accepted \| superseded")
PROSE_STATUS = re.compile(r"\*\*Status:\*\*\s*([a-z-]+)", re.I)
PROSE_RELATES = re.compile(r"Relates to:\*?\*?\s*(.+)")
TITLE_RE = re.compile(r"^#\s*ADR-[\d]+\s*[—:-]\s*(.+?)\s*$", re.M)
ADR_ID_RE = re.compile(r"ADR-\d+")
REQ_RE = re.compile(r"REQ-\d+")


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML: 'key: value' scalars and '[a, b]' inline lists only.

    Deliberately duplicated from governance/tools/gen_adr_index.py: that module
    lives in the governance repo's tree and is absent from a product repo's
    .specdev/tools/, so it cannot be imported from here.
    """
    m = FRONTMATTER.match(text)
    if not m:
        return None
    fm: dict = {}
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].rstrip() if not line.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        key, sep, val = line.partition(":")
        if not sep:
            continue
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            fm[key.strip()] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            fm[key.strip()] = val.strip("'\"")
    return fm


def as_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def sections(text: str) -> dict[str, str]:
    """Map each '## Heading' (lowercased) to its body. '###' is not a section."""
    out: dict[str, str] = {}
    cur, buf = None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1).strip().lower(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def load_adr(path: Path) -> dict:
    """Parse one ADR. Frontmatter wins; prose is the fallback for older ADRs."""
    text = path.read_text(encoding="utf-8-sig")
    m = FRONTMATTER.match(text)
    body = text[m.end():] if m else text
    fm = parse_frontmatter(text) or {}

    id_match = ADR_ID_RE.search(body)
    title_match = TITLE_RE.search(body)
    status_match = PROSE_STATUS.search(body)
    prose_reqs: list[str] = []
    for rel in PROSE_RELATES.findall(body):
        prose_reqs += REQ_RE.findall(rel)

    return {
        "path": path,
        "file": path.name,
        "text": text,
        "body": body,
        "fm": fm,
        "has_fm": bool(fm),
        "is_template": bool(TEMPLATE_STATUS.search(text)),
        "id": fm.get("id") or (id_match.group(0) if id_match else path.stem),
        "title": fm.get("title") or (title_match.group(1) if title_match else ""),
        "status": (fm.get("status") or (status_match.group(1) if status_match else "")).lower(),
        "prose_status": (status_match.group(1) if status_match else "").lower(),
        "relates_to": as_list(fm.get("relates_to")) or prose_reqs,
        "prose_relates_to": prose_reqs,
        "applies_to": as_list(fm.get("applies_to")),
        "scopes": as_list(fm.get("scopes")),
        "supersedes": as_list(fm.get("supersedes")),
        "superseded_by": str(fm.get("superseded_by") or "").strip(),
        "summary": str(fm.get("summary") or "").strip(),
        "sections": sections(body),
    }


def load_adrs(directory: Path, skip_templates: bool = True) -> list[dict]:
    if not directory.is_dir():
        return []
    out = []
    for p in sorted(directory.glob("ADR-*.md")):
        a = load_adr(p)
        if skip_templates and a["is_template"]:
            continue
        out.append(a)
    return out


def adr_dir(root: Path, unit: str, mode: str) -> Path:
    if mode == "org":
        return root / "governance" / "adr"
    return root / unit / ".specdev" / "adr"


def detect_mode(root: Path, unit: str) -> str:
    org = ((root / "governance" / "adr").is_dir()
           and (root / "governance" / "classification.json").exists())
    local = (root / unit / ".specdev" / "adr").is_dir()
    if org and local:
        raise SystemExit(
            "ERROR: this repo holds both an org ADR directory (governance/adr) "
            "and a local one (.specdev/adr) — pass --mode local|org to say "
            "which layer this ADR belongs to."
        )
    if org:
        return "org"
    if local:
        return "local"
    raise SystemExit(
        "ERROR: no ADR directory found. Expected "
        f"{(root / unit / '.specdev' / 'adr').as_posix()} (local) or "
        f"{(root / 'governance' / 'adr').as_posix()} (org)."
    )


def next_id(adrs: list[dict], mode: str) -> str:
    width = 4 if mode == "org" else 3
    nums = [int(a["id"].split("-")[1]) for a in adrs
            if re.fullmatch(r"ADR-\d+", a["id"])]
    return f"ADR-{max(nums, default=0) + 1:0{width}d}"


def resolve_mode(args) -> str:
    if args.mode != "auto":
        return args.mode
    if getattr(args, "file", None):
        parts = Path(args.file).resolve().as_posix()
        if "/governance/adr/" in parts:
            return "org"
        if "/.specdev/adr/" in parts:
            return "local"
    return detect_mode(Path(args.root), args.unit)


def add_common(p):
    p.add_argument("--root", default=".")
    p.add_argument("--unit", default=".")
    p.add_argument("--mode", default="auto", choices=["auto", "local", "org"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SpecDev ADR authoring support")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_common(sub.add_parser("next-id", help="print the next free ADR id"))
    args = ap.parse_args(argv)

    root = Path(args.root)
    mode = resolve_mode(args)

    if args.cmd == "next-id":
        print(next_id(load_adrs(adr_dir(root, args.unit, mode)), mode))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_adr.py -v`
Expected: PASS (14 tests, counting the 5 `next_id` parametrizations).

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/adr.py tests/test_adr.py
git commit -m "feat(adr): ADR parsing, mode detection, and next-id"
```

---

### Task 2: `adr.py lint` — the quality gate

**Files:**
- Modify: `assets/specdev/tools/adr.py`
- Test: `tests/test_adr.py`

**Interfaces:**
- Consumes: `load_adr`, `load_adrs`, `sections`, `adr_dir`, `resolve_mode` (Task 1)
- Produces:
  - `spec_req_ids(root: Path, unit: str) -> set[str]`
  - `lint_one(a: dict, mode: str, req_ids: set[str]) -> list[str]` — error strings
  - `lint` subcommand

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adr.py`:

```python
GOOD_ORG = """---
id: ADR-0002
title: Customer data stays in the EU
status: accepted
applies_to: [customer & dev+]
scopes: [data-handling]
summary: Any repo holding customer data pins its storage and compute to EU regions.
---

# ADR-0002 — Customer data stays in the EU

## Context

Customer contracts commit us to EU-only processing of personal data.

## Options

1. **EU-only regions everywhere**
   - Pros: one rule, easy to audit
   - Cons: higher latency for non-EU users
2. **Per-tenant region pinning**
   - Pros: latency follows the customer
   - Cons: a routing bug becomes a contractual breach

## Decision

EU-only regions for every repo that stores or processes customer data.

## Consequences

- Positive: a single auditable rule; no per-tenant routing to get wrong.
- Negative / risks: non-EU users see higher latency.

## Conformance

- [ ] Every region value in `.specdev/architecture-config.json` starts with `eu-`.
- [ ] No `us-` or `ap-` region appears in deployment manifests.
"""


def drop_section(text: str, heading: str) -> str:
    """Remove one '## Heading' block from an ADR fixture."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            skipping = line.strip().lower() == f"## {heading}".lower()
        if not skipping:
            out.append(line)
    return "".join(out)


def lint_text(tmp_path, text, mode="local", name=None, req_ids=frozenset()):
    d = tmp_path / ("governance/adr" if mode == "org" else ".specdev/adr")
    d.mkdir(parents=True, exist_ok=True)
    name = name or ("ADR-0002-eu.md" if mode == "org" else "ADR-003.md")
    p = d / name
    p.write_text(text, encoding="utf-8")
    return adr.lint_one(adr.load_adr(p), mode, set(req_ids))


def test_lint_accepts_a_good_local_adr(tmp_path):
    assert lint_text(tmp_path, GOOD_LOCAL, req_ids={"REQ-002", "REQ-005"}) == []


def test_lint_accepts_a_good_org_adr(tmp_path):
    assert lint_text(tmp_path, GOOD_ORG, mode="org") == []


def test_lint_requires_frontmatter(tmp_path):
    errs = lint_text(tmp_path, PROSE_ONLY_LOCAL, name="ADR-001.md")
    assert any("frontmatter" in e for e in errs)


def test_lint_rejects_id_filename_mismatch(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL, name="ADR-004.md",
                     req_ids={"REQ-002", "REQ-005"})
    assert any("filename" in e for e in errs)


def test_lint_rejects_bad_status(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace("status: accepted", "status: agreed"),
                     req_ids={"REQ-002", "REQ-005"})
    assert any("status" in e for e in errs)


def test_lint_rejects_placeholder_text(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace("Postgres", "TBD").replace(
        "Stateless signed JWTs, with a 15-minute access token lifetime.", "TBD"),
        req_ids={"REQ-002", "REQ-005"})
    assert any("placeholder" in e for e in errs)


def test_lint_requires_two_options(tmp_path):
    one_option = GOOD_LOCAL.split("2. **Stateless signed JWTs**")[0] + "\n## Decision\n\nJWTs.\n"
    errs = lint_text(tmp_path, one_option, req_ids={"REQ-002", "REQ-005"})
    assert any("at least two options" in e for e in errs)


def test_lint_requires_pros_and_cons_on_each_option(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace(
        "   - Cons: revocation is only as fast as the token lifetime", "   - Cons:"),
        req_ids={"REQ-002", "REQ-005"})
    assert any("Cons" in e for e in errs)


def test_lint_requires_non_empty_decision(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace(
        "Stateless signed JWTs, with a 15-minute access token lifetime.", ""),
        req_ids={"REQ-002", "REQ-005"})
    assert any("Decision" in e for e in errs)


def test_lint_requires_both_consequence_directions(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace(
        "- Negative / risks: a stolen token stays valid for up to 15 minutes.", ""),
        req_ids={"REQ-002", "REQ-005"})
    assert any("Negative" in e for e in errs)


def test_lint_rejects_unknown_req(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL, req_ids={"REQ-002"})
    assert any("REQ-005" in e for e in errs)


def test_lint_skips_req_check_when_no_spec_exists(tmp_path):
    assert lint_text(tmp_path, GOOD_LOCAL, req_ids=set()) == []


def test_lint_detects_status_drift(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace("**Status:** accepted",
                                                  "**Status:** proposed"),
                     req_ids={"REQ-002", "REQ-005"})
    assert any("drift" in e for e in errs)


def test_lint_detects_relates_to_drift(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace("**Relates to:** REQ-002, REQ-005",
                                                  "**Relates to:** REQ-002"),
                     req_ids={"REQ-002", "REQ-005"})
    assert any("drift" in e for e in errs)


def test_lint_org_requires_conformance_items(tmp_path):
    errs = lint_text(tmp_path, drop_section(GOOD_ORG, "Conformance"), mode="org")
    assert any("Conformance" in e for e in errs)


def test_lint_org_rejects_placeholder_conformance_item(tmp_path):
    errs = lint_text(tmp_path, GOOD_ORG.replace(
        "- [ ] Every region value in `.specdev/architecture-config.json` starts with `eu-`.",
        "- [ ] <checkable statement 1>"), mode="org")
    assert any("placeholder" in e or "Conformance" in e for e in errs)


def test_lint_reports_unfilled_template(tmp_path):
    errs = lint_text(
        tmp_path,
        "---\nid: ADR-003\n---\n\n# ADR-003 — <decision title>\n\n"
        "**Status:** proposed | accepted | superseded\n",
    )
    assert any("template" in e for e in errs)


def test_spec_req_ids_reads_active_and_archived_specs(tmp_path):
    base = tmp_path / ".specdev"
    (base / "specs").mkdir(parents=True)
    (base / "spec.md").write_text("REQ-001 and REQ-002", encoding="utf-8")
    (base / "specs" / "FEAT-001-old.md").write_text("REQ-009", encoding="utf-8")
    assert adr.spec_req_ids(tmp_path, ".") == {"REQ-001", "REQ-002", "REQ-009"}


def test_lint_cli_returns_1_on_a_bad_adr(tmp_path):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL.replace("status: accepted",
                                                           "status: agreed"))
    assert adr.main(["lint", "--root", str(tmp_path)]) == 1


def test_lint_cli_returns_0_on_a_clean_directory(tmp_path):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    assert adr.main(["lint", "--root", str(tmp_path)]) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_adr.py -k lint -v`
Expected: FAIL with `AttributeError: module 'adr' has no attribute 'lint_one'`.

- [ ] **Step 3: Implement lint**

Add to `assets/specdev/tools/adr.py`, after `next_id`:

```python
PLACEHOLDER = re.compile(r"<[^<>\n]{2,}>|\bTBD\b|\bTODO\b")
LOCAL_REQUIRED = ("id", "title", "status", "date", "relates_to")
ORG_REQUIRED = ("id", "title", "status", "applies_to", "summary")
OPTION_RE = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*", re.M)
CONFORMANCE_ITEM = re.compile(r"^\s*-\s*\[[ xX]\]\s*(.*)$", re.M)


def spec_req_ids(root: Path, unit: str) -> set[str]:
    """Every REQ id declared by the unit's active + archived specs."""
    base = root / unit / ".specdev"
    paths = [base / "spec.md"]
    if (base / "specs").is_dir():
        paths += sorted((base / "specs").glob("*.md"))
    ids: set[str] = set()
    for p in paths:
        if p.exists():
            ids |= set(REQ_RE.findall(p.read_text(encoding="utf-8-sig")))
    return ids


def _option_blocks(text: str) -> list[str]:
    """Split an '## Options' body into one string per numbered option."""
    starts = [m.start() for m in OPTION_RE.finditer(text)]
    return [text[s:e] for s, e in zip(starts, starts[1:] + [len(text)])]


def _labelled(text: str, label: str) -> str:
    """Content following 'Label:' on its bullet, '' when absent or empty."""
    m = re.search(rf"{label}\s*:\s*(.*)", text, re.I)
    return m.group(1).strip() if m else ""


def lint_one(a: dict, mode: str, req_ids: set[str]) -> list[str]:
    """Structural quality gate for one ADR. Empty list = clean."""
    errs: list[str] = []
    name = a["file"]

    if a["is_template"]:
        return [f"{name}: unfilled template — fill it in or delete it"]

    required = ORG_REQUIRED if mode == "org" else LOCAL_REQUIRED
    if not a["has_fm"]:
        errs.append(f"{name}: no YAML frontmatter — new ADRs must declare "
                    f"{', '.join(required)}")
    else:
        for key in required:
            if not a["fm"].get(key):
                errs.append(f"{name}: frontmatter key '{key}' is missing or empty")

    stem = a["path"].stem
    if a["id"] and not (stem == a["id"] or stem.startswith(a["id"] + "-")):
        errs.append(f"{name}: id '{a['id']}' disagrees with the filename")

    if a["status"] and a["status"] not in STATUSES:
        errs.append(f"{name}: status '{a['status']}' not in {sorted(STATUSES)}")

    for m in PLACEHOLDER.finditer(a["text"]):
        errs.append(f"{name}: placeholder text left in — '{m.group(0)}'")
        break

    secs = a["sections"]
    options = _option_blocks(secs.get("options", ""))
    if len(options) < 2:
        errs.append(f"{name}: '## Options' needs at least two options — an ADR "
                    "with nothing rejected records no decision")
    for block in options:
        title = OPTION_RE.search(block).group(1)
        for label in ("Pros", "Cons"):
            if not _labelled(block, label):
                errs.append(f"{name}: option '{title}' has an empty {label}")

    if not secs.get("decision", "").strip():
        errs.append(f"{name}: '## Decision' is empty")

    consequences = secs.get("consequences", "")
    if not _labelled(consequences, "Positive"):
        errs.append(f"{name}: '## Consequences' has no Positive entry")
    if not _labelled(consequences, r"Negative(?: / risks)?"):
        errs.append(f"{name}: '## Consequences' has no Negative / risks entry — "
                    "a decision with no cost has not been thought through")

    if mode == "local":
        unknown = [r for r in a["relates_to"] if req_ids and r not in req_ids]
        for r in unknown:
            errs.append(f"{name}: relates_to names {r}, which is in no spec")
        if a["has_fm"]:
            if a["prose_status"] and a["prose_status"] != a["status"]:
                errs.append(f"{name}: status drift — frontmatter says "
                            f"'{a['status']}', the '**Status:**' line says "
                            f"'{a['prose_status']}'")
            if set(a["prose_relates_to"]) != set(a["relates_to"]):
                errs.append(f"{name}: relates_to drift — frontmatter has "
                            f"{sorted(a['relates_to'])}, the 'Relates to:' line "
                            f"has {sorted(a['prose_relates_to'])}")
    else:
        items = [i.strip() for i in CONFORMANCE_ITEM.findall(secs.get("conformance", ""))]
        if not items:
            errs.append(f"{name}: '## Conformance' has no '- [ ]' items — org "
                        "ADRs must state what a conforming repo looks like")
        for item in items:
            if not item or PLACEHOLDER.search(item):
                errs.append(f"{name}: Conformance item is a placeholder: '{item}'")

    return errs
```

Then extend `main`. Replace the subparser block and dispatch:

```python
    add_common(sub.add_parser("next-id", help="print the next free ADR id"))
    lint_p = sub.add_parser("lint", help="structural quality gate")
    add_common(lint_p)
    lint_p.add_argument("--file", help="lint one ADR instead of the directory")
    args = ap.parse_args(argv)

    root = Path(args.root)
    mode = resolve_mode(args)

    if args.cmd == "next-id":
        print(next_id(load_adrs(adr_dir(root, args.unit, mode)), mode))
        return 0

    if args.cmd == "lint":
        req_ids = spec_req_ids(root, args.unit) if mode == "local" else set()
        targets = ([load_adr(Path(args.file))] if args.file
                   else load_adrs(adr_dir(root, args.unit, mode)))
        errs = [e for a in targets for e in lint_one(a, mode, req_ids)]
        for e in errs:
            print(f"ERROR: {e}")
        if errs:
            print(f"\nADR lint FAILED: {len(errs)} error(s) in {len(targets)} ADR(s)")
            return 1
        print(f"ADR lint passed ({len(targets)} ADR(s)).")
        return 0
    return 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_adr.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/adr.py tests/test_adr.py
git commit -m "feat(adr): lint — reject thin ADRs and frontmatter/prose drift"
```

---

### Task 3: `adr.py conflicts` — hard errors

**Files:**
- Modify: `assets/specdev/tools/adr.py`
- Test: `tests/test_adr.py`

**Interfaces:**
- Consumes: `load_adrs` (Task 1)
- Produces: `hard_conflicts(adrs: list[dict]) -> list[str]`, and the
  `conflicts` subcommand (shortlist added in Task 4)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adr.py`:

```python
def rec(aid, status="accepted", supersedes=(), superseded_by="", scopes=(),
        relates_to=(), applies_to=(), title="t", summary="s"):
    """A minimal in-memory ADR record, as load_adr would produce."""
    return {
        "id": aid, "file": f"{aid}.md", "title": title, "status": status,
        "supersedes": list(supersedes), "superseded_by": superseded_by,
        "scopes": list(scopes), "relates_to": list(relates_to),
        "applies_to": list(applies_to), "summary": summary,
        "sections": {"decision": "d"},
    }


def test_hard_conflicts_clean_set():
    assert adr.hard_conflicts([rec("ADR-001"), rec("ADR-002")]) == []


def test_hard_conflicts_duplicate_id():
    errs = adr.hard_conflicts([rec("ADR-001"), rec("ADR-001")])
    assert any("duplicate id" in e for e in errs)


def test_hard_conflicts_dangling_supersedes():
    errs = adr.hard_conflicts([rec("ADR-002", supersedes=["ADR-099"])])
    assert any("ADR-099" in e for e in errs)


def test_hard_conflicts_target_still_accepted():
    errs = adr.hard_conflicts([
        rec("ADR-001", status="accepted"),
        rec("ADR-002", supersedes=["ADR-001"]),
    ])
    assert any("still 'accepted'" in e for e in errs)


def test_hard_conflicts_non_reciprocal_pair():
    errs = adr.hard_conflicts([
        rec("ADR-001", status="superseded", superseded_by="ADR-003"),
        rec("ADR-002", supersedes=["ADR-001"]),
    ])
    assert any("reciprocal" in e for e in errs)


def test_hard_conflicts_already_superseded_by_another():
    errs = adr.hard_conflicts([
        rec("ADR-001", status="superseded", superseded_by="ADR-002"),
        rec("ADR-002", supersedes=["ADR-001"]),
        rec("ADR-003", supersedes=["ADR-001"]),
    ])
    assert any("already superseded" in e for e in errs)


def test_hard_conflicts_superseded_without_successor():
    errs = adr.hard_conflicts([rec("ADR-001", status="superseded")])
    assert any("superseded_by" in e for e in errs)


def test_hard_conflicts_successor_without_superseded_status():
    errs = adr.hard_conflicts([
        rec("ADR-001", status="accepted", superseded_by="ADR-002"),
        rec("ADR-002", supersedes=["ADR-001"]),
    ])
    assert any("status" in e for e in errs)


def test_hard_conflicts_valid_supersession_chain_is_clean():
    assert adr.hard_conflicts([
        rec("ADR-001", status="superseded", superseded_by="ADR-002"),
        rec("ADR-002", supersedes=["ADR-001"]),
    ]) == []


def test_conflicts_cli_returns_1_on_hard_error(tmp_path):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    write_local(tmp_path, "ADR-004.md",
                GOOD_LOCAL.replace("id: ADR-003", "id: ADR-004")
                          .replace("# ADR-003", "# ADR-004")
                + "\nsupersedes noise\n")
    # ADR-004 declares a supersession of an id that does not exist
    p = tmp_path / ".specdev" / "adr" / "ADR-004.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "scopes: [auth, session]", "scopes: [auth, session]\nsupersedes: [ADR-099]"),
        encoding="utf-8")
    assert adr.main(["conflicts", "--root", str(tmp_path)]) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_adr.py -k hard_conflicts -v`
Expected: FAIL with `AttributeError: module 'adr' has no attribute 'hard_conflicts'`.

- [ ] **Step 3: Implement `hard_conflicts`**

Add to `assets/specdev/tools/adr.py`, after `lint_one`:

```python
def hard_conflicts(adrs: list[dict]) -> list[str]:
    """Structural conflicts needing no judgment. Empty list = clean."""
    errs: list[str] = []
    by_id: dict[str, dict] = {}
    for a in adrs:
        if a["id"] in by_id:
            errs.append(f"duplicate id {a['id']}: {by_id[a['id']]['file']} and {a['file']}")
            continue
        by_id[a["id"]] = a

    successors: dict[str, list[str]] = {}
    for a in adrs:
        for target in a["supersedes"]:
            successors.setdefault(target, []).append(a["id"])

    for a in adrs:
        for target_id in a["supersedes"]:
            target = by_id.get(target_id)
            if target is None:
                errs.append(f"{a['file']}: supersedes {target_id}, which does not exist")
                continue
            claimants = [c for c in successors.get(target_id, []) if c != a["id"]]
            if claimants:
                errs.append(f"{a['file']}: {target_id} is already superseded by "
                            f"{', '.join(sorted(claimants))} — an ADR is replaced once")
            elif target["status"] != "superseded":
                errs.append(f"{a['file']}: supersedes {target_id}, but {target_id} is "
                            f"still '{target['status']}' — set it to 'superseded'")
            elif target["superseded_by"] != a["id"]:
                errs.append(f"{a['file']}: supersession is not reciprocal — "
                            f"{target_id}.superseded_by is "
                            f"'{target['superseded_by'] or '(unset)'}', expected {a['id']}")

        if a["status"] == "superseded" and not a["superseded_by"]:
            errs.append(f"{a['file']}: status is 'superseded' with no superseded_by — "
                        "record which ADR replaced it")
        if a["superseded_by"]:
            if a["superseded_by"] not in by_id:
                errs.append(f"{a['file']}: superseded_by {a['superseded_by']}, "
                            "which does not exist")
            elif a["status"] != "superseded":
                errs.append(f"{a['file']}: superseded_by is set but status is "
                            f"'{a['status']}' — it must be 'superseded'")

    return errs
```

Register the subcommand in `main` (shortlist output arrives in Task 4):

```python
    conf_p = sub.add_parser("conflicts", help="structural + candidate conflicts")
    add_common(conf_p)
    conf_p.add_argument("--file", help="the ADR being written or changed")
    conf_p.add_argument("--json", action="store_true", help="machine-readable output")
```

```python
    if args.cmd == "conflicts":
        adrs = load_adrs(adr_dir(root, args.unit, mode))
        errs = hard_conflicts(adrs)
        for e in errs:
            print(f"ERROR: {e}")
        if errs:
            print(f"\nADR conflicts FAILED: {len(errs)} structural error(s)")
            return 1
        print(f"No structural conflicts ({len(adrs)} ADR(s)).")
        return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_adr.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/adr.py tests/test_adr.py
git commit -m "feat(adr): conflicts — duplicate ids and supersession-chain integrity"
```

---

### Task 4: `adr.py conflicts` — the overlap shortlist

**Files:**
- Modify: `assets/specdev/tools/adr.py`
- Test: `tests/test_adr.py`

**Interfaces:**
- Consumes: `check_org_adrs.entry_matches(entry, classification, axes, vmap)` and
  `check_org_adrs.build_value_map(axes)` — imported as siblings from the same
  `tools/` directory; `classification` maps each axis to a **set** of values.
- Produces:
  - `all_classifications(axes: dict) -> Iterator[dict[str, set[str]]]`
  - `applies_to_overlap(a_entries, b_entries, axes, vmap) -> dict | None`
  - `shortlist(target: dict, adrs: list[dict], mode: str, axes: dict | None) -> list[dict]`
    — each item `{"id", "file", "title", "summary", "reasons": [str, ...]}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adr.py`:

```python
AXES = {
    "maturity": {"ordered": True,
                 "values": {"poc": {"rank": 0}, "dev": {"rank": 1}, "prod": {"rank": 2}}},
    "audience": {"ordered": False, "values": {"internal": {}, "customer": {}}},
}


def test_shortlist_flags_shared_scope():
    target = rec("ADR-005", scopes=["auth"])
    out = adr.shortlist(target, [rec("ADR-003", scopes=["auth", "session"])], "local", None)
    assert [i["id"] for i in out] == ["ADR-003"]
    assert "auth" in out[0]["reasons"][0]


def test_shortlist_flags_shared_req():
    target = rec("ADR-005", relates_to=["REQ-002"])
    out = adr.shortlist(target, [rec("ADR-003", relates_to=["REQ-002"])], "local", None)
    assert any("REQ-002" in r for r in out[0]["reasons"])


def test_shortlist_ignores_disjoint_adrs():
    target = rec("ADR-005", scopes=["billing"], relates_to=["REQ-009"])
    others = [rec("ADR-003", scopes=["auth"], relates_to=["REQ-002"])]
    assert adr.shortlist(target, others, "local", None) == []


def test_shortlist_ignores_non_accepted_adrs():
    target = rec("ADR-005", scopes=["auth"])
    others = [rec("ADR-003", status="superseded", superseded_by="ADR-004", scopes=["auth"]),
              rec("ADR-004", status="proposed", scopes=["auth"])]
    assert adr.shortlist(target, others, "local", None) == []


def test_shortlist_ignores_itself():
    target = rec("ADR-005", scopes=["auth"])
    assert adr.shortlist(target, [rec("ADR-005", scopes=["auth"])], "local", None) == []


def test_applies_to_overlap_rank_semantics():
    vmap = adr.build_value_map(AXES)
    # 'dev+' covers dev and prod; 'prod' is inside it
    assert adr.applies_to_overlap(["dev+"], ["prod"], AXES, vmap) is not None
    # 'poc' alone never overlaps 'dev+'
    assert adr.applies_to_overlap(["poc"], ["dev+"], AXES, vmap) is None


def test_applies_to_overlap_conjunction_semantics():
    vmap = adr.build_value_map(AXES)
    assert adr.applies_to_overlap(["customer & dev+"], ["customer"], AXES, vmap) is not None
    assert adr.applies_to_overlap(["customer & dev+"], ["internal"], AXES, vmap) is None


def test_applies_to_overlap_all_matches_everything():
    vmap = adr.build_value_map(AXES)
    assert adr.applies_to_overlap(["all"], ["internal & poc"], AXES, vmap) is not None


def test_shortlist_org_uses_applies_to():
    target = rec("ADR-0005", applies_to=["prod"], scopes=["deployment"])
    others = [rec("ADR-0002", applies_to=["customer & dev+"], scopes=["data-handling"]),
              rec("ADR-0003", applies_to=["poc"], scopes=["deployment"])]
    out = adr.shortlist(target, others, "org", AXES)
    ids = [i["id"] for i in out]
    # ADR-0002 overlaps on classification; ADR-0003 shares a scope tag only
    assert set(ids) == {"ADR-0002", "ADR-0003"}


def test_conflicts_cli_prints_shortlist_and_exits_0(tmp_path, capsys):
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    new = (GOOD_LOCAL.replace("id: ADR-003", "id: ADR-005")
                     .replace("# ADR-003", "# ADR-005"))
    p = write_local(tmp_path, "ADR-005.md", new)
    rc = adr.main(["conflicts", "--root", str(tmp_path), "--file", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ADR-003" in out
    assert "shortlist" in out.lower()


def test_conflicts_cli_json_shape(tmp_path, capsys):
    import json as _json
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    new = (GOOD_LOCAL.replace("id: ADR-003", "id: ADR-005")
                     .replace("# ADR-003", "# ADR-005"))
    p = write_local(tmp_path, "ADR-005.md", new)
    adr.main(["conflicts", "--root", str(tmp_path), "--file", str(p), "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["hard_errors"] == []
    assert payload["shortlist"][0]["id"] == "ADR-003"
    assert payload["shortlist"][0]["reasons"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_adr.py -k "shortlist or overlap" -v`
Expected: FAIL with `AttributeError: module 'adr' has no attribute 'shortlist'`.

- [ ] **Step 3: Implement the shortlist**

Add the sibling import to `adr.py`. Final import order in the file: docstring →
`import argparse, itertools, json, re, sys` → `from pathlib import Path` →
version guard (it must run before anything that could fail on 3.9) → this
block:

```python
# entry_matches / build_value_map are the SAME applies_to semantics the org-ADR
# CI gate uses. Importing rather than reimplementing keeps the shortlist and the
# gate from ever disagreeing. check_org_adrs.py sits beside this file in both
# layouts: .specdev/tools/ in a product repo, assets/specdev/tools/ in the
# governance repo.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_org_adrs  # noqa: E402  (vendored sibling module)

build_value_map = check_org_adrs.build_value_map
entry_matches = check_org_adrs.entry_matches
```

`itertools` and `json` are new imports at this point — add them to the import
line from Task 1. Then add after `hard_conflicts`:

```python
def all_classifications(axes: dict):
    """Every concrete classification the scheme can express, one axis value
    each. Schemes are tiny (a handful of values per axis), so enumerating is
    exact and cheaper to reason about than interval arithmetic."""
    names = sorted(axes)
    value_lists = [sorted(axes[n].get("values", {})) for n in names]
    for combo in itertools.product(*value_lists):
        yield {n: {v} for n, v in zip(names, combo)}


def applies_to_overlap(a_entries, b_entries, axes, vmap) -> dict | None:
    """A classification bound by BOTH ADRs, or None. The witness is returned so
    the message can name who is caught by both."""
    for cls in all_classifications(axes):
        if (any(entry_matches(e, cls, axes, vmap) for e in a_entries)
                and any(entry_matches(e, cls, axes, vmap) for e in b_entries)):
            return cls
    return None


def _summary(a: dict) -> str:
    if a.get("summary"):
        return a["summary"]
    decision = a["sections"].get("decision", "").strip().splitlines()
    return decision[0] if decision else ""


def shortlist(target: dict, adrs: list[dict], mode: str, axes: dict | None) -> list[dict]:
    """Accepted ADRs that could contradict `target`. Judgment happens upstream —
    this only narrows the field the skill has to read."""
    vmap = build_value_map(axes) if (mode == "org" and axes) else {}
    out = []
    for other in adrs:
        if other["id"] == target["id"] or other["status"] != "accepted":
            continue
        reasons = []
        shared_scopes = sorted(set(target["scopes"]) & set(other["scopes"]))
        if shared_scopes:
            reasons.append(f"shares scope(s): {', '.join(shared_scopes)}")
        if mode == "local":
            shared_reqs = sorted(set(target["relates_to"]) & set(other["relates_to"]))
            if shared_reqs:
                reasons.append(f"both decide {', '.join(shared_reqs)}")
        elif axes:
            witness = applies_to_overlap(target["applies_to"], other["applies_to"],
                                         axes, vmap)
            if witness:
                where = ", ".join(f"{a}: {next(iter(witness[a]))}" for a in sorted(witness))
                reasons.append(f"both bind ({where})")
        if reasons:
            out.append({"id": other["id"], "file": other["file"],
                        "title": other["title"], "summary": _summary(other),
                        "reasons": reasons})
    return out
```

Replace the `conflicts` dispatch in `main`:

```python
    if args.cmd == "conflicts":
        adrs = load_adrs(adr_dir(root, args.unit, mode))
        errs = hard_conflicts(adrs)
        axes = None
        if mode == "org":
            scheme = root / "governance" / "classification.json"
            if scheme.exists():
                axes = json.loads(scheme.read_text(encoding="utf-8-sig"))["axes"]

        items = []
        if args.file:
            target = load_adr(Path(args.file))
            items = shortlist(target, adrs, mode, axes)

        if args.json:
            print(json.dumps({"hard_errors": errs, "shortlist": items}, indent=2))
            return 1 if errs else 0

        for e in errs:
            print(f"ERROR: {e}")
        if errs:
            print(f"\nADR conflicts FAILED: {len(errs)} structural error(s)")
            return 1
        if not args.file:
            print(f"No structural conflicts ({len(adrs)} ADR(s)). "
                  "Pass --file to shortlist overlap candidates.")
            return 0
        if not items:
            print(f"No structural conflicts and an empty shortlist "
                  f"({len(adrs)} ADR(s) checked).")
            return 0
        print(f"Shortlist — {len(items)} accepted ADR(s) overlap this one; "
              "read their Decision sections and rule on each:")
        for i in items:
            print(f"  {i['id']} ({i['file']}) — {i['title']}")
            print(f"      why: {'; '.join(i['reasons'])}")
            if i["summary"]:
                print(f"      says: {i['summary']}")
        return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_adr.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/adr.py tests/test_adr.py
git commit -m "feat(adr): overlap shortlist for scopes, REQs, and applies_to"
```

---

### Task 5: Templates + the back-compat proof

**Files:**
- Modify: `assets/specdev/adr/ADR-001.md`
- Modify: `assets/specdev/adr/ADR-deployment-platform.md`
- Modify: `governance/adr/TEMPLATE.md`
- Test: `tests/test_adr.py`

**Interfaces:**
- Consumes: `load_adr`, `lint_one` (Tasks 1–2); `gen_traceability.parse_adrs`
  and `validate_spec` loaded the same way `load_mod()` loads `adr.py`.
- Produces: nothing new — this task proves the format is safe.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adr.py`:

```python
def load_tool(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "assets" / "specdev" / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_new_format_is_still_read_by_gen_traceability(tmp_path):
    """The prose 'Relates to:' line is what the traceability matrix scrapes."""
    gt = load_tool("gen_traceability")
    write_local(tmp_path, "ADR-003.md", GOOD_LOCAL)
    by_req = gt.parse_adrs(tmp_path / ".specdev" / "adr")
    assert by_req["REQ-002"] == {"ADR-003"}
    assert by_req["REQ-005"] == {"ADR-003"}


def test_new_format_counts_as_accepted_for_validate_spec():
    """validate_spec.py greps the prose '**Status:** accepted' line."""
    import re as _re
    assert _re.search(r"\*\*Status:\*\*\s*accepted", GOOD_LOCAL, _re.I)
    assert not _re.search(
        r"\*\*Status:\*\*\s*proposed \| accepted \| superseded", GOOD_LOCAL)


def test_shipped_local_templates_are_still_skipped_as_templates():
    for name in ("ADR-001.md", "ADR-deployment-platform.md"):
        p = ROOT / "assets" / "specdev" / "adr" / name
        a = adr.load_adr(p)
        assert a["is_template"] is True, f"{name} must keep its placeholder Status line"
        assert a["has_fm"] is True, f"{name} must ship the frontmatter block"


def test_shipped_org_template_documents_supersession():
    text = (ROOT / "governance" / "adr" / "TEMPLATE.md").read_text(encoding="utf-8-sig")
    assert "supersedes:" in text
    assert "superseded_by:" in text


def test_existing_org_adr_still_lints_clean():
    p = ROOT / "governance" / "adr" / "ADR-0001-repo-classification.md"
    assert adr.lint_one(adr.load_adr(p), "org", set()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_adr.py -k "template or gen_traceability or lints_clean" -v`
Expected: FAIL — `test_shipped_local_templates_are_still_skipped_as_templates`
fails on `has_fm is True`; `test_shipped_org_template_documents_supersession`
fails on the missing keys.

- [ ] **Step 3: Update the three templates**

Prepend to `assets/specdev/adr/ADR-001.md` (everything below the existing
`# ADR-001 — <decision title>` line stays exactly as it is):

```markdown
---
id: ADR-001
title: <decision title>
status: proposed          # proposed | accepted | superseded
date: <YYYY-MM-DD>
relates_to: [REQ-001]     # every REQ this decision serves
scopes: []                # free tags, e.g. [auth, storage, deployment]
supersedes: []            # ADR ids this replaces (optional)
superseded_by:            # set together with status: superseded (optional)
---
```

Do the same for `assets/specdev/adr/ADR-deployment-platform.md`, using
`id: ADR-deployment-platform`, `scopes: [deployment]`, and whatever
`relates_to` placeholder that file already implies. **Keep the literal
`**Status:** proposed | accepted | superseded` line in both** — that line is
how `validate_spec.py:90` and `adr.py` recognise an unfilled template.

In `governance/adr/TEMPLATE.md`, add two keys to the frontmatter block, after
`scopes`:

```yaml
supersedes: []          # ADR ids this replaces (optional)
superseded_by:          # the ADR that replaced this one; set with status: superseded
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_adr.py -v`
Expected: PASS.

Then confirm nothing downstream broke:

Run: `python -m pytest tests/ -v`
Expected: PASS — the full suite, including `test_specdev_ci.py` and
`test_adoption_findings.py`.

Run: `python governance/tools/gen_adr_index.py --check`
Expected: `ADR index OK (1 ADRs).` — `TEMPLATE.md` is not matched by the
`ADR-*.md` glob, so the index is unaffected.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/adr/ governance/adr/TEMPLATE.md tests/test_adr.py
git commit -m "feat(adr): frontmatter in the shipped ADR templates, back-compat proven"
```

---

### Task 6: The `adr` skill and `/specdev:adr` command

**Files:**
- Create: `skills/adr/SKILL.md`
- Create: `commands/adr.md`

**Interfaces:**
- Consumes: `adr.py next-id | lint | conflicts --json` (Tasks 1–4), the
  templates (Task 5).
- Produces: the skill name `adr` (invoked as `specdev:adr`) referenced by
  Task 7's wiring.

- [ ] **Step 1: Write `skills/adr/SKILL.md`**

```markdown
---
name: adr
description: Author an architecture decision record — local (.specdev/adr/) or org (governance/adr/) — through a guided interview, and prove it does not conflict with an already-accepted decision. Use when recording an architectural decision, when the specdev pipeline reaches its Architecture step, or when superseding an existing ADR. Trigger on "ADR", "architecture decision", "record this decision", "supersede ADR", "decision record".
---

# adr — guided ADR authoring with conflict detection

An ADR that lists no rejected option records no decision, and an ADR that
silently contradicts an accepted one is worse than none. This skill interviews
for the first and mechanically prevents the second.

You drive `adr.py`; you never skip it. The tool decides what is decidable —
ids, structure, supersession chains, and which accepted ADRs *could* overlap.
You judge the one thing it cannot: whether two decisions actually contradict.

## Locate the tool and the layer

`adr.py` is at `.specdev/tools/adr.py` in a product repo, or
`assets/specdev/tools/adr.py` in a governance repo. If neither exists, fall
back to `${CLAUDE_PLUGIN_ROOT}/assets/specdev/tools/adr.py`.

Mode is detected by the tool:

- **local** — `<unit>/.specdev/adr/ADR-###.md`. A product decision, linked to
  REQs. In a monorepo, run `python .specdev/tools/units.py list` first and pass
  `--unit <unit>`; the tools always live at the repo root.
- **org** — `governance/adr/ADR-####-<slug>.md`. An org-wide decision binding
  repos by classification, carrying a **Conformance** section that the
  `adr-checker` agent verifies.

If the tool reports both layers exist, ask which one this decision belongs to.
A decision that binds only this product is local; one that binds other repos is
org.

## The interview — one question at a time

Ask these in order. Do not batch them, and do not write anything until you have
answers to all of them.

1. **What is the decision, in one present-tense sentence?**
   "Sessions are stateless JWTs", not "we should probably look at JWTs".
2. **What forces it?** Constraints, deadlines, existing architecture,
   non-functional requirements. This becomes **Context**.
3. **Local: which REQs does it serve?** Get concrete `REQ-###` ids from
   `.specdev/spec.md`.
   **Org: who does it bind?** An `applies_to` list against
   `governance/classification.json` — entries are OR, `&` is AND, `value+` is
   that rank and above on an ordered axis.
4. **What did you seriously consider and reject, and why?** Push for at least
   two options with real Pros *and* Cons. One option means the decision has not
   been made; say so once. If the user stands by it, write the ADR
   `status: proposed`, not `accepted`.
5. **What does this cost you?** Negative consequences, risks, follow-ups. An
   ADR whose Consequences are all positive has not been thought through.
6. **Org only: how would a checker prove a repo conforms?** Each answer is one
   Conformance item naming a file that exists, a config value, or a pattern
   present or absent in code. "Teams should be careful" is not checkable.

**When you are the author** (the specdev pipeline reached its Architecture step
and there is no human in the loop for this decision), answer the ladder from
the spec and the architecture context yourself, and ask the user only where the
spec is silent. Nothing else relaxes: the same lint and the same conflict gate
apply.

## Write it

1. `python .specdev/tools/adr.py next-id [--unit <u>]` for the id.
2. Copy the matching template — `.specdev/adr/ADR-001.md` (local) or
   `governance/adr/TEMPLATE.md` (org) — and fill every field from the
   interview. Never invent a different shape.
3. **Local ADRs carry the frontmatter AND the prose `**Status:**` /
   `**Relates to:**` lines.** Both, always, saying the same thing:
   `gen_traceability.py` and `validate_spec.py` read the prose, and `lint`
   fails on drift between the two.
4. `python .specdev/tools/adr.py lint --file <path>`. Fix what it reports.
   **Do not hand the user a file that has not passed lint.**

## Prove it does not conflict

Run `python .specdev/tools/adr.py conflicts --file <path> --json`.

**Hard errors** (exit 1) are structural: duplicate ids, a `supersedes` pointing
nowhere, a half-finished supersession. Fix them; they are never acceptable.

**The shortlist** is your reading list, and nothing else is. Open ONLY the
shortlisted ADRs, read their **Decision** sections, and rule on each:
contradiction or coexistence. Do not read the whole ADR directory — the
shortlist exists so you don't.

On a real contradiction, land on exactly one of these. Never write the ADR
anyway:

- **Revise** the new ADR to agree with the accepted one.
- **Supersede** the old one — one atomic pair of edits: `supersedes: [ADR-00X]`
  on the new ADR, and on the old one `status: superseded` +
  `superseded_by: <new id>` in the frontmatter *and* `**Status:** superseded`
  in the prose. Then re-run `conflicts` to prove the chain is reciprocal.
- **Narrow** `scopes` / `applies_to` so the overlap genuinely disappears — only
  when the two decisions really do address different things. Narrowing scope to
  dodge a check you would otherwise fail is the one move you must not make.

## Local ADRs must also respect the org layer

If `.specdev/org.json` is configured, a local decision can contradict a binding
org ADR. Check cheaply: read `.specdev/adr/org-compliance.json` and fetch the
org index once —
`curl -fsSL https://raw.githubusercontent.com/<governance_repo>/<ref>/<path>/index.json`
(add `-H "Authorization: Bearer $GOVERNANCE_TOKEN"` for a private repo). The
`summary` line of each applicable ADR is enough to spot a contradiction; do not
read org ADR bodies into this thread.

If the local decision contradicts a binding org ADR, either revise it, or — if
the deviation is deliberate and justified — write the local ADR as the
documented deviation and tell the user the `adr-checker` agent must record
`superseded-by-local` before the next PR. **Do not dispatch `adr-checker`
yourself**: authoring is this skill's job, verification is that agent's, and
the specdev pipeline dispatches it at the Architecture step and before every PR.

## Finish

- **Org mode only:** run `python governance/tools/gen_adr_index.py` and commit
  the regenerated `index.json` + `INDEX.md`. An org ADR that is not in the index
  binds nobody.
- **Local mode:** run `python .specdev/tools/validate_spec.py` and confirm the
  new ADR is counted.
- Report: the id, the file, what it decides, what it supersedes if anything,
  and any shortlisted ADR you ruled *not* in conflict — with your reason. That
  last line is the part a reviewer cannot reconstruct.

## Guardrails

- Never write an ADR that has not passed `lint`.
- Never write an ADR while `conflicts` reports a hard error.
- Never edit `org-compliance.json` — only the `adr-checker` agent writes it.
- Never let a local ADR supersede an org ADR. The only path is a documented
  deviation plus a `superseded-by-local` manifest entry.
- Renumbering an existing ADR breaks the traceability matrix. Supersede, never
  rewrite history.
```

- [ ] **Step 2: Write `commands/adr.md`**

```markdown
---
description: Author an architecture decision record (local or org) with a guided interview and conflict checks.
argument-hint: [decision in one sentence]
---

Author an ADR for: **$ARGUMENTS**

Use the `adr` skill. In short:

1. Locate `adr.py` (`.specdev/tools/` in a product repo, `assets/specdev/tools/`
   in a governance repo) and let it detect the layer; in a monorepo, pick the
   unit with `python .specdev/tools/units.py list` and pass `--unit <unit>`.
2. Run the interview — decision, forces, REQs (or `applies_to`), rejected
   options, costs, and Conformance items for an org ADR. One question at a
   time. If `$ARGUMENTS` already answers a question, confirm rather than re-ask.
3. Allocate the id with `adr.py next-id`, fill the matching template, and pass
   `adr.py lint --file <path>` before showing the user anything.
4. Run `adr.py conflicts --file <path> --json`, read only the shortlisted ADRs,
   and resolve any real contradiction by revising, superseding, or narrowing
   scope — never by writing it anyway.
5. Org mode: regenerate the index with
   `python governance/tools/gen_adr_index.py` and commit it.

Do not open a PR automatically. Report the id, the file, and any shortlisted
ADR you ruled not in conflict, with the reason.
```

- [ ] **Step 3: Verify the skill loads**

Run: `python -c "import pathlib,re; t=pathlib.Path('skills/adr/SKILL.md').read_text(encoding='utf-8'); m=re.match(r'^---\n(.*?)\n---\n', t, re.S); assert m, 'no frontmatter'; assert 'name: adr' in m.group(1); assert 'description:' in m.group(1); print('SKILL.md frontmatter OK')"`
Expected: `SKILL.md frontmatter OK`

- [ ] **Step 4: Commit**

```bash
git add skills/adr/SKILL.md commands/adr.md
git commit -m "feat(adr): the adr skill and /specdev:adr command"
```

---

### Task 7: Wire it into the pipeline

**Files:**
- Modify: `skills/specdev/SKILL.md:57-78` (step 4) and `:106-116` (step 7)
- Modify: `commands/new-feature.md:36-37`
- Modify: `README.md`
- Modify: `.claude-plugin/plugin.json`

**Interfaces:**
- Consumes: the skill name `adr` from Task 6.
- Produces: nothing further; this is the last task.

- [ ] **Step 1: Mandate the skill in specdev step 4**

In `skills/specdev/SKILL.md`, replace the first sentence of step 4:

```markdown
4. **Architecture.** Record decisions as `.specdev/adr/ADR-###.md` with
   `Relates to: REQ-###`. Skip only if the change fits existing architecture.
```

with:

```markdown
4. **Architecture.** Record every decision by invoking the **`adr`** skill —
   it runs the decision interview, allocates the id, lints the result, and
   proves the ADR does not conflict with an already-accepted one. **Do not
   hand-write a file into `.specdev/adr/`**: a hand-written ADR skips the
   conflict check, which is the whole point of the artifact. Skip the step
   entirely only if the change fits existing architecture.
```

- [ ] **Step 2: Route the two other ADR-writing paths through the skill**

In the same step 4, in the *New product* bullet, change:

```markdown
     Record it in `adr/ADR-deployment-platform.md`, then set
```

to:

```markdown
     Record it via the **`adr`** skill (scope tag `deployment`), then set
```

In step 7, change the deviation clause:

```markdown
   amend the local ADRs for a justified deviation)
```

to:

```markdown
   amend the local ADRs for a justified deviation — via the **`adr`** skill,
   so the deviation ADR is linted and conflict-checked like any other)
```

In `commands/new-feature.md`, replace the closing two lines:

```markdown
Then for non-trivial architecture, offer to draft an `ADR-###` under
`.specdev/adr/` with `Relates to:` the affected REQs.
```

with:

```markdown
Then for non-trivial architecture, offer to draft an ADR — invoke the `adr`
skill (or `/specdev:adr`) rather than writing the file directly, so the
decision is interviewed, linted, and checked against the ADRs already accepted.
```

- [ ] **Step 3: Document it in the README**

In the component table that contains the `governance/` row (around
`README.md:75`), add a row for the skill, matching the existing table's column
shape. Then add a section after the *Org ADR governance* section
(`README.md:213`):

```markdown
## Writing ADRs

`/specdev:adr` (skill: `adr`) authors both kinds of decision record — local
`.specdev/adr/ADR-###.md` and org `governance/adr/ADR-####-<slug>.md` — and is
the only supported way to write one. It interviews for the parts an ADR is
actually for (what you rejected, and what the decision costs you), then runs
`.specdev/tools/adr.py`:

| Command | What it decides |
|---------|-----------------|
| `adr.py next-id` | the next free id, per layer |
| `adr.py lint --file F` | structure: two real options, non-empty consequences, a linked REQ, no placeholder text, no frontmatter/prose drift |
| `adr.py conflicts --file F` | hard errors (duplicate ids, broken supersession chains) plus a **shortlist** of accepted ADRs that overlap this one on scope, REQ, or `applies_to` |

The tool narrows; the skill judges. A real contradiction is resolved by
revising the new ADR, superseding the old one, or narrowing scope — never by
writing it anyway.

Local ADRs written this way carry YAML frontmatter *and* the older prose
`**Status:**` / `**Relates to:**` lines, so `gen_traceability.py` and
`validate_spec.py` keep working unchanged; `lint` fails if the two ever drift
apart.
```

- [ ] **Step 4: Bump the plugin version**

In `.claude-plugin/plugin.json`, set `"version": "0.7.0"` and append to the
`description`: ` Includes the adr skill for guided, conflict-checked
architecture decision records.`

- [ ] **Step 5: Verify the whole suite and the wiring**

Run: `python -m pytest tests/ -v`
Expected: PASS.

Run: `python -c "import json,pathlib; d=json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text(encoding='utf-8')); assert d['version']=='0.7.0'; assert 'adr skill' in d['description']; print('plugin.json OK')"`
Expected: `plugin.json OK`

Run: `python governance/tools/gen_adr_index.py --check`
Expected: `ADR index OK (1 ADRs).`

Run: `git grep -n "hand-write a file into" skills/specdev/SKILL.md`
Expected: one match in step 4.

- [ ] **Step 6: Commit**

```bash
git add skills/specdev/SKILL.md commands/new-feature.md README.md .claude-plugin/plugin.json
git commit -m "feat(adr): route every ADR through the adr skill; docs and version bump"
```

---

## Self-review notes

- **Spec coverage.** Goals map to tasks: both layers → Tasks 1, 4, 6;
  interview + lint → Tasks 2, 6; structural + shortlist conflict detection →
  Tasks 3, 4; forced resolution → Task 6; zero breakage → Task 5.
- **Moved check.** The spec lists "`supersedes` / `superseded_by` naming an id
  with no corresponding file" under `lint`. It is inherently cross-file, so it
  lives in `conflicts` (Task 3) instead — `lint` stays a per-file check.
- **Non-goals honoured.** No CI wiring, no ADR migration, no `adr-checker`
  dispatch from the skill, no edits to `validate_spec.py`,
  `gen_traceability.py`, `check_org_adrs.py`, or `gen_adr_index.py`.
