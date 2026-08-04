# SpecDev (Claude Code plugin)

A reusable spec-driven delivery pipeline. Pairs **arckit**-style spec +
architecture artifacts with a **superpowers**-style brainstorm → plan → TDD
build, gated by **GitHub PRs**, with **fully-automatic deployment**, **two QA
phases**, and a generated **traceability matrix**.

## Install

### Claude Code CLI (terminal)

```
/plugin marketplace add Loopdev-ai/specdev   # or a local clone path
/plugin install specdev@loopdev-specdev
```

### Claude Code VS Code extension

The extension only lists plugins from marketplaces you have added — it ships
with none of this repo's, so **you must add the marketplace by hand first**.
Until you do, the plugin browser shows *"No marketplaces configured. Add one
above to discover plugins."*

1. Open the Claude Code panel and go to **Plugins** ("Install and manage
   plugins") to open the **Manage Plugins** dialog.
2. In the marketplace field — placeholder *"GitHub repo, URL, or path…"* —
   enter the **repo**, not the marketplace name:

   ```
   Loopdev-ai/specdev
   ```

   Press **Add**. A local clone path works here too.
3. Find `specdev` under **Search plugins…**, install it, and pick a scope:
   - **Install for you** — available in all your projects
   - **Install for this project** — shared with all collaborators
   - **Install locally** — only for you, only in this repo
4. Click **Restart** when the *"Restart Claude to apply plugin changes"* notice
   appears. Plugin commands do not load until you do.

> **The source and the marketplace name differ.** You *add* `Loopdev-ai/specdev`
> (the repo) but the marketplace registers itself as `loopdev-specdev` — which is
> why the CLI installs `specdev@loopdev-specdev`. Entering `loopdev-specdev` as
> the source will not resolve.

Alternatively, run **Claude Code: Install Plugin** from the Command Palette and
enter `specdev@Loopdev-ai/specdev` in the `plugin@marketplace` prompt. If the
marketplace is not yet added it offers **Add Marketplace & Continue**, which
does both steps at once.

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
| Skill `adr` | interviews, allocates the id, lints, and conflict-checks an architecture decision record (local or org) — the only supported way to write one |
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

### Headless builds (`specdev-build.yml`)

The build can be handed off to CI, where the coordinator runs unattended. Three
properties keep an unattended run honest:

- **The job is not green because the process exited.** It ends by asserting the
  mode's terminal state — an Implementation PR exists for this unit and FEAT
  (and, for `poc`, was merged) — and that `BUILD.md` is no longer the shipped
  template. `deploy-poc` is gated on that assertion, so a build that produced
  nothing cannot trigger a deployment.
- **The checkpoint leaves the runner.** `BUILD.md` and `run.json` are pushed to
  `specdev/checkpoint/<unit>/<FEAT-###>` on success, failure, timeout and
  circuit-break alike; the runner's working tree is destroyed at teardown, so
  the push is what makes a run resumable. With `auto_resume` (default on) a
  re-dispatch restores that ref before the agent starts.
- **A circuit breaker runs inside the agent loop**, as a hook — a post-run
  check cannot refund a run that has already spent the money. Limits live in
  `ci.json`:

| `ci.json` key | Default | Trips when |
|---|---|---|
| `max_permission_denials` | 15 | cumulative denied tool calls reach it |
| `max_consecutive_tool_failures` | 15 | that many tool calls fail in a row |
| `max_cost_usd` | 10 | accumulated spend reaches the ceiling |
| `auto_resume` | `true` | — restores the checkpoint ref on re-dispatch |

On a trip the agent is stopped, the checkpoint is pushed, and the job exits
with a status distinct from a hard failure — the signal to re-dispatch rather
than debug. Every run uploads a build-outcome record (turns, cost, denial
counts and a histogram of denied tools, terminal state reached vs. required)
to the step summary and as an artifact.

The workflow's `--allowedTools` list has a **required floor** documented inline
above it. It is a session-level permission that subagents inherit, so cutting
it below the union of what `.claude/agents/*.md` declare disables the
delegation model rather than hardening it. Containment comes from the scope
check, the no-self-merge rule, the human PR gate, the circuit breaker and the
egress deny list — not from tool-prefix scarcity.

## Deploy determination (built in)

Deploy, rollback, and health are **determined by a profile, not hard-coded**.
`detect_deploy.py` inspects the repo for platform signatures (fly.toml,
vercel.json, k8s/Helm manifests, serverless.yml, SAM, a `.specdev/deploy/`
script, …) and writes `.specdev/deploy.profile.json`. `deploy.py` reads that
profile and runs the right commands, so the workflows stay generic. **Rollback
is target-aware:** native where the platform supports it (Fly/Vercel/Helm/k8s),
otherwise redeploy the previous git release tag — stateless, no per-repo TODO.
Unknown targets are written as `manual`, which means "no automatic deployment"
— the deploy chain skips with a notice rather than guessing at a platform.

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
  Fail-closed applies to a **real** target: while `target` is still `manual`
  (a new product, or a repo that never deploys) the whole chain skips with a
  notice instead — pick a platform to turn it on.
- **Document** — anything not auto-discoverable is captured as the build happens
  in `BUILD.md` → *Deployment Facts*, with its source, alongside the profile.

## After `/specdev:init` — required wiring

> The four stack-specific `post-dev-qa` gates (lint, unit tests, coverage,
> SAST) ship **failing**, with a message naming what to replace. That is
> deliberate: a stub that `echo`s exits 0, so an unwired repo would have a QA
> gate reporting green while verifying nothing.

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

## Writing ADRs

`/specdev:adr` (skill: `adr`) authors both kinds of decision record — local
`.specdev/adr/ADR-###.md` and org `governance/adr/ADR-####-<slug>.md` — and is
the only supported way to write one. It interviews for the parts an ADR is
actually for (what you rejected, and what the decision costs you), then runs
`.specdev/tools/adr.py`:

| Command | What it decides |
|---------|-----------------|
| `adr.py next-id` | the next free id, per layer |
| `adr.py lint --file F` | structure: two real options, non-empty consequences, a linked REQ, no placeholder text, no frontmatter/prose drift |
| `adr.py conflicts --file F` | hard errors (duplicate ids, broken supersession chains) plus a **shortlist** of accepted ADRs that overlap this one on scope, REQ, or `applies_to` |

The tool narrows; the skill judges. A real contradiction is resolved by
revising the new ADR, superseding the old one, or narrowing scope — never by
writing it anyway.

Local ADRs written this way carry YAML frontmatter *and* the older prose
`**Status:**` / `**Relates to:**` lines, so `gen_traceability.py` and
`validate_spec.py` keep working unchanged; `lint` fails if the two ever drift
apart.

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

## Monorepos: multiple governed units

By default a repository **is** one governed unit and nothing below applies —
single-root repos need no configuration and are entirely unaffected.

A repo holding several projects at different maturities can instead declare each
as its own **governed unit**: a directory containing a `.specdev/`. A spike and a
production service in the same repo are then classified, gated, and built
independently. This exists because the alternative is unsafe in one direction —
classify such a repo `poc` and its production code silently escapes the org ADRs
that should bind it; classify it `prod` and every spike must clear a production
bar or carry an exception, which teaches teams to route around governance.

Convert an existing repo:

```bash
python .specdev/tools/units.py migrate --unit infrastructure
python .specdev/tools/units.py check
```

That moves the unit-scoped artifacts into `infrastructure/.specdev/`, leaves the
repo-scoped ones (`tools/`, `ci.json`) at the root, and writes
`.specdev/units.json`:

```json
{
  "schema_version": 1,
  "governance_repo": "your-org/governance",
  "ref": "main",
  "path": "governance/adr",
  "units": [
    "demos",
    "infrastructure",
    {"path": "soc-automation", "depends_on": ["infrastructure"]}
  ]
}
```

- **The governance link is repo-wide** and lives only in the registry. Each
  unit's `.specdev/org.json` carries just its `classification`. Redeclaring a
  link key on a unit with a different value is a hard error — two units pinning
  different governance refs is a footgun with no upside.
- **`depends_on` drives effective classification.** A unit is governed at the
  level of the highest-classified unit that depends on it, transitively, because
  anything a production system imports is inside the production blast radius.
  Moving risky code into a `poc` unit that a `prod` unit imports therefore does
  **not** escape governance. The reverse does not escalate: a `poc` demo
  importing a `prod` library stays `poc`.
- **Branches carry the unit:** `spec/<unit>/<name>`, `poc/<unit>/<name>`. A
  segment is read as a unit only when it names a registered unit, so existing
  `spec/<name>` branches keep working. A PR touching files outside its named
  unit fails the scope check.
- **Builds run in parallel across units** and serialise only within one.
- **Per-unit artifacts are never merged.** An SoA carries a scope statement, so
  concatenating two units' SoAs yields a document true of neither; the rolled-up
  index links to each unit's file instead.

Two CI notes that matter:

- Mark each gate's **`summary`** job the required status check, not the matrix
  legs. Leg names are dynamic (`check (infrastructure)`), so branch protection
  cannot require them — a newly added unit would otherwise arrive as a silently
  unrequired check.
- `org-adr-check` and the nightly `specdev-sweep` are **never** filtered by
  changed paths. The ADR staleness check is driven by the upstream index, not
  the local diff: an org ADR can change with no local commit at all. Filtering
  them would leave a quiet repo looking green while its verifications rot.

Registry validation runs in every gate's `discover` job; run it locally with
`python .specdev/tools/units.py check`. Both drift directions fail — a
`.specdev/` that is not registered, and a registered unit with no `.specdev/`.

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
    tools/units.py       governed-unit registry: the only module that knows a repo can hold >1 unit
  workflows/             → copied to .github/workflows/ in target repos
    deploy-unit.yml      reusable staging→QA→prod chain for ONE unit; deploy.yml matrixes it
    specdev-sweep.yml    nightly unfiltered all-unit ADR-staleness sweep
```
