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
