#!/usr/bin/env python3
"""Generate the SpecDev compliance matrix + Statement(s) of Applicability.

Compliance is a second traceability axis layered on the one SpecDev already
keeps. Where gen_traceability links REQ -> test -> commit -> release, this links
CONTROL -> evidence (REQ / ADR / test / commit / config / gate) for one or more
control frameworks (ISO 27001, ISO 42001, SOC 2 TSC, NIST 800-53). Pure stdlib.

Inputs (under .specdev/compliance/):
    compliance.config.json     in-scope frameworks + options
    frameworks/<id>.json       control catalogs (ids + titles)
    frameworks/crosswalk.json  cross-framework equivalence groups
    control-mapping.json       per-control status/applicability/evidence (you edit)

Repo signals merged as automatic evidence:
    .specdev/adr/ADR-*.md   lines `Satisfies controls: <ids>`  -> ADR evidence
    git log                 trailer  `Controls: <ids>`         -> commit evidence (+date)
    spec.md / specs/*.md    a REQ's `Controls: <ids>` line     -> REQ evidence
    test files              any control id appearing in text   -> test evidence

Usage:
    python .specdev/tools/gen_compliance.py             # write matrix + SoA(s)
    python .specdev/tools/gen_compliance.py --scaffold  # seed control-mapping.json
    python .specdev/tools/gen_compliance.py --check-gaps # CI gate, no write
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402  (vendored sibling module)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

TEST_GLOBS = ("**/*test*.*", "**/*spec*.*", "**/test_*.*", "**/*_test.*")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".specdev"}
STATUSES = {"implemented", "partial", "planned", "not-applicable"}


def git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Catalogs                                                                     #
# --------------------------------------------------------------------------- #
def load_catalogs(base: Path, config: dict) -> list[dict]:
    cats = []
    for fid in config.get("frameworks", []):
        f = base / "frameworks" / f"{fid}.json"
        if not f.exists():
            print(f"WARNING: framework '{fid}' in config but {f} not found", file=sys.stderr)
            continue
        cat = load_json(f)
        _maybe_expand_oscal(base, cat, config)
        cats.append(cat)
    return cats


def _maybe_expand_oscal(base: Path, cat: dict, config: dict) -> None:
    """Replace 800-53 family stubs with real controls from an OSCAL catalog,
    scoped to the configured baseline, when the user has dropped one in."""
    imp = cat.get("oscal_import")
    if not imp or not imp.get("enabled"):
        return
    # catalog_path in the file is repo-relative (.specdev/...); resolve from repo root.
    root = base.parent.parent
    cat_path = root / imp["catalog_path"]
    if not cat_path.exists():
        print(f"NOTE: OSCAL import enabled for {cat['id']} but {cat_path} missing — listing families only", file=sys.stderr)
        return
    try:
        oscal = load_json(cat_path)
    except Exception as e:
        print(f"NOTE: could not read OSCAL catalog ({e}); families only", file=sys.stderr)
        return
    baseline_ids = _oscal_baseline_ids(root, imp, config)
    controls = []
    for grp in oscal.get("catalog", {}).get("groups", []):
        fam = grp.get("id", "").upper()
        for c in grp.get("controls", []):
            cid = c.get("id", "").upper()
            if baseline_ids and cid not in baseline_ids:
                continue
            controls.append({"id": cid, "title": c.get("title", ""), "theme": fam})
    if controls:
        cat["controls"] = controls


def _oscal_baseline_ids(root: Path, imp: dict, config: dict) -> set[str]:
    bp = imp.get("baseline_path")
    if not bp:
        return set()
    p = root / bp
    if not p.exists():
        return set()
    try:
        prof = load_json(p)
    except Exception:
        return set()
    ids: set[str] = set()
    for imp_ in prof.get("profile", {}).get("imports", []):
        for inc in imp_.get("include-controls", []):
            for wid in inc.get("with-ids", []):
                ids.add(wid.upper())
    return ids


def all_controls(cats: list[dict]) -> list[tuple[str, dict]]:
    out = []
    for cat in cats:
        for c in cat.get("controls", []):
            out.append((cat["id"], c))
    return out


# --------------------------------------------------------------------------- #
# Automatic evidence discovery                                                 #
# --------------------------------------------------------------------------- #
def id_finder(known_ids: set[str]):
    if not known_ids:
        return lambda text: set()
    # longest-first so A.5.10 wins over A.5.1; boundaries stop prefix collisions.
    pats = [(cid, re.compile(r"(?<![A-Za-z0-9])" + re.escape(cid) + r"(?![A-Za-z0-9])"))
            for cid in sorted(known_ids, key=len, reverse=True)]

    def find(text: str) -> set[str]:
        return {cid for cid, pat in pats if pat.search(text)}
    return find


def adr_evidence(root: Path, find) -> dict[str, set[str]]:
    by_ctrl: dict[str, set[str]] = {}
    adir = root / ".specdev" / "adr"
    if not adir.exists():
        return by_ctrl
    for f in sorted(adir.glob("ADR-*.md")):
        t = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"#\s*(ADR-\d+)", t)
        aid = m.group(1) if m else f.stem
        for line in re.findall(r"Satisfies controls:\*?\*?\s*(.+)", t):
            for cid in find(line):
                by_ctrl.setdefault(cid, set()).add(aid)
    return by_ctrl


def commit_evidence(find) -> tuple[dict[str, set[str]], dict[str, str]]:
    by_ctrl: dict[str, set[str]] = {}
    latest: dict[str, str] = {}
    for entry in git("log", "--pretty=%H%x1f%cI%x1f%B%x1e").split("\x1e"):
        parts = entry.split("\x1f")
        if len(parts) < 3:
            continue
        sha, cdate, body = parts[0].strip(), parts[1].strip(), parts[2]
        for line in re.findall(r"Controls:\s*(.+)", body):
            for cid in find(line):
                by_ctrl.setdefault(cid, set()).add(sha[:7])
                if cid not in latest or cdate > latest[cid]:
                    latest[cid] = cdate
    return by_ctrl, latest


def spec_evidence(root: Path, find) -> dict[str, set[str]]:
    """A control listed on a REQ's `Controls:` line -> that REQ is evidence."""
    by_ctrl: dict[str, set[str]] = {}
    files = []
    sd = root / ".specdev"
    if (sd / "spec.md").exists():
        files.append(sd / "spec.md")
    if (sd / "specs").exists():
        files.extend(sorted((sd / "specs").glob("*.md")))
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        cur = None
        for ln in text.splitlines():
            m = re.match(r"^###\s+(REQ-\d+)\b", ln)
            if m:
                cur = m.group(1)
            elif cur and re.search(r"Controls:", ln):
                for cid in find(ln):
                    by_ctrl.setdefault(cid, set()).add(cur)
    return by_ctrl


def test_evidence(root: Path, find) -> dict[str, set[str]]:
    by_ctrl: dict[str, set[str]] = {}
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
            rel = f.relative_to(root).as_posix() if root in f.parents else f.as_posix()
            for cid in find(t):
                by_ctrl.setdefault(cid, set()).add(rel)
    return by_ctrl


# --------------------------------------------------------------------------- #
# Merge + record                                                               #
# --------------------------------------------------------------------------- #
def build_records(cats, mapping, auto):
    """Per control: applicability, effective status, merged evidence, gaps."""
    adr_e, commit_e, commit_d, spec_e, test_e = auto
    records = []
    for fwid, c in all_controls(cats):
        cid = c["id"]
        entry = mapping.get(cid, {})
        ev = set()
        for x in entry.get("evidence", []):
            ev.add(str(x))
        for a in adr_e.get(cid, ()):
            ev.add(a)
        for s in commit_e.get(cid, ()):
            ev.add(f"commit:{s}")
        for r in spec_e.get(cid, ()):
            ev.add(r)
        for t in test_e.get(cid, ()):
            ev.add(f"test:{t}")

        applicable = entry.get("applicable", True)
        status = entry.get("status")
        if status not in STATUSES:
            status = None
        if status is None:
            status = "implemented" if ev else "unmapped"
            auto_status = True
        else:
            auto_status = False
        records.append({
            "framework": fwid,
            "id": cid,
            "title": c.get("title", ""),
            "theme": c.get("theme", ""),
            "applicable": applicable,
            "status": status,
            "auto_status": auto_status,
            "evidence": sorted(ev),
            "owner": entry.get("owner", ""),
            "reviewed": entry.get("reviewed", ""),
            "justification": entry.get("justification", ""),
            "latest_date": commit_d.get(cid, ""),
        })
    return records


def gaps_for(records, config) -> list[tuple[str, str]]:
    out = []
    fail_planned = config.get("fail_on_planned", False)
    for r in records:
        if not r["applicable"]:
            if not r["justification"]:
                out.append((r["id"], "marked not-applicable without justification"))
            continue
        st = r["status"]
        if st == "unmapped":
            out.append((r["id"], "applicable but unmapped (no status, no evidence)"))
        elif st in ("implemented", "partial") and not r["evidence"]:
            out.append((r["id"], f"status '{st}' but no evidence linked"))
        elif st == "planned" and fail_planned:
            out.append((r["id"], "still planned (fail_on_planned=true)"))
    return out


def stale_for(records, cats, config) -> list[tuple[str, str]]:
    days = config.get("evidence_freshness_days")
    period = any(c.get("period_of_performance") for c in cats)
    if not days or not period:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for r in records:
        if not r["applicable"] or r["status"] not in ("implemented", "partial"):
            continue
        newest = r["latest_date"] or (r["reviewed"] + "T00:00:00+00:00" if r["reviewed"] else "")
        if not newest:
            out.append((r["id"], "no dated evidence (SOC 2 needs evidence across the period)"))
            continue
        try:
            d = datetime.fromisoformat(newest.replace("Z", "+00:00"))
            if (now - d).days > int(days):
                out.append((r["id"], f"newest evidence {(now - d).days}d old (> {days}d)"))
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# Outputs                                                                      #
# --------------------------------------------------------------------------- #
def crosswalk_for(cid: str, crosswalk: dict) -> str:
    others = []
    for g in crosswalk.get("groups", []):
        members = {k: v for k, v in g.items() if k not in ("topic",)}
        if any(cid in v for v in members.values()):
            for fw, ids in members.items():
                for x in ids:
                    if x != cid:
                        others.append(x)
    return ", ".join(sorted(set(others))) if others else "—"


def ev_cell(ev: list[str]) -> str:
    return ", ".join(ev) if ev else "—"


def write_matrix(out: Path, records, cats, crosswalk, config):
    by_fw = {c["id"]: c for c in cats}
    lines = [
        "# Compliance Matrix\n",
        "_Generated by `gen_compliance.py` — do not edit by hand. Edit `control-mapping.json`._\n",
        f"- **System:** {config.get('system_name', '—')}",
        f"- **Frameworks:** {', '.join(by_fw.keys())}",
        f"- **Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n",
    ]
    for cat in cats:
        recs = [r for r in records if r["framework"] == cat["id"]]
        app = [r for r in recs if r["applicable"]]
        impl = [r for r in app if r["status"] in ("implemented", "partial")]
        lines.append(f"\n## {cat['framework']}  (`{cat['id']}`)\n")
        lines.append(f"_Applicable: {len(app)}/{len(recs)} • with evidence: "
                     f"{sum(1 for r in app if r['evidence'])} • implemented/partial: {len(impl)}_\n")
        lines.append("| Control | Title | Theme | Appl. | Status | Evidence | Crosswalk |")
        lines.append("|---------|-------|-------|-------|--------|----------|-----------|")
        for r in recs:
            st = r["status"] + (" (auto)" if r["auto_status"] and r["status"] != "unmapped" else "")
            lines.append(
                f"| {r['id']} | {r['title']} | {r['theme']} | "
                f"{'Y' if r['applicable'] else 'N'} | {st} | {ev_cell(r['evidence'])} | "
                f"{crosswalk_for(r['id'], crosswalk)} |"
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_soa(base: Path, cat: dict, records, config):
    recs = [r for r in records if r["framework"] == cat["id"]]
    app = sum(1 for r in recs if r["applicable"])
    out = base / f"statement-of-applicability-{cat['id']}.md"
    lines = [
        f"# Statement of Applicability — {cat['framework']}\n",
        "_Generated by `gen_compliance.py`. Justify every exclusion (Appl. = N)._\n",
        f"- **System:** {config.get('system_name', '—')}",
        f"- **Applicable controls:** {app}/{len(recs)}",
        f"- **Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n",
        "| Control | Title | Applicable | Justification | Status | Evidence |",
        "|---------|-------|------------|---------------|--------|----------|",
    ]
    for r in recs:
        just = r["justification"] or ("—" if r["applicable"] else "**MISSING — required for exclusion**")
        lines.append(
            f"| {r['id']} | {r['title']} | {'Yes' if r['applicable'] else 'No'} | "
            f"{just} | {r['status']} | {ev_cell(r['evidence'])} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def do_scaffold(base: Path, cats, mapping):
    added = 0
    for _, c in all_controls(cats):
        if c["id"] not in mapping:
            mapping[c["id"]] = {
                "applicable": True,
                "status": "planned",
                "evidence": [],
                "owner": "",
                "reviewed": "",
                "justification": "",
            }
            added += 1
    path = base / "control-mapping.json"
    path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Scaffolded {path} — {added} new control(s), {len(mapping)} total.")
    return 0


# --------------------------------------------------------------------------- #
def run_one(args) -> int:
    """Generate for exactly one unit root."""

    root = Path(args.root)
    base = root / ".specdev" / "compliance"
    cfg_path = base / "compliance.config.json"
    if not cfg_path.exists():
        print(f"ERROR: {cfg_path} not found — run /specdev:init or add the compliance/ kit.", file=sys.stderr)
        return 1
    config = load_json(cfg_path)
    cats = load_catalogs(base, config)
    if not cats:
        print("ERROR: no in-scope framework catalogs loaded (check compliance.config.json)", file=sys.stderr)
        return 1

    mapping_path = base / "control-mapping.json"
    mapping = load_json(mapping_path) if mapping_path.exists() else {}

    if args.scaffold:
        return do_scaffold(base, cats, mapping)

    known = {c["id"] for _, c in all_controls(cats)}
    find = id_finder(known)
    commit_e, commit_d = commit_evidence(find)
    auto = (adr_evidence(root, find), commit_e, commit_d,
            spec_evidence(root, find), test_evidence(root, find))
    records = build_records(cats, mapping, auto)

    gaps = gaps_for(records, config)
    stale = stale_for(records, cats, config)

    if args.check_gaps:
        if gaps or stale:
            if gaps:
                print(f"Compliance gaps ({len(gaps)}):")
                for cid, why in gaps:
                    print(f"  {cid}: {why}")
            if stale:
                print(f"Stale evidence ({len(stale)}):")
                for cid, why in stale:
                    print(f"  {cid}: {why}")
            return 1
        app = sum(1 for r in records if r["applicable"])
        print(f"OK: all {app} applicable controls have evidence or a documented exclusion.")
        return 0

    crosswalk = load_json(base / "frameworks" / "crosswalk.json") if (base / "frameworks" / "crosswalk.json").exists() else {}
    write_matrix(base / "compliance-matrix.md", records, cats, crosswalk, config)
    soas = [write_soa(base, cat, records, config) for cat in cats if cat.get("requires_soa")]
    app = sum(1 for r in records if r["applicable"])
    print(f"Wrote {base / 'compliance-matrix.md'} — {len(records)} controls, "
          f"{app} applicable, {len(gaps)} gap(s)"
          + (f", {len(stale)} stale" if stale else "") + ".")
    for s in soas:
        print(f"Wrote {s}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--scaffold", action="store_true",
                    help="add a stub mapping entry for every in-scope control (preserves existing)")
    ap.add_argument("--check-gaps", action="store_true",
                    help="exit non-zero if any applicable control lacks evidence/justification (no write)")
    ap.add_argument("--all-units", action="store_true",
                    help="generate for every registered unit")
    args = ap.parse_args()

    if not args.all_units:
        return run_one(args)

    rc = 0
    for unit in units.unit_paths(args.root):
        base = Path(args.root) / unit / ".specdev" / "compliance"
        if not (base / "compliance.config.json").exists():
            continue  # unit has no compliance kit; not an error
        sub = argparse.Namespace(**vars(args))
        sub.all_units = False
        sub.root = str(Path(args.root) / unit)
        print(f"--- {unit} ---")
        rc |= run_one(sub)
    if not args.check_gaps and not args.scaffold:
        idx = units.write_rollup_index(args.root, "compliance/compliance-matrix.md",
                                       "Compliance index")
        if idx:
            print(f"Wrote {idx}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
