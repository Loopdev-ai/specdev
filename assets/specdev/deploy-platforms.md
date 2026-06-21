# Deployment Platform Selection Guide

Choosing where to run is an **architecture decision**, not something to inherit
from whatever infra happens to exist. Record the choice as an ADR
(`adr/ADR-deployment-platform.md`), then lock it in `deploy.profile.json`.

## Default bias: simplest thing that meets the requirements

Pick the lightest option that satisfies the spec's real needs (traffic,
statefulness, compliance, existing infra, team ops capacity). Complexity is a
cost paid every day, not a one-time setup. **Kubernetes is rarely the right
first choice** — only adopt it when you genuinely need it (see below).

## Options, lightest → heaviest

| Platform | `target` | Choose when | Avoid when |
|----------|----------|-------------|------------|
| Static host (Pages/S3/CDN) | `static`* | Pure frontend / SSG, no server | You need server-side logic |
| Vercel / Netlify | `vercel` / `netlify` | Frontend + light serverless, preview URLs | Heavy backend, long jobs |
| PaaS (Fly.io, Render, Railway) | `fly` / `script` | Small-to-mid web service, one container, minimal ops | Complex multi-service topology |
| Serverless (Lambda/SAM, Cloud Functions) | `serverless` / `sam` | Event-driven, spiky/low traffic, pay-per-use | Long-running, heavy CPU, stateful |
| Managed containers (Cloud Run, App Runner, ECS) | `cloudrun` / `script` | Containerized service, autoscale-to-zero, no cluster to run | You need fine-grained orchestration |
| Kubernetes / Helm | `kubernetes` / `helm` | Many interdependent services, you ALREADY run a cluster, a platform team owns it, or specific scheduling/scale needs | A single service, a small team, or "because it's standard" |

\* `static` and any platform without a built-in recipe run through the **`script`**
target: provide `.specdev/deploy/deploy.sh` (and optionally `rollback.sh`). This
is the universal adapter — the kit is never tied to a fixed platform list.

## Decision questions (answer these in the ADR)

1. Is there server-side logic, or is it static/frontend only?
2. One service or several interdependent ones?
3. Traffic shape: steady, spiky, or near-zero most of the time?
4. Stateful (DB/queues/sessions) or stateless?
5. Existing infra/accounts the org already operates?
6. Team's ops capacity — who runs this at 2am?
7. Compliance/residency constraints on where it can run?

## After deciding

1. Write `adr/ADR-deployment-platform.md` (options, decision, rationale).
2. Set `deploy.profile.json` `target`, fill `params`, set `"locked": true` so
   detection won't override the decision.
3. If the platform needs config files (Dockerfile, fly.toml, k8s manifests,
   serverless.yml…), scaffold them so the build/deploy steps have what they need.
4. Run `deploy.py preflight --env staging` / `--env production` until green.
