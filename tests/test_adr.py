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
    assert any("status 'agreed' not in" in e for e in errs)


def test_lint_rejects_placeholder_text(tmp_path):
    errs = lint_text(tmp_path, GOOD_LOCAL.replace(
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
