# Design: Higher accuracy with fewer human stops

**Date:** 2026-11-05
**Status:** Draft — for review (Alex, Chad)
**Origin:** "Evaluate specdev, how can we make it more automated so there is
higher accuracy but less human in the loop? Perhaps there have been something
changes to Claude in the past few months." Grounded against
`Loopdev-ai/specdev@d501a79` (2026-09-30) and the Claude Code documentation as
of v2.1.311 (2026-10-26). The features this design relies on were re-checked
against the published docs on 2026-11-06 (changelog head: v2.1.318,
2026-11-04). See *Platform facts relied on* for each one.

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

H1, H3, H4 and H8 are the human gates the pipeline is built around, and this
design keeps them. The target is the rest: remove H5 and H7, fold H2 into H4,
and make H6, H9 and H10 rarer by fixing causes 1–6 above.

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
  record (`verify.json`) it already uploads. That gives one row per build
  without anyone collecting them.
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
  `<state dir>/review/<phase>/<agent_type>-<agent_id>.json` and prints the
  path as `additionalContext`. The coordinator passes that path to `record`.
  Nothing is retyped.

`review_ledger.py record` also accepts `--from-hook-dir <dir>`, which records
every file the hook wrote since the last pass and refuses if a dispatched
reviewer type is missing. Wiring: the CI breaker settings JSON gains a
`SubagentStop` entry, and the plugin ships the same entry in
`hooks/hooks.json` for interactive sessions.

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
- **Safety:** it never blocks twice without a new commit in between, the same
  rule `continue-gate` uses so a stuck agent cannot spin. It never blocks when
  `BUILD.md` records a deliberate stop (*Blocked:* filled in). It blocks at
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

## Rollout

Each phase is its own PR, has its own tests, and is judged against the
Phase 0 numbers before the next one starts.

| Phase | Ships | Removes / targets | Risk |
|---|---|---|---|
| 0 | §0 `stats`, outcome record, escapes workflow | — (baseline) | None. Read-only. |
| 1 | §1 hook validation, §2 effort, §3 Stop hook, §7 front-load, assume-and-record, deploy facts at init | H2, H5, H7; cause 5, 6 | Low. The hooks fail open on internal error (log and allow), except §1's schema check, which is the point. |
| 2 | §4 reproduced findings | cause 3 | Medium. Needs the worktree-checkout behaviour pinned by a test against the CI's Claude Code version. |
| 3 | §5 acceptance-author + lock | cause 1; H9 | Medium-high. It changes the build's shape (wave 0) and the terminal state. It ships behind `ci.json` `acceptance_tests: true` (default `false` for one release, then `true`). |
| 4 | §6 per-wave intent check, scoped passes | causes 2, 4 | Medium. It changes `check` rule 3. `schema_version` 2 for `review.json`, and v1 ledgers are still read. |

**Upgrade-together** applies as before. Phases 1, 3 and 4 change tools,
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
  open. `record --from-hook-dir` refuses when a dispatched reviewer type has
  no file. The shared schema function is used by both the hook and `record`.
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
