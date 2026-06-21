# ADR-### — Deployment platform

**Status:** proposed | accepted | superseded by ADR-###
**Date:**
**Relates to:** <REQs with hosting/scale/compliance implications>

> Use this when the deploy target is a *decision* (new product, or a migration),
> not something detected from existing files. See `../deploy-platforms.md`.
> After accepting, set `deploy.profile.json` `target` + `"locked": true`.

## Context

Answer the selection questions (see the guide):

- Server-side logic vs static/frontend only:
- One service or several interdependent:
- Traffic shape (steady / spiky / near-zero):
- Stateful or stateless:
- Existing infra/accounts the org runs:
- Team ops capacity (who operates it):
- Compliance / data-residency constraints:

## Options considered

1. **<Option A>** — fit / pros / cons / ongoing ops cost
2. **<Option B>** — fit / pros / cons / ongoing ops cost
3. **<Option C>** — …

## Decision

**Chosen:** <platform>  → `deploy.profile.json` target: `<target>`

Rationale (tie to the Context — especially why a *simpler* option was or wasn't
sufficient, and explicitly why not something heavier like Kubernetes):

## Consequences

- Ongoing ops burden:
- Rollback strategy: native | redeploy-previous-tag (per the target)
- Config to scaffold: <Dockerfile / fly.toml / manifests / serverless.yml / …>
- Revisit if: <traffic, team, or topology changes that would change the call>
