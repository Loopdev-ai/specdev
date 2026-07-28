#!/usr/bin/env python3
"""Generate the SpecDev traceability matrix.

Links REQ -> ADR -> tests -> commits -> PR -> release tag -> QA runs into a
single markdown table committed beside BUILD.md. Pure stdlib; degrades
gracefully when run outside CI or outside a git repo.

REQ IDs are collected cumulatively across the whole product, not just the
feature in the active spec: a durable `product-requirements.md` catalog (if
present), every archived feature spec under `.specdev/specs/*.md`, and the
active `.specdev/spec.md` are all unioned. So `--check-gaps` keeps verifying
shipped features even after their spec is archived.

Usage:
    python .specdev/tools/gen_traceability.py [--root .] [--out PATH]
    python .specdev/tools/gen_traceability.py --check-gaps   # CI gate, no write

Environment (supplied by CI, optional locally):
    PR_NUMBER, RELEASE_TAG, STAGING_QA_RUN, PROD_QA_RUN
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402  (vendored sibling module)

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

TEST_GLOBS = ("**/*test*.*", "**/*spec*.*", "**/test_*.*", "**/*_test.*")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".specdev"}


def git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def parse_reqs(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for rid, rest in re.findall(r"^###\s+(REQ-\d+)\b[ \t—-]*(.*)$", text, re.M):
        out[rid] = rest.strip()
    return out


def spec_sources(root: Path) -> list[Path]:
    """Every file that may declare REQ IDs, oldest-to-newest precedence.

    Traceability is cumulative across the whole product, not just the feature
    currently in `spec.md`. We union REQs from a durable catalog
    (`product-requirements.md`), every archived feature spec (`specs/*.md`), and
    the active `spec.md`, so archiving a finished feature never drops its REQs
    from the matrix or the gap check.
    """
    sd = root / ".specdev"
    sources: list[Path] = []
    catalog = sd / "product-requirements.md"
    if catalog.exists():
        sources.append(catalog)
    specs_dir = sd / "specs"
    if specs_dir.exists():
        sources.extend(sorted(specs_dir.glob("*.md")))
    active = sd / "spec.md"
    if active.exists():
        sources.append(active)
    return sources


def collect_reqs(sources: list[Path]) -> dict[str, str]:
    reqs: dict[str, str] = {}
    for f in sources:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for rid, title in parse_reqs(text).items():
            # First sighting wins, but let a later file supply a missing title.
            if rid not in reqs or (not reqs[rid] and title):
                reqs[rid] = title
    return reqs


def parse_adrs(adr_dir: Path) -> dict[str, set[str]]:
    by_req: dict[str, set[str]] = {}
    if not adr_dir.exists():
        return by_req
    for f in sorted(adr_dir.glob("ADR-*.md")):
        t = f.read_text(encoding="utf-8")
        m = re.search(r"#\s*(ADR-\d+)", t)
        aid = m.group(1) if m else f.stem
        for rel in re.findall(r"Relates to:\*?\*?\s*(.+)", t):
            for rid in re.findall(r"REQ-\d+", rel):
                by_req.setdefault(rid, set()).add(aid)
    return by_req


def find_tests(root: Path) -> dict[str, set[str]]:
    by_req: dict[str, set[str]] = {}
    seen: set[Path] = set()
    for pat in TEST_GLOBS:
        for f in root.glob(pat):
            if f in seen or not f.is_file() or SKIP_DIRS & set(f.parts):
                continue
            seen.add(f)
            try:
                t = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            try:
                rel = f.relative_to(root).as_posix()
            except ValueError:
                rel = f.as_posix()
            for rid in set(re.findall(r"REQ-\d+", t)):
                by_req.setdefault(rid, set()).add(rel)
    return by_req


def find_commits() -> dict[str, set[str]]:
    by_req: dict[str, set[str]] = {}
    for entry in git("log", "--pretty=%H%x1f%B%x1e").split("\x1e"):
        if "\x1f" not in entry:
            continue
        sha, body = entry.split("\x1f", 1)
        for ref in re.findall(r"Refs:\s*(.+)", body):
            for rid in re.findall(r"REQ-\d+", ref):
                by_req.setdefault(rid, set()).add(sha.strip()[:7])
    return by_req


def cell(values: set[str] | None) -> str:
    return ", ".join(sorted(values)) if values else "—"


def run_one(args) -> int:
    """Generate for exactly one unit root."""

    root = Path(args.root)
    sources = spec_sources(root)
    if not sources:
        sd = root / ".specdev"
        print(f"ERROR: no REQ sources under {sd} "
              f"(need spec.md, specs/*.md, or product-requirements.md)",
              file=sys.stderr)
        return 1

    reqs = collect_reqs(sources)
    # Feature header reflects the active feature in spec.md (if any).
    active = root / ".specdev" / "spec.md"
    text = active.read_text(encoding="utf-8") if active.exists() else ""
    adr_by_req = parse_adrs(root / ".specdev" / "adr")
    tests_by_req = find_tests(root)
    commits_by_req = find_commits()
    gaps = sorted(rid for rid in reqs if rid not in tests_by_req)

    if args.check_gaps:
        if gaps:
            print("Traceability gap — requirements with no linked test:")
            for g in gaps:
                print(f"  {g} {reqs[g]}")
            return 1
        print(f"OK: all {len(reqs)} requirements have at least one linked test.")
        return 0

    fm = re.search(r"\*\*Feature ID:\*\*\s*(\S+)", text)
    feature = fm.group(1) if fm else "—"
    pr = os.environ.get("PR_NUMBER", "—")
    release = os.environ.get("RELEASE_TAG", "—")
    qa_staging = os.environ.get("STAGING_QA_RUN", "—")
    qa_prod = os.environ.get("PROD_QA_RUN", "—")

    lines = [
        "# Traceability Matrix\n",
        "_Generated by `gen_traceability.py` — do not edit by hand._\n",
        f"- **Feature:** {feature}",
        f"- **Impl PR:** {pr}  •  **Release:** {release}",
        f"- **Staging QA:** {qa_staging}  •  **Prod QA:** {qa_prod}",
        f"- **Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n",
        "| REQ | Title | ADRs | Tests | Commits | PR | Release | Staging QA | Prod QA |",
        "|-----|-------|------|-------|---------|----|---------|-----------|---------|",
    ]
    for rid in sorted(reqs):
        lines.append(
            f"| {rid} | {reqs[rid] or '—'} | {cell(adr_by_req.get(rid))} | "
            f"{cell(tests_by_req.get(rid))} | {cell(commits_by_req.get(rid))} | "
            f"{pr} | {release} | {qa_staging} | {qa_prod} |"
        )
    if gaps:
        lines.append("\n## Gaps\n")
        for g in gaps:
            lines.append(f"- ⚠ {g} ({reqs[g] or 'untitled'}) has no linked test")

    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out} - {len(reqs)} requirements, {len(gaps)} gap(s).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=".specdev/traceability.md")
    ap.add_argument("--check-gaps", action="store_true",
                    help="exit non-zero if any REQ has no linked test (no file write)")
    ap.add_argument("--all-units", action="store_true",
                    help="generate for every registered unit")
    args = ap.parse_args()

    if not args.all_units:
        return run_one(args)

    rc = 0
    for unit in units.unit_paths(args.root):
        sub = argparse.Namespace(**vars(args))
        sub.all_units = False
        sub.root = str(Path(args.root) / unit)
        print(f"--- {unit} ---")
        rc |= run_one(sub)
    if not args.check_gaps:
        idx = units.write_rollup_index(args.root, "traceability.md",
                                       "Traceability index")
        if idx:
            print(f"Wrote {idx}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
