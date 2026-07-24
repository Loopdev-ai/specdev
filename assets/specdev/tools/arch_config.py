#!/usr/bin/env python3
"""SpecDev architecture / runtime hosting config store.

CRUD + validation over .specdev/architecture-config.json — the per-environment
record of runtime hosting values (cloud tenants, networks, databases, app
servers, service/API endpoints, service accounts, cloud app registrations, key
vaults, storage accounts). Secrets are NEVER stored: secret-bearing fields hold
a `secret_ref` pointer (key vault / env var / file) resolved by the product
runtime.

Usage:
    python .specdev/tools/arch_config.py <command> [options]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1

# category -> field roles. Allowed fields = required + optional + secret + COMMON_OPTIONAL.
CATEGORIES = {
    "cloud_tenants":     {"required": ["name", "cloud", "tenant_id", "account_id"], "optional": ["default_region"], "secret": []},
    "network_ranges":    {"required": ["name", "cidr", "scope"], "optional": ["region", "network_name"], "secret": []},
    "databases":         {"required": ["name", "engine", "host", "port", "database"], "optional": ["username", "ssl_mode"], "secret": ["secret_ref"]},
    "app_servers":       {"required": ["name", "hostname"], "optional": ["port", "protocol", "role", "os"], "secret": []},
    "service_endpoints": {"required": ["name", "url"], "optional": ["protocol"], "secret": ["secret_ref"]},
    "api_endpoints":     {"required": ["name", "base_url", "auth_type"], "optional": ["version"], "secret": ["secret_ref"]},
    "service_accounts":  {"required": ["name", "provider", "identifier"], "optional": ["roles"], "secret": ["secret_ref"]},
    "app_registrations": {"required": ["name", "cloud", "client_id", "tenant_id"], "optional": ["target_resource"], "secret": ["secret_ref"]},
    "key_vaults":        {"required": ["name", "cloud", "vault_uri"], "optional": ["tenant_id", "region"], "secret": []},
    "storage_accounts":  {"required": ["name", "cloud", "account_name", "kind"], "optional": ["endpoint", "region"], "secret": ["secret_ref"]},
}
COMMON_OPTIONAL = ["description", "default"]
INT_FIELDS = {"port"}
LIST_FIELDS = {"roles"}

SECRET_PROVIDERS = {
    "key_vault": ["vault", "secret_name"],
    "env": ["env_var"],
    "file": ["path"],
}

# Best-effort backstop so a literal secret never reaches the committed file.
# Complements (does not replace) the repo's gitleaks gate.
LEAK_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|pwd|accountkey|apikey|api_key|client_secret)\s*=\s*\S+"),
    # A bare high-entropy blob (access key, connection secret, JWT segment) pasted
    # with no keyword= prefix. 40+ base64 chars covers hex too (hex ⊂ base64).
    # Realistic config values (GUIDs, https URLs, ≤24-char storage names) stay under
    # this run length because '.', '-', and ':' break the run.
    re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/])"),
]


def allowed_fields(category):
    spec = CATEGORIES[category]
    return set(spec["required"]) | set(spec["optional"]) | set(spec["secret"]) | set(COMMON_OPTIONAL)


def config_path(root):
    return Path(root) / ".specdev" / "architecture-config.json"


def load(root):
    p = config_path(root)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found (run /specdev:init to scaffold it)")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{p} must contain a JSON object")
    return doc


def save(root, doc):
    p = config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def get_env(doc, env):
    envs = doc.get("environments", {})
    if env not in envs:
        raise KeyError(f"environment '{env}' not found (add it with: add-env --env {env})")
    return envs[env]


def find_record(env_obj, category, name):
    for rec in env_obj.get(category, []):
        if isinstance(rec, dict) and rec.get("name") == name:
            return rec
    return None


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_doc(doc):
    errors = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    envs = doc.get("environments")
    if not isinstance(envs, dict):
        errors.append("'environments' must be an object")
        return errors
    for env_name, env_obj in envs.items():
        if not isinstance(env_obj, dict):
            errors.append(f"[{env_name}] must be an object")
            continue
        for cat in env_obj:
            if cat not in CATEGORIES:
                errors.append(f"[{env_name}] unknown category '{cat}'")
        vault_names = {
            r["name"]
            for r in (env_obj.get("key_vaults") or [])
            if isinstance(r, dict) and r.get("name")
        }
        for cat, spec in CATEGORIES.items():
            records = env_obj.get(cat, [])
            if not isinstance(records, list):
                errors.append(f"[{env_name}].{cat} must be a list")
                continue
            seen = set()
            default_count = 0
            for rec in records:
                if not isinstance(rec, dict):
                    errors.append(f"[{env_name}].{cat} record must be an object")
                    continue
                name = rec.get("name")
                label = f"[{env_name}].{cat}[{name!r}]"
                if not name or not isinstance(name, str):
                    errors.append(f"{label} missing string 'name'")
                elif name in seen:
                    errors.append(f"{label} duplicate name")
                else:
                    seen.add(name)
                for f in spec["required"]:
                    if f not in rec or rec[f] in (None, ""):
                        errors.append(f"{label} missing required field '{f}'")
                allowed = allowed_fields(cat)
                for f in rec:
                    if f not in allowed:
                        errors.append(f"{label} unknown field '{f}'")
                if "default" in rec and not isinstance(rec["default"], bool):
                    errors.append(f"{label} 'default' must be boolean")
                elif rec.get("default") is True:
                    default_count += 1
                # --- secret + leak rules added in Task 3 ---
                errors.extend(_secret_errors(rec, spec, label, vault_names))
            if default_count > 1:
                errors.append(f"[{env_name}].{cat} has {default_count} records marked default (max 1)")
    return errors


def _secret_errors(rec, spec, label, vault_names):
    errs = []
    for sf in spec["secret"]:
        if sf in rec:
            errs.extend(_validate_secret_ref(rec[sf], f"{label}.{sf}", vault_names))
    # leak guard: no literal secrets in plain string fields
    for f, v in rec.items():
        if f in spec["secret"] or not isinstance(v, str):
            continue
        if any(pat.search(v) for pat in LEAK_PATTERNS):
            errs.append(f"{label}.{f} looks like a literal secret; use a secret_ref")
    return errs


def _validate_secret_ref(ref, label, vault_names):
    if not isinstance(ref, dict):
        return [f"{label} must be a secret_ref object (never a literal secret)"]
    errs = []
    provider = ref.get("provider")
    if provider not in SECRET_PROVIDERS:
        return [f"{label} secret_ref.provider must be one of {sorted(SECRET_PROVIDERS)}"]
    required = SECRET_PROVIDERS[provider]
    allowed = {"provider", *required}
    for k in required:
        if not ref.get(k):
            errs.append(f"{label} secret_ref missing '{k}' for provider '{provider}'")
    for k in ref:
        if k not in allowed:
            errs.append(f"{label} secret_ref unknown key '{k}'")
    if provider == "key_vault" and ref.get("vault") and ref["vault"] not in vault_names:
        errs.append(f"{label} secret_ref.vault '{ref['vault']}' has no matching key_vaults record in this environment")
    return errs


def parse_set(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"--set expects field=value, got '{p}'")
        k, v = p.split("=", 1)
        k = k.strip()
        if k in INT_FIELDS:
            out[k] = int(v)
        elif k in LIST_FIELDS:
            out[k] = [x.strip() for x in v.split(",") if x.strip()]
        else:
            out[k] = v
    return out


def parse_secret_ref(s):
    ref = {}
    for part in s.split(","):
        if "=" not in part:
            raise ValueError(f"--secret-ref expects comma-separated k=v pairs, got '{part}'")
        k, v = part.split("=", 1)
        ref[k.strip()] = v.strip()
    return ref


def _validate_and_save(root, doc):
    errs = validate_doc(doc)
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        die(f"refusing to write: {len(errs)} validation error(s)")
    save(root, doc)


def _apply_fields(rec, args):
    rec.update(parse_set(args.set))
    if getattr(args, "secret_ref", None):
        rec["secret_ref"] = parse_secret_ref(args.secret_ref)
    if getattr(args, "default", False):
        rec["default"] = True
    if getattr(args, "no_default", False):
        rec.pop("default", None)


def _require_category(category):
    if category not in CATEGORIES:
        die(f"unknown category '{category}' (valid: {', '.join(CATEGORIES)})")


def cmd_add(root, args):
    _require_category(args.category)
    doc = load(root)
    env_obj = get_env(doc, args.env)
    env_obj.setdefault(args.category, [])
    if find_record(env_obj, args.category, args.name):
        die(f"'{args.name}' already exists in [{args.env}].{args.category} (use edit)")
    rec = {"name": args.name}
    _apply_fields(rec, args)
    env_obj[args.category].append(rec)
    _validate_and_save(root, doc)
    print(f"added [{args.env}].{args.category}[{args.name}]")


def cmd_get(root, args):
    _require_category(args.category)
    doc = load(root)
    env_obj = get_env(doc, args.env)
    rec = find_record(env_obj, args.category, args.name)
    if not rec:
        die(f"'{args.name}' not found in [{args.env}].{args.category}")
    print(json.dumps(rec, indent=2))


def cmd_list(root, args):
    if args.category:
        _require_category(args.category)
    doc = load(root)
    envs = doc.get("environments", {})
    for env in ([args.env] if args.env else list(envs)):
        env_obj = envs.get(env, {})
        for cat in ([args.category] if args.category else list(CATEGORIES)):
            for rec in env_obj.get(cat, []):
                flag = " *default" if rec.get("default") else ""
                print(f"{env}\t{cat}\t{rec.get('name')}{flag}")


def cmd_add_env(root, args):
    doc = load(root)
    envs = doc.setdefault("environments", {})
    if args.env in envs:
        die(f"environment '{args.env}' already exists")
    envs[args.env] = {cat: [] for cat in CATEGORIES}
    _validate_and_save(root, doc)
    print(f"added environment '{args.env}'")


def cmd_list_envs(root, args):
    doc = load(root)
    for env in doc.get("environments", {}):
        print(env)


def cmd_edit(root, args):
    _require_category(args.category)
    doc = load(root)
    env_obj = get_env(doc, args.env)
    rec = find_record(env_obj, args.category, args.name)
    if not rec:
        die(f"'{args.name}' not found in [{args.env}].{args.category} (use add)")
    _apply_fields(rec, args)
    _validate_and_save(root, doc)
    print(f"edited [{args.env}].{args.category}[{args.name}]")


def cmd_delete(root, args):
    _require_category(args.category)
    doc = load(root)
    env_obj = get_env(doc, args.env)
    rec = find_record(env_obj, args.category, args.name)
    if not rec:
        die(f"'{args.name}' not found in [{args.env}].{args.category}")
    env_obj[args.category].remove(rec)
    _validate_and_save(root, doc)
    print(f"deleted [{args.env}].{args.category}[{args.name}]")


def cmd_validate(root, args):
    errs = validate_doc(load(root))
    for e in errs:
        print(f"ERROR: {e}")
    if errs:
        print(f"\narch-config INVALID: {len(errs)} error(s)")
        return 1
    print("arch-config valid.")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="SpecDev architecture / runtime hosting config store")
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="command", required=True)

    def target(p, name=True):
        p.add_argument("--env", required=True)
        p.add_argument("--category", required=True)
        if name:
            p.add_argument("--name", required=True)

    def mutators(p, allow_no_default=False):
        p.add_argument("--set", action="append", default=[])
        p.add_argument("--secret-ref", dest="secret_ref")
        p.add_argument("--default", action="store_true")
        if allow_no_default:
            p.add_argument("--no-default", action="store_true")

    p_add = sub.add_parser("add"); target(p_add); mutators(p_add)
    p_edit = sub.add_parser("edit"); target(p_edit); mutators(p_edit, allow_no_default=True)
    p_del = sub.add_parser("delete"); target(p_del)
    p_get = sub.add_parser("get"); target(p_get)
    p_list = sub.add_parser("list")
    p_list.add_argument("--env"); p_list.add_argument("--category")
    p_env = sub.add_parser("add-env"); p_env.add_argument("--env", required=True)
    sub.add_parser("list-envs")
    sub.add_parser("validate")
    return ap


DISPATCH = {
    "add": cmd_add, "get": cmd_get, "list": cmd_list,
    "add-env": cmd_add_env, "list-envs": cmd_list_envs,
    # "edit", "delete", "validate" added in Task 5
}
DISPATCH.update({"edit": cmd_edit, "delete": cmd_delete, "validate": cmd_validate})


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = DISPATCH.get(args.command)
    if fn is None:
        die(f"command '{args.command}' not implemented")
    try:
        return fn(args.root, args) or 0
    except (FileNotFoundError, KeyError, ValueError) as e:
        die(str(e))


if __name__ == "__main__":
    sys.exit(main())
