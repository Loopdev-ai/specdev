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
