# FEAT-XXX — <FEATURE NAME>

<!--
The Implementation PR body, written by the build and opened by a HUMAN.

The build does not open its own PR. It pushes `specdev/impl/<unit>/<FEAT-###>`
and prepares this file; you open the PR from that branch and paste this in.
That is not a limitation being worked around — it is the design:

  * A PR opened by GitHub Actions requires the repository setting "Allow
    GitHub Actions to create and approve pull requests", which grants CREATE
    and APPROVE together. Leaving it off makes the build's terminal state
    unreachable; turning it on lets Actions approve pull requests, bypassing
    the review the merge gate depends on. The build needs neither.
  * A bot-authored PR does not fire `on: pull_request`, so it receives NONE of
    the required checks and cannot merge. A PR you open does.

Replace every placeholder below. This file is asserted after the run: if the
stock markers above survive, the build failed.
-->

## What this implements

<!-- One paragraph. Which REQ-###s, and what a reviewer should look at first. -->

## Requirements covered

| REQ | Component(s) | Test that asserts it |
|---|---|---|
| REQ-001 | | |

## Wave ledger

<!-- Copy the per-wave verdicts from BUILD.md: what was built, QA green/red. -->

## Review loop

<!-- Paste `review_ledger.py --root <unit> render --phase impl` over this
section and the next: passes run against max_review_iterations, what each
found, and every dismissal with its reason. -->

## Unresolved review findings

<!-- Filled by the same render: "None — the final review pass was clean.", or
every blocking finding still open when the loop reached its cap, by id. The
build asserts that each open id is listed here. -->

## Deployment facts

<!-- Anything resolved into deploy.profile.json during the build, or "none". -->

## Verification

- [ ] `qa-verifier` green on the fully integrated result
- [ ] `gen_traceability.py --check-gaps` passes — every REQ has a linked test
- [ ] `adr-checker` green (or org governance not configured)
- [ ] `review_ledger.py check --phase impl` passes — the last review pass covers this branch's tip
- [ ] `deploy.py preflight` green for every target environment

## Notes for the reviewer

<!-- Blockers hit, deviations from the spec, anything deliberately deferred. -->
