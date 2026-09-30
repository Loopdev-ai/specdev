---
description: Build the approved feature as a strict coordinator — dispatch component-builder subagents in dependency waves, QA each wave with qa-verifier, keep traceability current.
argument-hint: [FEAT-### or feature-name]
---

Build the feature **$ARGUMENTS** from its merged spec.

**This command dispatches subagents by design. Running it is your authorization
to spawn `component-builder`, `qa-verifier`, `code-reviewer` and
`intent-reviewer` agents — do it; do not fall back to building or reviewing
inline.** You are a coordinator, not a builder: you hold only the
spec, the component DAG, and returned summaries. You never write component code,
full test output, or code surveys into this thread.

## Preconditions (check, then stop if unmet)

1. The Spec PR for this feature is **merged** (Gate 1 passed). If not, stop —
   the contract isn't locked.
2. You are on a `feat/<feature>` branch off the merged spec. If not, create it.
3. `.specdev/spec.md` holds this feature and `.specdev/components.md` has a
   filled component table with a **Depends on** column. If the table is empty,
   stop and ask the user to define components first.

## Terminal state — the run is not over until you reach it

**You are not done when you run out of things you feel like doing. You are done
when this build's terminal state is reached.** CI asserts it mechanically after
your turn ends (`build_outcome.py verify`), in these words:

- **The implementation branch** `specdev/impl/<unit>/<FEAT-###>` carries commits
  beyond the base branch. The workflow pushes it for you — your job is to
  **commit** your work, every wave, so there is something to push.
- **A prepared PR body** at `<unit>/.specdev/PR_BODY.md`, filled in, not the
  shipped template.
- **A real checkpoint** at `<unit>/.specdev/BUILD.md`, not the shipped template.
- **A recorded review loop** (prod) at `<unit>/.specdev/review.json`: the last
  pass clean over the branch tip, or `max_review_iterations` reached with every
  open finding listed in `PR_BODY.md`. `review_ledger.py check --phase impl` is
  the same assertion — run it yourself before you stop.

**Do not open the PR, in either mode, and do not merge anything.** A human
opens the PR from that branch. This is not a restriction being worked around:
a PR opened by the build would require the repository setting *Allow GitHub
Actions to create and approve pull requests*, which grants create and approve
together and lets Actions approve its way past review — and a bot-authored PR
receives none of the required checks anyway. If you find yourself reaching for
`gh pr create`, `gh pr review` or `gh pr merge`, stop: the branch and the body
are the deliverable.

`poc` differs only in what happens next — the poc environment deploys from
that branch directly. It has no PR and no merge either.

Ending your turn short of that is a **failed build**, not a partial success —
including ending it cleanly, mid-wave, with turns to spare. A wave whose QA is
still in flight is not a stopping point; neither is "the interesting part is
done". Keep going: form the next wave.

**If you cannot get there** — a builder is blocked, a tool is denied, the
circuit breaker trips, a contract needs a human — then stop deliberately and
leave the reason behind: write what you completed, what is outstanding, and
what blocked you into `BUILD.md`, **commit it**, and say so. That is the only
acceptable way to end short of the terminal state. CI pushes your committed
checkpoint to `specdev/checkpoint/<unit>/<FEAT-###>` whatever the outcome, so
stopping is cheap and a re-dispatch resumes from it — but only for what you
actually committed.

Interactively (this command, run by a human), the terminal state is the end of
*After the final wave* below: green QA, a complete review loop, green org-ADR
loop, `PR_BODY.md` filled in, `BUILD.md` at `qa`, and the user told the PR is
ready. Opening the
PR is the human's call in both cases — headless CI is no different.

## Build loop (repeat until every component is built)

1. **Plan waves.** Read the **Depends on** column of `.specdev/components.md` as
   a dependency DAG. A *wave* = every not-yet-built component whose dependencies
   are all built. Record the wave plan in `.specdev/BUILD.md` (the wave ledger).

2. **Dispatch the wave — in parallel.** Launch **one `component-builder` agent
   per component in the wave, all in a single message** (independent Agent calls
   run concurrently). Give each builder a *minimal contract* and nothing about
   its siblings:
   - its row from `components.md` (what it owns, its interface, its deps),
   - the exact `REQ-###` sections + **Acceptance** lines it must satisfy,
   - for a `modified` component, the current behavior to preserve.

3. **Collect summaries only.** Each builder returns a short summary (component,
   REQs covered + which test asserts each, files, test pass/fail, commit SHAs,
   issues). Record each in the `BUILD.md` wave ledger. Do **not** read the
   builders' source or full logs into this thread.
   - If a builder reports **blocked** or a cross-component conflict, resolve the
     contract in `components.md` *before* launching any dependent wave. Never
     proceed past a red builder.

4. **QA the wave (gate).** After the wave's builders finish, dispatch **one
   `qa-verifier` agent** over the integrated working tree. It runs the Gate-2
   mirror (tests + coverage + secret scan + `gen_traceability.py --check-gaps`)
   and returns a green/red verdict with only actionable failures.
   - **Green** → record the verdict in the ledger and continue.
   - **Red** → dispatch a `component-builder` to fix the specific failures it
     named, then re-run `qa-verifier`. **Do not start the next wave until the
     current one is green.** QA happens every wave, not only at PR time.

5. **Keep traceability current.** As part of each wave's QA, the
   `--check-gaps` run must pass: every `REQ-###` built this wave has a linked
   test and the builders' commits carry `Refs: REQ-###` trailers. If a REQ has
   no test, that wave is not done — send it back to a builder.

6. **Next wave.** Form the next wave from newly-unblocked components and repeat.

## After the final wave

1. Dispatch `qa-verifier` once more over the fully integrated result as the
   pre-PR dry run. It must be green.
2. Resolve/verify deployment facts if not already done: run `detect_deploy.py`,
   fill any `missing`/`REPLACE_ME` facts in `deploy.profile.json` and
   `BUILD.md` → *Deployment Facts*, then `deploy.py preflight --env staging` and
   `--env production` until green (the `preflight` job blocks the merge
   otherwise).
3. **Pre-PR review loop (prod mode) — the PR is held until it ends.** Follow
   the specdev skill's *Pre-PR review loop*. Start from the pass
   `python .specdev/tools/review_ledger.py --root <unit> status --phase impl`
   names, then per pass:
   - Capture `git rev-parse HEAD`, then dispatch a fresh **`code-reviewer`** and
     a fresh **`intent-reviewer`** in one message, with the unit root, the base branch and the dismissed list.
   - Save each one's closing JSON block and record the pass:
     `review_ledger.py --root <unit> record --phase impl --reviewed <that sha>
     --findings-json <a> --findings-json <b>`.
   - `clean` → go to step 4. `cap-reached` → stop fixing; the open findings
     are handed off in step 5. `stale` → run the next pass; `stale-at-cap` →
     `review_ledger.py --root <unit> new-run --phase impl`, then the next pass.
   - Otherwise dispatch `component-builder`s in review-fix mode with the open
     findings as their contract — each reproduces its finding with a failing
     test, then fixes it; dismiss a `not-reproducible` one with its evidence
     (`review_ledger.py --root <unit> dismiss --phase impl --id <id> --reason
     "..."`) — then `qa-verifier` green, commit, and run the next pass.
     **Automatically — do not ask the user between passes.**
4. **Org-ADR compliance loop (when `.specdev/org.json` is configured) — the
   PR is held until this is green.** Dispatch the **`adr-checker`** agent. It
   fetches the org ADR index, verifies every ADR applicable to this repo's
   classification, and writes `.specdev/adr/org-compliance.json`.
   - **Red** → for each named violation dispatch a `component-builder` with
     the violation as its contract (or amend the spec/local ADRs if the fix is
     a documented deviation), then re-run `qa-verifier`, then re-run
     `adr-checker`. Repeat **automatically — do not ask the user between
     iterations** — until green.
   - **Green** → record the verdict in the `BUILD.md` ledger and continue.
   - If this loop changed any code, run one more review pass (step 3) — the
     review check fails on code the last pass never saw. If `status` says
     `stale-at-cap`, run `review_ledger.py --root <unit> new-run --phase impl`
     first.
5. Fill in `.specdev/PR_BODY.md` — the Implementation PR body — from the wave
   ledger: REQs covered and the test asserting each, deployment facts resolved,
   anything deferred. Paste `review_ledger.py --root <unit> render --phase
   impl` over its `## Review loop` and `## Unresolved review findings`
   sections. It is asserted after the run, so the stock template surviving is
   a failed build.
6. Commit `.specdev/review.json`, then run `review_ledger.py --root <unit>
   check --phase impl`. It must pass. If the run reached its cap, tell the
   user which findings are unresolved.
7. Update `BUILD.md` status to `qa`, then tell the user the build is green and
   the next step is to open the Implementation PR (Gate 2) from the pushed
   branch, pasting `PR_BODY.md`. Do **not** open the PR automatically, and
   never announce PR readiness while `qa-verifier`, `adr-checker` or
   `review_ledger.py check` is red.

## Guardrails

- One component = one builder. Sequential/trivial single-component work still
  goes through a `component-builder` so its detail stays out of this thread.
- The coordinator's context must stay small — if you find yourself reading
  source files here, you're doing a builder's job. Send a subagent instead.
- Treat `BUILD.md` as the source of truth: a fresh session can resume the build
  from the wave ledger without re-reading this conversation.
- Only the `adr-checker` agent writes `.specdev/adr/org-compliance.json`; the
  coordinator never edits it and never reads org ADR bodies into this thread.
- Reviewers never fix and builders never review: every review pass is a fresh
  `code-reviewer` + `intent-reviewer`, and only `review_ledger.py` writes
  `.specdev/review.json`.
