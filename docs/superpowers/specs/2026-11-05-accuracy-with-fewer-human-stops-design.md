# Design: Higher accuracy with fewer human stops

**Date:** 2026-11-05
**Status:** Phases 0 and 1 implemented; Phase 1b (lessons from Spec Kit, §8)
proposed; Phases 2–5 wait for Phase 0 data. For review (Alex, Chad)
**Origin:** "Evaluate specdev, how can we make it more automated so there is
higher accuracy but less human in the loop? Perhaps there have been something
changes to Claude in the past few months." Grounded against
`Loopdev-ai/specdev@d501a79` (2026-09-30) and the Claude Code documentation as
of v2.1.311 (2026-10-26). The features this design relies on were re-checked
against the published docs on 2026-11-06 (changelog head: v2.1.318,
2026-11-04). See *Platform facts relied on* for each one.

§8 adds what Spec Kit does that helps autonomy. It was compared on
2026-11-06 against Spec Kit's command templates (`templates/commands/*.md`
on `github/spec-kit@main`, after its 1.0 release). Its docs site was not
reachable from the session, so anything documented only there was not seen.

## Problem

Two goals pull in different directions, and the design has to serve both:
**accuracy** (fewer defects and intent gaps reach the human at PR review) and
**autonomy** (fewer points where a build stops and waits for a person).

The pre-PR review loop (2026-09-29) was the right response to "Copilot keeps
finding lots of bugs." It also exposed where accuracy still leaks. The loop is
expensive (up to 10 passes) because it is compensating for problems upstream
of it:

1. **The only tests that gate a REQ come from the agent that implemented it.**
   `component-builder` writes the tests and then the code. `qa-verifier` and
   `--check-gaps` confirm that those tests pass and mention the REQ id. They do
   not confirm that the tests assert the Acceptance line. `intent-reviewer`
   catches that afterwards, by reading the code. A test written
   independently, before the code exists, would rule it out by construction.
2. **Defects pile up across waves before anyone reads the code.** Review
   happens once, over `base...HEAD`, after the final wave. A wrong
   interpretation in wave 1 has been built on by waves 2–6 before the first
   reviewer sees it. Each fix then lands on code that depends on it, which is
   where the later passes come from.
3. **Findings are not verified before builders act on them.** A blocking
   finding needs a `file:line` and a scenario written as prose. Whether it is
   real is first tested when a builder in review-fix mode tries to reproduce
   it. A false positive costs a builder dispatch, a QA run and a dismissal. A
   real one is reproduced twice, once in the reviewer's head and once by the
   builder.
4. **Every pass re-reviews the whole diff from scratch.** That is right for
   independence, but it means pass 7 rereads everything to confirm two
   one-line fixes. The cost per pass does not fall as the open set shrinks.
5. **The loop's correctness rests on transcription.** The coordinator copies
   each reviewer's closing JSON block into a file and hands it to
   `review_ledger.py record`. A reviewer that ends without a parseable block,
   or a coordinator that paraphrases one, is caught only when `record` fails,
   if at all. The ledger is mechanical; the step that feeds it is not.
6. **Every agent runs at the session's model and effort.** All seven agents
   declare `model: inherit` and no effort level. The reviewers, whose whole job
   is to see what the builder missed, think exactly as hard as the builder
   did.

### Where the build stops for a person today

| # | Stop | Where | Necessary? |
|---|---|---|---|
| H1 | Brainstorm Q&A | `new-feature` step 4 | **Yes.** This is where intent comes from. |
| H2 | `needs_human` spec findings, one batch **per pass** | spec loop | Partly. The questions are real, but up to 10 interruptions is not. |
| H3 | ADR decision interviews | `adr` skill | Yes for real decisions. |
| H4 | Spec PR approval and merge | Gate 1 | **Yes.** This locks the contract. |
| H5 | Missing deployment facts ("ask the user and document it") | build, step 8 | No. These are per-repo facts, asked again on every build that finds them missing. |
| H6 | A blocked builder or a contract only a human can settle | build | Sometimes. Today this is also how an ambiguous spec surfaces. |
| H7 | Re-dispatch after the 3 continuation attempts are used up | CI | No. It is a symptom of the coordinator ending its turn early (U17). |
| H8 | Open the Implementation PR, review, merge | Gate 2 | **Yes.** This is deliberate (see `specdev-build.yml` header). |
| H9 | Unresolved findings handed off at the cap | Gate 2 | Should be rare. Today this is the accuracy leak surfacing. |
| H10 | Copilot findings on the open PR, fixed by hand | post-PR | No. This is the original complaint. |
| H11 | `components.md` is empty when the build starts ("stop and ask the user to define components first") | `/specdev:build` precondition | No. Nothing fills or checks it before the Spec PR for a new product, so in CI this is a stop nobody can answer. Found in the Spec Kit comparison (§8.1). |

H1, H3, H4 and H8 are the human gates the pipeline is built around, and this
design keeps them. The target is the rest: remove H5, H7 and H11, fold H2
into H4, shorten H1, and make H6, H9 and H10 rarer by fixing causes 1–6
above.

## Constraints

Everything the 2026-09-29 design held still holds: coordinator context
discipline, reviewer independence (a reviewer never wrote the artifact and
never fixes it), the build never opens or merges a PR, poc has no PR, and no
retroactive validator rules against merged specs. Two more:

- **Mechanical over prompt.** This is repeated because it decides most of the
  choices below. Every new behaviour lands as a tool, a hook or a gate where
  that is possible, and as prompt text only where it is not.
- **Measure before tuning.** The repo has no data on how many passes a build
  takes, which kinds of finding recur, or what still escapes to Copilot.
  Without that, "the loop is expensive" and "accuracy improved" cannot be
  checked. Phase 0 exists so later phases can be judged.

## Platform facts relied on

Each of these was checked against the Claude Code docs (changelog head v2.1.318).
**Must verify** marks a fact this design depends on that the docs leave unclear.
Each one becomes a test or a spike before its phase ships.

| Fact | Used by | Note |
|---|---|---|
| Subagent frontmatter `effort: low\|medium\|high\|xhigh\|max` | §2 | `CLAUDE_CODE_SUBAGENT_EFFORT` (2.1.316) sets a default for agents that name none. Since 2.1.317, a subagent on a different model uses that model's saved or default effort, not the session's. |
| Subagent frontmatter `isolation: worktree` | §4 | The worktree is branched from the **default branch, not the parent's HEAD**. The reviewer must `git checkout --detach <reviewed sha>` inside it first. Commands that resolve to the main checkout fail there, which is the containment we want. The worktree is removed automatically when it has no changes. 2.1.311 fixed a worktree being removed while a background subagent was still using it. |
| `Stop` / `SubagentStop` hooks can return `{"decision":"block","reason":…}` (or exit 2) to keep the agent going | §1, §3 | `SubagentStop` input carries `agent_type`, `agent_id` and `last_assistant_message`. 2.1.311 fixed `SubagentStop` firing about twice a minute instead of once. **Must verify:** the minimum version CI pins. |
| Hooks passed with `--settings` run in a non-bare `-p` session | §1, §3 | Already relied on: the circuit breaker is wired this way in `specdev-build.yml`. |
| Plugin subagents **ignore** frontmatter `hooks`, `permissionMode` and `mcpServers` | §1 | So reviewer hooks go in settings (CI) and in the plugin's `hooks/hooks.json` (interactive), never in agent frontmatter. The vendored `.claude/agents/` copies are project agents, so they would honour frontmatter, but the plugin copies would not. One place keeps the two paths identical. |
| `claude -p --json-schema` returns `structured_output` | not used | It applies to the top-level run, not to a subagent's reply. The docs list no structured-output field for subagent frontmatter. The `SubagentStop` validator in §1 does that job. |

## Approach

- **A. Tune the loop.** Lower the cap, tighten reviewer prompts, add a model
  override. Cheap. It treats the cost and leaves causes 1–4 alone.
- **B. Move accuracy upstream, make the loop's plumbing mechanical, and
  remove the stops that are not decisions (chosen).** Six changes, rolled out
  in phases that each pay for themselves and are measured by Phase 0.
- **C. Replace the loop with a post-PR reviewer** such as `@claude review` or
  Copilot, with an auto-fix bot. Rejected for the same reason as last time:
  the PR must be fully formed before it exists. It also adds a stop (H10)
  instead of removing one.

## Design

### 0. Measure: `review_ledger.py stats` and an escape count

Nothing else ships until this does, because it is the yardstick for
everything else.

- `review_ledger.py --root <unit> stats [--phase P] [--json]` reports, from
  the ledger: passes to clean or to the cap, findings per pass split by `kind`
  and `severity`, dismissal rate, `not-reproducible` rate, and findings
  re-raised after dismissal. This is pure read-out; the ledger already holds
  all of it.
- `build_outcome.py` writes the `stats --json` summary into the outcome
  record (`verify.json`) it already uploads. That gives one row per CI
  build, but in a per-run artifact that expires.
- **Aggregation.** Neither of those is a baseline on its own: the ledger
  holds one feature at a time, and the artifacts expire. `loop_stats.py`
  reads every committed version of every `<unit>/.specdev/review.json` from
  git history, keeps the newest per (unit, FEAT), joins the escape counts
  from the retained `specdev-escapes-pr-*` artifacts on the PR's head branch,
  and carries counts forward from the previous snapshot once an artifact has
  expired. `specdev-loop-stats.yml` runs it weekly and commits the snapshot
  to the `specdev/stats` branch with git plumbing, so the default branch's
  checkout is never touched. That branch's history is the trend.
- **Escapes.** A small, optional, read-only workflow, `specdev-escapes.yml`,
  runs on `pull_request_review` for PRs from `specdev/impl/**`. It counts
  review comments by bot authors (Copilot, `@claude review`) and by humans,
  and appends the counts to the PR's step summary. It never posts. This is the
  number the original complaint was about. It is a measurement, not a gate.

**Success criteria for the whole design** (reviewed after each phase):
median passes to clean, at most one human interruption (H2 + H5 + H6) per
feature outside the four gates, and escaped findings per Implementation PR
trending down. No target numbers are set until Phase 0 has a baseline.

### 1. Reviewer output is validated by a hook, not transcribed (cause 5)

A new `.specdev/tools/review_hook.py` runs as a `SubagentStop` hook:

- It acts only when `agent_type` is `spec-reviewer`, `code-reviewer` or
  `intent-reviewer`. Every other agent passes through untouched.
- It extracts the last fenced JSON block from `last_assistant_message` and
  validates it against the finding schema that `review_ledger.py` already
  enforces. The schema moves into one shared function so the hook and
  `record` cannot drift. A blocking finding must have `where` and
  `scenario`; from §4 on, a `code-reviewer` blocking finding must also have
  `repro`.
- **Invalid:** it returns `{"decision":"block","reason":"<what is wrong>"}`,
  and the reviewer keeps going and fixes its own report. It blocks at most
  twice per `agent_id` (counted in a state file under `$RUNNER_TEMP`, or the
  session temp directory when interactive), then lets the agent stop. The
  coordinator then sees a missing file and re-dispatches. A hook must never
  loop forever.
- **Valid:** it writes the block to
  `<hook dir>/<phase>/<agent>--<agent_id>.json` and lets the agent stop. The
  hook directory is `specdev-review/` inside the git common directory
  (`SPECDEV_REVIEW_DIR` overrides it). Both the hook and the coordinator can
  find it, and it is never tracked. Nothing is retyped.

`review_ledger.py record --from-hook [--hook-dir D]` records exactly one saved
report per reviewer of the phase. It refuses when a reviewer's report is
missing (half a review) or duplicated (two dispatches), and moves the
recorded files to `recorded/` so a later pass cannot reuse them.
`--findings-json` remains for installs without the hook. Wiring: the CI
breaker settings JSON gains a `SubagentStop` entry, and the plugin ships the
same entry in `hooks/hooks.json` for interactive sessions. The plugin's copy
is called with `--source plugin` and does nothing when `SPECDEV_CI_HOOKS` is
set, so CI never runs it twice.

### 2. Reviewers think harder than builders (cause 6)

- `spec-reviewer`, `code-reviewer` and `intent-reviewer` declare
  `effort: xhigh`. `component-builder` and `qa-verifier` declare
  `effort: high`. `spec-explorer` and `adr-checker` stay at the default. This
  is a frontmatter change, and the existing agent-file tests are extended to
  assert it.
- `ci.json` gains an optional `reviewer_model` (default `inherit`).
  `/specdev:init` writes it into the vendored reviewers' `model:` when set.
  This lets an adopter try **model diversity**, a reviewer that is not the
  same model as the builder, as a measured experiment rather than a default.
  It goes through `--require-explicit`, because a model id is the kind of
  auditable choice the README already calls out.

Higher effort raises the cost of each pass. The bet is that it lowers the
number of passes. Phase 0 is what tells us whether it did.

### 3. A Stop hook holds the coordinator to the terminal state (H7)

U17 made continuation mechanical *between* attempts: up to three
`claude-code-action` invocations, each a cold start that re-reads `BUILD.md`.
A `Stop` hook can do the same thing *within* an attempt, before the turn ends:

- `build_outcome.py stop-hook` (CI only, enabled by the env var the workflow
  already sets) runs `verify` against the working tree. If the terminal state
  is not reached **and** the last stop-block produced a new commit, it
  returns `decision: block` with `verify`'s problems as the `reason`. That
  reason says what is missing, for example "impl review loop: pass 4 has 2
  open blocking findings", which is better guidance than the generic
  `CONTINUE_PROMPT`.
- **It checks the working tree, not the pushed branch.** `verify` reads the
  pushed implementation branch, which the workflow only pushes after the
  turn ends. So the hook uses `local_terminal_problems`, the same clauses
  checked against `HEAD`.
- **Safety:** it never blocks twice without a new commit in between, the same
  rule `continue-gate` uses so a stuck agent cannot spin. It never blocks when
  `BUILD.md` records a deliberate stop, meaning its new `## Stopped` section
  is filled in. It blocks at
  most `max_stop_blocks` times (new `ci.json` key, default 5). The circuit
  breaker's `PreToolUse` hook still bounds spend; a tripped breaker always
  wins.
- The cross-attempt continuation stays as the backstop for crashes,
  `--max-turns` exhaustion and timeouts. Expect attempts 2 and 3 to become
  rare, and expect a re-dispatch by a human (H7) to become rarer still.

This is not wired into interactive sessions. There, a person ending the turn
is a decision, not a failure.

### 4. Findings arrive with a failing test (causes 3, 1)

`code-reviewer` gains `isolation: worktree`, and its contract changes from
"never modify files" to "**never modify the main checkout**":

1. In its worktree, `git checkout --detach <reviewed sha>`. The worktree
   starts from the default branch, so without this step it would review the
   wrong code.
2. For each blocking `bug` or `security` finding, write the smallest test
   that reproduces it, run it, and watch it fail.
3. Report it as `"repro": {"path": "<test file>", "cmd": "<command>",
   "output": "<the failing assertion, ≤20 lines>"}` with the test's source
   inline (`"repro.source"`, capped at 200 lines). Because the worktree is
   discarded, the test has to travel in the report.
4. A blocking finding it cannot reproduce is downgraded to `minor` with
   `"suspected": true`. Every blocking finding is now one that was seen
   failing.

Review-fix mode starts from the reviewer's test. The builder adds
`repro.source` to the component's suite, confirms it fails on the branch, and
fixes the code. `not-reproducible` remains, and now means "the reviewer's test
does not fail here", which is rare and has a clear cause.

`intent-reviewer` gets the same treatment for `test` findings ("the linked
test does not assert the Acceptance line"). Its repro is a test that asserts
the Acceptance line and fails. `spec-reviewer` is unchanged: a spec has
nothing to execute.

This also removes a step from the loop. Reviewers no longer leave side
effects in the main tree, so "if `record` reports uncommitted changes, discard
them" goes away. The ledger's dirty-tree refusal stays as a backstop.

### 5. Acceptance tests are written before the code, by someone else (cause 1)

A new agent, **`acceptance-author`**, has `tools: Read, Write, Edit, Grep,
Glob, Bash` and `effort: xhigh`. It runs as **wave 0** of every `spec_bar:
full` build, before any `component-builder`:

- **Input:** the spec (Original Request, REQs and Acceptance lines),
  `components.md` (its interfaces and entry points), and for extend mode the
  `spec-explorer` map.
- **Output:** one black-box acceptance test per REQ, written against the
  entry point `components.md` declares (route, CLI, public function), never
  against internals. Each test is named with its REQ id and committed red,
  with `Refs: REQ-###`.
- `.specdev/tools/acceptance_lock.py` (new) records each acceptance test
  file's hash in `<unit>/.specdev/acceptance.lock`.
  `build_outcome.verify()` (prod) and `qa-verifier` fail when a locked file
  changed without a matching `acceptance_lock.py amend --file F --reason R`
  entry. **Builders cannot weaken the tests that gate them.** If an
  acceptance test disagrees with the agreed interface, the builder reports
  `acceptance-contract-mismatch`, and `acceptance-author` (never the builder)
  amends it. The amendment and its reason go in the lock file, and from there
  into `PR_BODY.md`.
- The build is not done until every locked acceptance test passes. That
  becomes a terminal-state clause and a `qa-verifier` check, alongside
  `--check-gaps`.

`intent-reviewer` check 2 ("is the REQ asserted?") becomes mostly mechanical:
the acceptance test exists, is locked, and passes. The reviewer's attention
moves to checks 1, 4 and 5 (end-to-end wiring, the Original Request, scope),
where reading is still needed.

`components.md` must name an entry point per REQ for this to work. That is
already the intent of its *Interface* column. `spec-reviewer` check 6 is
extended to flag a REQ whose owning component has no testable interface. This
is a reviewer rule, not a validator rule, so merged specs are unaffected.

`spec_bar: charter` (poc) is unchanged. There are no REQs to author against.

### 6. Review moves partly upstream, and later passes are scoped (causes 2, 4)

- **Per-wave intent check.** After each wave's `qa-verifier` goes green, one
  fresh `intent-reviewer` runs over that wave's diff in a new **wave mode**:
  only checks 1–3, only the wave's REQs. Its findings go back to that wave's
  builders before the next wave starts, the same way red QA already does. It
  is recorded in the ledger as phase `wave` (`record --phase wave --wave N`)
  and is **not** counted against `max_review_iterations`. A wrong
  interpretation is then caught when one wave has built on it, not six.
  `code-reviewer` stays pre-PR only. Bugs are cheap to fix late; wrong intent
  is not.
- **Scoped later passes.** From pass 2 on, the pre-PR reviewers get
  `--scope delta`: the diff since the last reviewed commit, plus the regions
  around the previous pass's findings. A delta pass that comes back clean is
  followed by **one** `--scope full` pass, and only a clean *full* pass ends
  the loop. The ledger records `scope` per pass, and `check` rule 3 changes
  from "last pass clean" to "last pass clean **and** full". The invariant
  "every line in the PR was reviewed" holds. What changes is that the
  confirm-the-fix passes in between no longer reread everything.

### 7. Fewer interruptions in the spec phase and at build time (H2, H5)

- **Front-load questions (H2).** In `/specdev:new-feature`, after the first
  draft and *before* the review loop, the coordinator dispatches one
  `spec-reviewer` in a new **interrogate mode**, which reports only
  `needs_human` findings. Their questions go into the brainstorm as a single
  batch. The loop that follows then rarely produces `needs_human` findings.
- **Assume and record (H2).** In the loop, a `needs_human` finding of
  `severity: minor`, or one where the reviewer offers a
  `"default": "<reading>"`, is no longer a question. The coordinator applies
  the default, dismisses the finding with reason `assumed: <reading>`, and
  lists it under a new `**Assumed:**` block in `## Open Questions`. That
  section is already outside the spec hash. The human reads it at the Spec PR
  review (H4), a stop that happens anyway. Only a blocking `needs_human`
  finding with no defensible default interrupts mid-loop, and those are
  batched into one question per pass as today.
- **Deployment facts move to init (H5).** `/specdev:init` already runs
  `detect_deploy.py`. It now also asks for every `missing`/`REPLACE_ME` fact
  once, writes the answers to `deploy.profile.json`, and runs `deploy.py
  preflight`. During a build, a missing fact no longer interrupts. It is
  recorded in `PR_BODY.md` under *Deployment facts unresolved*, and the
  existing `preflight` job in `deploy.yml` still blocks the merge. In CI
  nobody can be asked anyway, so this makes interactive builds behave like CI
  ones.

### 8. Lessons from Spec Kit (H1, H2, H6, H9, H11)

Spec Kit's core commands are `constitution`, `specify`, `clarify`, `plan`,
`tasks`, `analyze`, `checklist`, `implement` and, since 1.0, `converge`. Most
of what it does, specdev already does more strictly, which is what *Don't
copy* below is about. Six of its habits address stops this design has not
yet removed. §8.1–8.4 are small and mechanical (Phase 1b). §8.5–8.6 change
how a build runs and need a decision first (Phase 5).

Three of its patterns also back phases already planned:

- **`converge`** (implement, compare the code with the spec, append the gaps
  as tasks, repeat until "Converged") is the shape of our impl loop. Its gap
  types `missing`, `partial`, `contradicts` and `unrequested` match
  `intent-reviewer`'s checks.
- **The bug extension** (assess, fix, then verify the original symptom, with
  a verdict of `verified`, `partial` or `failed`) is §4's reproduce-first rule.
- **`plan`'s `quickstart.md`** (runnable end-to-end scenarios) and its
  per-story interface contracts are §5's acceptance tests in another form.

#### 8.1 The component map is settled before the Spec PR (H11, H6)

Spec Kit's `analyze` reports every requirement with no task and every task
with no requirement. In specdev, nothing checks `components.md` before the
build, and for a new product nothing fills it in. `/specdev:build` then stops
on an empty table, which in CI nobody can answer.

- `/specdev:new-feature` fills `components.md` after the REQs: proposed from
  the spec for a new product, from the `spec-explorer` map when extending.
  The Spec PR carries it, so the contract the human approves includes how
  the work is divided.
- A new check, `validate_spec.py --components`, requires:
  - every REQ to appear in some component's **Owns (REQs)**, and every REQ
    listed there to exist in the spec;
  - every **Depends on** entry to name a component in the table, or to be
    marked external (the template already allows
    `<component / external>`; the check needs the marking to tell an
    external system from a typo);
  - the dependencies between components to have no cycles;
  - every component row to have a non-empty, non-placeholder
    **Contract / Interface**: the entry point its REQs are reached through,
    which §5's acceptance tests need.
- `spec-validate.yml` runs it **only for `spec/**` heads**, the same rule as
  the spec review-ledger check. Already-merged specs without a
  `components.md` therefore never fail.
- The build precondition runs the same check. If the table is still empty
  (a spec merged before this change), the coordinator proposes the
  components from the spec, records them in `BUILD.md` and lists them in
  `PR_BODY.md` as a deviation for the reviewer, rather than stopping.
- `spec-reviewer` check 6 (ownership) stays as the judgment layer. The tool
  makes its mechanical half certain.

#### 8.2 Up-front questions are capped, ranked and come with answers (H1, H2)

Spec Kit's `clarify` scans ten fixed ambiguity categories and marks each
Clear, Partial or Missing. It never asks more than 5 questions, and each
comes with a recommended answer the user can accept by saying "yes". Its
`specify` allows at most 3 open questions and ranks them by impact: scope,
then security and privacy, then user experience, then technical detail.
§7's interrogate mode has none of this: no cap, no categories, no ranking,
and the defaults stay inside the findings JSON instead of reaching the user.

- Interrogate mode walks a fixed category list, adapted from Spec Kit's:
  scope and behaviour; data; interaction flow; non-functional qualities;
  integrations; edge cases and failures; constraints and trade-offs;
  terminology; what "done" looks like; leftover placeholders. Its report
  adds a `coverage` object with each category marked `clear`, `partial` or
  `missing`.
- **At most 5 questions**, ranked scope > security/privacy > UX > technical.
  Everything below the cut becomes an assumption (§7) instead of a question.
- **Every question carries a `default`**, shown to the user as
  *Recommended:*. Replying "yes" accepts all of them. Spec Kit asks one
  question at a time. Specdev keeps one batch, because one stop beats five.
- Answers are recorded as `question → answer` under the spec's
  `## Original Request` → *Clarified while brainstorming*, the format the
  template already uses.
- `review_ledger.py stats` gains the coverage counts, so Phase 0 shows which
  categories keep coming back as questions.

#### 8.3 A list of things never worth asking (H2)

Spec Kit's `specify` tells the agent not to ask about common defaults: data
retention, standard performance targets, ordinary error handling, standard
authentication. It makes an informed guess and records it as an assumption.

- `spec-reviewer` gets a **never-ask list**: data retention, standard
  performance targets, ordinary error messages, the stack's usual
  authentication, logging, pagination defaults. On these it raises an
  assumption with a `default`, never a `needs_human` question. It still
  raises a question when the Original Request, an accepted ADR or an
  applicable org ADR speaks to the topic and the spec contradicts it.
- The assumptions land under *Open Questions → Assumed* as in §7. The user
  still sees every one at Spec PR review.

#### 8.4 A free vague-wording check before the costly reviewer (H2; cause 6)

Spec Kit's `analyze` flags vague terms that have no measurable criterion
("fast", "robust", "intuitive"). Its `specify` reruns its own quality
checklist up to 3 times before warning the user. Specdev sends all of that
to an `xhigh`-effort `spec-reviewer` pass.

- `validate_spec.py` gains a **warning** for an Acceptance line that uses a
  term from a short vague-word list (fast, quick, robust, intuitive,
  user-friendly, scalable, efficient, seamless, easy, appropriate,
  reasonable, …) with no number, unit or concrete example on the line.
- It is a warning, never an error, under `--strict` too, for the reason in
  the Constraints: an error would fail already-merged specs.
- The coordinator fixes the warnings before the first review pass and hands
  any it keeps to `spec-reviewer` as already known, so that pass is spent on
  what only judgment can find.

#### 8.5 Technical unknowns are researched, not escalated (H6) — needs a decision

In Spec Kit's `plan`, each unknown becomes a research task for an agent, and
the result is recorded as Decision / Rationale / Alternatives. In specdev a
builder that hits a gap reports `blocked`, and the gap goes to a person
whatever its nature.

- A blocked builder says which kind of gap it hit: `intent` (what the user
  wants) or `technical` (how to build it).
- **Technical:** the coordinator dispatches a read-only research subagent,
  limited to the repository, its dependencies' metadata and vendored docs
  (CI denies `WebFetch` and `WebSearch`). The decision goes in a new
  `## Decisions` section of `BUILD.md` as Decision / Rationale /
  Alternatives, is listed in `PR_BODY.md`, and is given to the reviewers.
- **Intent:** still a person, through `## Stopped` (§3).
- **Decision needed:** may a build make an *architectural* choice on its own?
  The proposal is no. A choice that would need an ADR (the `adr` skill's
  scope tags decide) goes to `## Stopped`. Everything below that bar is
  researched and recorded.

#### 8.6 Waves follow priority, so an early stop still delivers a whole slice (H9, H7) — needs a decision

Spec Kit's `tasks` builds by user story in priority order (P1, P2, P3), each
an independently testable increment, MVP first. Specdev's REQs already carry
`Priority: must | should | could`, but nothing reads it: waves follow only
the dependency graph.

- **Wave order:** among the components a wave could start, those owning
  `must` REQs go first. Dependencies still win. This is free, and on its own
  it changes only the order.
- **Optional partial delivery** (`ci.json` `partial_delivery`, default
  `false`): when a run stops at the breaker or the review cap, the terminal
  state accepts a branch where every `must` REQ is built, has a passing
  test, and was reviewed. Every `should`/`could` REQ not delivered is listed
  in `PR_BODY.md` as deferred. The human then merges a smaller, complete
  change instead of restarting the build.
- **Decision needed:** whether a "musts done, rest deferred" branch may ever
  count as a finished build. It is a scope decision for the people who
  approve the Spec PR, which is why it defaults off.

## Rollout

Each phase is its own PR, has its own tests, and is judged against the
Phase 0 numbers before the next one starts.

| Phase | Ships | Removes / targets | Risk |
|---|---|---|---|
| 0 | §0 `stats`, outcome record, escapes workflow | — (baseline) | None. Read-only. |
| 1 | §1 hook validation, §2 effort, §3 Stop hook, §7 front-load, assume-and-record, deploy facts at init | H2, H5, H7; cause 5, 6 | Low. The hooks fail open on internal error (log and allow), except §1's schema check, which is the point. |
| 1b | §8.1 component map before the Spec PR, §8.2 capped and ranked questions with recommended answers, §8.3 never-ask list, §8.4 vague-wording warning | H11; shortens H1; fewer H2 and H6 | Low. The components check runs only on `spec/**` heads and the vague-wording check only warns, so merged specs are unaffected. |
| 2 | §4 reproduced findings | cause 3 | Medium. Needs the worktree-checkout behaviour pinned by a test against the CI's Claude Code version. |
| 3 | §5 acceptance-author + lock | cause 1; H9 | Medium-high. It changes the build's shape (wave 0) and the terminal state. It ships behind `ci.json` `acceptance_tests: true` (default `false` for one release, then `true`). |
| 4 | §6 per-wave intent check, scoped passes | causes 2, 4 | Medium. It changes `check` rule 3. `schema_version` 2 for `review.json`, and v1 ledgers are still read. |
| 5 | §8.5 research before escalating, §8.6 priority-ordered waves and optional partial delivery | H6, H9, H7 | Medium. Both wait on the decisions named in §8.5 and §8.6. Partial delivery changes the terminal state, so it ships behind `partial_delivery: false`. |

Phase 1b does not depend on Phase 0 data. It removes a stop (H11) and cuts
questions rather than tuning the review loop, so it can ship as soon as it
is reviewed.

**Upgrade-together** applies as before. Phases 1, 1b, 3, 4 and 5 change tools,
agents, skill and workflow in step, and the README's "upgrade tools,
workflows, skill and agents together" note is extended to name them.

## Considered and rejected

- **Lowering `max_review_iterations`.** It treats the cost, not the cause,
  and the 2026-09-29 design made the cap a quality gate on purpose. Revisit
  with Phase 0 data after Phase 4.
- **Reviewer `memory: project`** (persistent memory of past false positives
  across features). Memory auto-enables Write/Edit, which breaks the
  reviewers' read-only contract. Cross-feature memory would also make a
  pass's verdict depend on state outside the ledger, which is not auditable.
  The per-run dismissed list already prevents thrash within a feature.
- **`--json-schema` for reviewer output.** It applies to a `-p` run's top
  level, not to a subagent's reply. §1's hook gets the same guarantee.
- **Auto-opening the Implementation PR, or auto-merging the Spec PR.** These
  remove H4/H8, which are the gates the pipeline exists for, and opening a PR
  needs the Actions create-and-approve switch the build deliberately avoids.
- **`@claude review` or Copilot as an in-loop reviewer.** They are post-PR by
  construction (approach C). They remain the human's second opinion, and
  Phase 0 counts what they still find.
- **From Spec Kit, deliberately not copied:**
  - *Advisory analysis.* Spec Kit's `analyze` only recommends fixing even
    CRITICAL findings and never blocks. Specdev's checks are gates.
  - *Asking a person over unchecked checklists.* Spec Kit's `implement`
    stops and asks whenever a checklist has unchecked items. That is a
    human stop of the kind this design removes.
  - *Optional tests.* Spec Kit's `tasks` writes test tasks only when asked.
    Specdev's builders are test-first and `--check-gaps` requires a test
    per REQ.
  - *Hooks as instructions.* Spec Kit's "mandatory" hooks are text telling
    the agent to run them ("must actually be invoked, not just announced").
    That relies on the model complying, which is what specdev's real hooks
    and gates replace.
  - *One question at a time.* Spec Kit's `clarify` asks up to 5 questions
    one by one. §8.2 keeps the cap and the recommended answers but asks in
    one batch: one stop, not five.
  - *An uncapped converge loop.* `converge` has no iteration limit or
    record of what was reviewed. Specdev's ledger, cap and staleness check
    stay.

## Out of scope

- The poc lane, beyond the Stop hook (§3), which applies to any CI build.
- Changing the circuit breaker's limits. Higher reviewer effort spends inside
  them, and the README's budget note is updated to say so.
- Cross-job restore of `review.json` or `acceptance.lock`. The reasoning in
  2026-09-29 §5 applies to both.

## Testing

- **§0:** `stats` over fixture ledgers (clean on pass 1, capped, re-raise
  after dismissal). The escapes workflow is a read-only `pull_request_review`
  job with no write permission, checked by the existing workflow-permission
  tests.
- **§1:** `review_hook.py` given `SubagentStop` payloads: a non-reviewer
  `agent_type` passes through; a valid block is written and its path printed;
  invalid JSON or a blocking finding with no `scenario` is blocked; a third
  block for the same `agent_id` allows the stop; an internal error fails
  open. `record --from-hook` refuses when a reviewer of the phase has no
  saved report, or has two. The shared schema function is used by both the hook and `record`.
- **§2:** the agent-file tests assert the declared `effort`, and assert that
  `reviewer_model` is written only when set explicitly.
- **§3:** `stop-hook` blocks when `verify` fails with a new commit since the
  last block; allows when there is no new commit, when `BUILD.md` records a
  deliberate stop, when `max_stop_blocks` is reached, and outside CI.
- **§4:** `code-reviewer` frontmatter has `isolation: worktree`. The schema
  requires `repro` on blocking `bug`/`security` findings and accepts
  `suspected` minors. One integration test against the pinned Claude Code
  version confirms that a worktree subagent checks out the reviewed sha, not
  the default branch.
- **§5:** `acceptance_lock.py` lock, verify and amend, and `verify()`
  failing on an unamended change. The terminal-state wording in
  `required_terminal_state`, `commands/build.md` and `SKILL.md` names the
  acceptance clause. The feature flag defaults to `false`.
- **§6:** ledger v2 with `scope` and phase `wave`; `check` rule 3 fails a
  clean `delta` pass that has no clean `full` pass after it; a v1 ledger is
  still read and checked under v1 rules.
- **§7:** `new-feature.md` names interrogate mode and assume-and-record. The
  spec hash ignores the `**Assumed:**` block (it is inside Open Questions).
  `init.md` runs `deploy.py preflight`.
- **§8.1:** `validate_spec.py --components` fails on a REQ with no owner, an
  unknown unmarked dependency, a cycle and an empty Contract / Interface, and
  passes a complete
  table. `spec-validate.yml` runs it only for `spec/**` heads, so a merged
  spec without `components.md` still passes on other PRs. `new-feature.md`
  fills `components.md`, and `build.md`'s precondition proposes components
  instead of stopping.
- **§8.2:** `spec-reviewer` names the category list, the 5-question cap, the
  ranking and a required `default` in interrogate mode. `stats` counts
  coverage per category. `new-feature.md` presents defaults as
  *Recommended:* in one batch.
- **§8.3:** `spec-reviewer` carries the never-ask list and the
  ADR / Original Request exception.
- **§8.4:** `validate_spec.py` warns on a vague Acceptance line, does not
  warn when the same line carries a number or unit, and never turns it into
  an error under `--strict`.
- **§8.5–8.6:** written once the decisions are made.
