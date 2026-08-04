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
    profile.py show   [--root .] [--unit .] [--index FILE] [--key KEY]
    profile.py matrix [--root .] [--index FILE]
"""
import argparse
import json
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
