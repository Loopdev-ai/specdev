# SpecDev (Claude Code plugin)

A reusable spec-driven delivery pipeline. Pairs **arckit**-style spec +
architecture artifacts with a **superpowers**-style brainstorm → plan → TDD
build, gated by **GitHub PRs**, with **fully-automatic deployment**, **two QA
phases**, and a generated **traceability matrix**.

## Install

```
/plugin marketplace add alaneff/specdev      # or your git remote URL
/plugin install specdev@neffsec-specdev
```

Then, inside any repo you want to put on the pipeline:

```
/specdev:init                  # lay down .specdev/ + .github/workflows/
/specdev:new-feature my-thing  # start a spec/ branch + draft spec
/specdev:build FEAT-001        # coordinator build: waves + per-wave QA
```

The `specdev` skill auto-activates when you ask to build/extend through the
gated flow or when a repo contains `.specdev/`.

## What you get

| Component | Provides |
|-----------|----------|
| Skill `specdev` | the end-to-end workflow Claude follows (gates, TDD build, traceability rules) as a **strict coordinator** that offloads heavy work to subagents |
| Command `/specdev:init` | scaffolds the kit into the current repo |
| Command `/specdev:new-feature` | brainstorm + draft a spec on a `spec/` branch |
| Command `/specdev:build` | coordinator build loop: wave dispatch + per-wave QA + traceability |
| Agent `component-builder` | TDD-builds one component in isolation; runs in parallel waves |
| Agent `spec-explorer` | reverse-maps an existing codebase for extensions (read-only) |
| Agent `qa-verifier` | local Gate-2 dry run; returns only actionable failures |
| Assets `.specdev/` | spec, ADR, component-map, BUILD, traceability templates + Python tools |
| Assets workflows | the 5 GitHub Actions below |

## Context & parallelism

The skill runs the orchestrator as a **strict coordinator**: it holds only the
spec, the component dependency DAG, and a running status — never full source or
test output. All heavy work goes to subagents whose context is discarded, so a
long build doesn't fill the window. Components are built in **dependency waves**
(`components.md` "Depends on" column → DAG), with each wave's independent
components dispatched to parallel `component-builder` agents. **After every wave
a separate `qa-verifier` agent gates it** (tests + coverage + secret scan +
traceability gap check) — QA happens every build step, and stays independent of
the agent that wrote the code. Phase state is checkpointed to the `BUILD.md`
wave ledger, the source of truth.

**Driving pattern (this is what makes the above actually happen):** drive
multi-feature builds with **`/specdev:build`** — invoking the command is the
authorization to spawn subagents, so the coordinator dispatches instead of
quietly building inline (which fills context and collapses QA independence).
Work in **short sessions**: because durable state lives in the repo (`spec.md`,
the `BUILD.md` wave ledger, traceability, PRs), a fresh session resumes a build
from the first unfinished wave rather than carrying one marathon thread.

## Pipeline

```
SPEC PR  ──gate1: spec-validate──merge─▶
  feat branch ─ scaffold ─ TDD build ─▶
IMPL PR  ──gate2: post-dev-qa (required checks)──merge─▶
  deploy staging ─▶ post-deploy-qa(staging)        ← promotion gate
      ├─ FAIL ─▶ rollback + incident   (prod never reached)
      └─ PASS ─▶ AUTO-PROMOTE ─▶ deploy prod ─▶ post-deploy-qa(prod)
  ─▶ traceability matrix regenerated, committed beside BUILD.md
```

| Workflow | Trigger | Role |
|----------|---------|------|
| `spec-validate.yml` | PR to `spec/**` | Gate 1 — artifact completeness + REQ IDs |
| `post-dev-qa.yml` | PR to `main` | Gate 2 — tests, security, coverage, trace gaps |
| `deploy.yml` | push to `main` | build, tag, staging→QA→auto-prod→QA, rollback |
| `post-deploy-qa.yml` | called by deploy | staging promotion gate + prod verification |
| `traceability.yml` | after deploy | regenerate + commit the matrix |

## Deploy determination (built in)

Deploy, rollback, and health are **determined by a profile, not hard-coded**.
`detect_deploy.py` inspects the repo for platform signatures (fly.toml,
vercel.json, k8s/Helm manifests, serverless.yml, SAM, a `.specdev/deploy/`
script, …) and writes `.specdev/deploy.profile.json`. `deploy.py` reads that
profile and runs the right commands, so the workflows stay generic. **Rollback
is target-aware:** native where the platform supports it (Fly/Vercel/Helm/k8s),
otherwise redeploy the previous git release tag — stateless, no per-repo TODO.
Unknown targets are written as `manual` and fail loudly rather than guessing.

**The platform is a decision, not a default.** Detection is only for *existing*
infra. For a **new** product there's nothing to detect — choose the platform as
an architecture decision (`deploy-platforms.md` + `ADR-deployment-platform.md`),
biased to the simplest fit (the kit never defaults to Kubernetes), then set
`target` + `"locked": true` so detection won't override it. Any platform without
a built-in recipe runs through the **`script`** target
(`.specdev/deploy/deploy.sh`), so you're never tied to a fixed list.

**Destination facts are discovered, verified, or documented — never silent
stubs:**

- **Discover** — `detect_deploy.py` pulls facts offline where possible (k8s
  deployment name/namespace/image from manifests, the Fly app + `*.fly.dev`
  URL) and records each in `provenance` as `discovered`/`declared`/`missing`.
- **Verify** — `deploy.py preflight --env <env>` fails if any required fact or
  env URL is still a placeholder; the `preflight` job in `deploy.yml` gates the
  build, so **a merge cannot deploy with an unresolved spec**. `--probe` adds a
  best-effort live platform check. Passing stamps a `verified` timestamp.
- **Document** — anything not auto-discoverable is captured as the build happens
  in `BUILD.md` → *Deployment Facts*, with its source, alongside the profile.

## After `/specdev:init` — required wiring

1. Replace the **build/test** `TODO:`s in `.github/workflows/` (deploy/rollback/
   health are already wired to the profile).
2. Review `.specdev/deploy.profile.json`: set real env URLs, fix any
   `REPLACE_ME` params, and add the platform-CLI install/auth step noted in
   `deploy.yml`. A wrong profile is the one thing that defeats auto-prod safety.
3. Branch-protect `main` (require `post-dev-qa` checks + review); require
   `spec-validate` on `spec/**`.
4. Create `staging` and `production` Environments (no reviewer on production).

## Traceability convention

Spec assigns `REQ-###`; ADRs declare `Relates to: REQ-###`; tests reference
their `REQ-###`; commits end with a `Refs: REQ-###` trailer. `gen_traceability.py`
joins these with PR / release / QA-run data into the matrix, and `--check-gaps`
fails Gate 2 if any requirement has no test. The matrix is **cumulative across
the whole product**: it unions REQs from the active `spec.md`, every archived
`specs/*.md`, and an optional `product-requirements.md` catalog — so archiving a
finished feature never drops it from coverage.

## Layout

```
.claude-plugin/
  plugin.json            plugin manifest
  marketplace.json       so it installs via /plugin
commands/
  init.md                /specdev:init
  new-feature.md         /specdev:new-feature
  build.md               /specdev:build (wave dispatch + per-wave QA)
agents/
  component-builder.md   parallel TDD component builds
  spec-explorer.md       read-only reverse-map for extensions
  qa-verifier.md         local Gate-2 dry run
skills/specdev/SKILL.md  the workflow driver (strict coordinator)
assets/
  specdev/               → copied to .specdev/ in target repos
  workflows/             → copied to .github/workflows/ in target repos
```
