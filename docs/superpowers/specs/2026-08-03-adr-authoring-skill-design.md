# Design: `adr` skill — guided ADR authoring with conflict detection

**Date:** 2026-08-03
**Status:** Approved (brainstorming) — ready for implementation planning

## Problem

SpecDev consumes ADRs everywhere and authors them nowhere. Step 4 of the
`specdev` skill says "Record decisions as `.specdev/adr/ADR-###.md`"; the
`adr-checker` agent verifies a unit against org ADRs; `gen_traceability.py`
links REQ → ADR; `validate_spec.py` counts accepted ADRs. But nothing guides
either a human or Claude through *writing* one, and nothing checks that a new
ADR is consistent with the ADRs already accepted.

Two failure modes follow. First, thin ADRs: a title, a paragraph, no rejected
options, no consequences — a record that a decision happened, not a record of
why. Second, silent contradiction: ADR-007 mandates stateless JWTs while
ADR-003 already mandated server-side sessions, and nothing notices, because
no tool compares decisions to each other.

This design adds a skill that interviews for a good ADR and refuses to write a
conflicting or thin one, for both ADR layers SpecDev already has.

## Goals

- One skill that authors **both** local unit ADRs (`.specdev/adr/`) and org
  ADRs (`governance/adr/`), detecting which from the repo.
- A Socratic interview that produces a genuine ADR — real rejected options,
  real consequences — and a lint that hard-fails a thin one.
- Deterministic detection of structural conflicts (duplicate ids, broken
  supersession chains) and deterministic *shortlisting* of semantic-conflict
  candidates, so the skill judges a handful of ADRs rather than a directory.
- A forced resolution when a real conflict is found: revise, supersede, or
  narrow scope — never "write it anyway".
- Zero breakage of already-installed `.specdev/` directories.

## Non-goals (YAGNI)

- A CI gate for the new checks. `spec-validate.yml` and
  `gen_adr_index.py --check` stay as they are; the skill is the entry point.
  (Revisit once the checks have proven themselves in practice.)
- Migrating existing ADRs to the new frontmatter. Old prose-only ADRs remain
  valid and parse via fallback.
- Dispatching the `adr-checker` agent from this skill. Authoring and
  verification stay separate jobs; `specdev` steps 4 and 7 dispatch the checker.
- Changing `validate_spec.py`, `gen_traceability.py`, or `check_org_adrs.py`.
- ADR supersession across layers (a local ADR can never supersede an org ADR;
  that path is the existing `superseded-by-local` deviation record).

## Key decisions (from brainstorming)

1. **One skill, both layers.** Mode is detected: `governance/adr/` +
   `classification.json` → org mode; `.specdev/adr/` (or
   `<unit>/.specdev/adr/`) → local mode. When both are present (the SpecDev
   repo itself), ask once — it is genuinely ambiguous.
2. **Tool narrows, skill judges.** A new stdlib-only `adr.py` does what is
   decidable: id collisions, supersession-chain integrity, lint, and an
   overlap shortlist. Semantic conflict is the skill's judgment, applied to
   the shortlist only.
3. **One mode-aware tool, not two.** `assets/specdev/tools/adr.py` with
   `--mode local|org`. A governance repo is a copy of the plugin repo, so the
   file is already checked out there; product repos get it at
   `.specdev/tools/adr.py` via `/specdev:init`.
4. **Local ADRs gain frontmatter and keep their prose lines.** Additive, so
   existing tooling and existing repos are untouched; `lint` makes the
   redundancy a checked invariant.
5. **Lint is a hard gate.** The skill does not write the file until lint
   passes. Fewer than two real options, empty consequences, missing REQ link,
   or leftover placeholder text blocks the write.
6. **Triggering is explicit and wired.** `/specdev:adr` for direct use, plus a
   mandate in `specdev` step 4 so Claude routes through the skill mid-pipeline
   instead of hand-writing the file.

## Architecture

### New files

| Path | Purpose |
|------|---------|
| `skills/adr/SKILL.md` | the skill — interview, mode detection, conflict adjudication |
| `commands/adr.md` | `/specdev:adr` slash command |
| `assets/specdev/tools/adr.py` | stdlib-only CLI: `next-id`, `lint`, `conflicts` |
| `tests/test_adr.py` | pytest suite for the CLI + back-compat proof |

### Modified files

| Path | Change |
|------|--------|
| `skills/specdev/SKILL.md` | step 4 (and the deployment-platform ADR, and step 7's deviation ADR) must invoke `specdev:adr` |
| `governance/adr/TEMPLATE.md` | add optional `supersedes` / `superseded_by` frontmatter keys |
| `assets/specdev/adr/ADR-001.md`, `assets/specdev/adr/ADR-deployment-platform.md` | the seeded local templates gain the frontmatter block, keeping their placeholder `**Status:**` line intact so `validate_spec.py` still skips them as unfilled templates |
| `commands/init.md` | note that `adr.py` ships with the tools (no logic change — it copies the whole dir) |
| `README.md` | skill table row + an ADR-authoring section |
| `.claude-plugin/plugin.json` | version 0.6.0 → 0.7.0, description mentions the skill |

## Local ADR format

Frontmatter is **added**; the existing prose lines **stay**:

```yaml
---
id: ADR-003
title: Sessions are stateless JWTs
status: accepted          # proposed | accepted | superseded
date: 2026-08-03
relates_to: [REQ-002, REQ-005]
scopes: [auth, session]
supersedes: [ADR-001]     # optional
superseded_by: ADR-007    # optional; required when status is superseded
---

# ADR-003 — Sessions are stateless JWTs

**Status:** accepted
**Relates to:** REQ-002, REQ-005

## Context / ## Options / ## Decision / ## Consequences
```

The duplication is deliberate. [`gen_traceability.py`](../../../assets/specdev/tools/gen_traceability.py)
scrapes the prose `Relates to:` line and
[`validate_spec.py`](../../../assets/specdev/tools/validate_spec.py) greps
`**Status:** accepted`; changing either would break every already-installed
`.specdev/`. New ADRs therefore carry both representations, and `lint` fails on
**drift** between them — the redundancy becomes an enforced invariant rather
than a trap. An ADR without frontmatter parses from prose alone, so existing
ADRs stay valid.

Org ADRs keep the shape defined in `governance/adr/TEMPLATE.md` (frontmatter +
`Conformance`), gaining only `supersedes` / `superseded_by` — today
`status: superseded` exists with no field recording *by what*.

## `adr.py` CLI

Stdlib only, consistent with the rest of `.specdev/tools/`. Frontmatter parsing
reuses the minimal-YAML approach already proven in
`governance/tools/gen_adr_index.py` (scalars and inline lists).

```bash
python .specdev/tools/adr.py next-id   [--root R] [--mode local|org]
python .specdev/tools/adr.py lint      [--file F] [--root R] [--mode M]
python .specdev/tools/adr.py conflicts [--file F] [--root R] [--mode M] [--json]
```

`--root` carries the governed unit in a monorepo, matching every other SpecDev
tool. `--mode` defaults to auto-detection.

### `next-id`

Next free id in the target directory — `ADR-###` (3 digits) in local mode,
`ADR-####` (4) in org mode. Gaps in the sequence are not reused.

### `lint` — the quality gate

Exit 1 on any of:

- frontmatter missing or a required key empty
  (local: `id`, `title`, `status`, `date`, `relates_to`;
  org: `id`, `title`, `status`, `applies_to`, `summary`)
- `id` disagrees with the filename
- `status` outside `proposed | accepted | superseded`
- placeholder residue: `<...>` angle-bracket stubs, `TBD`, `TODO`, or a
  verbatim template line
- `## Options` has fewer than two numbered options, or an option has an empty
  Pros or Cons
- `## Decision` is empty, or names none of the options
- `## Consequences` lacks a non-empty Positive **or** a non-empty
  Negative / risks
- local: a `relates_to` REQ that does not exist in the unit's `spec.md`
- local: frontmatter/prose drift on `status` or `relates_to`
- org: `## Conformance` has no checkbox items, or an item is empty/placeholder

`lint` is a per-file check; anything requiring the whole directory (including
`supersedes` / `superseded_by` resolution) belongs to `conflicts` below.

Whether a Conformance item is *objectively checkable* is judgment, not
grammar — the tool checks presence, the skill checks quality.

### `conflicts` — two tiers

**Hard errors (exit 1).** No judgment required:

- duplicate ids in the directory
- `supersedes: X` where X does not exist, or X is already superseded by a
  different ADR
- non-reciprocal supersession: A supersedes B but B lacks `superseded_by: A`
- `status: superseded` without `superseded_by`, or `superseded_by` on an ADR
  whose status is not `superseded`
- an ADR still `accepted` that another ADR claims to supersede

**Shortlist (exit 0).** Candidates needing judgment, each printed as id, title,
file, one-line summary, and the reason flagged:

- another **accepted** ADR sharing at least one `scopes` tag
- local: sharing at least one `relates_to` REQ
- org: overlapping `applies_to` — two entries overlap when some classification
  satisfies both, using the axis / rank / `&` semantics of
  `check_org_adrs.py` (list entries OR, `&` AND, `value+` = that rank and above
  on an ordered axis)

`--json` emits the shortlist machine-readably for the skill to consume without
re-parsing prose.

## Conflict adjudication (the skill's half)

The skill reads the Decision section of shortlisted ADRs **only** — never the
whole directory — and asks whether the new decision contradicts each one. On a
real contradiction it must land on one of three resolutions and will not write
the file otherwise:

1. **Revise** the new ADR to agree with the accepted one.
2. **Supersede** the old one — an atomic pair of edits: `supersedes` on the new
   ADR, and `status: superseded` + `superseded_by` on the old (both
   representations, prose and frontmatter). Re-run `conflicts` to prove the
   chain is reciprocal.
3. **Narrow** `scopes` / `applies_to` so the overlap genuinely disappears —
   only when the two decisions really do address different things.

### Cross-layer check (local mode)

A local decision can contradict a binding org ADR. Cheap path only: read
`.specdev/adr/org-compliance.json` and fetch the org `index.json` (one file,
summaries suffice). If the new ADR contradicts a binding org ADR, the skill
either revises it or — if the deviation is deliberate and justified — writes it
in the `superseded-by-local` shape and tells the user to re-run `adr-checker`
before opening a PR. The skill never dispatches that agent itself.

## The interview

One question at a time, in order:

1. What is the decision, in one present-tense sentence?
2. What forces it? (constraints, deadlines, existing architecture,
   non-functional requirements)
3. Local: which REQs does it serve? Org: who does it bind (`applies_to`)?
4. What did you seriously consider and reject, and why?
5. What does this cost you? (negative consequences, risks, follow-ups)
6. Org only: how would a checker *prove* a repo conforms? Each answer becomes
   one Conformance item naming a file, config value, or code pattern.

Fewer than two real options means the decision has not actually been made. The
skill pushes back once; if the user stands by it, the ADR is written
`status: proposed` rather than `accepted`.

**Claude-authored mode.** When the skill is invoked mid-pipeline (specdev step
4) rather than by a human at a keyboard, Claude answers the ladder from the
spec and the architecture context, and asks the user only where the spec is
silent. Lint and the conflict gate are identical — nothing is relaxed because
the author is a machine.

## Testing

`tests/test_adr.py`, pytest, matching the existing suite's style (temp dirs,
subprocess or direct import of the CLI):

- `next-id` on an empty directory, a contiguous one, and one with gaps, in both
  modes
- `lint` rejects one fixture per failure class above, and accepts a good ADR of
  each shape
- `conflicts` catches every hard-error class: duplicate id, dangling
  `supersedes`, non-reciprocal pair, already-superseded target, accepted target
- shortlist true-positives (scope overlap, REQ overlap, `applies_to` overlap
  including `+` rank and `&` conjunction) **and** true-negatives — disjoint
  ADRs must not be flagged, or the shortlist is noise
- **back-compat proof:** an ADR written by `adr.py` is fed to
  `gen_traceability.parse_adrs()` and to `validate_spec.py`, asserting both
  still recognize its REQ links and accepted status; and a prose-only ADR with
  no frontmatter still lints and shortlists

## Risks

- **Shortlist noise.** Over-broad `scopes` tagging makes every ADR overlap
  every other. Mitigated by the true-negative tests and by the skill prompting
  for narrow, specific scope tags during the interview.
- **Format drift between the two representations.** Mitigated by the drift
  check in `lint`, which is the reason the redundancy is acceptable at all.
- **Interview friction.** A user who already knows the decision cold still
  answers six questions. Accepted deliberately: the friction is the feature,
  and `status: proposed` is the escape hatch for a decision that is not ready.
