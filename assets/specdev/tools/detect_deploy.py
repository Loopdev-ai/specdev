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
from pathlib import Path

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
        params.update(app="REPLACE_ME", namespace="default",
                      image="REPLACE_ME", registry="REPLACE_ME")
        return "kubernetes", sig, params

    if sig := first(root, ".specdev/deploy/deploy.sh"):
        return "script", sig, params

    if sig := first(root, "Dockerfile"):
        # Artifact type is known but the destination isn't — push to the script
        # escape hatch and flag it.
        params["note"] = "Dockerfile found but no orchestration target; add .specdev/deploy/deploy.sh or set a concrete target."
        return "script", sig, params

    return "manual", None, params


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = Path(args.root)

    target, detected_from, params = detect(root)
    rollback = "native" if target in NATIVE_ROLLBACK else (
        "redeploy-previous-tag" if target in {"netlify", "serverless", "sam", "script"} else "manual")
    confidence = "low" if (target in {"manual", "script"} or "REPLACE_ME" in params.values()) else "high"

    profile = {
        "target": target,
        "detected_from": detected_from,
        "confidence": confidence,
        "rollback": rollback,
        "health_path": "/health",
        "params": params,
        "environments": {
            "staging": {"url": "https://staging.example.com"},
            "production": {"url": "https://example.com"},
        },
    }

    # Preserve anything the user already edited.
    out = root / PROFILE_REL
    if out.exists():
        old = json.loads(out.read_text(encoding="utf-8"))
        profile["environments"] = old.get("environments", profile["environments"])
        merged = {**profile["params"], **old.get("params", {})}
        profile["params"] = merged
        profile["health_path"] = old.get("health_path", profile["health_path"])

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
