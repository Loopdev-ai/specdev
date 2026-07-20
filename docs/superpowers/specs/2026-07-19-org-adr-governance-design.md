# Org ADR Governance — design

**Date:** 2026-07-19
**Status:** approved (design validated in conversation prior to this doc)

## Problem

SpecDev's kit is copy-on-init: every product repo owns its own ADRs and nothing
links back to a central architecture authority. Three gaps:

1. No repo of record for cross-cutting (org-wide) ADRs.
2. No way to scope an ADR to a class of repos (POC vs Dev vs Prod).
3. Initialized repos are not pointed at a centralized source of truth, and
   nothing verifies a feature complies with org decisions before a PR opens.

## Design overview

The **SpecDev plugin repo becomes the architectural repo of record**. Org ADRs
live in `governance/adr/` with machine-readable frontmatter; a generated index
makes tier-scoped, context-light consumption cheap. Product repos are linked
back at init time via `.specdev/org.json` and a marked CLAUDE.md section.
Compliance is enforced twice:

- **Authoring time (model-driven, automated):** an `adr-checker` subagent
  fetches the index, filters ADRs applicable to this repo's classification,
  judges the work against them, and writes a verification manifest. The
  spec/build loops **must not open a PR** until this checker returns green —
  red verdicts loop back through `component-builder` fixes automatically.
- **Review time (deterministic CI gate):** `check_org_adrs.py` re-fetches the
  index at the pinned ref and fails the PR check if any applicable ADR lacks a
  current manifest entry (content-hash match), or an exclusion lacks
  justification. Determinism comes from hashes, not model judgment.

Everything between human PR reviews is automated; subagents hold the heavy
context (full ADR bodies, code comparison) and return only verdicts.

## Components

### 1. Governance layer (this repo — the repo of record)

- `governance/classification.json` — the classification scheme: `poc`, `dev`,
  `prod` seeded, arbitrary additional classifications allowed. Each has a
  description and an ordering (`rank`) so ADRs can also target "dev and above".
- `governance/adr/TEMPLATE.md` — org ADR template. YAML frontmatter:
  `id`, `title`, `status` (proposed|accepted|superseded), `applies_to`
  (list of classifications or `all`, or `<tier>+` meaning that rank and above),
  `scopes` (free tags: deployment, auth, data, …), `summary` (one line).
  Body adds a **Conformance** section: concrete, checkable statements of what a
  complying repo looks like — this is what the checker verifies against.
- `governance/adr/ADR-0001-repo-classification.md` — seed accepted ADR that
  defines the classification scheme itself (`applies_to: [all]`).
- `governance/tools/gen_adr_index.py` — stdlib-only. Parses frontmatter,
  validates it (`--check`: required fields, unique ids, classifications exist,
  valid status), and writes:
  - `governance/adr/index.json` — machine index: per ADR the frontmatter plus
    `file` and `sha256` of the file content (lets consumers detect staleness
    with a single fetch);
  - `governance/adr/INDEX.md` — human table.
- `.github/workflows/adr-index.yml` — this repo's own CI: on changes under
  `governance/`, run `--check` and fail if the committed index is stale.

### 2. Product-repo assets (copied by `/specdev:init`)

- `assets/specdev/org.json` — link-back template:
  `{"governance_repo": "REPLACE_ME owner/name", "ref": "main",
    "classification": "REPLACE_ME", "path": "governance/adr"}`.
  All tooling is inert while values are `REPLACE_ME` (compliance.yml pattern).
- `assets/specdev/tools/check_org_adrs.py` — the deterministic gate. Reads
  `org.json` (inert-exit 0 if unconfigured), fetches `index.json` at the pinned
  ref (raw.githubusercontent.com; `GITHUB_TOKEN` honored for private repos;
  `--index <file>` for offline/testing), computes the applicable set
  (status `accepted` AND classification match, honoring `all` and `tier+`),
  then validates `.specdev/adr/org-compliance.json`:
  - every applicable ADR has an entry;
  - entry status ∈ {met, not-applicable, superseded-by-local};
  - non-`met` entries carry a justification;
  - each entry's recorded `sha256` matches the index (ADR changed upstream →
    verification is stale → fail with "re-run adr-checker");
  - the repo's classification exists in the governance scheme.
- `assets/workflows/org-adr-check.yml` — runs the script on every PR (spec and
  implementation); skips cleanly when `org.json` is absent.

### 3. Verification manifest (written by the checker, verified by CI)

`.specdev/adr/org-compliance.json`:

```json
{
  "governance_repo": "org/specdev", "ref": "main", "classification": "prod",
  "entries": [
    {"id": "ADR-0001", "status": "met", "sha256": "…",
     "evidence": "spec.md REQ-002; .specdev/adr/ADR-003.md", "justification": ""}
  ]
}
```

The split: the **agent** makes the semantic judgment and records it with the
ADR's content hash; **CI** deterministically proves the judgment is complete
and current. A changed org ADR invalidates only its own entry.

### 4. `adr-checker` agent (`agents/adr-checker.md`)

Read-mostly subagent (Bash for fetching; Write only for the manifest).
Dispatched by the skill (a) during the Architecture step, (b) before opening
the Spec PR, (c) after the final build wave before the Implementation PR.
It: reads `org.json` → fetches `index.json` → filters by classification →
fetches full bodies only for applicable ADRs that are new or hash-changed →
compares their Conformance sections against the repo (spec, local ADRs, code)
→ returns a terse green/red verdict (qa-verifier style) and, on green, updates
the manifest. The coordinator never reads ADR bodies.

### 5. Pipeline wiring

- `SKILL.md`: org-check at Architecture; **hold both PRs until green**; the
  pre-Impl-PR loop is `adr-checker → component-builder fix → qa-verifier →
  adr-checker` repeated until green, fully automated.
- `commands/build.md`: the loop added to "After the final wave" — PR readiness
  is only announced after both qa-verifier and adr-checker are green.
- `commands/init.md`: configure `org.json` (governance repo, ref,
  classification — a decision the user confirms), append a marked
  `<!-- specdev:org-adr-governance -->` section to the repo's CLAUDE.md, add
  the required-check item to the post-install checklist.

## Error handling

- Unconfigured `org.json` → everything inert (no failures in repos that opt out).
- Fetch failure in CI → check fails loudly (fail-closed once configured).
- Unknown classification / malformed frontmatter → `--check` fails in the
  governance repo's own CI before consumers ever see it.
- Conflict the checker can't resolve (a genuine exception) → recorded as
  `superseded-by-local` **only** with a justification and a local ADR; CI
  enforces the justification's presence, humans judge it at PR review.

## Testing

Stdlib-only tools verified by running them: index generation + `--check` on the
seeded governance dir (pass and deliberately-broken fixtures), and
`check_org_adrs.py` against scratchpad fixtures covering inert, green, missing
entry, stale-hash, and missing-justification paths.

## Out of scope

- Push-based propagation (governance repo reaching into product repos).
- A semantic (model-run) CI step — the deterministic hash gate + PR review is
  the enforcement; a claude-code-action reviewer can be layered later.
- Retrofitting already-initialized repos (they can re-run `/specdev:init`,
  which is additive/no-overwrite).

## Addendum (2026-07-19): multi-axis classifications

Superseding the single-tier scheme above: `classification.json` now defines
**axes** (`{"axes": {"maturity": {"ordered": true, "values": {...ranked}},
"audience": {"ordered": false, "values": {...}}}}`), value names unique across
axes. A repo's `org.json` declares one value per axis
(`{"maturity": "prod", "audience": "internal"}`; a bare string is accepted
only for single-axis schemes — otherwise the gate errors and demands a full
declaration, fail-closed). ADR `applies_to`: list entries OR, `&` within an
entry AND, `<value>+` = rank-and-above on ordered axes only (validated by
`gen_adr_index.py --check`). `index.json` embeds `axes` in place of
`classifications`. Verified by a 15-case fixture suite (AND/OR/rank scoping,
missing-axis, unknown value/axis, plus all original inert/stale/justification
paths).
