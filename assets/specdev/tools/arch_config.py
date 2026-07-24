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
    return json.loads(p.read_text(encoding="utf-8"))


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
    return []  # replaced in Task 3
