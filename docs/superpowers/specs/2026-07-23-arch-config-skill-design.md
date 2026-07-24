# Design: `arch-config` skill for SpecDev

**Date:** 2026-07-23
**Status:** Approved (brainstorming) — ready for implementation planning

## Problem

Products built with SpecDev need a durable, auditable record of the
**runtime hosting environment values** their runtime consumes: cloud tenants,
network ranges, databases, app servers, service/API endpoints, service
accounts, cloud app registrations, key vaults, and storage accounts. Today
there is no structured place to capture these — they live in tribal knowledge,
ad-hoc docs, or deployment scripts.

This design adds a SpecDev **skill** that manages those values as
create / edit / delete operations against a JSON datastore in the product repo,
with typed validation and a CI gate.

## Goals

- One well-known, committed JSON datastore per product repo holding hosting
  config, safe to commit (no secret material).
- Create / edit / delete a single configuration value through a skill-driven,
  deterministic CLI.
- Typed, validated records across 10 resource categories.
- Per-environment scoping (dev / staging / production / …).
- A CI gate that fails a PR on an invalid datastore.

## Non-goals (YAGNI)

- A runtime client library for the product app to *read* the config. The file
  is plain JSON keyed by environment; any app reads it directly.
- Import/export, migration, or format-conversion tooling.
- Storing secret **values**. Secrets are referenced, never stored (see
  *Secret handling*).
- Managing environments beyond create + list (no rename/delete-env in v1).

## Key decisions (from brainstorming)

1. **References only for secrets.** No secret material ever lands in the JSON.
   Secret-bearing fields hold a `secret_ref` pointer (Key Vault / env var /
   file). Safe to commit; the product runtime resolves the pointer.
2. **Per-environment scoping.** Values are grouped under a named environment.
   The same logical resource can differ per environment.
3. **`.specdev/` + stdlib Python CLI.** Datastore at
   `.specdev/architecture-config.json`; CRUD + validation in a new stdlib-only
   `.specdev/tools/arch_config.py`. The skill drives the tool and never
   hand-edits the JSON. Consistent with existing SpecDev tooling.
4. **Typed per category.** Each of the 10 categories has defined fields and
   per-type validation.
5. **Strict referential integrity.** A `key_vault` secret_ref must resolve to a
   real `key_vaults` record in the same environment.
6. **CI gate in scope.** A workflow runs `arch_config.py validate` on PRs that
   touch the datastore.
7. **`default` flag per category, scoped per environment.** Any record may be
   flagged `"default": true`. At most one default per category per environment,
   so a runtime that has selected its environment can resolve "the product's
   default key vault / storage / tenant" unambiguously.

## Deliverables

| File | Role |
|------|------|
| `skills/arch-config/SKILL.md` | The skill Claude follows for create/edit/delete. Auto-activates on arch-config triggers or when `.specdev/architecture-config.json` exists. |
| `assets/specdev/tools/arch_config.py` | Stdlib-only CLI: CRUD + `validate`. Copied to `.specdev/tools/` by `/specdev:init`. |
| `assets/specdev/architecture-config.json` | Seed datastore (schema_version + one empty `production` env). Becomes `.specdev/architecture-config.json` in product repos. |
| `assets/workflows/arch-config-validate.yml` | CI gate: runs `arch_config.py validate` on PRs/pushes touching the datastore. Copied to `.github/workflows/` by init. |
| `tests/test_arch_config.py` | pytest suite for the tool (the repo's first tests). |

No plugin.json change is needed — skills are auto-discovered from `skills/`,
and init already copies the whole `assets/specdev/` tree and
`assets/workflows/*.yml`.

## Datastore schema

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

- The **CRUD unit** ("a configuration value") is one named record inside one
  category inside one environment.
- `name` is the record's stable id, **unique within its category + environment**.
- Every record may carry an optional `description` (string) and an optional
  `default` (boolean).

### Categories and typed fields

Secret-bearing fields hold a `secret_ref`, never a literal value. `?` marks an
optional field. All records also allow `description` and `default`.

| Category (key) | Fields |
|---|---|
| `cloud_tenants` | `name`, `cloud`, `tenant_id`, `account_id`, `default_region?` |
| `network_ranges` | `name`, `cidr`, `scope` (vnet/vpc/subnet/other), `region?`, `network_name?` |
| `databases` | `name`, `engine`, `host`, `port`, `database`, `username?`, `secret_ref?` (password), `ssl_mode?` |
| `app_servers` | `name`, `hostname`, `port?`, `protocol?`, `role?`, `os?` |
| `service_endpoints` | `name`, `url`, `protocol?`, `secret_ref?` |
| `api_endpoints` | `name`, `base_url`, `auth_type` (none/api_key/oauth/mtls), `version?`, `secret_ref?` |
| `service_accounts` | `name`, `provider`, `identifier` (upn/email/principal), `secret_ref?` (credential), `roles?` |
| `app_registrations` | `name`, `cloud`, `client_id`, `tenant_id`, `target_resource?`, `secret_ref?` (client secret/cert) |
| `key_vaults` | `name`, `cloud`, `vault_uri`, `tenant_id?`, `region?` |
| `storage_accounts` | `name`, `cloud`, `account_name`, `endpoint?`, `kind` (blob/adls/s3/gcs/file/other), `region?`, `secret_ref?` (access key/connection string) |

Required vs optional fields per category are enforced by `validate` (required =
fields without `?` above, except `secret_ref` which is only required where a
category has no non-secret way to be useful — v1 treats all `secret_ref` fields
as optional to avoid forcing a pointer before the vault is catalogued).

`cloud` is a free string with recommended values `azure | aws | gcp | other`
(not a hard enum, so the tool doesn't block an unlisted provider).

### `secret_ref` shape

```json
"secret_ref": {
  "provider": "key_vault",       // key_vault | env | file
  "vault": "core-kv",            // provider=key_vault: name of a key_vaults record (same env)
  "secret_name": "db-password",  // provider=key_vault
  "env_var": "DB_PASSWORD",      // provider=env
  "path": "/run/secrets/db"      // provider=file
}
```

Validation per provider:
- `key_vault` → requires `vault` + `secret_name`; `vault` must match the `name`
  of a `key_vaults` record in the **same environment** (strict referential
  integrity).
- `env` → requires `env_var`.
- `file` → requires `path`.

## CLI tool — `arch_config.py`

Stdlib only (`argparse`, `json`, `pathlib`, `sys`, `re`), mirroring the other
`.specdev/tools/*.py`. Default `--root .`; datastore path
`<root>/.specdev/architecture-config.json`.

```
list     [--env E] [--category C]                          # summary rows
get      --env E --category C --name N                      # full record JSON
add      --env E --category C --name N \
         --set field=value ... \
         [--secret-ref provider=key_vault,vault=..,secret_name=..] \
         [--default]                                        # must NOT already exist
edit     --env E --category C --name N \
         --set field=value ... [--secret-ref ...] [--default|--no-default]  # must exist
delete   --env E --category C --name N
add-env  --env E                                            # create empty environment
list-envs
validate                                                    # whole-file check; exit non-zero on any error
```

Behavior:
- `--set field=value` sets scalar fields (repeatable). Unknown fields for the
  category are rejected.
- `--secret-ref k=v,k=v` builds the `secret_ref` object.
- `add`/`edit`/`delete` run the full `validate` on the resulting document
  **before** writing, and write **atomically** (temp file + replace). A
  validation failure aborts the write and exits non-zero.
- `add` fails if the name already exists in that category+env; `edit` fails if
  it does not.
- `validate` enforces: known categories only; no unknown fields; required
  fields present per category; `secret_ref` well-formed per provider; strict
  `key_vault` referential integrity; at most one `default` per category+env;
  and a **secret-leak guard**.

### Secret-leak guard

`validate` fails if a secret-bearing field (`secret_ref` position) holds a
string instead of a `secret_ref` object, or if any field value matches
high-signal secret patterns (e.g. `password=`, PEM headers, long base64/hex
blobs, connection strings containing `Password=`/`AccountKey=`). This is a
best-effort backstop so a literal secret never reaches the committed file; it
complements (does not replace) the repo's existing gitleaks gate.

## The skill — `skills/arch-config/SKILL.md`

Frontmatter `description` triggers on: "architecture config", "runtime config",
"hosting values", "capture tenant/database/endpoint/storage/key-vault config",
managing runtime environment values, or the presence of
`.specdev/architecture-config.json`.

Body covers:
- **The reference-only secret rule** stated up front: never paste a secret;
  capture a `secret_ref` pointing to a key vault / env var / file.
- Per-environment scoping and how to pick the environment.
- The 10-category cheat-sheet with each category's fields.
- The `default` flag semantics (one per category per environment).
- The exact `arch_config.py` invocation for each CRUD action, and to run
  `validate` after a batch of edits.
- The rule that the skill **drives the tool** and never hand-edits the JSON.

## CI gate — `arch-config-validate.yml`

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
        with: { python-version: "3.x" }
      - run: python .specdev/tools/arch_config.py validate
```

Path-filtered so it only runs when the datastore changes. Inert in repos that
never create the file (the seed ships empty-but-valid, so it passes from day
one). Making it a required status check is left to the repo's branch-protection
setup, consistent with the other SpecDev gates.

## Testing

`tests/test_arch_config.py` (pytest, run from repo root; imports the tool by
file path via `importlib`). Coverage:

- **add / get / edit / delete** round-trips; uniqueness (`add` dup fails,
  `edit` missing fails).
- **Per-environment scoping**: same name in two envs is independent;
  `add-env` / `list-envs`.
- **Typed validation**: unknown category rejected; unknown field rejected;
  missing required field rejected.
- **secret_ref**: each provider's required fields; strict `key_vault`
  referential integrity (dangling vault ref fails; valid ref passes).
- **Secret-leak guard**: literal secret in a secret field fails.
- **`default`**: two defaults in one category+env fail; one is fine; defaults in
  different envs are independent.
- **Atomic write**: a validation failure during `add`/`edit` leaves the file
  unchanged.

## Delivery / consistency notes

- Init already copies `assets/specdev/` → `.specdev/` and
  `assets/workflows/*.yml` → `.github/workflows/`, so all four shipped files
  land with no change to `init.md`. A one-line mention in the init post-install
  checklist ("architecture config gate: make `arch-config-validate` a required
  check if you use it") is optional polish, included in the plan.
- The seed `architecture-config.json` in this plugin repo doubles as the
  template; it ships with one empty `production` environment and all 10
  category arrays present and empty, so `validate` passes immediately.
```
