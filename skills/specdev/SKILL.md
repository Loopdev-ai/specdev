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
4. **Architecture.** Record decisions as `.specdev/adr/ADR-###.md` with
   `Relates to: REQ-###`. Skip only if the change fits existing architecture.
5. **Open the Spec PR.** `spec-validate.yml` runs `validate_spec.py` as a
   required check. Get it green, get human approval, merge. This locks the
   contract — do not renumber REQs afterward.
6. **Build on `feat/<feature>`** off the merged spec — as a coordinator, not a
   builder (see *Context discipline* and *Parallel dispatch protocol* below):
   - Scaffold to match `components.md`; log files in `.specdev/BUILD.md`.
   - Build components in dependency waves via the **`component-builder`** agent
     (parallel within a wave). You do not write component code yourself.
   - Each builder enforces TDD and `Refs: REQ-###` trailers; you only hand it
     its component row + spec section + acceptance criteria, then record its
     summary in `BUILD.md`.
   - After integration, dispatch the **`qa-verifier`** agent for a local Gate-2
     dry run before opening the PR.
7. **Open the Implementation PR (Gate 2).** `post-dev-qa.yml` runs tests,
   security scan, coverage, and `gen_traceability.py --check-gaps` (fails if any
   REQ has no test). These are required status checks. Get review + green, merge.
8. **Merge is the deploy trigger.** `deploy.yml` builds, tags an immutable
   release, deploys staging, then runs `post-deploy-qa.yml` against staging.
   **Staging QA is the promotion gate** (prod is fully automatic, no human
   gate): pass → auto-promote to prod → prod QA; fail → rollback + incident.
   Deploy/rollback/health are **determined by the profile**, not hard-coded:
   `detect_deploy.py` writes `.specdev/deploy.profile.json`, and `deploy.py`
   executes it. Rollback is target-aware (native where supported, else redeploy
   the previous git release tag). If the profile is `manual` or has `REPLACE_ME`
   params, resolve it before relying on auto-prod.
9. **Traceability is automatic.** `traceability.yml` regenerates the matrix and
   commits it beside `BUILD.md`. Never hand-edit `.specdev/traceability.md`.

## Context discipline (strict coordinator)

You are a coordinator, not a builder. Keep this thread small so a long feature
never fills the context window:

- **Hold only:** the spec, the component DAG from `components.md`, and a running
  status. Never hold full source files, full test output, or full code surveys.
- **Offload all heavy work to subagents** whose context is discarded — reverse-
  mapping (`spec-explorer`), component builds (`component-builder`), QA
  (`qa-verifier`). Only their short summaries return to you.
- **Checkpoint to disk after every phase.** Write decisions, the build log, and
  open items into `.specdev/BUILD.md` so the conversation can be safely
  summarized/compacted without losing the thread. Treat `BUILD.md` as the
  source of truth, not your context.
- **Read narrowly.** When you must look at a file, read the relevant section,
  not the whole file. If you need broad answers across many files, send an
  explorer subagent and keep its conclusion only.

## Parallel dispatch protocol

Turn `components.md` into a build schedule:

1. Read the **Depends on** column as a dependency DAG.
2. **Wave** = all components whose dependencies are already built. Launch the
   whole wave at once: multiple `component-builder` agents in a single message
   (independent calls run in parallel).
3. Wait for the wave, record each summary in `BUILD.md`, then form the next wave
   from newly-unblocked components. Repeat until done.
4. Each builder gets a minimal contract — its component row, its `REQ-###`
   sections, its acceptance tests — and nothing about sibling components.
5. If a builder reports a cross-component conflict, resolve the contract in
   `components.md` before launching the dependent wave.
6. After the final wave, run `qa-verifier` once over the integrated result.

Sequential or trivial single-component work can skip waves, but still goes
through `component-builder` so its detail stays out of this thread.

## Guardrails

- Never start the build before the Spec PR is merged.
- Never weaken `post-dev-qa.yml` or staging QA — with automatic prod they are
  the only thing between a merge and production.
- Keep `FEAT-###` / `REQ-###` IDs stable for the life of the feature; the whole
  matrix is keyed on them.
- Rollback is built in (profile-driven), but confirm `.specdev/deploy.profile.json`
  is correct — a wrong target or placeholder URL is the one thing that defeats
  auto-prod safety. Run `detect_deploy.py` and review it during `/specdev:init`.

## Helper commands

- `/specdev:init` — install the kit into the current repo.
- `/specdev:new-feature <name>` — start a `spec/<name>` branch + draft spec.

## Tools (run locally to preview gate results)

```
python .specdev/tools/validate_spec.py --strict      # Gate 1 check
python .specdev/tools/gen_traceability.py            # write the matrix
python .specdev/tools/gen_traceability.py --check-gaps  # Gate 2 test-coverage check
```
