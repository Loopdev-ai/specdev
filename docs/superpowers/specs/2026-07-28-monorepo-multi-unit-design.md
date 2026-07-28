# Design: Monorepo support — multiple governed units in one repository

**Date:** 2026-07-28
**Status:** Approved (brainstorming) — ready for implementation planning
**Origin:** Adopter feedback from Faro Health, grounded against `Loopdev-ai/specdev@48323ef`

## Problem

SpecDev's unit of governance is the **repository**. Every layer assumes exactly
one of everything per repo: one `.specdev/org.json` with one `classification`,
one `spec.md`, one `run.json`, one in-flight build, one set of workflows with
root-absolute paths.

A repository holding several projects at different maturities therefore cannot
be classified correctly, and the failure is asymmetric — the wrong choice is
the dangerous one. Given a repo with a dozen subprojects where some are
throwaway spikes and some touch real systems and real data:

- Declare `poc`, and the production subprojects silently escape the org ADRs
  that would otherwise bind them, plus the compliance gate.
- Declare `prod`, and every spike must clear a production bar or carry a
  `superseded-by-local` exception — which is how teams learn to route around
  governance entirely.

There is no third option today. This design adds one.

## The seam that already exists

Seven of the eight tools under `assets/specdev/tools/` already accept `--root`
and resolve every path beneath it:

| Accepts `--root` | Does not |
|---|---|
| `check_org_adrs`, `validate_spec`, `gen_traceability`, `gen_compliance`, `detect_deploy`, `arch_config`, `run_manifest` | `deploy.py` |

The tools are already unit-relative; it is the **orchestration around them**
that is repo-absolute. Every workflow invokes `python .specdev/tools/<tool>.py`
with no `--root`, and `spec-validate.yml` path-filters on literal strings.
This makes the change substantially smaller than it first appears.

## Goals

- A repository may contain one or more **governed units**, each with its own
  classification, spec, compliance artifacts, traceability, and build lifecycle.
- The existing single-root layout keeps working **byte-identically**, with no
  new files and no migration required.
- Governance cannot be laundered by relocating code into a less-governed unit.
- Per-unit verdicts are visible individually; overall status is one check.
- Concurrent builds in different units do not serialise against each other.

## Non-goals (YAGNI)

- **Changing the governance repo side.** `gen_adr_index.py`,
  `classification.json`, the ADR frontmatter format, `applies_to` matching, and
  the `sha256` staleness mechanism all work as-is. Org ADRs bind *units* rather
  than *repos* — a documentation relabel, not a code change.
- **Merging per-unit artifacts.** Explicitly rejected; see decision 6.
- **Pure glob discovery** with no registry. Rejected; see decision 2.
- **Cross-unit spec or component sharing.** A unit is self-contained. Shared
  code is its own unit with a declared dependency.
- **Automatic dependency inference** from imports/build files. Dependencies are
  declared explicitly in the registry.

## Key decisions (from brainstorming)

### 1. A governed unit is a directory containing a `.specdev/`

A repo has one or more. Today's single-root layout is the degenerate case:
`unit_paths(root)` returns the registry's units, or `["."]` when there is no
registry. That one expression is the entire backward-compatibility story.

### 2. Discovery is an explicit registry, validated against a glob

`.specdev/units.json` at the repo root. An explicit list rather than pure
globbing for `**/.specdev/`, because globbing turns a vendored copy or a test
fixture into a governed unit by accident.

Both directions of drift are bugs, so both fail: a `--check` mode errors when a
discovered `.specdev/` is missing from the registry **and** when a registered
unit has no `.specdev/`. This mirrors `gen_adr_index.py --check` failing on a
stale index — the repo already has the right idiom for this.

An `ignore` glob list excludes vendored trees and fixtures deliberately rather
than by omission.

### 3. Repo-wide config splits from per-unit config (hybrid)

`governance_repo`, `ref` and `path` are properly repo-wide — letting two units
pin different governance refs is a footgun with no upside. `classification` is
properly per-unit.

- **Root `.specdev/units.json`** carries the governance link and the unit list
  (with `depends_on`).
- **Per-unit `<unit>/.specdev/org.json`** carries only `classification`.
- If a per-unit `org.json` redeclares the link and it disagrees with the
  registry, that is a **hard error**, not a silent precedence rule.
- A single-root repo keeps its existing `org.json` (link + classification)
  untouched and never grows a `units.json`.

```json
// .specdev/units.json — multi-unit only
{
  "schema_version": 1,
  "governance_repo": "faro/governance",
  "ref": "main",
  "path": "governance/adr",
  "ignore": ["**/vendor/**", "tests/fixtures/**"],
  "units": [
    "demos",
    "infrastructure",
    {"path": "soc-automation", "depends_on": ["infrastructure"]}
  ]
}
```

A bare string is a unit with no declared dependencies; the object form is
required only to declare `depends_on`.

### 4. Effective classification propagates dependency-ward

**Dependents pull their dependencies up**, transitively. If a `prod` unit
depends on unit X, X is effectively `prod`, because anything a production
system imports is inside the production blast radius.

This is deliberately the reverse of the direction proposed in the originating
feedback, which specified "the max over its own declaration and every unit it
declares a dependency on". That rule does not achieve its own stated goal.
Trace the laundering scenario through it:

```
risky code moved to  poc/risky/     (declared poc)
prod-svc  depends_on poc/risky

Feedback's rule:  prod-svc effective = max(prod, poc) = prod   (unchanged)
                  poc/risky effective = poc                    (still ungoverned)
                  -> laundering SUCCEEDS

This design:      poc/risky effective = prod                   (governed)
                  -> laundering BLOCKED
```

The converse direction — a `poc` demo importing a `prod` unit — is a real but
secondary risk (prod credentials reachable from a spike), and is **not**
escalated here: reading a production library does not make a spike production,
and escalating it cascades classification across a repo's whole connected
component until teams stop declaring dependencies at all.

Mechanics:

- Classification becomes `axis -> set[value]` internally. Ranked axes collapse
  to the max rank; unordered axes union.
- For a single-unit repo every set has size 1 and behaviour is bit-identical.
- Cycles in `depends_on` are a hard error, not a fixpoint iteration.
- When effective exceeds declared, the tool **prints the escalation and the
  edge that caused it**. Silent escalation is unexplainable at the point of
  failure.

### 5. `units.py` is the only module that knows about multiplicity

```
load_registry(root)       -> dict | None
unit_paths(root)          -> list[str]          # registry units, or ["."]
discover(root)            -> list[str]          # glob, minus ignores
check(root)               -> exit 1 on drift    # both directions
effective(root, axes)     -> {unit: {axis: {values}}}
parse_ref(ref, registry)  -> (unit, feat)
```

Every other tool and workflow calls into this. No second place learns the
layout rules.

### 6. Per-unit artifacts are never merged

Compliance in particular: a Statement of Applicability carries a **scope
statement**, and merging two units' SoAs produces a document that is true of
neither. The originating feedback reports 59 of 93 ISO 27001 Annex A controls
being excludable only because the SoA's scope was a single repository.

Same for `traceability.md`: per-unit, with an optional rolled-up index that
links out rather than concatenating.

### 7. `org-adr-check` is never path-filtered

The staleness half of `check_org_adrs.py` is **not diff-driven**. An upstream
ADR edit invalidates every unit's recorded `sha256` with no local change at
all. Today `org-adr-check.yml` has no path filter, so it always runs and always
catches this.

Introducing per-unit path filters would lose that property. Therefore
`org-adr-check` matrixes over **all** units, unfiltered, on every PR — this is
a hard constraint, not a performance trade-off. Getting it wrong yields a repo
that looks green for months while its verifications rot.

Additionally, because `ref` may be a moving branch, governance can change with
zero repo activity. A nightly `specdev-sweep.yml` runs the full unfiltered gate
set across all units and opens/updates an issue on failure.

### 8. The aggregate summary job is the required check

Matrix legs produce dynamic check names (`org-adr-check (infrastructure)`),
which GitHub branch protection cannot require — a newly added unit would
silently add an unrequired check.

So each gate workflow ends with an `if: always()` **`summary` job** that
renders the per-unit verdict table into the job summary and fails if any leg
failed. That job is the single required status check. Individual legs carry the
detail; the summary carries the verdict.

### 9. Build routing by validated branch segment

`spec/<unit>/<name>` and `poc/<unit>/<name>`. The segment is authoritative, and
resolution is back-compatible:

| Ref | Registry | Resolves to |
|---|---|---|
| `spec/add-retry-logic` | absent | unit `.`, feat `add-retry-logic` |
| `spec/infrastructure/vpc-peering` | unit exists | unit `infrastructure`, feat `vpc-peering` |
| `spec/foo/bar` | no unit `foo` | unit `.`, feat `foo/bar` |

A segment is read as a unit **only** when a registry exists and the segment
matches a registered unit. A PR whose changed files fall outside its named unit
fails the gate, so the branch name cannot lie about scope.

### 10. Build concurrency is per-unit

- `<unit>/.specdev/run.json` and `<unit>/.specdev/BUILD.md`. The
  one-in-flight-build-per-repo invariant becomes one per unit.
- `concurrency: specdev-build-<unit>` — parallel across units, serialised
  within one.
- Release tags namespaced per unit (`poc-<unit>-<date>-<sha7>`), which today
  would collide.

**Residual risk, named explicitly:** concurrent builds still push to `main`
independently (traceability bot commits, poc self-merges). Per-unit concurrency
groups do not prevent that race. Mitigation is rebase-and-retry on push,
combined with restricting each build's commits to its own unit directory —
which makes conflicts impossible in practice rather than merely unlikely.

## Bugs fixed in passing

Found while grounding the feedback; both block multi-unit and are wrong today:

1. **`gen_traceability.py:144`** — `--out` defaults to the root-absolute
   `.specdev/traceability.md`, so the tool ignores its own `--root` and writes
   to the repo root regardless. Becomes `root`-relative.
2. **`deploy.py:250`** — the only tool with no `--root`. Gains one.

Also unit-relative-ised: `.specdev/specs/**` (archived specs) and the
`paths-ignore` filters in `post-dev-qa.yml` and `compliance.yml`, all currently
written as root-absolute literals.

## Out of scope for this branch

Six unrelated findings from the same adoption report land on a separate branch
off `main`, ahead of this work: unrestricted agent tool surface in
`specdev-build.yml`, the `gitleaks-action` paid-licence fail-closed,
`secrets` being unavailable in `jobs.<id>.steps.if`, `ci_get` returning the
string `"None"` at exit 0, four `echo "TODO"` QA gates reporting green, and the
undeclared Python ≥3.10 requirement.

Note one correction to that report: the missing `--allowedTools` is **not**
`deploy-poc.yml`-specific. `deploy-poc.yml` runs no agent at all. The
unrestricted agent is `specdev-build.yml:114`, which is the **prod** path too —
a broader finding than reported.

## Components

| Component | Responsibility | Depends on |
|---|---|---|
| `tools/units.py` | Registry load, discovery, drift check, effective classification, ref parsing, migration | — |
| `tools/check_org_adrs.py` | Set-valued classification matching; `--all-units` | `units.py` |
| `tools/gen_compliance.py` | Per-unit SoA; rolled-up index | `units.py` |
| `tools/gen_traceability.py` | Unit-relative `--out`; rolled-up index | `units.py` |
| `tools/deploy.py` | `--root`; per-unit deploy profile | — |
| `tools/run_manifest.py` | Per-unit `run.json`; unit-aware init | `units.py` |
| Gate workflows | `discover` → matrix → `summary` | `units.py` |
| `specdev-sweep.yml` | Nightly unfiltered all-unit sweep | `units.py` |
| `agents/adr-checker.md` | Unit root parameter replacing two hardcoded paths | — |

## Migration

`python .specdev/tools/units.py migrate --unit <path>` moves a root `.specdev/`
into a subdirectory, writes the registry, and splits `org.json` into
registry-link plus per-unit classification. Without a tool, every adopter
hand-rolls this differently.

## Testing

TDD, in `tests/test_units.py`, extending the existing pytest suite:

- **Back-compat identity** — no registry ⇒ every tool resolves exactly as today.
- **Registry drift** — `--check` fails on discovered-but-unregistered and on
  registered-but-missing.
- **Cycle detection** — `depends_on` cycle is a hard error.
- **Ref parsing** — including the ambiguous `spec/foo/bar` case.
- **Laundering regression** — a `prod` unit depends on a `poc`-declared
  directory; assert that directory's effective classification is `prod`. This
  is the test that encodes decision 4 and must not be deleted.
- **Link disagreement** — per-unit `org.json` contradicting the registry fails.
- **Set-valued matching** — unordered-axis union and ranked-axis max against
  `applies_to` entries.

## Phasing within the branch

Both phases land on `feat/monorepo-support`, sequenced so the branch is
reviewable in two halves:

- **Phase 1 — verification.** `units.py`, `--root` threading, matrixed
  verification gates, the summary job, the nightly sweep. Mostly mechanical,
  because the tools already take `--root`. This alone unblocks governing a
  monorepo.
- **Phase 2 — build and deploy.** Per-unit `run.json`, branch-segment routing,
  per-unit concurrency and tags, `--root` on `deploy.py`, per-unit deploy
  profiles. Contains every genuinely hard concurrency question.
