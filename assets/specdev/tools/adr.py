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
import itertools
import json
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

# entry_matches / build_value_map are the SAME applies_to semantics the org-ADR
# CI gate uses. Importing rather than reimplementing keeps the shortlist and the
# gate from ever disagreeing. check_org_adrs.py sits beside this file in both
# layouts: .specdev/tools/ in a product repo, assets/specdev/tools/ in the
# governance repo.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_org_adrs  # noqa: E402  (vendored sibling module)

build_value_map = check_org_adrs.build_value_map
entry_matches = check_org_adrs.entry_matches

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


PLACEHOLDER = re.compile(r"<[^<>\n]{2,}>|\bTBD\b|\bTODO\b")
# Placeholder scanning must ignore code spans: `<value>+` and `` `<owner/name>` ``
# are syntax documentation (e.g. governance/adr/ADR-0001-repo-classification.md),
# not a leftover template stub like a bare <decision title> in prose. Strip
# fenced code blocks first (they may themselves contain single backticks), then
# double-backtick spans, then single-backtick spans, replacing each with spaces
# so surrounding offsets/line structure are preserved for any later matching.
FENCED_CODE_BLOCK = re.compile(r"```.*?```", re.S)
DOUBLE_BACKTICK_SPAN = re.compile(r"``[^\n]*?``")
SINGLE_BACKTICK_SPAN = re.compile(r"`[^`\n]*`")


def strip_code_spans(text: str) -> str:
    """Blank out fenced code blocks and backtick-delimited code spans (single
    and double backtick) so placeholder scanning only sees prose."""
    text = FENCED_CODE_BLOCK.sub(lambda m: " " * len(m.group(0)), text)
    text = DOUBLE_BACKTICK_SPAN.sub(lambda m: " " * len(m.group(0)), text)
    text = SINGLE_BACKTICK_SPAN.sub(lambda m: " " * len(m.group(0)), text)
    return text


LOCAL_REQUIRED = ("id", "title", "status", "date", "relates_to")
ORG_REQUIRED = ("id", "title", "status", "applies_to", "summary")
OPTION_RE = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*", re.M)
CONFORMANCE_ITEM = re.compile(r"^\s*-\s*\[[ xX]\]\s*(.*)$", re.M)


def spec_req_ids(root: Path, unit: str) -> set[str]:
    """Every REQ id declared by the unit's active + archived specs."""
    base = root / unit / ".specdev"
    paths = [base / "spec.md"]
    if (base / "specs").is_dir():
        paths += sorted((base / "specs").glob("*.md"))
    ids: set[str] = set()
    for p in paths:
        if p.exists():
            ids |= set(REQ_RE.findall(p.read_text(encoding="utf-8-sig")))
    return ids


def _option_blocks(text: str) -> list[str]:
    """Split an '## Options' body into one string per numbered option."""
    starts = [m.start() for m in OPTION_RE.finditer(text)]
    return [text[s:e] for s, e in zip(starts, starts[1:] + [len(text)])]


def _labelled(text: str, label: str) -> str:
    r"""Content following 'Label:' on its bullet, '' when absent or empty.

    The whitespace after the colon must be horizontal-only (`[ \t]*`, not
    `\s*`): `\s*` also matches newlines, so an empty '- Pros:' bullet
    followed by a '- Cons: ...' line would consume the newline and read the
    NEXT label's text as its own content, silently passing lint.
    """
    m = re.search(rf"{label}\s*:[ \t]*(.*)", text, re.I)
    return m.group(1).strip() if m else ""


def lint_one(a: dict, mode: str, req_ids: set[str]) -> list[str]:
    """Structural quality gate for one ADR. Empty list = clean."""
    errs: list[str] = []
    name = a["file"]

    if a["is_template"]:
        return [f"{name}: unfilled template — fill it in or delete it"]

    required = ORG_REQUIRED if mode == "org" else LOCAL_REQUIRED
    if not a["has_fm"]:
        errs.append(f"{name}: no YAML frontmatter — new ADRs must declare "
                    f"{', '.join(required)}")
    else:
        for key in required:
            if not a["fm"].get(key):
                errs.append(f"{name}: frontmatter key '{key}' is missing or empty")

    stem = a["path"].stem
    if a["id"] and not (stem == a["id"] or stem.startswith(a["id"] + "-")):
        errs.append(f"{name}: id '{a['id']}' disagrees with the filename")

    if a["status"] and a["status"] not in STATUSES:
        errs.append(f"{name}: status '{a['status']}' not in {sorted(STATUSES)}")

    for m in PLACEHOLDER.finditer(strip_code_spans(a["text"])):
        errs.append(f"{name}: placeholder text left in — '{m.group(0)}'")
        break

    secs = a["sections"]
    options = _option_blocks(secs.get("options", ""))
    if len(options) < 2:
        errs.append(f"{name}: '## Options' needs at least two options — an ADR "
                    "with nothing rejected records no decision")
    for block in options:
        title = OPTION_RE.search(block).group(1)
        for label in ("Pros", "Cons"):
            if not _labelled(block, label):
                errs.append(f"{name}: option '{title}' has an empty {label}")

    if not secs.get("decision", "").strip():
        errs.append(f"{name}: '## Decision' is empty")

    consequences = secs.get("consequences", "")
    if not _labelled(consequences, "Positive"):
        errs.append(f"{name}: '## Consequences' has no Positive entry")
    if not _labelled(consequences, r"Negative(?: / risks)?"):
        errs.append(f"{name}: '## Consequences' has no Negative / risks entry — "
                    "a decision with no cost has not been thought through")

    if mode == "local":
        unknown = [r for r in a["relates_to"] if req_ids and r not in req_ids]
        for r in unknown:
            errs.append(f"{name}: relates_to names {r}, which is in no spec")
        if a["has_fm"]:
            if a["prose_status"] and a["prose_status"] != a["status"]:
                errs.append(f"{name}: status drift — frontmatter says "
                            f"'{a['status']}', the '**Status:**' line says "
                            f"'{a['prose_status']}'")
            if set(a["prose_relates_to"]) != set(a["relates_to"]):
                errs.append(f"{name}: relates_to drift — frontmatter has "
                            f"{sorted(a['relates_to'])}, the 'Relates to:' line "
                            f"has {sorted(a['prose_relates_to'])}")
    else:
        items = [i.strip() for i in CONFORMANCE_ITEM.findall(secs.get("conformance", ""))]
        if not items:
            errs.append(f"{name}: '## Conformance' has no '- [ ]' items — org "
                        "ADRs must state what a conforming repo looks like")
        for item in items:
            if not item or PLACEHOLDER.search(strip_code_spans(item)):
                errs.append(f"{name}: Conformance item is a placeholder: '{item}'")

    return errs


def lint_target(a: dict, mode: str, req_ids: set[str],
                 single_file: bool) -> tuple[list[str], list[str]]:
    """CLI-facing wrapper around lint_one: (errors, warnings).

    A pre-existing prose-only ADR (no YAML frontmatter) is explicitly NOT
    migrated per the design's Non-goals, and remains valid — sweeping a whole
    directory must not hard-fail on one. Linting a single --file means you
    are authoring/editing that ADR right now, so the same gap there is a real
    ERROR, not a warning.
    """
    if not single_file and not a["has_fm"]:
        return [], [f"{a['file']}: no YAML frontmatter — legacy ADR, left as-is "
                     "(pass --file to hold it to the current structure)"]
    return lint_one(a, mode, req_ids), []


def hard_conflicts(adrs: list[dict]) -> list[str]:
    """Structural conflicts needing no judgment. Empty list = clean."""
    errs: list[str] = []
    by_id: dict[str, dict] = {}
    for a in adrs:
        if a["id"] in by_id:
            errs.append(f"duplicate id {a['id']}: {by_id[a['id']]['file']} and {a['file']}")
            continue
        by_id[a["id"]] = a

    successors: dict[str, list[str]] = {}
    for a in adrs:
        for target in a["supersedes"]:
            successors.setdefault(target, []).append(a["id"])

    for a in adrs:
        for target_id in a["supersedes"]:
            target = by_id.get(target_id)
            if target is None:
                errs.append(f"{a['file']}: supersedes {target_id}, which does not exist")
                continue
            claimants = [c for c in successors.get(target_id, []) if c != a["id"]]
            if claimants:
                errs.append(f"{a['file']}: {target_id} is already superseded by "
                            f"{', '.join(sorted(claimants))} — an ADR is replaced once")
            elif target["status"] != "superseded":
                errs.append(f"{a['file']}: supersedes {target_id}, but {target_id} is "
                            f"still '{target['status']}' — set it to 'superseded'")
            elif target["superseded_by"] != a["id"]:
                errs.append(f"{a['file']}: supersession is not reciprocal — "
                            f"{target_id}.superseded_by is "
                            f"'{target['superseded_by'] or '(unset)'}', expected {a['id']}")

        if a["status"] == "superseded" and not a["superseded_by"]:
            errs.append(f"{a['file']}: status is 'superseded' with no superseded_by — "
                        "record which ADR replaced it")
        if a["superseded_by"]:
            if a["superseded_by"] not in by_id:
                errs.append(f"{a['file']}: superseded_by {a['superseded_by']}, "
                            "which does not exist")
            elif a["status"] != "superseded":
                errs.append(f"{a['file']}: superseded_by is set but status is "
                            f"'{a['status']}' — it must be 'superseded'")

    return errs


def all_classifications(axes: dict):
    """Every concrete classification the scheme can express, one axis value
    each. Schemes are tiny (a handful of values per axis), so enumerating is
    exact and cheaper to reason about than interval arithmetic.

    LIMITATION, deliberately unaddressed: this yields exactly one value per
    axis, so it never generates a classification bound by TWO values on the
    SAME axis at once (e.g. an applies_to pulled up to cover both `internal`
    and `customer` on an `audience` axis simultaneously). An overlap that
    only exists for that multi-value combination would be missed. In
    practice a repo's own classification is single-valued per axis; the
    multi-value case only arises from a defensive pull-up across dependents,
    which is rare enough that exact single-value enumeration remains the
    right precision/cost trade-off over interval arithmetic."""
    names = sorted(axes)
    value_lists = [sorted(axes[n].get("values", {})) for n in names]
    for combo in itertools.product(*value_lists):
        yield {n: {v} for n, v in zip(names, combo)}


def applies_to_overlap(a_entries, b_entries, axes, vmap) -> dict | None:
    """A classification bound by BOTH ADRs, or None. The witness is returned so
    the message can name who is caught by both."""
    for cls in all_classifications(axes):
        if (any(entry_matches(e, cls, axes, vmap) for e in a_entries)
                and any(entry_matches(e, cls, axes, vmap) for e in b_entries)):
            return cls
    return None


SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
SUMMARY_MAX = 160


def _summary(a: dict) -> str:
    if a.get("summary"):
        return a["summary"]
    decision = a["sections"].get("decision", "").strip()
    if not decision:
        return ""
    paragraph = " ".join(decision.split("\n\n", 1)[0].split())
    text = SENTENCE_END.split(paragraph, maxsplit=1)[0]
    if len(text) > SUMMARY_MAX:
        text = text[:SUMMARY_MAX - 3].rstrip() + "..."
    return text


def shortlist(target: dict, adrs: list[dict], mode: str, axes: dict | None) -> list[dict]:
    """Accepted ADRs that could contradict `target`. Judgment happens upstream —
    this only narrows the field the skill has to read."""
    vmap = build_value_map(axes) if (mode == "org" and axes) else {}
    out = []
    for other in adrs:
        if other["id"] == target["id"] or other["status"] != "accepted":
            continue
        reasons = []
        shared_scopes = sorted(set(target["scopes"]) & set(other["scopes"]))
        if shared_scopes:
            reasons.append(f"shares scope(s): {', '.join(shared_scopes)}")
        if mode == "local":
            shared_reqs = sorted(set(target["relates_to"]) & set(other["relates_to"]))
            if shared_reqs:
                reasons.append(f"both decide {', '.join(shared_reqs)}")
        elif axes:
            witness = applies_to_overlap(target["applies_to"], other["applies_to"],
                                         axes, vmap)
            if witness:
                where = ", ".join(f"{a}: {next(iter(witness[a]))}" for a in sorted(witness))
                reasons.append(f"both bind ({where})")
        if reasons:
            out.append({"id": other["id"], "file": other["file"],
                        "title": other["title"], "summary": _summary(other),
                        "reasons": reasons})
    return out


UNIT_UNSET = object()  # sentinel: distinguishes "--unit not passed" from "--unit ."


def resolve_mode(args) -> str:
    if args.mode != "auto":
        return args.mode
    if getattr(args, "file", None):
        parts = Path(args.file).resolve().as_posix()
        if "/governance/adr/" in parts:
            return "org"
        if "/.specdev/adr/" in parts:
            return "local"
    unit = args.unit if args.unit is not UNIT_UNSET else "."
    return detect_mode(Path(args.root), unit)


def infer_unit_from_file(file_path: Path, root: Path) -> str | None:
    """When `file_path` lives under `<unit>/.specdev/adr/`, return `<unit>`
    relative to `root` (posix-style, "." if the unit IS the root). None if
    the file isn't under a recognisable local ADR directory."""
    resolved = file_path.resolve()
    adr_directory = resolved.parent
    if adr_directory.name != "adr" or adr_directory.parent.name != ".specdev":
        return None
    unit_root = adr_directory.parent.parent
    try:
        rel = unit_root.relative_to(root.resolve())
    except ValueError:
        return None
    return rel.as_posix() if str(rel) != "." else "."


def resolve_unit(args, root: Path, mode: str) -> str:
    """--unit, if the caller passed it, always wins. Otherwise, when --file
    is given, the ADR set compared against (and, for lint, the spec whose
    REQ ids are checked) must come from the FILE'S OWN unit — not the '.'
    default — or a monorepo run silently checks the wrong (often empty)
    directory and reports a false green."""
    if args.unit is not UNIT_UNSET:
        return args.unit
    if mode == "local" and getattr(args, "file", None):
        inferred = infer_unit_from_file(Path(args.file), root)
        if inferred is not None:
            return inferred
    return "."


def add_common(p):
    p.add_argument("--root", default=".")
    p.add_argument("--unit", default=UNIT_UNSET,
                    help="governed unit (default: '.', or the --file's own "
                         "unit when --file is given and --unit is omitted)")
    p.add_argument("--mode", default="auto", choices=["auto", "local", "org"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SpecDev ADR authoring support")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_common(sub.add_parser("next-id", help="print the next free ADR id"))
    lint_p = sub.add_parser("lint", help="structural quality gate")
    add_common(lint_p)
    lint_p.add_argument("--file", help="lint one ADR instead of the directory")
    conf_p = sub.add_parser("conflicts", help="structural + candidate conflicts")
    add_common(conf_p)
    conf_p.add_argument("--file", help="the ADR being written or changed")
    conf_p.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    root = Path(args.root)
    mode = resolve_mode(args)
    unit = resolve_unit(args, root, mode)

    if args.cmd == "next-id":
        print(next_id(load_adrs(adr_dir(root, unit, mode)), mode))
        return 0

    if args.cmd == "lint":
        req_ids = spec_req_ids(root, unit) if mode == "local" else set()
        if args.file:
            targets = [load_adr(Path(args.file))]
            single_file = True
        else:
            targets = load_adrs(adr_dir(root, unit, mode))
            single_file = False
        errs, warns = [], []
        for a in targets:
            e, w = lint_target(a, mode, req_ids, single_file)
            errs += e
            warns += w
        for w in warns:
            print(f"WARN: {w}")
        for e in errs:
            print(f"ERROR: {e}")
        if errs:
            print(f"\nADR lint FAILED: {len(errs)} error(s) in {len(targets)} ADR(s)")
            return 1
        suffix = f", {len(warns)} warning(s)" if warns else ""
        print(f"ADR lint passed ({len(targets)} ADR(s){suffix}).")
        return 0

    if args.cmd == "conflicts":
        adrs = load_adrs(adr_dir(root, unit, mode))
        errs = hard_conflicts(adrs)
        axes = None
        if mode == "org":
            scheme = root / "governance" / "classification.json"
            if scheme.exists():
                axes = json.loads(scheme.read_text(encoding="utf-8-sig"))["axes"]

        items = []
        if args.file:
            target = load_adr(Path(args.file))
            items = shortlist(target, adrs, mode, axes)

        if args.json:
            print(json.dumps({"hard_errors": errs, "shortlist": items}, indent=2))
            return 1 if errs else 0

        for e in errs:
            print(f"ERROR: {e}")
        if errs:
            print(f"\nADR conflicts FAILED: {len(errs)} structural error(s)")
            return 1
        if not args.file:
            print(f"No structural conflicts ({len(adrs)} ADR(s)). "
                  "Pass --file to shortlist overlap candidates.")
            return 0
        if not items:
            print(f"No structural conflicts and an empty shortlist "
                  f"({len(adrs)} ADR(s) checked).")
            return 0
        print(f"Shortlist — {len(items)} accepted ADR(s) overlap this one; "
              "read their Decision sections and rule on each:")
        for i in items:
            print(f"  {i['id']} ({i['file']}) — {i['title']}")
            print(f"      why: {'; '.join(i['reasons'])}")
            if i["summary"]:
                print(f"      says: {i['summary']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
