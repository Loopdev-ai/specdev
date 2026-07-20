#!/usr/bin/env python3
"""Compile the org ADR index for the SpecDev governance layer.

Parses YAML frontmatter from governance/adr/ADR-*.md, validates it against
governance/classification.json, and writes:

  governance/adr/index.json  — machine index (single fetch for consumers):
                               classification scheme + per-ADR metadata and a
                               sha256 of the ADR file, so product repos can
                               detect stale verifications without pulling
                               every ADR body.
  governance/adr/INDEX.md    — human table.

Usage:
    python governance/tools/gen_adr_index.py [--root .] [--check]

--check validates and exits 1 if any frontmatter is invalid OR the committed
index.json is stale (differs from what would be regenerated). Wire it as a
required check in this repo's CI (adr-index.yml) so a bad or unindexed org
ADR can never merge.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REQUIRED = ("id", "title", "status", "applies_to", "summary")
STATUSES = {"proposed", "accepted", "superseded"}
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML: 'key: value' scalars and '[a, b]' inline lists only."""
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


def build_value_map(axes: dict) -> tuple[dict, list[str]]:
    """Map each value name -> its axis; error on duplicates across axes."""
    vmap: dict = {}
    errs: list[str] = []
    for axis, adef in axes.items():
        for v, vdef in adef.get("values", {}).items():
            if v in vmap:
                errs.append(f"classification.json: value '{v}' defined on both "
                            f"'{vmap[v]}' and '{axis}' — value names must be unique across axes")
            vmap[v] = axis
            if adef.get("ordered") and not isinstance(vdef.get("rank"), int):
                errs.append(f"classification.json: ordered axis '{axis}' value '{v}' has no integer rank")
    return vmap, errs


def applies_to_errors(entry: str, axes: dict, vmap: dict) -> list[str]:
    """Validate one applies_to entry: 'all', or '&'-joined conditions of
    '<value>' / '<value>+' ('+' only on ordered axes)."""
    if entry == "all":
        return []
    errs = []
    for cond in (c.strip() for c in entry.split("&")):
        val = cond[:-1] if cond.endswith("+") else cond
        if not val or val not in vmap:
            errs.append(f"applies_to condition '{cond}' names no known classification value")
        elif cond.endswith("+") and not axes[vmap[val]].get("ordered"):
            errs.append(f"applies_to condition '{cond}': axis '{vmap[val]}' is unordered — '+' is invalid")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--check", action="store_true",
                    help="validate only; fail if invalid or committed index is stale")
    args = ap.parse_args()

    root = Path(args.root)
    adr_dir = root / "governance" / "adr"
    scheme_path = root / "governance" / "classification.json"
    errors: list[str] = []

    if not scheme_path.exists():
        print(f"ERROR: {scheme_path} not found")
        return 1
    axes = json.loads(scheme_path.read_text(encoding="utf-8-sig"))["axes"]
    vmap, errors = build_value_map(axes)
    errors = list(errors)

    adrs, seen_ids = [], set()
    for path in sorted(adr_dir.glob("ADR-*.md")):
        text = path.read_text(encoding="utf-8-sig")  # tolerate a Windows-editor BOM
        fm = parse_frontmatter(text)
        if fm is None:
            errors.append(f"{path.name}: no YAML frontmatter")
            continue
        missing = [k for k in REQUIRED if not fm.get(k)]
        if missing:
            errors.append(f"{path.name}: missing frontmatter key(s): {', '.join(missing)}")
            continue
        if fm["id"] in seen_ids:
            errors.append(f"{path.name}: duplicate id {fm['id']}")
        seen_ids.add(fm["id"])
        if fm["status"] not in STATUSES:
            errors.append(f"{path.name}: status '{fm['status']}' not in {sorted(STATUSES)}")
        applies = fm["applies_to"] if isinstance(fm["applies_to"], list) else [fm["applies_to"]]
        for a in applies:
            for err in applies_to_errors(a, axes, vmap):
                errors.append(f"{path.name}: {err}")
        adrs.append({
            "id": fm["id"],
            "title": fm["title"],
            "status": fm["status"],
            "applies_to": applies,
            "scopes": fm.get("scopes", []) if isinstance(fm.get("scopes", []), list) else [fm["scopes"]],
            "summary": fm["summary"],
            "file": path.name,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        print(f"\nADR index FAILED: {len(errors)} error(s)")
        return 1

    index = {"axes": axes, "adrs": adrs}
    index_json = json.dumps(index, indent=2) + "\n"

    lines = [
        "# Org ADR index",
        "",
        "_Generated by `governance/tools/gen_adr_index.py` — do not hand-edit._",
        "",
        "| ID | Title | Status | Applies to | Scopes | Summary |",
        "|----|-------|--------|-----------|--------|---------|",
    ]
    for a in adrs:
        lines.append(
            f"| [{a['id']}]({a['file']}) | {a['title']} | {a['status']} "
            f"| {', '.join(a['applies_to'])} | {', '.join(a['scopes'])} | {a['summary']} |"
        )
    index_md = "\n".join(lines) + "\n"

    json_path, md_path = adr_dir / "index.json", adr_dir / "INDEX.md"
    if args.check:
        stale = []
        if not json_path.exists() or json_path.read_text(encoding="utf-8") != index_json:
            stale.append(str(json_path))
        if not md_path.exists() or md_path.read_text(encoding="utf-8") != index_md:
            stale.append(str(md_path))
        if stale:
            print("ERROR: index is stale — regenerate with "
                  "'python governance/tools/gen_adr_index.py' and commit:")
            for s in stale:
                print(f"  {s}")
            return 1
        print(f"ADR index OK ({len(adrs)} ADRs).")
        return 0

    json_path.write_text(index_json, encoding="utf-8")
    md_path.write_text(index_md, encoding="utf-8")
    print(f"Wrote {json_path} and {md_path} ({len(adrs)} ADRs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
