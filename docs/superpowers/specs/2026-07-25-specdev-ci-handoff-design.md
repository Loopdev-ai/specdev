# Design: SpecDev CI handoff — unattended build in a GitHub runner

**Date:** 2026-07-25
**Status:** Approved (brainstorming) — ready for implementation planning

## Problem

Today the SpecDev pipeline splits into two halves. The **agent work** —
brainstorm → spec → architecture → the `/specdev:build` TDD loop (dispatching
`component-builder` / `qa-verifier` / `adr-checker` / `spec-explorer`
subagents) → opening PRs — runs interactively in Claude Code on a developer's
machine. The **deterministic gates** — every workflow under
`assets/workflows/` — already run in GitHub runners as Python validators and
deployers, with no Claude involved.

There is no way to *start* a feature interactively and then *hand it off* so
the build continues unattended in CI until it reaches a terminal state. This
design adds that handoff: a developer does the judgment-heavy part locally, and
a GitHub runner runs Claude Code headlessly to carry the build to completion.

## Goals

- Start a feature in local Claude Code, then hand off to a GitHub runner that
  runs the SpecDev build to a defined terminal state without a human at the
  keyboard.
- Two operating modes, chosen per feature: **prod** (human owns the contract)
  and **poc** (maximally hands-off after the initial brainstorm).
- Reuse the existing `specdev` skill and its subagents unchanged — the runner
  runs the same orchestration a local session does.
- Keep the runner's context bounded and the build resumable across job
  boundaries.
- Contain the blast radius of the hands-off **poc** mode.

## Non-goals (YAGNI)

- A bespoke Agent-SDK orchestrator. The `specdev` skill + `claude-code-action`
  already express the loop; a second orchestrator would have to be maintained
  in lockstep. (Approach C, rejected.)
- Runtime plugin installation in CI. Vendoring the skill/agents into the repo
  keeps CI hermetic and version-pinned. (Approach B, rejected.)
- On-demand `@claude` triggers, labels, or manual dispatch as the *primary*
  kickoff. The trigger is event-driven; `workflow_dispatch` exists only as a
  re-trigger/resume affordance.
- Removing or weakening any existing human gate in **prod** mode, or any QA
  gate in either mode.
- A fourth "fully autonomous" mode (spec → prod with zero human gates). Out of
  scope; **prod** keeps two human gates and **poc** is contained to an isolated
  environment.

## Key decisions (from brainstorming)

1. **Core mechanism — approach A: `claude-code-action` + vendored skill.**
   A new `specdev-build.yml` uses `anthropics/claude-code-action` to run Claude
   Code headlessly. `/specdev:init` additionally copies the `specdev` skill and
   the four subagents into the target repo's `.claude/skills/` and
   `.claude/agents/`, so the runner's coordinator has the same offload targets a
   local session has. Chosen over runtime plugin install (network/version drift)
   and a bespoke SDK runner (duplicate orchestrator).
2. **Two modes, chosen per feature, encoded by branch prefix.** Mode is not a
   repo-wide setting — two features can be in flight at once — so it rides on the
   branch. See *Modes* below.
3. **Event-driven kickoff.** Merging the Spec PR (prod) or pushing the
   `poc/<name>` branch (poc) fires the build. No button to press.
4. **Terminal states.** prod: open the Implementation PR and **stop** (a human
   merges → existing deploy chain). poc: **self-merge** the Implementation PR →
   deploy to an isolated POC environment.
5. **POC is contained.** poc deploys only to a dedicated `poc` environment;
   auto-promotion to production is structurally disabled for poc runs.
6. **Auth via `ANTHROPIC_API_KEY` — no second secret.** Stored as a repo/org
   secret, billed per token. The poc deploy is invoked *directly* by the build
   workflow (reusable `workflow_call`), which sidesteps the `GITHUB_TOKEN`
   recursion rule, so no PAT is required for the poc self-merge (see *Deploy
   integration*).
7. **Context stays bounded by the skill's existing discipline, plus
   resumability.** Subagent isolation + `BUILD.md` checkpoint are unchanged; the
   only CI addition is re-invoking the coordinator in resume-from-`BUILD.md`
   mode so a long build completes across checkpointed sessions.
8. **GitHub-hosted runner is the default**, with `.specdev/ci.json` carrying a
   `runner` knob so a team can flip to an uncapped self-hosted runner.

## Modes

Mode rides on the branch prefix and is recorded in a per-feature run manifest.

| Mode | Branches | Handoff (what fires the runner) | Terminal state |
|------|----------|---------------------------------|----------------|
| **prod** | `spec/<name>` → `feat/<name>` | You brainstorm + spec + architecture locally, open and **merge the Spec PR** | Runner builds → opens the Implementation PR → **stops**. A human merges → existing `deploy.yml` (staging → staging-QA gate → auto-prod). |
| **poc** | `poc/<name>` | You brainstorm locally, then **push the `poc/<name>` branch** | Runner writes the spec, does architecture, builds → opens the Implementation PR → **self-merges** (plain `GITHUB_TOKEN`) → the build calls the reusable `deploy-poc.yml` to deploy the isolated `poc` env. No prod. |

### `.specdev/run.json` — per-feature run manifest

The coordinator writes and commits this on the feature branch. It is the signal
every downstream workflow reads to know which mode a given merge belongs to.

```json
{
  "feat": "FEAT-023",
  "mode": "prod",             // prod | poc
  "poc_environment": "poc"    // present only when mode = poc
}
```

Because deploys are per-merge and sequential, a workflow reacting to a merge on
`main` reads the just-merged `run.json` to learn that merge's mode. `run.json`
is overwritten per feature; that is correct — each merge carries its own.

## Deliverables

| File | Role |
|------|------|
| `assets/workflows/specdev-build.yml` | **The runner.** `claude-code-action` invoking the coordinator in resume-from-`BUILD.md` mode. Triggers in *Trigger & control flow*. Copied to `.github/workflows/` by init. |
| `assets/workflows/deploy-poc.yml` | POC deploy as a **reusable** workflow (`on: workflow_call`); the build calls it after a completed poc self-merge to deploy the isolated `poc` env and run post-deploy-QA. No prod promotion. |
| `assets/specdev/run.json` | Seed run manifest (schema shape; mode defaults filled by the coordinator per feature). Becomes `.specdev/run.json`. |
| `assets/specdev/ci.json` | CI config: `{ "runner": "github-hosted" \| "self-hosted", "max_session_minutes": 300 }`. Becomes `.specdev/ci.json`. |
| `.claude/skills/specdev/SKILL.md` (vendored copy target) | The specdev skill, copied into the target repo by init so the headless runner loads it. |
| `.claude/agents/{component-builder,qa-verifier,adr-checker,spec-explorer}.md` (vendored copy targets) | The four subagents, copied by init so the coordinator can offload in CI. |
| `assets/specdev/tools/detect_deploy.py` (edit) | Learn/emit a `poc` environment in `deploy.profile.json` alongside `staging`/`production`. |
| `assets/workflows/deploy.yml` (edit) | Early guard: the standard staging→prod chain is skipped when the merged `run.json` mode is `poc`. |
| `commands/init.md` (edit) | Copy the vendored `.claude/` assets; document the `ANTHROPIC_API_KEY` secret and the `poc` environment setup. |
| `tests/test_specdev_ci.py` | pytest for `run.json` read/write + schema, `detect_deploy.py` poc-env handling, and the deploy-guard decision. |

`init` already copies the whole `assets/specdev/` tree and `assets/workflows/*.yml`.
This design adds a `.claude/` copy step (skill + agents) and the two new config
seeds, so most deliverables land through the existing copy mechanism.

## Trigger & control flow — `specdev-build.yml`

```yaml
on:
  pull_request:
    types: [closed]
    branches: [main]          # prod: Spec PR merged from a spec/** head
  push:
    branches: ['poc/**']      # poc: branch pushed
  workflow_dispatch:
    inputs:
      feat: { required: true, type: string }
      mode: { required: true, type: string }   # re-trigger / resume-after-timeout
```

Path selection inside the workflow:

- **prod path** — `github.event.pull_request.merged == true` and the head branch
  matches `spec/**`. Check out `feat/<name>` (create from the merged spec if
  absent), then invoke the coordinator: *"Build FEAT-### in prod mode; open the
  Implementation PR; do not merge."* The runner stops at the open PR.
- **poc path** — a push to `poc/**`. Invoke the coordinator: *"Write the spec,
  do architecture, and build FEAT-### in poc mode; open the Implementation PR
  and merge it."* When the build reaches its terminal state, the workflow calls
  the reusable `deploy-poc.yml` (see *Deploy integration*).
- **Common invocation** — both paths call Claude Code with: *"Resume the SpecDev
  build from `.specdev/BUILD.md`; if incomplete, continue the wave loop until the
  terminal state for this mode."*

### Resumability

If a run reaches the job's `timeout-minutes` with `BUILD.md` still listing open
items, a final always()-step re-dispatches the workflow
(`gh workflow run specdev-build.yml -f feat=FEAT-### -f mode=<mode>`). Each
invocation rehydrates durable state from `BUILD.md` (not replayed conversation),
so a large build completes across several short, checkpointed sessions. This is
also the retry story when a run dies. On a self-hosted runner
(`ci.json.runner == "self-hosted"`, no 6h cap) the resume loop is a no-op safety
net rather than the norm.

## Context management

Unchanged from the skill's coordinator discipline
(`skills/specdev/SKILL.md` → *Context discipline*):

- The coordinator holds only the spec, the component DAG from `components.md`,
  and running status — never full source, full test output, or code surveys.
- Every component build, QA pass, ADR check, and code survey is dispatched to a
  **vendored** subagent whose own context is discarded; only its short summary
  returns. This is why approach A must copy `.claude/agents/*.md` into the repo —
  without those definitions the runner's coordinator has nowhere to offload and
  would exhaust its window on the first component.
- `BUILD.md` is the durable source of truth, written after every phase, so the
  session can be compacted (or the job can end) without losing the thread.
- Auto-compaction remains a backstop for coordinator-thread growth, identical to
  local behavior.

The **only** CI-specific addition is resume-from-`BUILD.md` invocation (above).

## Deploy integration

POC self-merge and prod merge both land on `main`, and `deploy.yml` triggers on
push to `main`. Therefore:

- **`deploy.yml` guard.** An early job reads the merged `.specdev/run.json`; if
  `mode == "poc"`, the workflow exits before the staging→prod chain. The
  battle-tested prod chain is otherwise untouched — no weakening of preflight,
  staging QA, or rollback.
- **`deploy-poc.yml`.** A **reusable** workflow (`on: workflow_call`) that
  deploys the `poc` environment via `deploy.py deploy --env poc`, runs
  `post-deploy-qa.yml` against it, and stops. There is no production job, so
  auto-promotion cannot occur for poc. It is invoked by `specdev-build.yml`
  (`uses: ./.github/workflows/deploy-poc.yml`) once the poc build reaches its
  terminal state — **not** by a push-to-`main` event.
- **`detect_deploy.py`** is extended to resolve a `poc` environment in
  `deploy.profile.json` (its own target/URL/health facts), so `deploy.py` and
  the `preflight` gate treat `poc` as a first-class environment.

### Why the poc deploy is invoked directly (no PAT)

A merge performed by the default `GITHUB_TOKEN` does **not** trigger other
workflows — GitHub's recursion guard. So a poc self-merge's push to `main` would
land the code but never fire a push-triggered deploy. Rather than merge with a
PAT to defeat the guard, the poc deploy is a reusable workflow the build **calls
directly** in the same run (`workflow_call`), where the recursion rule does not
apply. This removes the second secret and, with it, the silent-failure mode
(a missing/expired PAT merging but not deploying). The deploy is triggered only
on the **final** checkpointed invocation — when `BUILD.md` shows the build
complete — not on intermediate resume runs.

*Alternative (documented, not the default):* a team that wants poc deploys to be
independently event-triggered can instead store a `SPECDEV_PAT`
(scopes `contents:write`, `pull_requests:write`), self-merge with it, and make
`deploy-poc.yml` a push-triggered workflow guarded to `mode == "poc"`. This
mirrors prod's event model at the cost of a rotating secret.

## Auth, secrets, guardrails

- **Secrets:** `ANTHROPIC_API_KEY` (claude-code-action, per-token billing) is
  the only required secret. `init` lists it in the post-install checklist. (A
  `SPECDEV_PAT` is needed only for the optional event-triggered poc-deploy
  alternative above.)
- **Cost bounding:** `claude-code-action` runs with a pinned model and
  `--allowedTools` scoped to what the build needs. The coordinator discipline
  keeps the main thread's token use low; cost is dominated by bursty, discarded
  subagents rather than an ever-growing coordinator context.
- **Preserved guardrails:** `post-dev-qa.yml` and staging QA are never weakened;
  prod keeps two human gates (Spec PR + Implementation PR); poc's blast radius is
  the isolated `poc` environment with prod promotion structurally disabled;
  `org-adr-check` and the other required checks still gate every PR.

## Testing

`tests/test_specdev_ci.py` (pytest, run from repo root):

- **`run.json`**: schema validation; read/write round-trip; `poc_environment`
  present iff `mode == "poc"`.
- **`detect_deploy.py`**: a `poc` environment is resolved/emitted alongside
  `staging`/`production`; its facts are independent.
- **Deploy-guard decision**: a helper that, given a `run.json`, returns
  whether the prod chain should run — poc merge → skip; prod merge →
  proceed. (Tested at the Python level so the YAML `if:` is a thin wrapper over
  verified logic.)

Workflow YAML is lint-checked. The end-to-end agentic loop (headless
`claude-code-action` driving a real build) is validated by a **documented manual
dry run** on a throwaway repo, not automated in CI — running a paid, long agent
loop inside the test suite is out of scope.

## Delivery / consistency notes

- The `.claude/` vendored copy is a new step in `init`; everything else lands
  through the existing `assets/specdev/` and `assets/workflows/` copy mechanism.
- The seed `run.json` and `ci.json` ship valid so a fresh repo passes from day
  one; poc-specific behavior is inert until a `poc/<name>` branch is pushed.
- `deploy.yml` changes are additive (an early guard job); the existing jobs and
  their required-status-check semantics are unchanged for prod merges.
