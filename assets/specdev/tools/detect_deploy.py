#!/usr/bin/env python3
"""Determine a SpecDev repo's deploy target and write .specdev/deploy.profile.json.

Inspects the repo for platform signatures and proposes a profile (target,
params, rollback strategy, environment URLs) that deploy.py then executes — so
the GitHub workflows stay generic and rollback is built in, not a per-repo TODO.

Re-run any time the stack changes. Unknown targets are written as 'manual' and
flagged loudly — never silently guessed. Existing env URLs / params you've
edited are preserved.

Usage:
    python .specdev/tools/detect_deploy.py [--root .]
"""
import argparse
import json
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

try:  # UTF-8 stdout/stderr on Windows consoles (cp1252) so output never crashes
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROFILE_REL = ".specdev/deploy.profile.json"

# Platforms whose rollback we can do natively (deploy.py knows the command).
NATIVE_ROLLBACK = {"fly", "vercel", "helm", "kubernetes"}


def first(root: Path, *names: str) -> str | None:
    for n in names:
        if (root / n).exists():
            return n
        hits = list(root.glob(n))
        if hits:
            return hits[0].relative_to(root).as_posix()
    return None


def detect(root: Path):
    params: dict[str, str] = {}

    if sig := first(root, "fly.toml"):
        m = re.search(r'app\s*=\s*"([^"]+)"', (root / "fly.toml").read_text(encoding="utf-8"))
        params["app"] = m.group(1) if m else "REPLACE_ME"
        return "fly", sig, params

    if sig := first(root, "vercel.json", ".vercel"):
        return "vercel", sig, params

    if sig := first(root, "netlify.toml"):
        params["app"] = "REPLACE_ME"  # netlify site name/id
        return "netlify", sig, params

    if sig := first(root, "serverless.yml", "serverless.ts", "serverless.yaml"):
        return "serverless", sig, params

    if sig := first(root, "template.yaml", "samconfig.toml"):
        return "sam", sig, params

    if sig := first(root, "Chart.yaml", "**/Chart.yaml", "helm", "charts"):
        chart = Path(sig)
        chart_dir = chart.parent.as_posix() if chart.name == "Chart.yaml" else sig
        name = "REPLACE_ME"
        cy = root / sig if sig.endswith("Chart.yaml") else None
        if cy and cy.exists():
            m = re.search(r"^name:\s*(\S+)", cy.read_text(encoding="utf-8"), re.M)
            name = m.group(1) if m else name
        params.update(release=name, chart=chart_dir or ".", namespace="default")
        return "helm", sig, params

    if sig := first(root, "kustomization.yaml", "k8s", "kubernetes", "manifests"):
        params.update(scan_k8s(root, sig))
        return "kubernetes", sig, params

    if sig := first(root, ".specdev/deploy/deploy.sh"):
        return "script", sig, params

    if sig := first(root, "Dockerfile"):
        # Artifact type is known but the destination isn't — push to the script
        # escape hatch and flag it.
        params["note"] = "Dockerfile found but no orchestration target; add .specdev/deploy/deploy.sh or set a concrete target."
        return "script", sig, params

    return "manual", None, params


def scan_k8s(root: Path, sig: str) -> dict:
    """Offline discovery: pull deployment name, namespace, and image straight
    out of the Kubernetes manifests so they aren't left as REPLACE_ME."""
    base = root / sig
    files = list(base.rglob("*.y*ml")) if base.is_dir() else [base]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for doc in re.split(r"^---\s*$", text, flags=re.M):
            if not re.search(r"kind:\s*Deployment", doc):
                continue
            name = re.search(r"name:\s*([\w.-]+)", doc)
            ns = re.search(r"namespace:\s*([\w.-]+)", doc)
            img = re.search(r"image:\s*[\"']?([^\s\"']+)", doc)
            out = {"app": name.group(1) if name else "REPLACE_ME",
                   "namespace": ns.group(1) if ns else "default",
                   "image": "REPLACE_ME"}
            if img:
                ref = img.group(1)
                head, sep, tail = ref.rpartition(":")
                out["image"] = head if sep and "/" not in tail else ref
            return out
    return {"app": "REPLACE_ME", "namespace": "default", "image": "REPLACE_ME"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = Path(args.root)
    out = root / PROFILE_REL
    old = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None

    # A target chosen by architecture decision (ADR) is locked: detection must
    # not overwrite it. We still refresh discoverable params for that target.
    if old and old.get("locked"):
        target = old["target"]
        detected_from = old.get("detected_from") or "decided (ADR)"
        params = dict(old.get("params", {}))
        if target == "kubernetes" and (sig := first(root, "kustomization.yaml", "k8s", "kubernetes", "manifests")):
            params = {**scan_k8s(root, sig), **{k: v for k, v in params.items() if v != "REPLACE_ME"}}
    else:
        target, detected_from, params = detect(root)

    rollback = "native" if target in NATIVE_ROLLBACK else (
        "redeploy-previous-tag"
        if target in {"netlify", "serverless", "sam", "script", "cloudrun"} else "manual")
    confidence = "low" if (target in {"manual", "script"} or "REPLACE_ME" in params.values()) else "high"

    environments = {
        "staging": {"url": "https://staging.example.com"},
        "production": {"url": "https://example.com"},
        "poc": {"url": "https://poc.example.com"},
    }
    # Offline URL discovery where the platform domain is deterministic.
    if target == "fly" and params.get("app", "REPLACE_ME") != "REPLACE_ME":
        environments["production"]["url"] = f"https://{params['app']}.fly.dev"

    # Provenance: record where each fact came from so unresolved ones are visible
    # and resolved ones are documented (preflight stamps 'verified').
    provenance = {
        k: {"source": "discovered" if v != "REPLACE_ME" else "missing", "verified": False}
        for k, v in params.items()
    }

    profile = {
        "target": target,
        "detected_from": detected_from,
        "confidence": confidence,
        "rollback": rollback,
        "health_path": "/health",
        "params": params,
        "provenance": provenance,
        "environments": environments,
        "locked": bool(old and old.get("locked")),
    }

    # Preserve anything the user already edited, then re-derive provenance from
    # the merged facts.
    if old:
        profile["environments"] = old.get("environments", profile["environments"])
        old_params = old.get("params", {})
        merged = {**profile["params"], **old_params}
        profile["params"] = merged
        profile["health_path"] = old.get("health_path", profile["health_path"])
        old_prov = old.get("provenance", {})
        locked = profile["locked"]
        profile["provenance"] = {
            k: {
                "source": "missing" if v == "REPLACE_ME"
                          else ("decided" if locked
                                else "declared" if k in old_params and old_params[k] == v
                                else "discovered"),
                "verified": old_prov.get(k, {}).get("verified", False),
            }
            for k, v in merged.items()
        }
        if "verified" in old:
            profile["verified"] = old["verified"]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")

    print(f"Detected target: {target} ({confidence} confidence)"
          + (f" from {detected_from}" if detected_from else ""))
    print(f"Rollback strategy: {rollback}")
    print(f"Wrote {out}")
    flags = [k for k, v in params.items() if v == "REPLACE_ME"]
    if target == "manual":
        print("\nWARN: No deploy target detected. Edit the profile's 'target' or add "
              ".specdev/deploy/deploy.sh, then re-run.")
    if flags or "example.com" in json.dumps(profile["environments"]):
        print("\nWARN: Confirm before relying on auto-deploy:")
        for k in flags:
            print(f"   - params.{k} is a placeholder")
        print("   - environments.*.url are placeholders")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
