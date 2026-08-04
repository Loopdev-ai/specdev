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
