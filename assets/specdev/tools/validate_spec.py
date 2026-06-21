#!/usr/bin/env python3
"""Gate 1 validator for SpecDev spec artifacts.

Checks .specdev/spec.md for the structure the build pipeline depends on, that
each requirement is testable, and that ADRs and relative links resolve. Exits
non-zero on any error so spec-validate.yml blocks the merge.

Usage:
    python .specdev/tools/validate_spec.py [--root .] [--strict]

--strict treats warnings as errors (recommended in CI).
"""
import argparse
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"<[^>\n]+>|XXX|\bTBD\b", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args()

    root = Path(args.root)
    spec = root / ".specdev" / "spec.md"
    errors: list[str] = []
    warnings: list[str] = []

    if not spec.exists():
        print(f"ERROR: {spec} not found")
        return 1
    text = spec.read_text(encoding="utf-8")

    # Feature ID present and not a placeholder
    m = re.search(r"\*\*Feature ID:\*\*\s*(FEAT-\S+)", text)
    if not m:
        errors.append("missing '**Feature ID:** FEAT-###'")
    elif PLACEHOLDER.search(m.group(1)):
        errors.append(f"Feature ID is still a placeholder: {m.group(1)}")

    # Requirements exist
    reqs = re.findall(r"^###\s+(REQ-\d+)\b(.*)$", text, re.M)
    if not reqs:
        errors.append("no requirements found (expected '### REQ-001 — title')")

    # Every REQ block has a concrete Acceptance line
    blocks = re.split(r"^###\s+REQ-\d+", text, flags=re.M)[1:]
    for (rid, _title), block in zip(reqs, blocks):
        if "Acceptance:" not in block:
            errors.append(f"{rid} has no 'Acceptance:' line (tests derive from it)")
        else:
            first = block.split("Acceptance:", 1)[1].splitlines()[0]
            if PLACEHOLDER.search(first):
                warnings.append(f"{rid} acceptance looks like a placeholder")

    # Out of Scope section is present and concrete
    oos = re.search(r"##\s+Out of Scope\s*(.+?)(^##\s|\Z)", text, re.S | re.M)
    if not oos:
        errors.append("missing '## Out of Scope' section")
    else:
        bullets = [
            ln for ln in oos.group(1).splitlines()
            if ln.strip().startswith("- ") and not PLACEHOLDER.search(ln)
        ]
        if not bullets:
            errors.append("'## Out of Scope' has no concrete entries")

    # ADRs (skip unfilled templates — Status still the literal placeholder line)
    adr_dir = root / ".specdev" / "adr"
    template_status = re.compile(r"\*\*Status:\*\*\s*proposed \| accepted \| superseded")
    real_adrs = []
    for adr in (sorted(adr_dir.glob("ADR-*.md")) if adr_dir.exists() else []):
        t = adr.read_text(encoding="utf-8")
        if template_status.search(t):
            continue  # unedited template, not an actual decision
        real_adrs.append(adr)
        if not re.search(r"\*\*Status:\*\*\s*accepted", t, re.I):
            warnings.append(f"{adr.name} is not 'accepted'")
    adrs = real_adrs
    if not adrs:
        warnings.append("no accepted ADRs in .specdev/adr/ (ok for changes within existing architecture)")

    # Relative links resolve
    for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text):
        if re.match(r"^(https?:|#|mailto:)", target):
            continue
        if not (spec.parent / target.split("#")[0]).exists():
            warnings.append(f"broken link: [{label}]({target})")

    for w in warnings:
        print(f"WARN:  {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if errors or (args.strict and warnings):
        print(f"\nGate 1 FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"\nGate 1 passed ({len(reqs)} requirements, {len(adrs)} ADRs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
