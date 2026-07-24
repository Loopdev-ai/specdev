# SpecDev (Claude Code plugin)

A reusable spec-driven delivery pipeline. Pairs **arckit**-style spec +
architecture artifacts with a **superpowers**-style brainstorm → plan → TDD
build, gated by **GitHub PRs**, with **fully-automatic deployment**, **two QA
phases**, and a generated **traceability matrix**.

## Install

```
/plugin marketplace add Loopdev-ai/specdev   # or a local clone path
/plugin install specdev@loopdev-specdev
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
| Agent `adr-checker` | verifies the repo against applicable org ADRs; writes the compliance manifest; gates every PR |
| Skill `arch-config` | capture/edit/delete a product's runtime hosting config (10 categories) as per-environment, reference-only records in `.specdev/architecture-config.json` |
| `governance/` (this repo) | the org's **architectural repo of record**: tier-scoped org ADRs, classification scheme, generated index |
| Assets `.specdev/` | spec, ADR, component-map, BUILD, traceability templates + Python tools |
| Assets `.specdev/compliance/` | optional control-framework layer (ISO 27001/42001, SOC 2, NIST 800-53) — catalogs, crosswalk, SoA/risk/DPIA templates, `gen_compliance.py` |
| Assets workflows | the GitHub Actions below |

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
| `org-adr-check.yml` | every PR | org-ADR gate — applicable org ADRs verified & hash-current |
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

## Org ADR governance (architectural repo of record)

This repo doubles as the org's **repo of record** for cross-cutting
architecture decisions. Org ADRs live in
[governance/adr/](governance/adr/) with machine-readable frontmatter —
`applies_to` scopes each decision to repo **classifications** defined in
[governance/classification.json](governance/classification.json). The scheme
is **multi-axis**: seeded axes are `maturity` (`poc < dev < prod`, ordered)
and `audience` (`internal`, `customer`); add axes or values freely. Each repo
declares one value per axis, and `applies_to` entries compose — list entries
are OR, `&` is AND, `dev+` means "that rank and above" — so
`[customer & dev+]` binds customer-facing repos at dev maturity or higher and
leaves an internal POC untouched.
`gen_adr_index.py` compiles them into a single-fetch `index.json` (per-ADR
content hash + the classification scheme), and this repo's own `adr-index.yml`
check keeps the index honest.

Product repos are linked back at `/specdev:init`: `.specdev/org.json` records
the governance repo, ref, and the repo's classification, and a marked
`CLAUDE.md` block makes the link hold in every session. Enforcement is
two-layered and pull-based (the governance repo never reaches into product
repos):

- **Authoring time (automated, subagent):** the `adr-checker` agent fetches
  the index, filters to applicable ADRs, judges each ADR's **Conformance**
  section against the repo, and writes the verification manifest
  `.specdev/adr/org-compliance.json`. The pipeline **holds every PR** — Spec
  and Implementation — until the checker is green; red verdicts loop
  `builder-fix → qa-verifier → re-check` automatically with no human prompt.
  Full ADR bodies stay in the checker's context, never the coordinator's.
- **Review time (deterministic CI):** `org-adr-check.yml` runs
  `check_org_adrs.py`, which re-fetches the index at the pinned ref and fails
  if any applicable ADR lacks a manifest entry, an exclusion lacks
  justification, or an entry's content hash no longer matches (the org ADR
  changed upstream → verification is stale). Determinism comes from hashes,
  not model judgment. Inert until `org.json` is configured.

A repo that cannot conform records a local ADR plus a justified
`superseded-by-local` manifest entry — visible to humans at PR review, never
silent.

## Compliance (control frameworks)

An optional layer maps your build to control frameworks — **ISO/IEC 27001 &
42001, SOC 2 Type II, NIST 800-53** — as a second traceability axis:
`CONTROL → evidence (REQ / ADR / test / commit / config / gate)`. It lives
entirely in files under `.specdev/compliance/` (catalogs, a cross-framework
crosswalk, SoA/risk-register/SSP/DPIA/AI-risk templates) plus one tool,
`gen_compliance.py` — nothing in the skill.

Controls earn evidence the same way requirements do: an ADR line
`Satisfies controls: A.8.24, CC6.1`, a commit trailer `Controls: A.8.24`, a REQ
`Controls:` tag, or a test naming the control are all discovered automatically.
`gen_compliance.py --check-gaps` (wired in `compliance.yml`) fails if any
*applicable* control lacks evidence or a documented exclusion; for SOC 2 Type II
it also flags evidence older than the configured freshness window. NIST 800-53
imports the official **OSCAL** catalog rather than bundling 1000+ controls. See
[assets/specdev/compliance/README.md](assets/specdev/compliance/README.md).

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
  adr-checker.md         org-ADR verification; gates every PR
skills/
  specdev/SKILL.md       the workflow driver (strict coordinator)
  arch-config/SKILL.md   runtime hosting config capture/edit/delete
governance/
  classification.json    multi-axis classification scheme (maturity, audience; extensible)
  adr/                   org ADRs (repo of record) + generated index.json/INDEX.md
  tools/gen_adr_index.py index compiler + frontmatter validator
.github/workflows/
  adr-index.yml          this repo's gate: valid frontmatter, fresh index
assets/
  specdev/               → copied to .specdev/ in target repos (incl. org.json, architecture-config.json seed, tools/arch_config.py)
  workflows/             → copied to .github/workflows/ in target repos
```
