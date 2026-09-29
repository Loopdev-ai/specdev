# Design: A bounded pre-PR review loop for the Spec and Implementation PRs

**Date:** 2026-09-29
**Status:** Approved (brainstorming) — ready for implementation planning
**Origin:** "The PRs that are sent in are not fully formed enough … Copilot
keeps finding lots of bugs in the code, so it isn't looping around enough. If
bugs are found or the original intent isn't delivered on, it should loop back
and fix … up to X times, in this case let's start with 10 … It should be using
subagents for this review." Grounded against `Loopdev-ai/specdev@6238681`.

## Problem

Neither PR is reviewed by anything that reads it for correctness or intent
before a human opens it.

| PR | What runs before it today | What nobody checks |
|---|---|---|
| Spec PR | `validate_spec.py --strict` (structure: FEAT id, REQ ids, an Acceptance line, concrete Out of Scope); `adr-checker` (org ADRs) | Is each Acceptance line unambiguous and testable? Does the spec say what the user asked for — nothing dropped, nothing invented? Are error and edge behaviours specified? Do REQs contradict each other? |
| Implementation PR | per-wave and pre-PR `qa-verifier` (tests, coverage, secret scan, `--check-gaps`); `adr-checker` loop | Is the code *correct*? `qa-verifier` runs the tests the **same `component-builder` wrote**, so any bug the builder did not think of passes green. Does each REQ actually work end to end, and does its linked test assert the Acceptance line or merely mention the id? |

That gap is exactly what Copilot fills after the PR opens, which is the
symptom reported. There is also no iteration budget anywhere: the one fix loop
that exists (org-ADR) runs "until green", unbounded, and exists only as prompt
text.

The intent half has a second cause: **the user's original request is not
recorded anywhere.** The brainstorm's answers are distilled straight into
REQs, so nothing downstream can check the REQs against what was asked.

## Constraints

- **Coordinator context discipline holds.** The coordinator never reads source
  or full review output; reviewers return findings only (`SKILL.md` →
  *Context discipline*).
- **Reviewer independence.** A reviewer is never the agent that wrote the
  artifact, and it judges without fixing — the same rule that makes
  `qa-verifier` meaningful.
- **Mechanical over prompt.** This repo's standing rule, from the U17
  continuation work: "a fix that depends on the model complying is not a fix."
  The reported failure *is* a compliance failure, so prompt text alone does not
  address it.
- **The build never opens a PR; a human does.** The loop ends in the same
  terminal state as today — a branch and a prepared body — never a PR.
- **poc has no PR.** Nothing here applies to the poc lane.
- **Do not break existing adopters.** `spec-validate.yml` runs `--strict` on
  every PR touching a unit, so any new *validator* rule is retroactively
  enforced against every already-merged spec.

## Approach

- **A. Prompt + agents only.** New reviewer agents and loop instructions in
  `SKILL.md` / `commands/*.md`. Cheap, but leaves the loop to the model's
  compliance — the exact failure reported.
- **B. A + a mechanical gate (chosen).** Every review pass is recorded in a
  per-unit ledger by a tool; `build_outcome.py verify` and `spec-validate.yml`
  fail when the loop never ran, when code changed after the last reviewed
  state, or when it stopped with blocking findings short of the cap.
- **C. Post-PR CI review** (a Copilot-style workflow on `pull_request`).
  Rejected: the requirement is that the PR is fully formed *before* it exists.

## Design

### 1. Three reviewer agents

New files under `agents/`, vendored by `/specdev:init` like the others.
`tools: Read, Grep, Glob, Bash` — no Write/Edit. They judge; they never fix.
Each pass dispatches **fresh** agents (new `Agent` calls, never a
`SendMessage` continuation), so no pass inherits a previous pass's blind
spots.

- **`spec-reviewer`** — the Spec PR loop. Input: unit root, the spec, the
  dismissed-findings list. Checks, against `## Original Request`:
  - every Acceptance line is observable, unambiguous and testable;
  - the REQs cover the request — nothing dropped, nothing added that was not
    asked for (which belongs in Out of Scope or needs the user);
  - error, empty, boundary and failure behaviour is specified wherever the
    request implies it;
  - no two REQs contradict; Out of Scope does not exclude something the
    request asked for;
  - every REQ is owned by a component in `components.md` (when it is filled);
  - extend mode: **Current behavior** is filled per REQ.
  A missing or placeholder `## Original Request` is itself a blocking finding:
  intent cannot be judged without it.
- **`code-reviewer`** — the Implementation PR loop. Input: unit root, base
  ref, the spec's REQ list (for context), the dismissed list. Reads the
  `base...HEAD` diff for the unit and hunts for defects: logic errors,
  unhandled edge cases, error handling that swallows or mis-reports,
  resource/concurrency bugs, security defects (injection, authz, unsafe
  deserialization, secrets), wrong API use, and **tests that do not assert
  what they claim**. Every blocking finding must carry a `file:line` and a
  concrete failure scenario ("input X in state Y yields Z, expected W"), the
  way Copilot's do. Style is `minor` and never blocks.
- **`intent-reviewer`** — the Implementation PR loop, in parallel with
  `code-reviewer`. Input: unit root, base ref, the spec including
  `## Original Request`, the dismissed list. Per REQ: is it delivered end to
  end (wired into the entry point, not only unit-tested in isolation)? Does
  the linked test actually assert the Acceptance line? Does anything in the
  Original Request or Problem go undelivered? Is there scope creep beyond the
  spec? Falls back to Problem + REQs for a spec that predates `## Original
  Request`.

### 2. Finding format

Each reviewer ends its report with exactly one fenced JSON block, which the
coordinator saves verbatim and hands to the ledger tool — counts are computed
by the tool, never hand-tallied:

```json
{"findings": [
  {"severity": "blocking", "kind": "bug",
   "where": "src/pager.py:42", "summary": "one line",
   "scenario": "page=0 returns the last page; expected the first",
   "fix": "suggested direction", "needs_human": false}
]}
```

`severity`: `blocking | minor`. `kind`: `bug | security | intent | test |
spec`. `needs_human` is meaningful only for `spec` findings. The tool assigns
stable ids — `S<pass>-<n>` (spec) and `I<pass>-<n>` (impl) — and prints them;
fixer contracts and dismissals reference those ids.

**Blocking** = a bug, a security defect, an intent gap, a test that does not
assert its REQ, or a spec defect that would let a wrong implementation pass.
Minor findings are recorded and rendered, and never extend the loop.

### 3. The loop

Bounded by `max_review_iterations` (new `ci.json` key, default **10**).

```
pass = next pass for this run (ledger `status`)
loop:
  dispatch the phase's reviewer(s) in parallel, fresh, with the dismissed list
  record the pass (ledger assigns ids, stamps what was reviewed)
  if no open blocking findings:        exit — clean
  if pass == max_review_iterations:    exit — hand off flagged (§6)
  fix every open blocking finding (below)
  pass += 1
```

**Implementation phase** — starts once the pre-PR `qa-verifier` dry run over
the final wave is green.

- Fixes go to `component-builder`, one per affected component, in parallel
  when their files are disjoint, with the findings as the contract. **Test
  first:** the builder writes a test that reproduces the finding and fails,
  then fixes it. If it cannot reproduce it, it returns `not-reproducible` with
  the evidence, and the coordinator dismisses the finding with that evidence
  as the reason.
- The coordinator may also dismiss a finding that contradicts the spec or an
  accepted ADR (e.g. it demands out-of-scope behaviour), citing the section.
- After the fixes, `qa-verifier` must be green (red goes back to a builder
  inside the same pass), then **commit** — reviewers only ever review
  committed code, and the ledger refuses to record a pass over a dirty tree.
- **Ordering with the org-ADR loop:** review loop first, then the
  `adr-checker` loop. If the ADR loop changes code, one more review pass runs
  (it counts against the same cap). The "no code changed since the last
  reviewed commit" check (§5) enforces this without a special case.
- **At the cap, the last pass's findings are not fixed.** Fixing them would
  put unreviewed code into the PR; they are handed off as unresolved instead
  (§6). The invariant is: every line in the PR was reviewed.

**Spec phase** — after `validate_spec.py --strict` passes, before the Spec PR
`adr-checker` run.

- One `spec-reviewer` per pass. The coordinator revises the spec itself — the
  spec is its artifact and is small — then re-runs `validate_spec.py
  --strict`.
- `needs_human` findings are batched into **one** question to the user per
  pass. An answer is folded into the spec; a deferral is dismissed ("deferred
  by the user") and written into `## Open Questions`.
- Then `adr-checker`, as today, then the user is told the spec is ready.

**Dismissed findings** are persisted in the ledger and passed to every later
reviewer as "already adjudicated — do not re-raise without new evidence", so a
false positive cannot thrash the loop to its cap.

**When the loop runs:** the Spec PR loop when the profile's `spec_pr` is
true; the Implementation PR loop in `prod` mode. `poc` runs neither — there
is no PR. No new profile key is needed.

### 4. Configuration

`ci.json` gains `"max_review_iterations": 10`, mirrored in `run_manifest.py`'s
`_FALLBACK_DEFAULTS` (the existing agreement test covers it). Resolved with
the existing `run_manifest.py --root <unit> ci --get max_review_iterations
--repo-root .`, so a unit can override the repo root. It must be an integer
≥ 1; `0` does **not** disable the loop. This is a quality gate, not a
ceiling, and an off switch would be the escape hatch the reported failure
already is.

### 5. The ledger and its tool

`<unit>/.specdev/review.json`, owned by a new
`.specdev/tools/review_ledger.py` (written only through the tool, like
`org-compliance.json` is written only by the checker):

```json
{
  "schema_version": 1,
  "feat": "FEAT-004",
  "phases": {
    "spec": {
      "runs": [{"started_at": "…", "passes": [
        {"pass": 1, "at": "…", "reviewed": "sha256:…",
         "findings": [{"id": "S1-1", "severity": "blocking", "kind": "spec",
                       "where": "REQ-002", "summary": "…"}]}]}],
      "dismissed": [{"id": "S1-3", "summary": "…", "reason": "…", "at": "…"}]
    },
    "impl": {"runs": [ … "reviewed": "<commit sha>" … ], "dismissed": [ … ]}
  }
}
```

CLI (`--root <unit>` precedes the subcommand, as in the sibling tools):

| Command | Does |
|---|---|
| `record --phase P --feat F --findings-json FILE [--findings-json FILE …]` | Appends the next pass to the current run, assigns ids, stamps `reviewed` itself (impl: `HEAD`, refusing a dirty non-`.specdev/` tree; spec: the spec hash), refuses a pass beyond the cap. A different `--feat` than the ledger's starts a fresh ledger (git history keeps the old one). |
| `dismiss --phase P --id ID --reason TEXT` | Moves a finding to the dismissed list. |
| `new-run --phase P` | Starts a new run: a re-review after human changes on an open PR. **Refuses unless the reviewed state is stale** (check rule 2), so it cannot be used to buy more passes for a run that hit its cap with nothing changed. |
| `status --phase P` | Next pass number, cap, open blocking ids, the dismissed list — what the coordinator (or a CI continuation attempt) needs to resume, and what goes into each reviewer's prompt. |
| `render --phase impl` | The markdown for PR_BODY's `## Review loop` and `## Unresolved review findings` sections. |
| `check --phase P` | The gate (below). Exit 1 with reasons. |

`check` fails when any of these holds, for the current run:

1. no pass is recorded, or the ledger's `feat` is not this feature's;
2. **the reviewed state is stale** — impl: any non-`.specdev/` path differs
   between the last pass's `reviewed` commit and `HEAD`; spec: the spec hash
   differs. The spec hash excludes the bookkeeping header lines
   (`**Status:**`, `**Spec PR:**`, `**Author:**`, `**Date:**`) and the
   `## Open Questions` section, so filling in the PR number or recording
   unresolved findings does not invalidate a review; any other edit does;
3. the last pass has open blocking findings **and** the run is short of
   `max_review_iterations` — the loop stopped early;
4. the run reached the cap with open blocking findings **and** some open id is
   missing from the hand-off location (§6).

A reviewed commit that is not in the local history (a shallow clone, or a
lost ref) is a failure with that reason — never a pass.

`review.json` is **not** restored on a cross-job re-dispatch
(`specdev-build.yml` restores only `BUILD.md` and `run.json`). That is
deliberate: its reviewed SHAs only mean something against the code they
reviewed, and that code is not restored either. Continuation attempts
*within* a job share the working tree, so they resume the pass count via
`status`, not from pass 1.

### 6. Hand-off at the cap

Per the user's decision: the branch and body are still produced, with the open
findings flagged.

- **Impl:** `PR_BODY.md` gains `## Review loop` (passes used / cap, per-pass
  counts, what was fixed and dismissed, with reasons) and `## Unresolved review
  findings` ("None — the final review pass was clean", or each open finding by
  id with its `where`, summary and scenario). `render` produces both.
- **Spec:** open findings go into the spec's existing `## Open Questions`, by
  id. That section is outside the spec hash, so recording them there does not
  go stale.

`check` rule 4 verifies every open id appears in its hand-off location.

### 7. Enforcement points

- **CI build (prod):** `build_outcome.verify()` adds `review_ledger` `check
  --phase impl` to the terminal state for `mode == "prod"`. The terminal-state
  wording in `required_terminal_state`, `commands/build.md` and `SKILL.md`
  grows a clause for it. Because `continue-gate` calls `verify`, an attempt
  that ends with the loop unfinished is continued mechanically, like an
  unfinished wave.
- **Spec PR:** `spec-validate.yml`'s `validate` leg adds `review_ledger.py
  --root <unit> check --phase spec`, **only when the head branch is
  `spec/**`** and the unit's `spec_pr` profile key is true. Gating it on the
  head branch is what keeps it from firing on Implementation PRs and on every
  existing adopter's unrelated PRs.
- **Interactive:** `commands/build.md` and `commands/new-feature.md` run the
  same `check` before telling the user a PR is ready.

### 8. Text and template changes

- `assets/specdev/spec.md`: new `## Original Request` section — the user's
  request verbatim, plus the brainstorm answers that shaped it. **Not**
  enforced by `validate_spec.py` (see Constraints — it would retroactively
  fail every merged spec under `--strict`); `spec-reviewer` enforces it for
  new specs.
- `assets/specdev/PR_BODY.md`: the two sections in §6, and a Verification
  checklist line for `review_ledger.py check --phase impl`.
- `assets/specdev/BUILD.md`: a short *Review loop* section pointing at
  `review_ledger.py status` as the resume point.
- `skills/specdev/SKILL.md`: a *Pre-PR review loop* section; pipeline steps 5
  and 7 call it; *Context discipline* lists the three reviewers; Guardrails
  gain "never announce a PR ready while `check` is red".
- `commands/build.md`: the loop inside *After the final wave*, before the
  org-ADR loop and before `PR_BODY.md`.
- `commands/new-feature.md`: capture the Original Request verbatim; run the
  loop after `validate_spec.py --strict`.
- `commands/init.md`, `README.md`: list the new agents and tool; README notes
  the budget interplay below.
- `assets/workflows/specdev-build.yml`: the prompt's terminal-state text and
  `CONTINUE_PROMPT` name the review loop and `status` as its resume point.
- `.sdlc/config.json`: add `review_ledger.py` to the lint command.

### 9. CI budget interplay (documented, not changed)

Ten passes of reviewers, fixers and QA is real spend. A long loop can reach the
circuit breaker (`max_cost_usd` 10, `max_tool_calls` 3000, `max_wall_minutes`
240) or the `continuation_cap_usd` 25 start gate. **The limits are left as
they are** — raising an adopter's spend ceilings is their decision — and the
README says which knob to turn. When the breaker trips mid-loop the ledger and
checkpoint record where it stopped, and the terminal-state assertion fails
honestly rather than handing off a half-reviewed branch.

## Out of scope

- Post-PR review automation; Copilot stays as the human's second opinion.
- Per-wave code review. The loop is pre-PR only, as asked.
- Changing breaker or continuation limits (§9).
- Any review in the poc lane.
- Enforcing `## Original Request` in `validate_spec.py`.
- An impl-ledger check in `post-dev-qa.yml` on the Implementation PR. The
  asymmetry with the Spec PR gate is deliberate: the spec is authored
  interactively and has no other mechanical enforcement point, whereas the
  implementation already has the build's terminal-state assertion. Commits a
  human pushes to an open Implementation PR are that human's review, not the
  loop's.
- Restoring `review.json` across a cross-job re-dispatch (§5).

## Testing

New `tests/test_review_ledger.py`, plus additions to existing suites:

- `record`: id assignment, counts computed from the JSON, refusal beyond the
  cap, refusal over a dirty tree (impl), fresh ledger on a new `--feat`.
- `check`, one test per failure rule: no pass; stale impl commit (a code file
  changed after the reviewed commit) versus a `.specdev/`-only change (still
  passes); stale spec hash versus an edit only to the header or Open Questions
  (still passes); stopped short of the cap with findings open; at the cap with
  an open id missing from `PR_BODY.md` / Open Questions versus all present;
  a clean last pass; an unknown reviewed commit.
- `dismiss` removes a finding from the open set; `new-run` resets the pass
  count when the reviewed state is stale and refuses when it is not; `status`
  reports the next pass correctly after a partial run.
- `verify()` fails in `prod` mode without a clean-or-handed-off ledger, and is
  unaffected in `poc` mode.
- `ci.json` / `_FALLBACK_DEFAULTS` agreement (existing test) covers the new
  key; `max_review_iterations: 0` is rejected.
- The three agent files exist, declare no Write/Edit, and fit inside the
  session allowlist (existing superset test).
- `SKILL.md`, `commands/build.md` and `commands/new-feature.md` name the loop,
  `max_review_iterations` and `review_ledger.py check`; `spec-validate.yml`
  runs the spec check only for `spec/**` heads.
- Installed layout: the tool runs from `.specdev/tools/` against the installed
  `ci.json`.
