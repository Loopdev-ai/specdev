#!/usr/bin/env python3
"""Aggregate the pre-PR review loop's numbers across every feature, so a
change to the loop can be judged against a baseline.

`review_ledger.py stats` describes ONE feature: the ledger
(<unit>/.specdev/review.json) holds one feature at a time and is replaced
when the next feature records its first pass. Each build's outcome record and
each PR's escape count are per-run artifacts that GitHub deletes after its
retention period. None of that is a baseline. This tool builds one:

  * Loop numbers come from GIT HISTORY. Every version of every review.json
    ever committed on the given refs is read back with `git show`, and the
    newest version per (unit, FEAT) is that feature's final ledger. Git
    history does not expire, covers interactive and CI builds alike, and
    finds ledgers of units that were later renamed or removed.
  * Escape counts (what reviewers still found on the open Implementation PR,
    from specdev-escapes.yml) are joined from a directory of `escapes.json`
    files, matched on the PR's head branch specdev/impl/<unit>/<FEAT>.
  * `--previous` carries escape counts forward from an earlier CSV, so a
    feature whose escapes artifact has expired keeps its numbers.

Output: a markdown summary + table (stdout or --md), and --csv / --json for
analysis. Read-only with respect to the repository.

Usage:
    loop_stats.py [--repo-dir .] [--ref origin/main ...]
                  [--escapes-dir DIR] [--previous loop-stats.csv]
                  [--csv OUT] [--json OUT] [--md OUT]
"""
import argparse
import csv
import json
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit("SpecDev tools require Python 3.10+")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_ledger  # noqa: E402  (vendored sibling module)

LEDGER_GLOB = ":(glob)**/.specdev/review.json"
LEDGER_SUFFIX = ".specdev/review.json"
IMPL_PREFIX = "specdev/impl/"
IMPL_KINDS = ("bug", "security", "intent", "test")

COLUMNS = [
    "unit", "feat", "ledger_commit", "ledger_date",
    "spec_runs", "spec_passes", "spec_blocking", "spec_minor",
    "spec_needs_human", "spec_dismissed", "spec_assumed",
    "spec_final_clean",
    "impl_runs", "impl_passes", "impl_blocking", "impl_minor",
    *[f"impl_{k}" for k in IMPL_KINDS],
    "impl_dismissed", "impl_not_reproducible", "impl_reraised",
    "impl_final_clean",
    "escapes_pr", "escapes_bot", "escapes_human", "escapes_reviews",
    "escapes_source",
]


def _git(args, cwd="."):
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True)
    except OSError:
        return 1, ""
    return p.returncode, p.stdout


def ref_unit(unit: str) -> str:
    """The unit as it appears in specdev/impl/<unit>/<FEAT> (same rule as
    build_outcome.ref_unit: the single-root unit '.' is 'root')."""
    parts = [p for p in (unit or ".").strip("/").split("/")
             if p not in ("", ".", "..")]
    return "/".join(parts) if parts else "root"


def unit_of(path: str) -> str:
    head = path[: -len(LEDGER_SUFFIX)].rstrip("/")
    return head or "."


def parse_when(value: str):
    """A git %cI date as an aware datetime, or None.

    Newer git prints UTC as a trailing 'Z', which datetime.fromisoformat only
    accepts from Python 3.11. On 3.10 every commit date failed to parse, and
    every ledger was silently skipped - so normalise it first, and let the
    caller warn about anything still unparseable rather than drop it."""
    v = (value or "").strip()
    if v.endswith(("Z", "z")):
        v = v[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def ledger_paths(repo_dir=".", refs=("HEAD",)) -> list[str]:
    """Every review.json path ever committed on these refs."""
    rc, out = _git(["log", *refs, "--format=", "--name-only", "--",
                    LEDGER_GLOB], repo_dir)
    if rc != 0:
        raise SystemExit(f"git log failed in {repo_dir} for refs {refs}")
    return sorted({ln.strip() for ln in out.splitlines()
                   if ln.strip().endswith(LEDGER_SUFFIX)})


def final_ledgers(repo_dir=".", refs=("HEAD",)):
    """{(unit, feat): {"doc", "commit", "date"}} - the newest committed
    version of each feature's ledger - and a list of warnings."""
    found, warnings = {}, []
    for path in ledger_paths(repo_dir, refs):
        rc, out = _git(["log", *refs, "--format=%H%x09%cI", "--", path],
                       repo_dir)
        if rc != 0:
            warnings.append(f"git log failed for {path}")
            continue
        for line in out.splitlines():
            sha, _, when = line.partition("\t")
            rc, text = _git(["show", f"{sha}:{path}"], repo_dir)
            if rc != 0:
                continue  # the commit that deleted it
            try:
                doc = review_ledger.parse_ledger(text, f"{sha[:12]}:{path}")
            except review_ledger.LedgerError as e:
                warnings.append(str(e))
                continue
            feat = doc.get("feat")
            if not feat:
                continue
            at = parse_when(when)
            if at is None:
                warnings.append(f"{sha[:12]}:{path}: unparseable commit date "
                                f"{when.strip()!r} - skipped")
                continue
            key = (unit_of(path), feat)
            if key not in found or at > found[key]["at"]:
                found[key] = {"doc": doc, "commit": sha, "at": at}
    return found, warnings


def row_for(unit: str, feat: str, entry: dict) -> dict:
    st = review_ledger.stats_of(entry["doc"])["phases"]
    sp, ip = st["spec"], st["impl"]
    row = {"unit": unit, "feat": feat, "ledger_commit": entry["commit"][:12],
           "ledger_date": entry["at"].isoformat()}
    for name, ph in (("spec", sp), ("impl", ip)):
        row[f"{name}_runs"] = ph["runs"]
        row[f"{name}_passes"] = ph["passes"]
        row[f"{name}_blocking"] = ph["findings"]["blocking"]
        row[f"{name}_minor"] = ph["findings"]["minor"]
        row[f"{name}_dismissed"] = ph["dismissed"]
        row[f"{name}_final_clean"] = (ph["final_pass_clean"]
                                      if ph["passes"] else "")
    row["spec_needs_human"] = sp["needs_human"]
    row["spec_assumed"] = sp["assumed"]
    for k in IMPL_KINDS:
        row[f"impl_{k}"] = ip["by_kind"].get(k, {}).get("blocking", 0)
    row["impl_not_reproducible"] = ip["not_reproducible"]
    row["impl_reraised"] = ip["reraised_after_dismissal"]
    for c in ("escapes_pr", "escapes_bot", "escapes_human",
              "escapes_reviews", "escapes_source"):
        row[c] = ""
    return row


def load_escapes(directory) -> dict:
    """{(ref_unit, feat): escapes} from every escapes.json under directory.
    The newest PR wins when a feature had more than one."""
    out = {}
    for f in sorted(Path(directory).rglob("*.json")):
        try:
            e = json.loads(f.read_text(encoding="utf-8-sig"))
            head = str(e["head"])
            pr = int(e["pr"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not head.startswith(IMPL_PREFIX):
            continue
        unit, _, feat = head[len(IMPL_PREFIX):].rpartition("/")
        if not unit or not feat:
            continue
        key = (unit, feat)
        if key not in out or pr > out[key]["pr"]:
            out[key] = {"pr": pr, "bot": int(e.get("by_bot", 0)),
                        "human": int(e.get("by_human", 0)),
                        "reviews": int(e.get("reviews_with_body", 0))}
    return out


def load_previous(path) -> dict:
    """{(unit, feat): row} from an earlier CSV, for carrying escapes."""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return {(r["unit"], r["feat"]): r for r in csv.DictReader(fh)}
    except (OSError, KeyError, csv.Error):
        return {}


def build_rows(repo_dir=".", refs=("HEAD",), escapes_dir=None,
               previous=None):
    ledgers, warnings = final_ledgers(repo_dir, refs)
    escapes = load_escapes(escapes_dir) if escapes_dir else {}
    prev = load_previous(previous) if previous else {}
    rows = []
    for (unit, feat), entry in ledgers.items():
        row = row_for(unit, feat, entry)
        esc = escapes.get((ref_unit(unit), feat))
        if esc:
            row.update(escapes_pr=esc["pr"], escapes_bot=esc["bot"],
                       escapes_human=esc["human"],
                       escapes_reviews=esc["reviews"],
                       escapes_source="artifact")
        else:
            old = prev.get((unit, feat))
            if old and str(old.get("escapes_pr", "")).strip():
                for c in ("escapes_pr", "escapes_bot", "escapes_human",
                          "escapes_reviews"):
                    row[c] = old.get(c, "")
                row["escapes_source"] = "carried"
        rows.append(row)
    rows.sort(key=lambda r: (r["ledger_date"], r["unit"], r["feat"]))
    return rows, warnings


def _nums(rows, col, only=None):
    out = []
    for r in rows:
        if only and not only(r):
            continue
        v = r.get(col)
        if v not in ("", None):
            out.append(float(v))
    return out


def summary(rows) -> dict:
    impl = [r for r in rows if r["impl_passes"]]
    spec = [r for r in rows if r["spec_passes"]]
    esc = [r for r in rows if str(r["escapes_pr"]).strip()]

    def med(xs):
        return round(statistics.median(xs), 2) if xs else None

    def total(col, among):
        return int(sum(_nums(among, col)))

    return {
        "features": len(rows),
        "impl": {
            "features": len(impl),
            "median_passes": med(_nums(impl, "impl_passes")),
            "max_passes": max(_nums(impl, "impl_passes"), default=None),
            "final_clean_rate": (round(sum(r["impl_final_clean"] is True
                                           for r in impl) / len(impl), 3)
                                 if impl else None),
            "blocking_by_kind": {k: total(f"impl_{k}", impl)
                                 for k in IMPL_KINDS},
            "dismissed": total("impl_dismissed", impl),
            "not_reproducible": total("impl_not_reproducible", impl),
            "reraised": total("impl_reraised", impl),
        },
        "spec": {
            "features": len(spec),
            "median_passes": med(_nums(spec, "spec_passes")),
            "needs_human": total("spec_needs_human", spec),
            "assumed": total("spec_assumed", spec),
        },
        "escapes": {
            "prs": len(esc),
            "median_bot": med(_nums(esc, "escapes_bot")),
            "median_human": med(_nums(esc, "escapes_human")),
        },
    }


def _cell(v):
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return "" if v is None else str(v)


def _na(v):
    return "n/a" if v is None else _cell(v)


def render_md(rows, summ, refs, warnings) -> str:
    i, s, e = summ["impl"], summ["spec"], summ["escapes"]
    rate = i["final_clean_rate"]
    clean = "n/a" if rate is None else f"{rate:.0%}"
    lines = [
        "# Review loop statistics",
        "",
        f"From git history of every `.specdev/review.json` on "
        f"{', '.join(f'`{r}`' for r in refs)}: {summ['features']} "
        f"feature(s).",
        "",
        f"- **Implementation loop:** {i['features']} feature(s); median "
        f"{_na(i['median_passes'])} pass(es), max {_na(i['max_passes'])}"
        f"; final pass clean in {clean} of them. Blocking findings: "
        + ", ".join(f"{k} {v}" for k, v in i["blocking_by_kind"].items())
        + f"; dismissed {i['dismissed']} ({i['not_reproducible']} not "
          f"reproducible); re-raised after dismissal {i['reraised']}.",
        f"- **Spec loop:** {s['features']} feature(s); median "
        f"{_na(s['median_passes'])} pass(es); questions for the user "
        f"{s['needs_human']}, of which assumed {s['assumed']}.",
        f"- **Escapes** (review comments on the open Implementation PR): "
        f"{e['prs']} PR(s) with data; median {_na(e['median_bot'])} by "
        f"bots, {_na(e['median_human'])} by people.",
        "",
        "| Unit | Feature | Spec passes | Impl passes | Impl blocking | "
        "Dismissed | Final clean | Escapes (bot/human) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        esc = (f"#{r['escapes_pr']}: {r['escapes_bot']}/{r['escapes_human']}"
               + (" (carried)" if r["escapes_source"] == "carried" else "")
               if str(r["escapes_pr"]).strip() else "—")
        lines.append(
            f"| `{r['unit']}` | {r['feat']} | {_cell(r['spec_passes'])} | "
            f"{_cell(r['impl_passes'])} | {_cell(r['impl_blocking'])} | "
            f"{_cell(r['impl_dismissed'])} | {_cell(r['impl_final_clean'])} "
            f"| {esc} |")
    if warnings:
        lines += ["", "**Skipped ledger versions:**", ""]
        lines += [f"- {w}" for w in warnings[:20]]
    return "\n".join(lines) + "\n"


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: (str(r[c]).lower() if isinstance(r.get(c), bool)
                            else r.get(c, "")) for c in COLUMNS})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-dir", default=".")
    ap.add_argument("--ref", action="append", default=None,
                    help="ref whose history to read (repeatable; default "
                         "HEAD)")
    ap.add_argument("--escapes-dir", default=None,
                    help="directory of escapes.json files from "
                         "specdev-escapes.yml artifacts")
    ap.add_argument("--previous", default=None,
                    help="an earlier loop-stats.csv to carry escape counts "
                         "forward from")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args(argv)
    refs = tuple(args.ref or ["HEAD"])
    rows, warnings = build_rows(args.repo_dir, refs, args.escapes_dir,
                                args.previous)
    summ = summary(rows)
    md = render_md(rows, summ, refs, warnings)
    if args.csv:
        write_csv(rows, args.csv)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summ, "rows": rows, "warnings": warnings},
                       indent=2, default=str) + "\n", encoding="utf-8")
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
