#!/usr/bin/env python3
"""Governance profile resolution for SpecDev product repos.

A *profile* scales how much of the pipeline runs at a unit's maturity, so a
disposable spike skips ceremony a production service must clear. The table is
declared per maturity value in the org's governance/classification.json and
travels to product repos inside governance/adr/index.json, which already
embeds the classification scheme verbatim.

Three rules, in order, and all three are load-bearing:

  1. Resolve from EFFECTIVE classification, never declared. units.effective()
     pulls a unit up when a higher-classified unit depends on it. Keying off
     the DECLARED value would let anyone park risky code in a poc unit, import
     it from prod, and skip QA, coverage and traceability along with the org
     ADRs -- a wider hole than the one multi-unit support exists to close.
  2. Strictest wins across a set. effective() returns a SET per axis;
     resolution is monotone, so a higher rank can never yield a looser profile.
  3. Fail closed. Unconfigured governance, an unreachable index, a missing or
     unparseable profile -> full production governance. "Inert" means STRICT
     here, the opposite of the org-ADR gate's inert-means-skip: undeclared
     governance is never a discount.

The FLOOR is applied after the table and cannot be disabled by it.

Usage:
    profile.py --root . show             [--unit .] [--index FILE] [--key KEY]
    profile.py --root . matrix           [--index FILE]
    profile.py --root . promotion-check  --changed-from REF [--index FILE]

NOTE: --root is a top-level argument and must precede the subcommand. --index
belongs to the subcommand and must follow it. Using --index before the
subcommand is a usage error.
"""
import argparse
import json
import subprocess
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

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import units  # noqa: E402  (vendored sibling module)
import check_org_adrs as cch  # noqa: E402

MATURITY_AXIS = "maturity"

# The strictest value of every profile key. These ARE the defaults: any key the
# table omits, mistypes, or that we cannot resolve lands here.
STRICTEST = {
    "spec_bar": "full",
    "spec_pr": True,
    "adrs": True,
    "per_wave_qa": True,
    "coverage_gate": True,
    "traceability": True,
    "compliance": True,
    "prod_promotion": True,
}

# Non-overridable. Credential protection and containment are not ceremony, so
# the table may not switch them off. Applied AFTER the table.
FLOOR = {
    "secret_scan": True,
    "scope_check": True,
    "sast": True,
    "findings": True,
    "smoke_test": True,
    "org_adr_check": True,
}

_SPEC_BAR_RANK = {"charter": 0, "full": 1}


def _strictest(key: str, a, b):
    """Fold two values for one key, keeping the more governed one."""
    if key == "spec_bar":
        return max((a, b), key=lambda v: _SPEC_BAR_RANK.get(v, 1))
    return bool(a) or bool(b)


def _sanitize(tbl) -> dict:
    """One declared profile -> a complete, type-checked profile.

    Anything unrecognised is dropped with a warning and the strictest default
    kept, so a typo in the org's table can never quietly widen the fast path."""
    out = dict(STRICTEST)
    if not isinstance(tbl, dict):
        return out
    for k, v in tbl.items():
        if k in FLOOR:
            print(f"WARNING: profile key '{k}' is part of the non-overridable "
                  f"floor — ignoring the table's value", file=sys.stderr)
            continue
        if k not in STRICTEST:
            print(f"WARNING: unknown profile key '{k}' — ignoring", file=sys.stderr)
            continue
        if k == "spec_bar":
            if v not in _SPEC_BAR_RANK:
                print(f"WARNING: spec_bar '{v}' is not one of "
                      f"{sorted(_SPEC_BAR_RANK)} — using '{STRICTEST[k]}'",
                      file=sys.stderr)
                continue
            out[k] = v
        else:
            if not isinstance(v, bool):
                print(f"WARNING: profile key '{k}' must be a boolean, got "
                      f"{v!r} — using {STRICTEST[k]}", file=sys.stderr)
                continue
            out[k] = v
    return out


def compose(values, axes: dict) -> dict:
    """Fold the profiles of every maturity value in `values`, strictest wins.

    An empty set means we could not determine a maturity, which is a
    fail-closed case, not an empty fold."""
    vdefs = axes.get(MATURITY_AXIS, {}).get("values", {})
    out = None
    for v in sorted(values):
        p = _sanitize(vdefs.get(v, {}).get("profile"))
        out = p if out is None else {k: _strictest(k, out[k], p[k]) for k in STRICTEST}
    if out is None:
        out = dict(STRICTEST)
    out.update(FLOOR)
    return out


def _fallback(reason: str) -> dict:
    """Fail closed, and say why. A silent strict fallback is indistinguishable
    from a correctly-resolved prod profile at the point of failure."""
    print(f"NOTE: falling back to full production governance — {reason}",
          file=sys.stderr)
    return {**STRICTEST, **FLOOR}


def _load_index(root: Path):
    """Reuse check_org_adrs' single fetcher rather than adding a second one, so
    exactly one code path decides which ref the org's policy is read at.
    fetch_index() exits the process on failure; for profiles that must be a
    fail-closed fallback instead, so SystemExit is caught (and, since any
    unhandled exception here must never propagate out of a resolution path
    that is supposed to be the fail-closed fallback of last resort, so is
    every other Exception).

    Returns (index_or_None, reason_or_None, adopted). `adopted` distinguishes
    "governance isn't configured here at all" (legitimately inert -- there is
    nothing to fail closed FROM) from "governance IS configured but the index
    couldn't be reached" (a real failure): callers that must stay silent in
    the first case and refuse to report success in the second need to tell
    them apart."""
    link = cch.resolve_link(root)
    if link is None or not str(link.get("governance_repo", "")).strip():
        # A registry/org.json with no governance_repo key at all (e.g. a
        # migrated unit's org.json, which deliberately carries none) is the
        # same "not adopted" case as no link at all -- not a crash. Without
        # this, fetch_index(link) below raises KeyError.
        return None, "governance not adopted (no org.json / registry link)", False
    if "REPLACE_ME" in json.dumps(link.get("governance_repo", "")):
        return None, "governance link still holds REPLACE_ME", False
    try:
        return cch.fetch_index(link), None, True
    except (SystemExit, Exception):
        return None, "org ADR index could not be fetched", True


def _max_strict(axes: dict) -> dict:
    """{axis: set(values)} at the strictest possible point of every axis.

    Used when a unit's classification is present but INVALID: that unit must
    still propagate maximal strictness to whatever depends on it via
    units.effective(), so a corrupt file can never be a lighter-touch stand-in
    for a declared value. Ordered axes collapse to their highest-rank value
    (mirroring units.combine()); unordered axes contribute every value, since
    there is no single "strictest" member to pick. Value names are read from
    the index rather than hardcoded, so this holds for any org's scheme."""
    out = {}
    for axis, adef in axes.items():
        values = adef.get("values", {})
        if not values:
            continue
        if adef.get("ordered"):
            top = max(values, key=lambda v: values[v].get("rank", -1))
            out[axis] = {top}
        else:
            out[axis] = set(values)
    return out


def _declared(root: Path, entries, axes) -> dict:
    """{unit: {axis: set(values)}} for every unit governance has an opinion on.

    A unit whose org.json is absent, or whose classification is missing or
    still REPLACE_ME, means governance isn't adopted there — it is omitted,
    which lands it in the fail-closed path.

    A unit whose classification is PRESENT but invalid (normalize_classification
    reports errors) is a misconfiguration, not an absence: it is included at
    the strictest point of every axis so units.effective() propagates that
    strictness to whatever depends on it, instead of silently contributing
    nothing."""
    out = {}
    for e in entries:
        p = root / e["path"] / ".specdev" / "org.json"
        if not p.exists():
            continue
        raw = cch.load_json(p).get("classification")
        if raw is None or "REPLACE_ME" in json.dumps(raw):
            continue
        norm, errors = cch.normalize_classification(raw, axes)
        if errors:
            for err in errors:
                print(f"WARNING: {e['path']}: {err}", file=sys.stderr)
            out[e["path"]] = _max_strict(axes)
            continue
        out[e["path"]] = norm
    return out


def _for_propagation(entries, axes, declared) -> dict:
    """`declared`, extended with max-strictness for every unit that declares
    NOTHING (no org.json, or REPLACE_ME) -- so an omitted classification pulls
    its dependencies up to strict exactly as a present-but-invalid one does.
    Property 1 (resolve from EFFECTIVE classification) is a hole otherwise: a
    unit that simply never adds an org.json contributes nothing to
    units.effective()'s pull-up, unlike the present-but-invalid path, which
    correctly injects _max_strict via `declared` itself.

    Used ONLY to seed units.effective()'s propagation. resolve_all's own
    fail-closed checks (`not declared`, `u not in declared`) must keep reading
    the ORIGINAL `declared` -- if they read this instead, a repo where NOBODY
    has adopted governance would be indistinguishable from one where every
    unit was declared, and the whole-repo 'governance not configured'
    fail-closed path would never fire."""
    out = dict(declared)
    strict = _max_strict(axes)
    for e in entries:
        if e["path"] not in out:
            out[e["path"]] = strict
    return out


def resolve_all(root=".", index=None) -> dict:
    """{unit path: profile} for every registered unit."""
    root = Path(root)
    entries = units.unit_entries(root)
    if index is None:
        index, why, _adopted = _load_index(root)
        if index is None:
            return {e["path"]: _fallback(why) for e in entries}
    axes = index.get("axes", {})
    declared = _declared(root, entries, axes)
    if not declared:
        return {e["path"]: _fallback("no unit declares a classification")
                for e in entries}
    eff = units.effective(entries, axes, _for_propagation(entries, axes, declared))
    out = {}
    for e in entries:
        u = e["path"]
        if u not in declared:
            out[u] = _fallback(f"unit '{u}' declares no usable classification")
            continue
        values = eff.get(u, {}).get(MATURITY_AXIS) or set()
        out[u] = compose(values, axes) if values else _fallback(
            f"unit '{u}' resolves to no '{MATURITY_AXIS}' value")
    return out


def resolve(root=".", unit=".", index=None) -> dict:
    """One unit's profile. Unknown units fail closed rather than KeyError."""
    allp = resolve_all(root, index)
    if unit not in allp:
        return _fallback(f"unit '{unit}' is not registered")
    return allp[unit]


def _emit(value) -> str:
    """Render one key for shell/YAML consumption: JSON booleans lowercase,
    strings bare (no quotes), so `if:` expressions read naturally."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def governance_adopted_at(root, ref: str) -> bool:
    """True when governance_repo is configured at a git ref, False otherwise.

    Reads via `git show` rather than the worktree so the BASE side of the
    comparison is the committed state, not whatever is checked out. Tries
    .specdev/units.json first, then .specdev/org.json."""
    for path in [".specdev/units.json", ".specdev/org.json"]:
        r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=str(root),
                           capture_output=True, text=True)
        if r.returncode == 0:
            try:
                doc = json.loads(r.stdout)
                repo = doc.get("governance_repo", "")
                if repo and not "REPLACE_ME" in json.dumps(repo):
                    return True
            except json.JSONDecodeError:
                pass
    return False


def maturity_at(root, unit: str, ref: str) -> str | None:
    """This unit's declared maturity at a git ref, or None if unreadable.

    Reads via `git show` rather than the worktree so the BASE side of the
    comparison is the committed state, not whatever is checked out."""
    rel = ".specdev/org.json" if unit == "." else f"{unit}/.specdev/org.json"
    r = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=str(root),
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        cls = json.loads(r.stdout).get("classification")
    except json.JSONDecodeError:
        return None
    if isinstance(cls, str):
        return cls
    if isinstance(cls, dict):
        return cls.get(MATURITY_AXIS)
    return None


def has_poc_history(root, unit: str) -> bool:
    """True when this unit has actually been BUILT under the poc lane.

    Two independent signals, either sufficient: run.json recording mode 'poc'
    (written by run_manifest.py), and a poc release tag (written by
    deploy-poc.yml, namespaced by unit slug the same way)."""
    rm = Path(root) / unit / ".specdev" / "run.json"
    if rm.exists():
        try:
            if cch.load_json(rm).get("mode") == "poc":
                return True
        except (OSError, ValueError, AttributeError):
            # ValueError covers json.JSONDecodeError and a bad-encoding
            # UnicodeDecodeError; AttributeError covers a well-formed but
            # non-dict run.json (a list/string/number has no .get). None of
            # these should abort the whole promotion check -- fall through to
            # the tag signal instead.
            pass
    slug = unit.replace("/", "-").replace(".", "-").strip("-")
    pattern = f"poc-{slug}-[0-9]*" if slug else "poc-[0-9]*"
    r = subprocess.run(["git", "tag", "--list", pattern], cwd=str(root),
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def promotion_errors(root=".", base=None, index=None) -> list[str]:
    """poc units are never promoted in place; they are reverse-mapped and
    rebuilt. Ranks come from the org index, so an org that renames its maturity
    values keeps a working rule instead of a silently inert one.

    Inert with no base: this is a diff-driven rule, and there is nothing to
    compare against. The staleness half of org-adr-check is what stays
    unfiltered, not this."""
    if not base:
        return []
    root = Path(root)
    if index is None:
        index, why, _adopted = _load_index(root)
        if index is None:
            print(f"NOTE: promotion check skipped — {why}", file=sys.stderr)
            return []
    ranks = {v: d.get("rank", -1) for v, d in
             index.get("axes", {}).get(MATURITY_AXIS, {}).get("values", {}).items()}
    errors = []
    for unit in units.unit_paths(root):
        was = maturity_at(root, unit, base)
        now = maturity_at(root, unit, "HEAD")
        if not was or not now or was == now:
            continue
        if was in ranks and now in ranks and ranks[now] <= ranks[was]:
            continue  # demotion or lateral move
        if not has_poc_history(root, unit):
            continue
        errors.append(
            f"unit '{unit}' was built under the poc lane and this change "
            f"promotes it {was} -> {now}. poc units are not promoted in "
            f"place: reverse-map it with the spec-explorer agent and rebuild "
            f"it as a new unit through the full pipeline.")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("show", help="one unit's resolved profile")
    ps.add_argument("--unit", default=".")
    ps.add_argument("--key", help="print just this key")
    ps.add_argument("--index", help="local index.json (skip fetching; offline/tests)")
    pm = sub.add_parser("matrix", help="{unit: profile} for every unit, one JSON line")
    pm.add_argument("--index", help="local index.json (skip fetching; offline/tests)")
    pp = sub.add_parser("promotion-check",
                        help="reject in-place promotion of a poc-built unit")
    pp.add_argument("--changed-from", required=True)
    pp.add_argument("--index", help="local index.json (skip fetching; offline/tests)")
    args = ap.parse_args()

    index = cch.load_json(Path(args.index)) if args.index else None

    if args.cmd == "promotion-check":
        base = (args.changed_from or "").strip()
        if not base:
            print(f"ERROR: promotion check cannot run: no base ref given. "
                  f"A promotion check without a base compares nothing, so it "
                  f"would report success without verifying anything. On "
                  f"workflow_dispatch, pass the run's own start SHA explicitly.",
                  file=sys.stderr)
            print("Refusing to report success without checking anything.",
                  file=sys.stderr)
            return 2
        # An explicit --index is used as-is (offline/tests). Otherwise resolve
        # the index HERE, before delegating to promotion_errors(), so this CLI
        # can tell "no unit is being promoted" (real success) apart from
        # "couldn't check" (fetch/token failure) -- promotion_errors() itself
        # stays silent-and-empty on either, by design, so it alone can't make
        # that distinction.
        if index is None:
            # Check adoption at both base and HEAD. If governance was adopted
            # at either point, we must enforce (exit 2 on unreachable index).
            # This prevents removing governance_repo in the same PR that
            # promotes a poc unit from bypassing the check.
            root = Path(args.root)
            adopted_base = governance_adopted_at(root, base)
            index, why, adopted_head = _load_index(root)
            adopted = adopted_base or adopted_head
            if index is None:
                if not adopted:
                    # Governance genuinely isn't configured here at either point --
                    # the same inert case check_org_adrs.py treats as OK, not a failure.
                    print(f"promotion check skipped — {why}.")
                    return 0
                print(f"ERROR: promotion check cannot run: {why}. A promotion "
                      f"check that cannot reach the governance index would "
                      f"report success without verifying anything.",
                      file=sys.stderr)
                print("Refusing to report success without checking anything.",
                      file=sys.stderr)
                return 2
        errs = promotion_errors(args.root, base, index)
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        if errs:
            return 1
        print("promotion check ok — no poc-built unit is being promoted in place.")
        return 0

    if args.cmd == "matrix":
        try:
            out = resolve_all(args.root, index)
        except Exception as exc:
            # A gate that cannot resolve profiles must run everything, not
            # emit nothing: a crash here would leave `matrix`'s
            # $GITHUB_OUTPUT capture empty (or the step non-zero), and every
            # downstream `if: fromJSON(...)[unit].KEY` then evaluates
            # fromJSON('') -- exactly the fail-OPEN failure mode property 3
            # forbids. Fall back to the strictest profile for every
            # registered unit instead.
            print(f"NOTE: profile matrix resolution failed ({exc}) — falling "
                  f"back to full production governance for every registered "
                  f"unit", file=sys.stderr)
            strict = {**STRICTEST, **FLOOR}
            try:
                entries = units.unit_entries(args.root)
            except Exception:
                entries = [dict(units.ROOT_UNIT)]
            out = {e["path"]: dict(strict) for e in entries}
        print(json.dumps(out, sort_keys=True))
        return 0

    prof = resolve(args.root, args.unit, index)
    if args.key:
        if args.key not in prof:
            print(f"ERROR: unknown profile key '{args.key}' — one of "
                  f"{sorted(prof)}", file=sys.stderr)
            return 1
        print(_emit(prof[args.key]))
        return 0
    print(json.dumps(prof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
