#!/usr/bin/env python3
"""Profile-driven deploy / rollback / health for the SpecDev pipeline.

Determination is built in: detect_deploy.py writes deploy.profile.json describing
the platform; this turns that profile into concrete actions so the GitHub
workflows stay generic. Rollback is target-aware — platforms with a native
rollback use it; everything else redeploys the previous git release tag
(stateless, no extra bookkeeping).

Usage:
    deploy.py deploy   --env staging    --tag v123 [--dry-run]
    deploy.py rollback --env production            [--dry-run]
    deploy.py health   --url https://...           [--dry-run]
    deploy.py url      --env staging
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PROFILE = Path(".specdev/deploy.profile.json")

RECIPES = {
    "fly": {
        "deploy": "flyctl deploy --app {app} --image-label {tag} --strategy rolling",
        "native_rollback": "flyctl releases rollback --app {app} --yes",
    },
    "vercel": {
        "deploy": "vercel deploy --prebuilt {prod_flag} --yes --token $VERCEL_TOKEN",
        "native_rollback": "vercel rollback --yes --token $VERCEL_TOKEN",
    },
    "netlify": {
        "deploy": "netlify deploy {prod_flag} --site {app} --message {tag}",
    },
    "serverless": {
        "deploy": "serverless deploy --stage {env}",
    },
    "sam": {
        "deploy": "sam deploy --config-env {env} --no-confirm-changeset --no-fail-on-empty-changeset",
    },
    "helm": {
        "deploy": "helm upgrade --install {release} {chart} --namespace {namespace} --set image.tag={tag} --wait",
        "native_rollback": "helm rollback {release} --namespace {namespace}",
    },
    "kubernetes": {
        "deploy": "kubectl -n {namespace} set image deployment/{app} {app}={registry}/{image}:{tag} && kubectl -n {namespace} rollout status deployment/{app}",
        "native_rollback": "kubectl -n {namespace} rollout undo deployment/{app}",
    },
    "script": {
        "deploy": "bash .specdev/deploy/deploy.sh {env} {tag}",
        "native_rollback": "bash .specdev/deploy/rollback.sh {env}",
    },
    "manual": {},
}

CTX_KEYS = ("app", "tag", "env", "url", "prod_flag", "health_path",
            "chart", "namespace", "release", "image", "registry")


def load_profile() -> dict:
    if not PROFILE.exists():
        sys.exit(f"ERROR: {PROFILE} missing — run detect_deploy.py first.")
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def ctx(profile: dict, env: str = "", tag: str = "") -> dict:
    c = {k: "" for k in CTX_KEYS}
    c.update(profile.get("params", {}))
    c["env"], c["tag"] = env, tag
    c["health_path"] = profile.get("health_path", "/health")
    c["url"] = profile.get("environments", {}).get(env, {}).get("url", "")
    c["prod_flag"] = "--prod" if env == "production" else ""
    return c


def run(cmd: str, dry: bool) -> None:
    print(f"$ {cmd}")
    if not dry:
        subprocess.run(cmd, shell=True, check=True)


def previous_tag() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "tag", "--sort=-creatordate"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    tags = [t for t in out.splitlines() if t.strip()]
    return tags[1] if len(tags) > 1 else None  # [0] is the release just shipped


def do_deploy(profile, env, tag, dry):
    target = profile["target"]
    tmpl = RECIPES.get(target, {}).get("deploy")
    if not tmpl:
        sys.exit(f"ERROR: target '{target}' has no deploy recipe. Set a concrete "
                 f"target in {PROFILE} or add .specdev/deploy/deploy.sh (target 'script').")
    run(tmpl.format(**ctx(profile, env, tag)), dry)
    print(f"Deployed {tag or '(current)'} to {env}.")


def do_rollback(profile, env, dry):
    target = profile["target"]
    mode = profile.get("rollback", "manual")

    if mode == "native":
        tmpl = RECIPES.get(target, {}).get("native_rollback")
        if target == "script" and not Path(".specdev/deploy/rollback.sh").exists():
            tmpl = None  # fall through to redeploy-previous
        if tmpl:
            run(tmpl.format(**ctx(profile, env)), dry)
            print(f"Native rollback issued for {env}.")
            return

    if mode in ("native", "redeploy-previous-tag"):
        prev = previous_tag()
        if not prev:
            sys.exit(f"ERROR: no previous release tag to roll {env} back to — "
                     f"manual intervention required (incident left open).")
        print(f"Rolling {env} back to previous release {prev}.")
        do_deploy(profile, env, prev, dry)
        return

    sys.exit(f"ERROR: rollback strategy '{mode}' is manual — intervene by hand.")


def do_health(profile, url, dry):
    target = url.rstrip("/") + profile.get("health_path", "/health")
    run(f'curl --fail --retry 5 --retry-delay 5 --show-error --silent "{target}"', dry)
    print(f"Health check passed: {target}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("deploy", "rollback", "health", "url"):
        p = sub.add_parser(name)
        p.add_argument("--env")
        p.add_argument("--tag", default="")
        p.add_argument("--url", default="")
        p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    profile = load_profile()

    if args.cmd == "deploy":
        do_deploy(profile, args.env, args.tag, args.dry_run)
    elif args.cmd == "rollback":
        do_rollback(profile, args.env, args.dry_run)
    elif args.cmd == "health":
        do_health(profile, args.url, args.dry_run)
    elif args.cmd == "url":
        print(profile.get("environments", {}).get(args.env, {}).get("url", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
