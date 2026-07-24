# arch-config Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SpecDev skill + stdlib CLI + seed datastore + CI gate that captures a product's runtime hosting configuration (10 resource categories) as per-environment, typed, reference-only records in `.specdev/architecture-config.json`.

**Architecture:** A single stdlib-only Python module `assets/specdev/tools/arch_config.py` owns the datastore schema, validation, and CRUD; a `skills/arch-config/SKILL.md` drives that tool; a seed JSON and a path-filtered GitHub Actions workflow ship as assets. Product repos receive all of it via the existing `/specdev:init` copy of `assets/specdev/` → `.specdev/` and `assets/workflows/*.yml` → `.github/workflows/`.

**Tech Stack:** Python 3.12 stdlib only (`argparse`, `json`, `os`, `re`, `pathlib`, `sys`); pytest for tests; GitHub Actions YAML.

## Global Constraints

- **Stdlib only** — no third-party imports in `arch_config.py` (matches every other `.specdev/tools/*.py`).
- **References only for secrets** — secret-bearing fields hold a `secret_ref` object, never a literal value. Enforced by `validate_doc`.
- **Datastore path** — `<root>/.specdev/architecture-config.json`; tool default `--root .`.
- **`schema_version` constant = `1`.**
- **Atomic writes** — every mutating command validates the whole document, then writes via temp file + `os.replace`; a validation error aborts the write with a non-zero exit.
- **The seed ships valid** — one empty `production` environment, all 10 category arrays present, so `validate` passes immediately.
- **JSON formatting** — `json.dumps(doc, indent=2)` + trailing newline.
- **Tests run from repo root** — `pytest`; the test imports the tool by file path via `importlib`.
- **The 10 categories and their fields are fixed by the spec** (see the schema table in `docs/superpowers/specs/2026-07-23-arch-config-skill-design.md`) — copied verbatim into `CATEGORIES` in Task 1.

---

## Module map (locked in Task 1, extended by later tasks)

`assets/specdev/tools/arch_config.py` grows across Tasks 1–5:

- **Task 1** — constants (`SCHEMA_VERSION`, `CATEGORIES`, `COMMON_OPTIONAL`, `INT_FIELDS`, `LIST_FIELDS`, `SECRET_PROVIDERS`, `LEAK_PATTERNS`), helpers (`allowed_fields`, `config_path`, `load`, `save`, `get_env`, `find_record`, `die`).
- **Task 2** — `validate_doc` (structural rules).
- **Task 3** — `_validate_secret_ref` + secret/leak rules folded into `validate_doc`.
- **Task 4** — `parse_set`, `parse_secret_ref`, `_validate_and_save`, `cmd_add`, `cmd_get`, `cmd_list`, `cmd_add_env`, `cmd_list_envs`, and `build_parser`/`main` dispatch.
- **Task 5** — `cmd_edit`, `cmd_delete`, `cmd_validate`, wired into the parser/dispatch.

Test file `tests/test_arch_config.py` grows alongside. Shared test preamble (added in Task 1, reused everywhere):

```python
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "assets" / "specdev" / "tools" / "arch_config.py"
SEED_PATH = ROOT / "assets" / "specdev" / "architecture-config.json"


def load_mod():
    spec = importlib.util.spec_from_file_location("arch_config", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ac = load_mod()


def seed_doc():
    return {
        "schema_version": ac.SCHEMA_VERSION,
        "environments": {
            "production": {cat: [] for cat in ac.CATEGORIES},
        },
    }


def write_seed(root):
    (root / ".specdev").mkdir(parents=True, exist_ok=True)
    (root / ".specdev" / "architecture-config.json").write_text(
        json.dumps(seed_doc(), indent=2) + "\n", encoding="utf-8"
    )
```

> **Note (test scaffolding):** `test_seed_file_matches_schema_shape` reads the
> seed via `SEED_PATH` (above), not a `MOD_PATH.parents[...]` derivation.

---

### Task 1: Schema constants, load/save, helpers

**Files:**
- Create: `assets/specdev/tools/arch_config.py`
- Create: `assets/specdev/architecture-config.json`
- Create: `tests/test_arch_config.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION: int`, `CATEGORIES: dict[str, dict]`, `COMMON_OPTIONAL: list[str]`, `INT_FIELDS: set[str]`, `LIST_FIELDS: set[str]`, `SECRET_PROVIDERS: dict[str, list[str]]`, `LEAK_PATTERNS: list[re.Pattern]`, `allowed_fields(category) -> set[str]`, `config_path(root) -> Path`, `load(root) -> dict`, `save(root, doc) -> None`, `get_env(doc, env) -> dict`, `find_record(env_obj, category, name) -> dict | None`, `die(msg) -> NoReturn`.

- [ ] **Step 1: Write the seed datastore**

Create `assets/specdev/architecture-config.json`:

```json
{
  "schema_version": 1,
  "environments": {
    "production": {
      "cloud_tenants": [],
      "network_ranges": [],
      "databases": [],
      "app_servers": [],
      "service_endpoints": [],
      "api_endpoints": [],
      "service_accounts": [],
      "app_registrations": [],
      "key_vaults": [],
      "storage_accounts": []
    }
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_arch_config.py` with the shared preamble above, then:

```python
def test_categories_are_the_ten_expected():
    assert set(ac.CATEGORIES) == {
        "cloud_tenants", "network_ranges", "databases", "app_servers",
        "service_endpoints", "api_endpoints", "service_accounts",
        "app_registrations", "key_vaults", "storage_accounts",
    }


def test_seed_file_matches_schema_shape():
    seed = json.loads(
        (MOD_PATH.parents[1] / "assets" / "specdev" / "architecture-config.json").read_text()
    )
    assert seed["schema_version"] == ac.SCHEMA_VERSION
    assert set(seed["environments"]["production"]) == set(ac.CATEGORIES)


def test_allowed_fields_includes_common_and_secret(tmp_path):
    fields = ac.allowed_fields("databases")
    assert {"name", "host", "secret_ref", "description", "default"} <= fields


def test_load_save_round_trip(tmp_path):
    write_seed(tmp_path)
    doc = ac.load(tmp_path)
    doc["environments"]["production"]["key_vaults"].append({"name": "kv"})
    ac.save(tmp_path, doc)
    again = ac.load(tmp_path)
    assert again["environments"]["production"]["key_vaults"][0]["name"] == "kv"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ac.load(tmp_path)


def test_find_record(tmp_path):
    env_obj = {"key_vaults": [{"name": "kv"}]}
    assert ac.find_record(env_obj, "key_vaults", "kv")["name"] == "kv"
    assert ac.find_record(env_obj, "key_vaults", "nope") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_arch_config.py -q`
Expected: FAIL — `arch_config.py` does not exist / `ModuleNotFoundError` on `exec_module`.

- [ ] **Step 4: Write the module skeleton**

Create `assets/specdev/tools/arch_config.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_arch_config.py -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add assets/specdev/tools/arch_config.py assets/specdev/architecture-config.json tests/test_arch_config.py
git commit -m "feat(arch-config): schema constants, seed datastore, load/save helpers"
```

---

### Task 2: Structural validation (`validate_doc`)

**Files:**
- Modify: `assets/specdev/tools/arch_config.py` (add `validate_doc`)
- Test: `tests/test_arch_config.py`

**Interfaces:**
- Consumes: `CATEGORIES`, `SCHEMA_VERSION`, `allowed_fields` (Task 1).
- Produces: `validate_doc(doc) -> list[str]` — returns a list of human-readable error strings; empty list means valid. This task covers structural rules only; Task 3 extends the SAME function with secret/leak rules.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arch_config.py`:

```python
def _doc_with(category, record):
    d = seed_doc()
    d["environments"]["production"][category].append(record)
    return d


def test_valid_seed_passes():
    assert ac.validate_doc(seed_doc()) == []


def test_wrong_schema_version_fails():
    d = seed_doc()
    d["schema_version"] = 2
    assert any("schema_version" in e for e in ac.validate_doc(d))


def test_environments_must_be_object():
    assert any("environments" in e for e in ac.validate_doc({"schema_version": 1, "environments": []}))


def test_unknown_category_fails():
    d = seed_doc()
    d["environments"]["production"]["bogus"] = []
    assert any("unknown category" in e for e in ac.validate_doc(d))


def test_missing_required_field_fails():
    d = _doc_with("key_vaults", {"name": "kv"})  # missing cloud, vault_uri
    errs = ac.validate_doc(d)
    assert any("cloud" in e for e in errs)
    assert any("vault_uri" in e for e in errs)


def test_unknown_field_fails():
    d = _doc_with("key_vaults", {"name": "kv", "cloud": "azure", "vault_uri": "https://x", "wat": 1})
    assert any("unknown field 'wat'" in e for e in ac.validate_doc(d))


def test_duplicate_name_fails():
    d = seed_doc()
    d["environments"]["production"]["app_servers"] = [
        {"name": "a", "hostname": "h1"}, {"name": "a", "hostname": "h2"},
    ]
    assert any("duplicate name" in e for e in ac.validate_doc(d))


def test_two_defaults_in_category_fail():
    d = seed_doc()
    d["environments"]["production"]["key_vaults"] = [
        {"name": "kv1", "cloud": "azure", "vault_uri": "https://1", "default": True},
        {"name": "kv2", "cloud": "azure", "vault_uri": "https://2", "default": True},
    ]
    assert any("default" in e for e in ac.validate_doc(d))


def test_defaults_in_different_envs_are_independent():
    d = seed_doc()
    d["environments"]["staging"] = {cat: [] for cat in ac.CATEGORIES}
    for env in ("production", "staging"):
        d["environments"][env]["key_vaults"] = [
            {"name": "kv", "cloud": "azure", "vault_uri": "https://x", "default": True}
        ]
    assert ac.validate_doc(d) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_arch_config.py -q -k validate or default or category or field or schema or environments`
Expected: FAIL — `AttributeError: module 'arch_config' has no attribute 'validate_doc'`.

- [ ] **Step 3: Implement structural `validate_doc`**

Add to `arch_config.py` (Task 3 will extend the marked section):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_arch_config.py -q`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/arch_config.py tests/test_arch_config.py
git commit -m "feat(arch-config): structural validation (categories, fields, defaults)"
```

---

### Task 3: Secret-ref validation, referential integrity, leak guard

**Files:**
- Modify: `assets/specdev/tools/arch_config.py` (replace `_secret_errors`, add `_validate_secret_ref`)
- Test: `tests/test_arch_config.py`

**Interfaces:**
- Consumes: `SECRET_PROVIDERS`, `LEAK_PATTERNS`, `CATEGORIES` (Task 1); `validate_doc` calls `_secret_errors` (Task 2).
- Produces: `_validate_secret_ref(ref, label, vault_names) -> list[str]`; final `_secret_errors(rec, spec, label, vault_names) -> list[str]` covering secret_ref shape, strict `key_vault` referential integrity, and the leak guard on non-secret string fields.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arch_config.py`:

```python
def _kv(name="core-kv", default=False):
    r = {"name": name, "cloud": "azure", "vault_uri": f"https://{name}.vault.azure.net"}
    if default:
        r["default"] = True
    return r


def test_secret_ref_key_vault_resolves():
    d = seed_doc()
    p = d["environments"]["production"]
    p["key_vaults"].append(_kv())
    p["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "key_vault", "vault": "core-kv", "secret_name": "db-pw"},
    })
    assert ac.validate_doc(d) == []


def test_secret_ref_dangling_vault_fails():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "key_vault", "vault": "ghost", "secret_name": "x"},
    })
    assert any("no matching key_vaults record" in e for e in ac.validate_doc(d))


def test_secret_ref_env_requires_env_var():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "env"},
    })
    assert any("env_var" in e for e in ac.validate_doc(d))


def test_secret_ref_unknown_provider_fails():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "smoke-signal", "x": "y"},
    })
    assert any("provider" in e for e in ac.validate_doc(d))


def test_secret_ref_must_be_object_not_string():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": "hunter2",
    })
    assert any("must be a secret_ref object" in e for e in ac.validate_doc(d))


def test_leak_guard_flags_literal_secret_in_plain_field():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "Password=hunter2", "port": 5432, "database": "app",
    })
    assert any("literal secret" in e for e in ac.validate_doc(d))


def test_leak_guard_flags_private_key():
    d = seed_doc()
    d["environments"]["production"]["app_servers"].append({
        "name": "s", "hostname": "-----BEGIN RSA PRIVATE KEY-----",
    })
    assert any("literal secret" in e for e in ac.validate_doc(d))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_arch_config.py -q -k secret or leak`
Expected: FAIL — dangling vault / env_var / provider / leak assertions fail because `_secret_errors` currently returns `[]`.

- [ ] **Step 3: Implement the secret rules**

In `arch_config.py`, replace the placeholder `_secret_errors` with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_arch_config.py -q`
Expected: PASS (all tasks so far).

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/arch_config.py tests/test_arch_config.py
git commit -m "feat(arch-config): secret_ref validation, referential integrity, leak guard"
```

---

### Task 4: CLI — parsing, add/get/list/add-env/list-envs, argparse dispatch

**Files:**
- Modify: `assets/specdev/tools/arch_config.py` (parsing helpers, mutating helpers, commands, parser, `main`)
- Test: `tests/test_arch_config.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: `parse_set(pairs) -> dict`, `parse_secret_ref(s) -> dict`, `_validate_and_save(root, doc) -> None`, `cmd_add`, `cmd_get`, `cmd_list`, `cmd_add_env`, `cmd_list_envs` (each `(root, args) -> int | None`), `build_parser() -> argparse.ArgumentParser`, `main(argv=None) -> int`. `main` catches `FileNotFoundError`/`KeyError`/`ValueError` and routes them through `die`; validation-failed writes raise `SystemExit` via `die`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arch_config.py`:

```python
def test_add_then_get_round_trip(tmp_path):
    write_seed(tmp_path)
    rc = ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
        "--name", "core-kv", "--set", "cloud=azure", "--set", "vault_uri=https://core.vault.azure.net",
        "--default",
    ])
    assert rc == 0
    doc = ac.load(tmp_path)
    rec = doc["environments"]["production"]["key_vaults"][0]
    assert rec["name"] == "core-kv" and rec["default"] is True


def test_add_coerces_port_to_int(tmp_path):
    write_seed(tmp_path)
    ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "databases",
        "--name", "db", "--set", "engine=postgres", "--set", "host=h",
        "--set", "port=5432", "--set", "database=app",
    ])
    assert ac.load(tmp_path)["environments"]["production"]["databases"][0]["port"] == 5432


def test_add_with_secret_ref(tmp_path):
    write_seed(tmp_path)
    ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
        "--name", "core-kv", "--set", "cloud=azure", "--set", "vault_uri=https://x",
    ])
    rc = ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "databases",
        "--name", "db", "--set", "engine=postgres", "--set", "host=h", "--set", "port=5432",
        "--set", "database=app",
        "--secret-ref", "provider=key_vault,vault=core-kv,secret_name=db-pw",
    ])
    assert rc == 0
    ref = ac.load(tmp_path)["environments"]["production"]["databases"][0]["secret_ref"]
    assert ref == {"provider": "key_vault", "vault": "core-kv", "secret_name": "db-pw"}


def test_add_duplicate_name_exits(tmp_path):
    write_seed(tmp_path)
    args = ["--root", str(tmp_path), "add", "--env", "production", "--category", "app_servers",
            "--name", "s", "--set", "hostname=h"]
    ac.main(args)
    with pytest.raises(SystemExit):
        ac.main(args)


def test_add_invalid_record_aborts_write(tmp_path):
    write_seed(tmp_path)
    before = ac.config_path(tmp_path).read_text()
    with pytest.raises(SystemExit):
        ac.main([  # dangling vault ref -> validation fails -> no write
            "--root", str(tmp_path), "add", "--env", "production", "--category", "databases",
            "--name", "db", "--set", "engine=postgres", "--set", "host=h", "--set", "port=5432",
            "--set", "database=app", "--secret-ref", "provider=key_vault,vault=ghost,secret_name=x",
        ])
    assert ac.config_path(tmp_path).read_text() == before  # unchanged


def test_add_env_and_list_envs(tmp_path, capsys):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add-env", "--env", "staging"])
    assert set(ac.load(tmp_path)["environments"]) == {"production", "staging"}
    ac.main(["--root", str(tmp_path), "list-envs"])
    out = capsys.readouterr().out
    assert "staging" in out and "production" in out


def test_list_shows_default_flag(tmp_path, capsys):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
             "--name", "core-kv", "--set", "cloud=azure", "--set", "vault_uri=https://x", "--default"])
    ac.main(["--root", str(tmp_path), "list", "--category", "key_vaults"])
    assert "*default" in capsys.readouterr().out


def test_same_name_different_env_independent(tmp_path):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add-env", "--env", "staging"])
    for env in ("production", "staging"):
        ac.main(["--root", str(tmp_path), "add", "--env", env, "--category", "app_servers",
                 "--name", "web", "--set", f"hostname={env}-web"])
    doc = ac.load(tmp_path)
    assert doc["environments"]["production"]["app_servers"][0]["hostname"] == "production-web"
    assert doc["environments"]["staging"]["app_servers"][0]["hostname"] == "staging-web"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_arch_config.py -q -k add or list or env`
Expected: FAIL — `main` / `parse_set` / commands not defined.

- [ ] **Step 3: Implement parsing, commands, parser, main**

Add to `arch_config.py`:

```python
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


def cmd_add(root, args):
    if args.category not in CATEGORIES:
        die(f"unknown category '{args.category}'")
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
    doc = load(root)
    env_obj = get_env(doc, args.env)
    rec = find_record(env_obj, args.category, args.name)
    if not rec:
        die(f"'{args.name}' not found in [{args.env}].{args.category}")
    print(json.dumps(rec, indent=2))


def cmd_list(root, args):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_arch_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/arch_config.py tests/test_arch_config.py
git commit -m "feat(arch-config): CLI add/get/list/add-env/list-envs + argparse dispatch"
```

---

### Task 5: CLI — edit, delete, validate

**Files:**
- Modify: `assets/specdev/tools/arch_config.py` (add `cmd_edit`, `cmd_delete`, `cmd_validate`; register in `DISPATCH`)
- Test: `tests/test_arch_config.py`

**Interfaces:**
- Consumes: Task 4 helpers (`_apply_fields`, `_validate_and_save`, `find_record`, `get_env`, `validate_doc`).
- Produces: `cmd_edit`, `cmd_delete`, `cmd_validate` (each `(root, args) -> int | None`); `cmd_validate` returns `1` on invalid, `0` on valid.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_arch_config.py`:

```python
def _seed_with_kv(tmp_path):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add", "--env", "production", "--category", "app_servers",
             "--name", "web", "--set", "hostname=h1"])


def test_edit_updates_field(tmp_path):
    _seed_with_kv(tmp_path)
    rc = ac.main(["--root", str(tmp_path), "edit", "--env", "production", "--category", "app_servers",
                  "--name", "web", "--set", "hostname=h2"])
    assert rc == 0
    assert ac.load(tmp_path)["environments"]["production"]["app_servers"][0]["hostname"] == "h2"


def test_edit_missing_record_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "edit", "--env", "production", "--category", "app_servers",
                 "--name", "ghost", "--set", "hostname=h"])


def test_edit_no_default_clears_flag(tmp_path):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
             "--name", "kv", "--set", "cloud=azure", "--set", "vault_uri=https://x", "--default"])
    ac.main(["--root", str(tmp_path), "edit", "--env", "production", "--category", "key_vaults",
             "--name", "kv", "--no-default"])
    assert "default" not in ac.load(tmp_path)["environments"]["production"]["key_vaults"][0]


def test_delete_removes_record(tmp_path):
    _seed_with_kv(tmp_path)
    rc = ac.main(["--root", str(tmp_path), "delete", "--env", "production", "--category", "app_servers",
                  "--name", "web"])
    assert rc == 0
    assert ac.load(tmp_path)["environments"]["production"]["app_servers"] == []


def test_delete_missing_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "delete", "--env", "production", "--category", "app_servers",
                 "--name", "ghost"])


def test_validate_command_passes_on_seed(tmp_path):
    write_seed(tmp_path)
    assert ac.main(["--root", str(tmp_path), "validate"]) == 0


def test_validate_command_fails_on_bad_doc(tmp_path):
    write_seed(tmp_path)
    doc = ac.load(tmp_path)
    doc["environments"]["production"]["key_vaults"].append({"name": "kv"})  # missing required
    ac.save(tmp_path, doc)
    assert ac.main(["--root", str(tmp_path), "validate"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_arch_config.py -q -k edit or delete or validate_command`
Expected: FAIL — commands route to `die("command 'edit' not implemented")` → `SystemExit`, and `validate` not in `DISPATCH`.

- [ ] **Step 3: Implement edit, delete, validate**

Add to `arch_config.py`:

```python
def cmd_edit(root, args):
    doc = load(root)
    env_obj = get_env(doc, args.env)
    rec = find_record(env_obj, args.category, args.name)
    if not rec:
        die(f"'{args.name}' not found in [{args.env}].{args.category} (use add)")
    _apply_fields(rec, args)
    _validate_and_save(root, doc)
    print(f"edited [{args.env}].{args.category}[{args.name}]")


def cmd_delete(root, args):
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
```

Then extend the dispatch table:

```python
DISPATCH.update({"edit": cmd_edit, "delete": cmd_delete, "validate": cmd_validate})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_arch_config.py -q`
Expected: PASS (full suite).

- [ ] **Step 5: Commit**

```bash
git add assets/specdev/tools/arch_config.py tests/test_arch_config.py
git commit -m "feat(arch-config): CLI edit/delete/validate commands"
```

---

### Task 6: The skill — `skills/arch-config/SKILL.md`

**Files:**
- Create: `skills/arch-config/SKILL.md`

**Interfaces:**
- Consumes: the `arch_config.py` CLI surface (Tasks 4–5).
- Produces: a discoverable skill. No code; verification is a content check.

- [ ] **Step 1: Write the skill**

Create `skills/arch-config/SKILL.md`:

```markdown
---
name: arch-config
description: Capture and manage a product's runtime hosting configuration (cloud tenants, network ranges, databases, app servers, service/API endpoints, service accounts, cloud app registrations, key vaults, storage accounts) as per-environment, reference-only records in .specdev/architecture-config.json. Use when the user wants to record, edit, or remove architecture/runtime/hosting config values, or when a repo has a .specdev/architecture-config.json. Trigger on "architecture config", "runtime config", "hosting values", "capture tenant/database/endpoint/key vault/storage config".
---

# arch-config — runtime hosting configuration store

Manage the hosting values a product's runtime needs, per environment, in
`.specdev/architecture-config.json`. You drive the `arch_config.py` CLI; never
hand-edit the JSON (the tool validates and writes atomically).

## The one rule that matters: never store secrets

Secret material (passwords, client secrets, access keys, credentials) is NEVER
written to this file. Capture a **`secret_ref`** pointer instead:

- `provider=key_vault,vault=<key_vaults name>,secret_name=<name>` — the vault
  must be catalogued as a `key_vaults` record in the same environment.
- `provider=env,env_var=<VAR>` — resolved from the runtime environment.
- `provider=file,path=<path>` — resolved from a mounted secret file.

The tool's `validate` rejects a literal secret in any field. If a user pastes
one, stop and capture a reference instead.

## Scope: everything is per-environment

Every record lives under a named environment (seed ships `production`). Add
more with `add-env`. The same logical resource can differ per environment.

## The 10 categories and their fields

Required fields have no mark; `?` = optional. Every record also allows
`description` and `default`. Secret-bearing fields (`secret_ref`) are optional.

- **cloud_tenants** — name, cloud, tenant_id, account_id, default_region?
- **network_ranges** — name, cidr, scope, region?, network_name?
- **databases** — name, engine, host, port, database, username?, ssl_mode?, secret_ref?
- **app_servers** — name, hostname, port?, protocol?, role?, os?
- **service_endpoints** — name, url, protocol?, secret_ref?
- **api_endpoints** — name, base_url, auth_type, version?, secret_ref?
- **service_accounts** — name, provider, identifier, roles?, secret_ref?
- **app_registrations** — name, cloud, client_id, tenant_id, target_resource?, secret_ref?
- **key_vaults** — name, cloud, vault_uri, tenant_id?, region?
- **storage_accounts** — name, cloud, account_name, kind, endpoint?, region?, secret_ref?

`roles` takes a comma-separated list; `port` is coerced to an integer.

## The `default` flag

Tag one record per category per environment as `--default` to mark it the
product's default for that resource (e.g. the default key vault, storage
account, or cloud tenant). The tool rejects a second default in the same
category + environment.

## Commands

```bash
# Create
python .specdev/tools/arch_config.py add --env production --category key_vaults \
  --name core-kv --set cloud=azure --set vault_uri=https://core.vault.azure.net --default

# Reference a secret (vault must already exist as a key_vaults record)
python .specdev/tools/arch_config.py add --env production --category databases \
  --name app-db --set engine=postgres --set host=db.internal --set port=5432 \
  --set database=app --set username=app \
  --secret-ref provider=key_vault,vault=core-kv,secret_name=app-db-password

# Edit / clear the default flag / delete
python .specdev/tools/arch_config.py edit   --env production --category databases --name app-db --set host=db2.internal
python .specdev/tools/arch_config.py edit   --env production --category key_vaults --name core-kv --no-default
python .specdev/tools/arch_config.py delete --env production --category app_servers --name web-01

# Inspect
python .specdev/tools/arch_config.py list [--env production] [--category databases]
python .specdev/tools/arch_config.py get  --env production --category databases --name app-db
python .specdev/tools/arch_config.py list-envs
python .specdev/tools/arch_config.py add-env --env staging

# Validate (same check the CI gate runs)
python .specdev/tools/arch_config.py validate
```

## Workflow

1. Confirm the environment (default `production`; `add-env` if needed).
2. For each value the user gives, pick the category, map fields with `--set`,
   and turn any secret into a `--secret-ref`.
3. Run `add` (new) or `edit` (existing); the tool validates before writing.
4. After a batch, run `validate` and report the result.
5. Values with `default` set are the ones the product runtime resolves first.

## Guardrails

- Never write a secret value; always a `secret_ref`.
- Never hand-edit `.specdev/architecture-config.json` — use the CLI.
- A `key_vault` secret_ref must point to a real `key_vaults` record in the same
  environment; catalog the vault first.
- At most one `default` per category per environment.
```

- [ ] **Step 2: Verify frontmatter + tool references are consistent**

Run: `python -c "import re,pathlib; t=pathlib.Path('skills/arch-config/SKILL.md').read_text(); assert t.startswith('---'); assert 'arch_config.py validate' in t; assert '--secret-ref' in t; print('skill ok')"`
Expected: prints `skill ok`.

- [ ] **Step 3: Commit**

```bash
git add skills/arch-config/SKILL.md
git commit -m "feat(arch-config): add the arch-config skill"
```

---

### Task 7: CI gate + init note

**Files:**
- Create: `assets/workflows/arch-config-validate.yml`
- Modify: `commands/init.md` (one checklist line)

**Interfaces:**
- Consumes: `arch_config.py validate` (Task 5).
- Produces: a path-filtered workflow shipped to product repos by init; a mention in the init post-install checklist.

- [ ] **Step 1: Write the workflow**

Create `assets/workflows/arch-config-validate.yml`:

```yaml
name: arch-config-validate
on:
  pull_request:
    paths: [".specdev/architecture-config.json"]
  push:
    branches: [main]
    paths: [".specdev/architecture-config.json"]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.x"
      - name: Validate architecture-config.json
        run: python .specdev/tools/arch_config.py validate
```

- [ ] **Step 2: Verify the workflow is valid YAML and wired to the tool**

Run: `python -c "import pathlib; t=pathlib.Path('assets/workflows/arch-config-validate.yml').read_text(); assert 'arch_config.py validate' in t; assert 'architecture-config.json' in t; print('workflow ok')"`
Expected: prints `workflow ok`.

(If PyYAML is available, also: `python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('assets/workflows/arch-config-validate.yml').read_text()); print('yaml ok')"`. Skip if PyYAML is not installed — it is not a project dependency.)

- [ ] **Step 3: Add the init checklist line**

In `commands/init.md`, in the post-install checklist (the bulleted list after "Then print this post-install checklist:"), add one bullet:

```markdown
- Architecture/runtime hosting config: capture values with the `arch-config`
  skill (writes `.specdev/architecture-config.json`); make `arch-config-validate`
  a required status check if you use it.
```

- [ ] **Step 4: Verify the init edit**

Run: `python -c "import pathlib; t=pathlib.Path('commands/init.md').read_text(); assert 'arch-config-validate' in t; print('init ok')"`
Expected: prints `init ok`.

- [ ] **Step 5: Commit**

```bash
git add assets/workflows/arch-config-validate.yml commands/init.md
git commit -m "feat(arch-config): CI validation gate + init checklist note"
```

---

### Task 8: Full-suite green + README mention

**Files:**
- Modify: `README.md` (asset/skill inventory rows)

**Interfaces:**
- Consumes: all prior tasks.
- Produces: documentation parity; a final green run.

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -q`
Expected: PASS — all `tests/test_arch_config.py` cases.

- [ ] **Step 2: Run the tool's own lint gate (py_compile, matches .sdlc/config.json)**

Run: `python -m py_compile assets/specdev/tools/arch_config.py`
Expected: no output, exit 0.

- [ ] **Step 3: Add README rows**

In `README.md`:
- In the "What you get" table, add a row:
  `| Skill \`arch-config\` | capture/edit/delete a product's runtime hosting config (10 categories) as per-environment, reference-only records in \`.specdev/architecture-config.json\` |`
- In the Layout `assets/` block, note `architecture-config.json` (seed) and `tools/arch_config.py`, and under `skills/` note `arch-config/`.

- [ ] **Step 4: Verify README mentions the skill**

Run: `python -c "import pathlib; t=pathlib.Path('README.md').read_text(); assert 'arch-config' in t and 'architecture-config.json' in t; print('readme ok')"`
Expected: prints `readme ok`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(arch-config): document the arch-config skill and assets"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- 10 categories + typed fields → Task 1 (`CATEGORIES`) + Task 2/3 (validation).
- References-only secrets + `secret_ref` shape → Task 3.
- Per-environment scoping → Tasks 1–5 (env-keyed CRUD; `add-env`/`list-envs`).
- Strict `key_vault` referential integrity → Task 3.
- `default` flag, one per category+env → Task 2 (uniqueness) + Task 4/5 (`--default`/`--no-default`).
- Datastore at `.specdev/architecture-config.json` + seed → Task 1.
- Stdlib CLI CRUD + `validate` → Tasks 4–5.
- The skill → Task 6.
- CI gate → Task 7.
- Tests → Tasks 1–5.
- Delivery via init (no init.md logic change, one checklist note) → Task 7; README → Task 8.

**2. Placeholder scan** — no TBD/TODO; every code step shows complete code; every command shows expected output. The Task 2 `_secret_errors` stub is intentional and explicitly replaced in Task 3 (documented in both tasks).

**3. Type consistency** — `validate_doc(doc) -> list[str]` used identically in Tasks 2, 3, 5. `_secret_errors(rec, spec, label, vault_names)` signature matches its call site in Task 2 and its Task 3 definition. `_apply_fields(rec, args)` defined in Task 4, reused in Task 5's `cmd_edit`. `find_record`, `get_env`, `_validate_and_save`, `parse_set`, `parse_secret_ref` signatures are stable across tasks. CLI flag names (`--set`, `--secret-ref`→`args.secret_ref`, `--default`, `--no-default`, `--env`, `--category`, `--name`, `--root`) are consistent between `build_parser` and every `cmd_*`.
