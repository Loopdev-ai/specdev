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


def write_unit_local(root: Path, unit: str, name: str, text: str) -> Path:
    d = root / unit / ".specdev" / "adr"
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


def test_lint_catches_empty_pros_followed_by_cons(tmp_path):
    """Regression: an empty 'Pros:' immediately followed by a 'Cons:' line must
    not swallow the Cons line's text as if it were the Pros content. Before the
    fix, _labelled's post-colon `\\s*` matched the newline plus the next line's
    leading whitespace, so 'Pros:' read as 'Cons: revocation is only ...'."""
    text = GOOD_LOCAL.replace(
        "   - Pros: no shared store; either service verifies alone\n"
        "   - Cons: revocation is only as fast as the token lifetime",
        "   - Pros:\n"
        "   - Cons: revocation is only as fast as the token lifetime",
    )
    errs = lint_text(tmp_path, text, req_ids={"REQ-002", "REQ-005"})
    assert any("empty Pros" in e for e in errs)


def test_lint_catches_empty_positive_followed_by_negative(tmp_path):
    """Same bug in the Consequences block: an empty 'Positive:' before a
    'Negative / risks:' line must not read the Negative line's text as the
    Positive content."""
    text = GOOD_LOCAL.replace(
        "- Positive: no Redis to operate; either service verifies a caller alone.\n"
        "- Negative / risks: a stolen token stays valid for up to 15 minutes.",
        "- Positive:\n"
        "- Negative / risks: a stolen token stays valid for up to 15 minutes.",
    )
    errs = lint_text(tmp_path, text, req_ids={"REQ-002", "REQ-005"})
    assert any("no Positive entry" in e for e in errs)


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


def test_lint_directory_warns_not_errors_on_legacy_adr(tmp_path, capsys):
    """A pre-existing prose-only ADR (no frontmatter) is explicitly NOT
    migrated per the design's Non-goals, and remains valid. Sweeping a
    directory must warn, not fail, on it."""
    write_local(tmp_path, "ADR-001.md", PROSE_ONLY_LOCAL)
    rc = adr.main(["lint", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN:" in out
    assert "ERROR:" not in out


def test_lint_file_still_errors_on_legacy_adr_without_frontmatter(tmp_path, capsys):
    """Linting a single --file means you are authoring/editing it now, so
    missing frontmatter there is a real ERROR, not a warning."""
    p = write_local(tmp_path, "ADR-001.md", PROSE_ONLY_LOCAL)
    rc = adr.main(["lint", "--root", str(tmp_path), "--file", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR:" in out
    assert "frontmatter" in out


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


def test_conflicts_file_infers_unit_from_path_in_monorepo(tmp_path, capsys):
    """Regression: --file must resolve the ADR set it's checked against (and,
    for lint, the spec whose REQ ids are checked) from the file's OWN unit,
    not from --unit's untouched default '.'. Before the fix this silently
    reported an empty shortlist / 0 ADR(s) checked against the wrong (empty)
    directory, a false green in any monorepo."""
    write_unit_local(tmp_path, "svc-a", "ADR-003.md", GOOD_LOCAL)
    b_new = (GOOD_LOCAL.replace("id: ADR-003", "id: ADR-005")
                       .replace("# ADR-003", "# ADR-005"))
    write_unit_local(tmp_path, "svc-b", "ADR-005.md", b_new)
    # Same scopes/relates_to as svc-a's ADR-003 -> should shortlist against it,
    # and must NOT be compared against svc-b's unrelated ADR-005.
    target_text = (GOOD_LOCAL.replace("id: ADR-003", "id: ADR-004")
                             .replace("# ADR-003", "# ADR-004"))
    target_path = write_unit_local(tmp_path, "svc-a", "ADR-004.md", target_text)

    rc = adr.main(["conflicts", "--root", str(tmp_path), "--file", str(target_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ADR-003" in out
    assert "ADR-005" not in out


def test_lint_file_infers_unit_for_req_ids_in_monorepo(tmp_path):
    """Regression: lint --file must check relates_to against ITS OWN unit's
    spec, not --unit's default '.' (which has no spec.md in a monorepo, so
    the unknown-REQ check would have silently no-opped)."""
    (tmp_path / "svc-a" / ".specdev").mkdir(parents=True)
    (tmp_path / "svc-a" / ".specdev" / "spec.md").write_text(
        "REQ-002 and REQ-005", encoding="utf-8")
    (tmp_path / "svc-b" / ".specdev").mkdir(parents=True)
    (tmp_path / "svc-b" / ".specdev" / "spec.md").write_text(
        "REQ-999 only", encoding="utf-8")

    p = write_unit_local(tmp_path, "svc-a", "ADR-003.md", GOOD_LOCAL)
    assert adr.main(["lint", "--root", str(tmp_path), "--file", str(p)]) == 0

    bad = (GOOD_LOCAL.replace("relates_to: [REQ-002, REQ-005]", "relates_to: [REQ-999]")
                     .replace("**Relates to:** REQ-002, REQ-005", "**Relates to:** REQ-999")
                     .replace("id: ADR-003", "id: ADR-006")
                     .replace("# ADR-003", "# ADR-006"))
    p2 = write_unit_local(tmp_path, "svc-a", "ADR-006.md", bad)
    assert adr.main(["lint", "--root", str(tmp_path), "--file", str(p2)]) == 1


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


def _fill_deployment_platform_template() -> str:
    """Take the shipped ADR-deployment-platform.md template as-is and
    mechanically replace its placeholders with plausible content, preserving
    its actual guidance shape (headings, labels, the extra Consequences
    bullets) exactly as authored. Reads from disk so a structural regression
    in the real template (e.g. reverting '## Options' to '## Options
    considered') fails this test."""
    p = ROOT / "assets" / "specdev" / "adr" / "ADR-deployment-platform.md"
    text = p.read_text(encoding="utf-8-sig")
    text = (text
        .replace("id: ADR-deployment-platform", "id: ADR-005")
        .replace("status: proposed          # proposed | accepted | superseded",
                 "status: accepted")
        .replace("date: <YYYY-MM-DD>", "date: 2026-08-03")
        .replace("relates_to: []            # REQs with hosting/scale/compliance implications",
                 "relates_to: [REQ-010]")
        .replace("# ADR-### — Deployment platform", "# ADR-005 — Deployment platform")
        .replace("**Status:** proposed | accepted | superseded by ADR-###",
                 "**Status:** accepted")
        .replace("**Date:**", "**Date:** 2026-08-03")
        .replace("**Relates to:** <REQs with hosting/scale/compliance implications>",
                 "**Relates to:** REQ-010")
        .replace("- Server-side logic vs static/frontend only:",
                 "- Server-side logic vs static/frontend only: a small API plus a static frontend.")
        .replace("- One service or several interdependent:",
                 "- One service or several interdependent: one service.")
        .replace("- Traffic shape (steady / spiky / near-zero):",
                 "- Traffic shape (steady / spiky / near-zero): near-zero at launch.")
        .replace("- Stateful or stateless:",
                 "- Stateful or stateless: stateless.")
        .replace("- Existing infra/accounts the org runs:",
                 "- Existing infra/accounts the org runs: an existing Fly.io account.")
        .replace("- Team ops capacity (who operates it):",
                 "- Team ops capacity (who operates it): one engineer, part-time.")
        .replace("- Compliance / data-residency constraints:",
                 "- Compliance / data-residency constraints: none beyond standard SaaS terms.")
        .replace("1. **<Option A>** — fit / ongoing ops cost",
                 "1. **Fly.io** — fits a small stateless service; pay-per-use; minimal ops")
        .replace("2. **<Option B>** — fit / ongoing ops cost",
                 "2. **AWS ECS Fargate** — fits containerized workloads at any scale")
        .replace("3. **<Option C>** — …",
                 "3. **Kubernetes (EKS)** — fits large multi-service systems")
        .replace("   - Pros: <why it fits>\n   - Cons: <why it doesn't, or the cost>",
                  "   - Pros: near-zero idle cost, one-command deploy\n"
                  "   - Cons: fewer managed add-ons than a hyperscaler", 1)
        .replace("   - Pros: <why it fits>\n   - Cons: <why it doesn't, or the cost>",
                  "   - Pros: deep integration with existing AWS services\n"
                  "   - Cons: meaningfully more ops surface for a near-zero-traffic service", 1)
        .replace("   - Pros: <why it fits>\n   - Cons: <why it doesn't, or the cost>",
                  "   - Pros: maximum flexibility for future growth\n"
                  "   - Cons: disproportionate ops burden for one stateless service", 1)
        .replace("**Chosen:** <platform>  → `deploy.profile.json` target: `<target>`",
                 "**Chosen:** Fly.io  → `deploy.profile.json` target: `fly`")
        .replace("- Positive: <what this choice buys — cost, simplicity, speed to ship>",
                 "- Positive: near-zero idle cost and a one-command deploy path.")
        .replace("- Negative / risks: <ongoing ops burden — who operates it, and what could go wrong>",
                 "- Negative / risks: fewer managed add-ons than a hyperscaler if the product grows.")
        .replace("- Config to scaffold: <Dockerfile / fly.toml / manifests / serverless.yml / …>",
                 "- Config to scaffold: Dockerfile, fly.toml")
        .replace("- Revisit if: <traffic, team, or topology changes that would change the call>",
                 "- Revisit if: traffic grows enough to need multiple regions or services")
    )
    return text


def test_filled_deployment_platform_template_lints_clean(tmp_path):
    """Regression: the shipped ADR-deployment-platform.md template, once
    filled in with the SAME section headings/labels it ships with, must pass
    its own quality gate. Before the fix it failed with 3 errors: '## Options
    considered' (lint wants '## Options') and Consequences bullets that were
    not 'Positive:' / 'Negative / risks:'."""
    text = _fill_deployment_platform_template()
    for placeholder in ("<Option", "<platform>", "<target>", "<YYYY-MM-DD>",
                        "<why it fits>", "<what this choice buys"):
        assert placeholder not in text, f"fixture still has unfilled {placeholder!r}"
    errs = lint_text(tmp_path, text, name="ADR-005.md", req_ids={"REQ-010"})
    assert errs == []


def test_existing_org_adr_still_lints_clean():
    p = ROOT / "governance" / "adr" / "ADR-0001-repo-classification.md"
    assert adr.lint_one(adr.load_adr(p), "org", set()) == []


def test_lint_ignores_placeholder_syntax_inside_code_spans(tmp_path):
    """A backticked `<value>+` is syntax documentation, not a leftover template
    stub — the real-world case is governance/adr/ADR-0001-repo-classification.md."""
    text = GOOD_LOCAL.replace(
        "Two services must both authenticate the same caller without a shared store.",
        "Two services must both authenticate the same caller without a shared "
        "store. Config syntax like `<value>+` and `<owner/name>` is documented "
        "here for reference.",
    )
    assert lint_text(tmp_path, text, req_ids={"REQ-002", "REQ-005"}) == []


def test_lint_still_flags_bare_placeholder_outside_code_spans(tmp_path):
    """A bare, un-backticked placeholder in the same spot must still be caught."""
    text = GOOD_LOCAL.replace(
        "Two services must both authenticate the same caller without a shared store.",
        "Two services must both authenticate the same caller without a shared "
        "store. Fill in <decision title> here.",
    )
    errs = lint_text(tmp_path, text, req_ids={"REQ-002", "REQ-005"})
    assert any("placeholder" in e for e in errs)


def test_lint_double_backtick_spans_are_line_bounded(tmp_path):
    """Regression test: the DOUBLE_BACKTICK_SPAN regex must be line-bounded to
    prevent unmatched `` on different lines from blanketing everything between.

    Before the fix: DOUBLE_BACKTICK_SPAN used re.S (DOTALL), so .*? matched
    across newlines. An unclosed `` in Context and an unclosed `` in
    Consequences would match as one giant span, blanking everything between
    them—including a bare <decision title> placeholder in Decision—so lint
    would not catch it.

    This test has:
    - `` literal code (unclosed) in Context section
    - <decision title> placeholder in Decision section
    - more stuff `` (unclosed) in Consequences section

    Before fix: `` to `` matches across both, hiding the placeholder.
    After fix: line bounds prevent this cross-section blanking.
    """
    text = GOOD_LOCAL.replace(
        "Two services must both authenticate the same caller without a shared store.",
        "Two services must authenticate. `` literal code",
    ).replace(
        "Stateless signed JWTs, with a 15-minute access token lifetime.",
        "<decision title>",
    ).replace(
        "- Follow-ups: revisit if we ever need instant revocation.",
        "- Follow-ups: see more stuff ``",
    )
    errs = lint_text(tmp_path, text, req_ids={"REQ-002", "REQ-005"})
    assert any("placeholder" in e for e in errs), (
        "Should catch <decision title> in Decision even when it lies between "
        "unclosed `` on different lines (the regex must be line-bounded)"
    )


def test_summary_falls_back_to_decisions_first_sentence():
    a = rec("ADR-010", summary="")
    a["sections"] = {"decision": "We chose X because it is simplest. It also costs less."}
    assert adr._summary(a) == "We chose X because it is simplest."


def test_summary_falls_back_to_whole_decision_when_no_sentence_break():
    a = rec("ADR-011", summary="")
    a["sections"] = {"decision": "Stateless signed JWTs"}
    assert adr._summary(a) == "Stateless signed JWTs"


def test_summary_truncates_long_text_with_ellipsis():
    long_text = "This decision " + ("really " * 30) + "matters a lot"
    a = rec("ADR-012", summary="")
    a["sections"] = {"decision": long_text}
    out = adr._summary(a)
    assert len(out) <= 160
    assert out.endswith("...")


def test_summary_prefers_explicit_summary_field():
    a = rec("ADR-013", summary="explicit summary wins")
    a["sections"] = {"decision": "Something else entirely. More text."}
    assert adr._summary(a) == "explicit summary wins"
