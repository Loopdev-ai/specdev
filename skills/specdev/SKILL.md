---
name: specdev
description: Spec-driven delivery workflow with GitHub PR gates, automatic deployment, two-phase QA, and a traceability matrix. Use when the user wants to build a new product/feature OR extend an existing one through a gated, auditable pipeline (spec → architecture → TDD build → PR gates → auto-deploy → post-deploy QA → traceability). Trigger on "specdev", "spec-driven", "build with gates", "AI-SDLC", or when a repo contains a .specdev/ directory.
---

# SpecDev — spec-driven delivery pipeline

Drive a feature from idea to verified production through enforced gates. arckit
owns the upstream artifacts (spec + architecture); a superpowers-style
brainstorm → plan → TDD build owns the downstream. GitHub PRs are the gates;
deployment, QA, and traceability are automatic.

## Prerequisites

- The repo must contain `.specdev/` and `.github/workflows/`. If it does not,
  run `/specdev:init` first (copies them from this plugin's assets).
- One feature = one `FEAT-###` = a Spec PR then an Implementation PR.

## Establish the governed unit FIRST

Run `python .specdev/tools/units.py list`.

- **One line, `.`** — a single-unit repo. Every `.specdev/…` path below is
  literal and nothing else in this section applies.
- **Several lines** — a monorepo of governed units. Pick the unit this feature
  belongs to and treat **every `.specdev/<artifact>` path below as
  `<unit>/.specdev/<artifact>`**: spec, components, ADRs, run.json, BUILD.md,
  compliance, traceability. The **tools stay at the repo root**
  (`.specdev/tools/`) regardless — pass `--root <unit>` to each one, and give
  the unit root to the `adr-checker` subagent.

  Branches carry the unit: `spec/<unit>/<name>` and `poc/<unit>/<name>`.

  **Do not modify files outside your unit.** A scope check fails the build if
  you do. Shared, unit-less files (top-level docs, CI config) are permitted but
  should be touched only when the feature genuinely requires it.

  A unit's **effective** classification can exceed what its `org.json` declares:
  if a higher-classified unit depends on yours, yours is governed at that higher
  level. `check_org_adrs.py` prints the escalation and the dependent that caused
  it. Judge against the effective value.

## The pipeline (do these in order)

1. **Detect mode.** New product → spec from scratch. Extending an existing one →
   dispatch the **`spec-explorer`** agent to reverse-map the touched code; take
   its returned component map into `.specdev/components.md` (each marked
   `new`/`modified`/`untouched`) and write the spec as a *diff* (fill
   **Current behavior** per requirement). Do not read the codebase survey into
   this thread yourself — that is the explorer's job to summarize.
2. **Brainstorm.** Socratic Q&A to pin users, problem, scope, out-of-scope.
   Don't write the spec until the problem is unambiguous.
3. **Spec (Gate 1 artifact).** Fill `.specdev/spec.md`: assign `FEAT-###`, one
   `REQ-###` per requirement, each with a testable **Acceptance** line, and a
   concrete **Out of Scope**. Use `/specdev:new-feature` to start this on a
   `spec/<feature>` branch.
4. **Architecture.** Record every decision by invoking the **`adr`** skill —
   it runs the decision interview, allocates the id, lints the result, and
   proves the ADR does not conflict with an already-accepted one. **Do not
   hand-write a file into `.specdev/adr/`**: a hand-written ADR skips the
   conflict check, which is the whole point of the artifact. Skip the step
   entirely only if the change fits existing architecture.
   **The deployment platform is one of these decisions, not a detection result:**
   - *New product:* there is nothing to detect. Choose the platform using
     `.specdev/deploy-platforms.md` — bias to the simplest option that meets the
     spec's real needs (do not default to Kubernetes). Record it via the
     **`adr`** skill (scope tag `deployment`), then set
     `deploy.profile.json` `target` + `params` and `"locked": true` so
     detection won't override the decision.
     Scaffold whatever platform config the choice needs (Dockerfile, fly.toml,
     manifests, …) so build/deploy have inputs.
   - *Existing product:* `detect_deploy.py` reads the current target; only write
     a platform ADR if you're deliberately migrating.
   - Any platform without a built-in recipe uses the `script` target
     (`.specdev/deploy/deploy.sh`) — the kit is never tied to a fixed list.

   **Org governance check (if `.specdev/org.json` is configured):** dispatch
   the **`adr-checker`** agent now — org ADRs from the architectural repo of
   record that apply to this repo's classification constrain these decisions.
   Resolve a conflict by changing the design, or by a *justified* deviation: a
   local ADR documenting it plus a `superseded-by-local` manifest entry. Never
   proceed silently past a violation, and never read org ADR bodies into this
   thread — that is the checker's job.
5. **Open the Spec PR.** Before opening it, the **`adr-checker`** agent must
   be green (when org governance is configured) — a spec that contradicts an
   applicable org ADR is fixed *before* review, not during. Then
   `spec-validate.yml` runs `validate_spec.py` and `org-adr-check.yml` runs
   `check_org_adrs.py` as required checks. Get them green, get human approval,
   merge. This locks the contract — do not renumber REQs afterward.
6. **Build on `feat/<feature>`** off the merged spec — run **`/specdev:build`**,
   which drives the loop below. You act as a coordinator, not a builder (see
   *Context discipline* and *Parallel dispatch protocol*):
   - Scaffold to match `components.md`; log files in `.specdev/BUILD.md`.
   - **MANDATORY: you MUST NOT write component code in the main thread.** Every
     component is built by a **`component-builder`** subagent. The coordinator
     holds only the spec, the component DAG, and returned summaries. Building
     inline is a process violation — it both fills the context window and
     collapses QA independence.
   - Build in dependency waves (parallel within a wave). You hand each builder
     its component row + spec section + acceptance criteria; it enforces TDD and
     `Refs: REQ-###` trailers and returns a summary you record in `BUILD.md`.
   - **Per-wave QA gate (MANDATORY): after EACH wave, dispatch the
     `qa-verifier` agent — do not proceed until it returns green.** QA happens
     every wave, not only before the PR. A red wave goes back to a builder.
   - **Per-wave traceability gate: after each wave, the qa-verifier's
     `gen_traceability.py --check-gaps` must pass** — every `REQ-###` built that
     wave has a linked test before the next wave starts.
   - **Starting a build authorizes subagent dispatch.** This workflow spawns
     subagents by design; the harness default ("don't spawn unless asked") does
     not apply once the user has invoked the SpecDev build — dispatch them.
7. **Open the Implementation PR (Gate 2) — only after the org-ADR loop is
   green.** With org governance configured, run this fully automatic loop
   first (no user prompts between iterations): dispatch **`adr-checker`** →
   on red, dispatch a `component-builder` to fix each named violation (or
   amend the local ADRs for a justified deviation — via the **`adr`** skill,
   so the deviation ADR is linted and conflict-checked like any other) →
   re-run `qa-verifier` → re-run `adr-checker` — repeat until green. **Do not
   open the PR while the checker reports violations.** Then `post-dev-qa.yml`
   runs tests, security scan, coverage, and `gen_traceability.py
   --check-gaps` (fails if any REQ has no test), and `org-adr-check.yml`
   deterministically re-proves the manifest against the org index. These are
   required status checks. Get review + green, merge.
8. **Merge is the deploy trigger.** `deploy.yml` builds, tags an immutable
   release, deploys staging, then runs `post-deploy-qa.yml` against staging.
   **Staging QA is the promotion gate** (prod is fully automatic, no human
   gate): pass → auto-promote to prod → prod QA; fail → rollback + incident.
   Deploy/rollback/health are **determined by the profile**, not hard-coded:
   `detect_deploy.py` writes `.specdev/deploy.profile.json`, and `deploy.py`
   executes it. Rollback is target-aware (native where supported, else redeploy
   the previous git release tag).

   **Resolve & verify deployment facts (do this during the build, not at the
   end):** run `detect_deploy.py` — it discovers what it can offline (e.g. k8s
   deployment name/namespace/image from manifests, the Fly app + URL) and marks
   the rest `missing`. For each `missing`/`REPLACE_ME` fact and each placeholder
   URL, either discover it (platform CLI) or **ask the user and document it** in
   the profile and in `BUILD.md` → *Deployment Facts* as it becomes known. Then
   run `deploy.py preflight --env staging` and `--env production` until green.
   The `preflight` job in `deploy.yml` enforces this — a merge cannot deploy
   with an unresolved spec, so resolve it before opening the Impl PR.
9. **Traceability is automatic.** `traceability.yml` regenerates the matrix and
   commits it beside `BUILD.md`. Never hand-edit `.specdev/traceability.md`.

## Context discipline (strict coordinator)

You are a coordinator, not a builder. Keep this thread small so a long feature
never fills the context window:

- **Hold only:** the spec, the component DAG from `components.md`, and a running
  status. Never hold full source files, full test output, or full code surveys.
- **Offload all heavy work to subagents** whose context is discarded — reverse-
  mapping (`spec-explorer`), component builds (`component-builder`), QA
  (`qa-verifier`), org-ADR verification (`adr-checker`). Only their short
  summaries return to you.
- **Checkpoint to disk after every phase.** Write decisions, the build log, and
  open items into `.specdev/BUILD.md` so the conversation can be safely
  summarized/compacted without losing the thread. Treat `BUILD.md` as the
  source of truth, not your context. In CI, `specdev-build.yml` pushes that
  file to `specdev/checkpoint/<unit>/<FEAT-###>` whatever the run's outcome —
  so a checkpoint you actually wrote survives a crash, a timeout and a
  circuit-break, and a re-dispatch resumes from it.
- **Read narrowly.** When you must look at a file, read the relevant section,
  not the whole file. If you need broad answers across many files, send an
  explorer subagent and keep its conclusion only.

## Parallel dispatch protocol

Turn `components.md` into a build schedule:

1. Read the **Depends on** column as a dependency DAG.
2. **Wave** = all components whose dependencies are already built. Launch the
   whole wave at once: multiple `component-builder` agents in a single message
   (independent calls run in parallel).
3. Wait for the wave, record each summary in `BUILD.md`, then **dispatch
   `qa-verifier` over the wave's integrated result and require green** before
   forming the next wave from newly-unblocked components. Repeat until done.
4. Each builder gets a minimal contract — its component row, its `REQ-###`
   sections, its acceptance tests — and nothing about sibling components.
5. If a builder reports a cross-component conflict, resolve the contract in
   `components.md` before launching the dependent wave.
6. A wave is **not done** until `qa-verifier` is green and `--check-gaps` passes
   for the REQs built in it. A red verdict goes back to a `component-builder`;
   never start the next wave on red. Run `qa-verifier` once more over the final
   integrated result as the pre-PR dry run.
7. **Commit the wave before forming the next one.** The moment a wave goes
   green, write its ledger row to `BUILD.md` and `git commit` — the wave, not
   the phase, is the unit of durability. A run killed mid-feature then resumes
   at the last GREEN wave rather than at the last phase boundary, and the
   commit is what the CI checkpoint push has to work with. Commit even when
   the feature is nowhere near done: an unfinished build that resumes is worth
   more than a tidy history.

Sequential or trivial single-component work can skip waves, but still goes
through `component-builder` so its detail stays out of this thread.

## Terminal state (when a build is actually over)

A build ends at a **terminal state**, not when you feel finished. CI asserts it
after your turn with `build_outcome.py verify`, in these words:

- the **implementation branch** `specdev/impl/<unit>/<FEAT-###>` carries commits
  beyond the base branch (the workflow pushes it — you must *commit*);
- a **prepared PR body** at `<unit>/.specdev/PR_BODY.md`, filled in;
- a **real checkpoint** at `<unit>/.specdev/BUILD.md`.

**The build never opens or merges a PR, in either mode.** A human opens it from
that branch: the build would otherwise need *Allow GitHub Actions to create and
approve pull requests*, a single switch that also lets Actions approve its way
past review, and a bot-authored PR gets none of the required checks in any
case. `poc` deploys from the branch directly rather than merging.

Ending a turn short of that is a failed build, not a partial success —
including ending cleanly, mid-wave, well inside the turn budget, with later
waves untouched. If a wave's QA is still in flight, the build is not over.

**If you cannot reach it** — blocked builder, denied tool, tripped breaker, a
contract only a human can settle — stop deliberately: write what is done, what
is outstanding and what blocked you into `BUILD.md`, commit it, and say so.
Never end a turn short of the terminal state without that record. The CI
checkpoint push makes stopping cheap, which is what makes stopping acceptable —
but it can only push what you committed.

## Guardrails

- Never start the build before the Spec PR is merged.
- Never weaken `post-dev-qa.yml` or staging QA — with automatic prod they are
  the only thing between a merge and production.
- Keep `FEAT-###` / `REQ-###` IDs stable for the life of the feature; the whole
  matrix is keyed on them.
- Never open a Spec or Implementation PR while `adr-checker` reports org-ADR
  violations. The fix loop (builder → QA → re-check) runs automatically; the
  PR itself is the only human gate. Never hand-edit
  `.specdev/adr/org-compliance.json` — only the checker writes it, and the
  `org-adr-check` CI gate will catch a forged or stale entry by content hash.
- Rollback is built in (profile-driven), but confirm `.specdev/deploy.profile.json`
  is correct — a wrong target or placeholder URL is the one thing that defeats
  auto-prod safety. Run `detect_deploy.py` and review it during `/specdev:init`.

## Helper commands

- `/specdev:init` — install the kit into the current repo.
- `/specdev:new-feature <name>` — start a `spec/<name>` branch + draft spec.
- `/specdev:build <FEAT-###>` — coordinator build loop: dependency-wave
  `component-builder` dispatch + per-wave `qa-verifier` + per-wave traceability.

## Tools (run locally to preview gate results)

```
python .specdev/tools/validate_spec.py --strict      # Gate 1 check
python .specdev/tools/gen_traceability.py            # write the matrix
python .specdev/tools/gen_traceability.py --check-gaps  # Gate 2 test-coverage check
python .specdev/tools/check_org_adrs.py              # org-ADR gate (inert until org.json is configured)
python .specdev/tools/adr.py lint                    # ADR quality gate (mode auto-detected)
python .specdev/tools/adr.py conflicts --file <path> --json  # structural + shortlist check for one ADR
```

In a monorepo add `--root <unit>` to each (the tools stay at the repo root),
and:

```
python .specdev/tools/units.py list                  # the repo's governed units
python .specdev/tools/units.py check                 # registry drift + validation
python .specdev/tools/check_org_adrs.py --unit <u>   # one unit's org-ADR gate
python .specdev/tools/adr.py lint --unit <u>         # one unit's ADR quality gate
python .specdev/tools/units.py migrate --unit <path> # single-root -> multi-unit
```
