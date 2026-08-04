# Design: Governance profiles — a real fast path for the `poc` lane

**Date:** 2026-08-03
**Status:** Approved (brainstorming) — ready for implementation planning
**Origin:** "Skip levels of governance to speed up the process and reduce
overhead on a certain path", grounded against `Loopdev-ai/specdev@d40b5a9`

## Problem

SpecDev's governance is nearly flat. `classification` (`maturity` × `audience`,
`governance/classification.json`) is load-bearing for exactly **one** thing:
which org ADRs bind a unit, via `check_org_adrs.py`. Every other layer runs
identically for a throwaway spike and a customer-facing production service.

| Layer | Cost | Conditional today |
|---|---|---|
| Brainstorm → `spec.md` with REQ IDs + acceptance | high (human) | no |
| Gate 1 `validate_spec.py --strict` | low | no |
| ADRs | medium | partly — "skip if it fits existing architecture" |
| Spec PR: human approval + merge round trip | high (latency) | only headless `poc` (self-merge) |
| `adr-checker` before *both* PRs | medium | inert until `org.json` set |
| Per-wave `qa-verifier` + trace-gap check | high (tokens, wall clock) | no |
| Gate 2: tests, SAST, coverage, trace gaps + review | high | no |
| Deploy → staging QA → auto-prod → QA | high | yes — poc uses an isolated env |

The only fast path that exists is `specdev-build.yml`'s `poc/**` lane, and it
collapses only *who merges* and *where it deploys* — not *how much governance
runs*. The spec bar, the two-PR structure, per-wave QA and the coverage gate
are still full-freight for a spike.

Worse, `SKILL.md` — what runs when a human drives `/specdev:build` locally —
has **no poc awareness at all**. A spike driven locally today gets the entire
production pipeline.

## Constraints

- **poc code is never directly promoted.** It is reverse-engineered for
  features and specs and rebuilt in a `dev`/`prod` unit. No graduation ratchet
  is needed for the code; instead, promotion-in-place must be *prevented*.
- **The reduction must be a declared, auditable lane, not an escape hatch.**
  The monorepo design's premise is that governance one can route around is
  worse than none.
- **Credential protection is not ceremony** and is not subject to the
  reduction.

## The seam that already exists

`gen_adr_index.py:141` writes `{"axes": axes, "adrs": adrs}` — the
classification scheme is embedded in `index.json` **verbatim**, and every
product repo already fetches that file at its pinned `ref`. A `profile` key
added to a maturity value therefore reaches every product repo through a fetch
that already happens. **No new distribution plumbing.**

Likewise, every gate workflow already has a `discover` job running
`units.py check`, which gives both profile resolution and the no-promotion
rule a home with zero new wiring.

## Approach

Considered three mechanisms:

- **A. Org-declared profiles (chosen).** Policy lives in the governance repo
  and is *pulled*; a product repo cannot grant itself a discount. One file
  answers "what does poc skip?". Cost: a schema plus resolution logic.
- **B. Hardcode the poc lane** in `SKILL.md` plus `if maturity == poc`
  conditionals across the gate workflows. Fastest to ship, but the policy
  scatters across eight files and each future gate is a place to forget the
  conditional.
- **C. A separate `/specdev:spike` lane.** Smallest blast radius, honestly
  reflects that a spike is a different activity — but duplicates deploy,
  checkpointing, circuit breaker and scope check, and the lanes will drift.

**Chosen: A, carried on B's existing `mode`/`unit` plumbing.** Policy in the
governance repo, mechanism reused from what `specdev-build.yml` already has.
A is the only option that keeps the reduction auditable and un-self-servable,
which is also what makes it the natural home for the no-promotion rule.

## Data model and resolution

Each `maturity` value in `governance/classification.json` gains a `profile`
object, riding `index.json` to product repos at the pinned `ref`.

| key | `poc` | `dev` | `prod` |
|---|---|---|---|
| `spec_bar` | `charter` | `full` | `full` |
| `spec_pr` | false | true | true |
| `adrs` | false | true | true |
| `per_wave_qa` | false | true | true |
| `coverage_gate` | false | true | true |
| `traceability` | false | true | true |
| `compliance` | false | false | true |
| `prod_promotion` | false | true | true |

`prod_promotion` earns its place by making "a poc never reaches prod" a fact in
the table rather than an accident of workflow topology.

New `assets/specdev/tools/profile.py`, one entry point:
`resolve(root, unit) -> dict`. It **reuses `check_org_adrs.py`'s index fetch**
rather than introducing a second fetcher, so there is exactly one code path
that decides which `ref` the org's policy is read at. Three rules, in order:

1. **Resolve from *effective* classification, never declared.** This is the
   load-bearing rule. `units.effective()` already pulls a unit up when a
   higher-classified unit depends on it. Keying the profile off *declared*
   maturity would reopen the escape hatch the monorepo design closes, and
   wider than before: park risky code in a `poc` unit, import it from `prod`,
   and it would skip QA, coverage and traceability as well as the org ADRs.
   Profile resolution goes through the same `effective()` call as
   `check_org_adrs.py`.
2. **Strictest wins across a set.** `effective()` returns a *set* per axis. For
   an ordered axis take the max rank; the resolved profile is the strictest
   across the set, per key. Resolution is monotone — a higher rank can never
   yield a looser profile.
3. **Fail closed.** Unconfigured `org.json`, unreachable index, missing
   `profile` key, or an unparseable value → **full prod governance**. "Inert"
   means strict here, the opposite of the ADR gate's inert-means-skip.
   Undeclared governance is never a discount.

**Distribution to CI.** Each gate's existing `discover` job resolves profiles
for all units once and emits them as a JSON output; matrix legs read their own
leg's profile from it. One fetch per workflow run, no per-leg network, and no
cached file on disk to tamper with.

## The floor (non-overridable)

Applied by `profile.py` *after* loading the table. An org that writes
`"secret_scan": false` gets it ignored plus a warning on stderr. The table can
remove ceremony; it cannot remove the floor.

**Process floor:**

- Unit **scope check** — containment of a spike to its own unit.
- **Findings capture** — see terminal state below.
- **A smoke test** — at least one test collected and passed.
- **org ADRs targeting `poc`** — `adr-checker` still runs; only ADRs whose
  `applies_to` names `poc` match, so it is a cheap single pass. This is the
  natural home for rules like "no real customer data in a spike".

**Credential floor** — five distinct mechanisms, none in the profile table:

1. **gitleaks secret scan** (`post-dev-qa.yml:101-121`), deliberately run
   repo-wide and unfiltered on every matrix leg.
2. **`arch_config.py validate`** — secret-bearing fields may hold only a
   `secret_ref` pointer (key vault / env var / file), never a literal, with a
   leak-guard regex backstop (`arch_config.py:63-72`) catching pasted
   passwords, API keys and high-entropy blobs. It triggers on the *file path*,
   not on classification, so it is already immune to the profile and stays out
   of the table entirely.
3. **SAST** — on for poc. Seconds of CI, zero human time; same argument as the
   secret scan.
4. **Credential isolation** — `deploy-poc` runs `environment: poc`, so a spike
   only ever sees the poc Environment's secrets, never staging's or
   production's. `prod_promotion: false` is what keeps that structurally true.
5. **Egress deny list + circuit breaker** in `specdev-build.yml`, which its own
   comment scopes to "the egress and credential paths, where the real risk is".

## Pipeline changes

`SKILL.md` gains a resolve-the-profile step in *Establish the governed unit
FIRST*, and the numbered pipeline becomes profile-conditional. Under the `poc`
profile:

| Step | Change |
|---|---|
| 2 Brainstorm | Short charter Q&A: goal, questions this spike must answer, timebox, what would make us abandon it |
| 3 Spec | `.specdev/CHARTER.md` instead of the REQ/acceptance structure; `validate_spec.py --profile charter` validates charter fields, not REQ IDs |
| 4 Architecture | The `adr` skill / `/specdev:adr` is not invoked, so no ADRs are authored; `adr-checker` still runs (floor) |
| 5 Spec PR | Skipped — the charter commits onto the `poc/**` branch |
| 6 Build | Waves still exist (dependency order is correctness, not governance) but no gate *between* them; one `qa-verifier` at the end in smoke mode |
| 7 Impl PR | Still opened, still self-merged — it remains the audit record even with thin gates |
| 8 Deploy | `deploy-poc` only |
| 9 Traceability | Skipped — no matrix, no `--check-gaps`, and no `Refs: REQ-###` commit trailers, since `spec_bar: charter` assigns no REQ IDs to reference |

**New terminal state:** `BUILD.md` must carry a non-empty `## Findings`
section. This extends the assertion `specdev-build.yml:624-625` already makes
(the `verify` step feeding `terminal_ok`, which `deploy-poc` is gated on)
rather than adding machinery — and it is the one thing that makes the spike
worth having run, given the code is reverse-engineered and rebuilt.

**Agents.**

- `qa-verifier` gains a **smoke mode**: run the suite, assert **≥1 test
  collected and passed**, run gitleaks, skip coverage and `--check-gaps`. The
  "≥1 passed" assertion catches a poc reported as working that never executed.
- `component-builder` under `spec_bar: charter` writes the smoke test then
  builds, rather than red-green per REQ — there are no REQs to drive TDD, and
  full-price discipline whose only surviving artifact is one test is not worth
  its cost.

**Workflows.** `post-dev-qa` makes coverage and trace-gaps conditional on the
profile; `spec-validate` learns charter mode; `traceability` and `compliance`
skip poc units; `specdev-build`'s verify step asserts Findings.

**Interaction with the `adr` skill** (landed in PR #10, after this design's
first draft): ADR authoring now routes through `skills/adr/SKILL.md` and
`assets/specdev/tools/adr.py` (`lint`, `conflicts`, `next-id`). Neither
subcommand is invoked by **any** workflow — the ADR quality gate is
skill-side only. `adrs: false` therefore needs **no CI conditional**: it is
purely "do not invoke the `adr` skill at step 4". This makes the reduction
smaller than it would otherwise have been, and it is why the profile table
needs no `adr_lint` key.

## No promotion in place

`units.py check` gains one rule: if a unit's maturity rank **increased** versus
the merge base **and** that unit has poc build history (a `poc-*` release tag,
or `run.json` recording mode `poc`), fail hard:

> poc units are not promoted in place; reverse-map with `spec-explorer` and
> rebuild in a new unit.

Every gate's `discover` job already runs `units.py check`, so this needs no new
wiring. It closes the gap ADR-0001 leaves open, where reclassification is
"a PR-reviewed change like any other".

## What this buys

- One of two PR round trips removed — the human-latency item.
- *N−1* `qa-verifier` subagent runs per build collapsed to one — the dominant
  token and wall-clock cost in the current loop.
- No ADR authoring for a spike.
- No spike ever blocked by a coverage floor or a REQ-without-test gap.

## Testing

New `tests/test_profiles.py`, extending the existing suite:

- **Escape-hatch test (most important):** a `poc` unit that a `prod` unit
  `depends_on` resolves to the **prod** profile — full QA, coverage,
  traceability. This is the regression that would otherwise silently widen the
  hole the monorepo design closes.
- Fail-closed: missing `org.json`, unreachable index, absent `profile` key all
  resolve to the prod profile.
- Strictest-wins across a multi-value effective set.
- Floor non-overridable: a table declaring `secret_scan: false` still resolves
  true and warns.
- `units.py check` rejects upward reclassification of a unit with poc build
  history.

## Out of scope

- Profiles keyed on axes other than `maturity`, and cross-axis composition.
  The table is `maturity`-only until something needs more.
- A fast path for `dev` or `internal` units, or a per-change (diff-size)
  lane. Those were considered and deferred.
- Any change to how `poc` code becomes production code. It is reverse-mapped
  with `spec-explorer` and rebuilt through the full pipeline, as today.
