#!/usr/bin/env python3
"""Deterministic org-ADR compliance gate for SpecDev product repos.

Reads .specdev/org.json (the link to the org's architectural repo of record),
fetches governance/adr/index.json at the pinned ref, computes which accepted
org ADRs apply to this repo's classification, and verifies the local
verification manifest .specdev/adr/org-compliance.json:

  * every applicable org ADR has an entry;
  * entry status is one of: met | not-applicable | superseded-by-local;
  * non-'met' entries carry a justification;
  * each entry's recorded sha256 matches the index — if the org ADR changed
    upstream, the old verification is stale and this gate fails until the
    adr-checker agent re-verifies.

The semantic judgment ("does this repo actually satisfy the ADR?") is made by
the adr-checker agent, which writes the manifest; this tool deterministically
proves that judgment is complete and current. Exits non-zero on any gap so
org-adr-check.yml blocks the merge.

Inert by design: if org.json is absent or still holds REPLACE_ME values, this
exits 0 so repos that haven't adopted org governance are unaffected.

Usage:
    python .specdev/tools/check_org_adrs.py [--root .] [--index FILE]

--index reads the index from a local file instead of fetching (offline/tests).
GITHUB_TOKEN (or GOVERNANCE_TOKEN) is used as a bearer token when fetching,
for private governance repos.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ALLOWED_STATUSES = {"met", "not-applicable", "superseded-by-local"}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fetch_index(org: dict) -> dict:
    url = (f"https://raw.githubusercontent.com/{org['governance_repo']}/"
           f"{org.get('ref', 'main')}/{org.get('path', 'governance/adr')}/index.json")
    req = urllib.request.Request(url)
    token = os.environ.get("GOVERNANCE_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"ERROR: could not fetch org ADR index from {url}: {e}")
        print("       (once org.json is configured this gate fails closed — "
              "check governance_repo/ref/path, and set GOVERNANCE_TOKEN for a private repo)")
        sys.exit(1)


def build_value_map(axes: dict) -> dict:
    """Map each classification value name -> its axis name."""
    return {v: axis for axis, adef in axes.items() for v in adef.get("values", {})}


def normalize_classification(raw, axes: dict) -> tuple[dict | None, list[str]]:
    """Accept a per-axis object ({'maturity': 'prod', ...}) or, for a
    single-axis scheme, a bare string. Every axis must be declared."""
    errors: list[str] = []
    if isinstance(raw, str):
        if len(axes) == 1:
            raw = {next(iter(axes)): raw}
        else:
            return None, [f"classification is a single value ('{raw}') but the org scheme "
                          f"defines multiple axes ({', '.join(sorted(axes))}) — declare one "
                          "value per axis in org.json, e.g. "
                          '{"maturity": "prod", "audience": "internal"}']
    if not isinstance(raw, dict):
        return None, ["classification must be an object of axis: value pairs"]
    for axis, value in raw.items():
        if axis not in axes:
            errors.append(f"classification axis '{axis}' is not in the org scheme "
                          f"({', '.join(sorted(axes))})")
        elif value not in axes[axis].get("values", {}):
            errors.append(f"classification '{axis}: {value}' is not a defined value "
                          f"({', '.join(sorted(axes[axis].get('values', {})))})")
    for axis in axes:
        if axis not in raw:
            errors.append(f"classification must declare a value for axis '{axis}' "
                          f"({', '.join(sorted(axes[axis].get('values', {})))})")
    return (raw, errors) if not errors else (None, errors)


def entry_matches(entry: str, classification: dict, axes: dict, vmap: dict) -> bool:
    """'all', or '&'-joined conditions ANDed; '<value>+' = that rank and above
    on the value's (ordered) axis. List entries are OR — handled by applies()."""
    if entry == "all":
        return True
    for cond in (c.strip() for c in entry.split("&")):
        plus = cond.endswith("+")
        val = cond[:-1] if plus else cond
        axis = vmap.get(val)
        if axis is None:
            return False  # unknown value: fail safe, never widen applicability
        repo_val = classification.get(axis)
        if repo_val is None:
            return False
        if plus:
            values = axes[axis]["values"]
            if values[repo_val].get("rank", -1) < values[val].get("rank", 0):
                return False
        elif repo_val != val:
            return False
    return True


def applies(adr: dict, classification: dict, axes: dict, vmap: dict) -> bool:
    if adr.get("status") != "accepted":
        return False
    return any(entry_matches(a, classification, axes, vmap)
               for a in adr.get("applies_to", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--index", help="local index.json (skip fetching)")
    args = ap.parse_args()

    root = Path(args.root)
    org_path = root / ".specdev" / "org.json"
    manifest_path = root / ".specdev" / "adr" / "org-compliance.json"
    errors: list[str] = []
    warnings: list[str] = []

    if not org_path.exists():
        print("org.json not found — org ADR governance not adopted; skipping (OK).")
        return 0
    org = load_json(org_path)
    if any("REPLACE_ME" in json.dumps(org.get(k, "")) for k in ("governance_repo", "classification")):
        print("org.json still has REPLACE_ME values — org ADR governance not configured; skipping (OK).")
        return 0

    index = load_json(Path(args.index)) if args.index else fetch_index(org)
    axes = index.get("axes", {})
    vmap = build_value_map(axes)
    classification, cls_errors = normalize_classification(org["classification"], axes)
    if cls_errors:
        for e in cls_errors:
            print(f"ERROR: {e}")
        return 1

    cls_str = ", ".join(f"{axis}: {classification[axis]}" for axis in sorted(classification))
    applicable = [a for a in index.get("adrs", []) if applies(a, classification, axes, vmap)]

    entries: dict[str, dict] = {}
    if manifest_path.exists():
        entries = {e.get("id"): e for e in load_json(manifest_path).get("entries", [])}
    elif applicable:
        print(f"ERROR: {manifest_path} not found but {len(applicable)} org ADR(s) apply — "
              "run the adr-checker agent to verify and write the manifest")
        return 1

    for adr in applicable:
        e = entries.get(adr["id"])
        if e is None:
            errors.append(f"{adr['id']} ({adr['title']}) applies to this repo ({cls_str}) "
                          "but has no manifest entry — not yet verified")
            continue
        status = e.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{adr['id']}: manifest status '{status}' not in "
                          f"{sorted(ALLOWED_STATUSES)}")
            continue
        if status != "met" and not str(e.get("justification", "")).strip():
            errors.append(f"{adr['id']}: status '{status}' requires a justification")
        if e.get("sha256") != adr["sha256"]:
            errors.append(f"{adr['id']}: org ADR changed upstream since verification "
                          "(sha mismatch) — re-run the adr-checker agent")

    known_ids = {a["id"] for a in index.get("adrs", [])}
    applicable_ids = {a["id"] for a in applicable}
    for eid in entries:
        if eid not in known_ids:
            warnings.append(f"manifest entry {eid} is not in the org index (removed upstream?)")
        elif eid not in applicable_ids:
            warnings.append(f"manifest entry {eid} no longer applies to this repo ({cls_str})")

    for w in warnings:
        print(f"WARN:  {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        print(f"\nOrg ADR gate FAILED: {len(errors)} error(s) "
              f"({len(applicable)} applicable org ADR(s) for {cls_str})")
        return 1
    print(f"\nOrg ADR gate passed: {len(applicable)} applicable org ADR(s) verified "
          f"for classification ({cls_str}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
