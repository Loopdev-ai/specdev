#!/usr/bin/env python3
"""Gate 1 validator for SpecDev spec artifacts.

Checks .specdev/spec.md for the structure the build pipeline depends on, that
each requirement is testable, and that ADRs and relative links resolve. Exits
non-zero on any error so spec-validate.yml blocks the merge.

Usage:
    python .specdev/tools/validate_spec.py [--root .] [--strict] [--components]

--strict treats warnings as errors (recommended in CI).
--components also checks .specdev/components.md against the spec: every REQ
owned, every dependency known (or marked external), no cycles, and a contract
on every component that owns a REQ. spec-validate.yml runs it only on spec/**
heads, so a spec merged before the check existed never fails it.

NOTE lines are advice (e.g. vague wording in an Acceptance line). They never
fail the gate, under --strict either: a new error here would fail every
already-merged spec on the next PR that touches its unit.
"""
import argparse
import re
import sys
from pathlib import Path

# SpecDev tools use PEP 604 unions (`dict | None`) in annotations, which are
# evaluated at def time and raise TypeError on Python 3.9. macOS ships 3.9.x as
# the system python3, so without this guard every tool dies with an opaque
# "unsupported operand type(s) for |". Checked before the sibling imports below,
# which carry the same annotations. The message is deliberately pure ASCII: this
# runs before the stdout UTF-8 reconfigure, so a non-ASCII character here would
# raise UnicodeEncodeError on a cp1252 console and replace the explanation with
# a traceback.
if sys.version_info < (3, 10):
    raise SystemExit(
        "SpecDev tools require Python 3.10+ (found "
        f"{sys.version_info.major}.{sys.version_info.minor}). "
        "On macOS the system python3 is 3.9.x; install a newer Python or use "
        "a virtualenv. In CI, actions/setup-python with python-version '3.x' "
        "satisfies this."
    )

PLACEHOLDER = re.compile(r"<[^>\n]+>|XXX|\bTBD\b", re.I)

CHARTER_SECTIONS = ("Goal", "Questions this spike must answer", "Timebox",
                    "Abandon criteria")

# Charter-specific placeholder regex: excludes angle-bracket spans starting with
# whitespace (comparisons like < 50k/sec) and spans containing @ (emails).
CHARTER_PLACEHOLDER = re.compile(r"<(?!\s)[^@>\n]+>|XXX|\bTBD\b", re.I)


def _validate_charter(root: Path, strict: bool) -> int:
    """Gate 1 for a `poc` unit. A spike answers questions; it does not deliver
    requirements, so there are no REQ IDs, acceptance lines or ADRs to check.
    What must NOT be skipped is that the charter is actually filled in — an
    unedited template is how a spike becomes untracked work."""
    charter = root / ".specdev" / "CHARTER.md"
    if not charter.exists():
        print(f"ERROR: {charter.as_posix()} not found "
              "(the poc profile's Gate 1 artifact)")
        return 1
    text = charter.read_text(encoding="utf-8")
    errors: list[str] = []
    warnings: list[str] = []

    for name in CHARTER_SECTIONS:
        m = re.search(rf"^##\s+{re.escape(name)}\s*$(.*?)(^##\s|\Z)",
                      text, re.S | re.M)
        if not m:
            errors.append(f"missing '## {name}' section")
            continue
        body = m.group(1).strip()
        if not body:
            errors.append(f"'## {name}' is empty")
        elif CHARTER_PLACEHOLDER.search(body):
            errors.append(f"'## {name}' still holds a placeholder")

    for w in warnings:
        print(f"WARN:  {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors or (strict and warnings):
        print(f"\nGate 1 (charter) FAILED: {len(errors)} error(s), "
              f"{len(warnings)} warning(s)")
        return 1
    print(f"\nGate 1 (charter) passed ({len(CHARTER_SECTIONS)} sections).")
    return 0


# Vague wording in an Acceptance line, unless the line also carries something
# concrete (a number, a quoted value or code). Spec Kit's `analyze` flags the
# same thing; here it is free advice before a costly reviewer pass.
VAGUE_TERMS = ("fast", "faster", "quick", "quickly", "slow", "robust",
               "intuitive", "user-friendly", "user friendly", "scalable",
               "efficient", "efficiently", "seamless", "seamlessly", "easy",
               "easily", "simple", "appropriate", "appropriately",
               "reasonable", "reasonably", "responsive", "performant",
               "secure", "securely", "reliable", "reliably", "flexible",
               "clean", "nice", "good", "properly", "correctly", "smooth",
               "minimal", "as needed", "etc")
_VAGUE = re.compile(r"\b(" + "|".join(re.escape(t) for t in VAGUE_TERMS)
                    + r")\b", re.I)
_CONCRETE = re.compile(r"\d|`[^`]+`|\"[^\"]+\"")


def vague_notes(reqs, blocks) -> list[str]:
    notes = []
    for (rid, _title), block in zip(reqs, blocks):
        if "Acceptance:" not in block:
            continue
        line = block.split("Acceptance:", 1)[1].splitlines()[0]
        if PLACEHOLDER.search(line) or _CONCRETE.search(line):
            continue
        hits = sorted({m.group(1).lower() for m in _VAGUE.finditer(line)})
        if hits:
            notes.append(f"{rid} acceptance uses {', '.join(repr(h) for h in hits)}"
                         f" with no number, quoted value or code to measure "
                         f"it by - a test cannot assert it as written")
    return notes


# ---- components.md ---------------------------------------------------------

_MARKER = re.compile(r"\((?:new|modified|untouched)\)", re.I)
_EXTERNAL = re.compile(r"^\s*(external\s*:|ext\s*:)|\(external\)", re.I)
_NONE = {"", "-", "—", "–", "none", "n/a", "na"}


def _name(cell: str) -> str:
    return " ".join(_MARKER.sub("", cell).replace("`", "").replace("*", "")
                    .split()).strip().lower()


def component_rows(text: str) -> tuple[list[dict], str | None]:
    """The component table's rows as dicts, or ([], why) when there is no
    recognisable table. Columns are found by header name, not position."""
    lines = [ln.strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        if not ln.startswith("|"):
            continue
        cols = [c.strip().lower() for c in ln.strip("|").split("|")]
        if not any(c.startswith("component") for c in cols):
            continue
        idx = {}
        for key, prefix in (("name", "component"), ("owns", "owns"),
                            ("contract", "contract"), ("deps", "depends")):
            hit = [j for j, c in enumerate(cols) if c.startswith(prefix)]
            if not hit:
                return [], f"the component table has no '{prefix}' column"
            idx[key] = hit[0]
        rows = []
        for row in lines[i + 1:]:
            if not row.startswith("|"):
                break
            cells = [c.strip() for c in row.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue  # separator row
            cells += [""] * (len(cols) - len(cells))
            if not any(cells):
                continue  # the template's empty spare row
            rows.append({k: cells[j] for k, j in idx.items()})
        return rows, None
    return [], "no component table (| Component | Owns (REQs) | ... |)"


def components_problems(root: Path, req_ids: list[str]) -> list[str]:
    """Why components.md cannot drive a build of this spec. Empty = it can."""
    path = root / ".specdev" / "components.md"
    if not path.exists():
        return [f"{path.as_posix()} not found - fill the component map before "
                f"the Spec PR so the build has a schedule to follow"]
    rows, why = component_rows(path.read_text(encoding="utf-8"))
    if why:
        return [f"{path.as_posix()}: {why}"]
    if not rows:
        return [f"{path.as_posix()} has no components - fill it before the "
                f"Spec PR so the build has a schedule to follow"]
    problems = []
    names = {}
    for r in rows:
        if not r["name"] or PLACEHOLDER.search(r["name"]):
            problems.append(f"components.md still has a template row "
                            f"({r['name'] or 'unnamed'!r})")
            continue
        n = _name(r["name"])
        if n in names:
            problems.append(f"component {r['name']!r} is listed twice")
        names[n] = r
    spec_reqs = set(req_ids)
    owned = {}
    for n, r in names.items():
        for rid in re.findall(r"REQ-\d+", r["owns"]):
            owned.setdefault(rid, []).append(n)
            if rid not in spec_reqs:
                problems.append(f"component {r['name']!r} owns {rid}, which "
                                f"is not in the spec")
        if re.search(r"REQ-\d+", r["owns"]) and (
                not r["contract"] or PLACEHOLDER.search(r["contract"])):
            problems.append(f"component {r['name']!r} owns REQs but has no "
                            f"Contract / Interface - name the entry point "
                            f"its REQs are reached through")
    for rid in req_ids:
        if rid not in owned:
            problems.append(f"{rid} is owned by no component in "
                            f"components.md")
    graph = {}
    for n, r in names.items():
        deps = []
        for raw in re.split(r"[,;]", r["deps"]):
            d = raw.strip()
            if d.lower().strip("` ") in _NONE or _EXTERNAL.search(d):
                continue
            if PLACEHOLDER.search(d):
                problems.append(f"component {r['name']!r} still has a "
                                f"placeholder in Depends on ({d!r})")
                continue
            key = _name(d)
            if key not in names:
                problems.append(
                    f"component {r['name']!r} depends on {d!r}, which is not "
                    f"in the table - mark an external system as "
                    f"'{d} (external)'")
                continue
            deps.append(key)
        graph[n] = deps
    seen, stack = set(), []

    def visit(n):
        if n in stack:
            loop = stack[stack.index(n):] + [n]
            problems.append("dependency cycle: " + " -> ".join(loop))
            return True
        if n in seen:
            return False
        seen.add(n)
        stack.append(n)
        for d in graph.get(n, []):
            if visit(d):
                stack.pop()
                return True
        stack.pop()
        return False

    for n in graph:
        if n not in seen and visit(n):
            break
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--profile", choices=("full", "charter"), default="full",
                    help="'charter' validates .specdev/CHARTER.md for a poc "
                         "unit instead of the full spec.md bar (see "
                         "profile.py). Default 'full'.")
    ap.add_argument("--components", action="store_true",
                    help="also check .specdev/components.md against the spec "
                         "(run on Spec PRs only)")
    args = ap.parse_args()

    root = Path(args.root)
    if args.profile == "charter":
        return _validate_charter(root, args.strict)
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

    if args.components:
        errors += components_problems(root, [rid for rid, _ in reqs])

    for n in vague_notes(reqs, blocks):
        print(f"NOTE:  {n}")
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
