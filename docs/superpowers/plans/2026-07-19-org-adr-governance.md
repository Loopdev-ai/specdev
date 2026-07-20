# Org ADR Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the SpecDev plugin repo the org's architectural repo of record: tier-scoped org ADRs (`governance/adr/`), product repos linked back at init, an `adr-checker` subagent loop that must be green before any PR opens, and a deterministic hash-based CI gate.

**Architecture:** Org ADRs carry YAML frontmatter (`applies_to` classifications); `gen_adr_index.py` compiles them into a single-fetch `index.json` (with per-ADR sha256 and the classification scheme embedded). Product repos hold `.specdev/org.json` (link) and `.specdev/adr/org-compliance.json` (verification manifest written by the `adr-checker` agent). CI's `check_org_adrs.py` re-fetches the index and deterministically proves the manifest is complete and hash-current.

**Tech Stack:** Python 3 stdlib only (matches existing `.specdev/tools/`), GitHub Actions, Claude Code plugin markdown (agents/commands/skills).

## Global Constraints

- Python tools: stdlib only, `#!/usr/bin/env python3`, argparse, exit-code driven, terse `WARN:`/`ERROR:` output (match `validate_spec.py`).
- All product-repo tooling must be **inert** when `org.json` is absent or still `REPLACE_ME` (match `compliance.yml` pattern).
- No `Date.now()`-style dynamic content in generated files beyond what tools compute at run time.
- Do not commit anything; leave changes for user review (repo owner reviews before commit).
- Classification names are lowercase slugs; `applies_to` values: `all`, `<name>`, or `<name>+` (that rank and above).
- Manifest entry statuses: exactly `met | not-applicable | superseded-by-local`.

---

### Task 1: Governance scheme, org ADR template, seed ADR

**Files:**
- Create: `governance/classification.json`
- Create: `governance/adr/TEMPLATE.md`
- Create: `governance/adr/ADR-0001-repo-classification.md`

**Interfaces:**
- Produces: `classification.json` schema `{"classifications": {"<name>": {"rank": int, "description": str}}}` consumed by Tasks 2 and 4; frontmatter keys `id, title, status, applies_to, scopes, summary` consumed by Task 2.

- [ ] **Step 1:** Write `governance/classification.json`:

```json
{
  "$comment": "Org repo classifications. Extensible: add entries with a unique name and rank. Rank orders tiers so an ADR can target '<name>+' (that rank and above). Referenced by governance/tools/gen_adr_index.py and, via index.json, by every product repo's check_org_adrs.py.",
  "classifications": {
    "poc": { "rank": 0, "description": "Proof of concept / spike. Disposable code; minimal governance." },
    "dev": { "rank": 1, "description": "Active development, not yet serving production traffic or real data." },
    "prod": { "rank": 2, "description": "Production: serves real users or handles real data. Full governance applies." }
  }
}
```

- [ ] **Step 2:** Write `governance/adr/TEMPLATE.md` — YAML frontmatter with the six keys, body sections Context / Options / Decision / Consequences / **Conformance** (checkable statements the adr-checker verifies against). Full content in Task 1 notes below.

- [ ] **Step 3:** Write `governance/adr/ADR-0001-repo-classification.md` — accepted, `applies_to: [all]`, `scopes: [governance]`; defines the classification scheme and requires every SpecDev repo to declare one in `.specdev/org.json`. Its Conformance section: org.json exists, classification valid, org-adr-check workflow enabled.

- [ ] **Step 4:** Verify: files exist; frontmatter parses per Task 2's parser (checked in Task 2).

### Task 2: `gen_adr_index.py` + generated index

**Files:**
- Create: `governance/tools/gen_adr_index.py`
- Create (generated): `governance/adr/index.json`, `governance/adr/INDEX.md`

**Interfaces:**
- Consumes: Task 1 files.
- Produces: `index.json` shape `{"classifications": {...}, "adrs": [{"id","title","status","applies_to","scopes","summary","file","sha256"}]}` consumed by Task 4 and the adr-checker agent. CLI: `python governance/tools/gen_adr_index.py [--root .] [--check]`; `--check` exits 1 on invalid frontmatter or a stale committed index.

- [ ] **Step 1:** Implement: minimal frontmatter parser (scalars + `[a, b]` lists), validation (required keys, unique ids, status ∈ proposed/accepted/superseded, `applies_to` values resolve against classification.json incl. `all`/`name+`), sha256 of each file, write index.json (sorted, indent=2) + INDEX.md table.
- [ ] **Step 2:** Run `python governance/tools/gen_adr_index.py` → generates both files listing ADR-0001.
- [ ] **Step 3:** Run `python governance/tools/gen_adr_index.py --check` → exit 0. Break a frontmatter key in a scratch copy → `--check` exit 1 with named error. Restore.

### Task 3: Governance CI for this repo

**Files:**
- Create: `.github/workflows/adr-index.yml`

- [ ] **Step 1:** Workflow: on `pull_request`/`push` touching `governance/**`, checkout, setup-python, run `python governance/tools/gen_adr_index.py --check`. Comment: make it a required check so an org ADR can't merge with bad metadata or a stale index.

### Task 4: Product-repo link + deterministic gate

**Files:**
- Create: `assets/specdev/org.json`
- Create: `assets/specdev/tools/check_org_adrs.py`

**Interfaces:**
- Consumes: index.json shape from Task 2.
- Produces: org.json schema `{"governance_repo","ref","path","classification"}`; manifest schema `.specdev/adr/org-compliance.json` `{"governance_repo","ref","classification","entries":[{"id","status","sha256","evidence","justification"}]}`; CLI `python .specdev/tools/check_org_adrs.py [--root .] [--index FILE] [--write-manifest-stub]` — exit 0 inert/green, 1 red. Consumed by Task 5 workflow, Task 6 agent, Task 7/8 docs.

- [ ] **Step 1:** Write `assets/specdev/org.json`:

```json
{
  "$comment": "Link to the org's architectural repo of record. Filled by /specdev:init. All org-ADR tooling is inert while values are REPLACE_ME.",
  "governance_repo": "REPLACE_ME (owner/name of the SpecDev governance repo)",
  "ref": "main",
  "path": "governance/adr",
  "classification": "REPLACE_ME (poc | dev | prod | другой defined classification)"
}
```

  (Fix: keep the comment fully English — "or another defined classification".)

- [ ] **Step 2:** Implement `check_org_adrs.py`: load org.json (missing/REPLACE_ME → print inert notice, exit 0); fetch `https://raw.githubusercontent.com/{repo}/{ref}/{path}/index.json` via urllib with optional `GITHUB_TOKEN` bearer header, or `--index` local file; validate classification against embedded scheme; applicable = accepted ∧ (`all` ∨ exact ∨ rank≥ for `name+`); validate manifest (exists, per-entry status set, justification required for non-met, sha256 equality vs index); `WARN` for manifest entries no longer applicable; summary + exit code.
- [ ] **Step 3:** Fixture verification in scratchpad: inert (no org.json) → 0; green (matching manifest, `--index` file) → 0; missing entry → 1; stale sha → 1; `not-applicable` w/o justification → 1; unknown classification → 1.

### Task 5: Product-repo CI workflow

**Files:**
- Create: `assets/workflows/org-adr-check.yml`

- [ ] **Step 1:** On `pull_request` (all branches — covers Spec PRs and Impl PRs); job `if: hashFiles('.specdev/org.json') != ''`; checkout + python; run `python .specdev/tools/check_org_adrs.py` with `GITHUB_TOKEN` env (note in comments: use a PAT secret if the governance repo is private to the runner token). Header comment: deterministic Gate — a PR cannot merge while an applicable org ADR is unverified or stale; mark required once org.json is configured.

### Task 6: `adr-checker` agent

**Files:**
- Create: `agents/adr-checker.md`

**Interfaces:**
- Consumes: org.json, index.json, manifest schemas (Task 4).
- Produces: agent name `adr-checker` referenced by SKILL.md/build.md/init.md; report format Verdict green/red + per-ADR table + violations; on green writes/updates `.specdev/adr/org-compliance.json`.

- [ ] **Step 1:** Frontmatter matching other agents (`tools: Bash, Read, Grep, Glob, Write`, `model: inherit`). Behavior: single index fetch; filter by classification; fetch full ADR bodies **only** for applicable ADRs that are new or hash-changed vs the manifest; judge each ADR's Conformance section against the repo (spec, local ADRs, config, code searches); return terse verdict (qa-verifier style); update manifest only when green for that entry; never dump ADR bodies into the return. Red report lists per-violation: ADR id, conformance statement, what in the repo violates it, suggested fix owner (spec vs component).

### Task 7: Wire the pipeline (skill + build command)

**Files:**
- Modify: `skills/specdev/SKILL.md` (Architecture step ~L33-46, Spec PR step ~L47-49, build loop step 6, add to agents/guardrails/helper lists)
- Modify: `commands/build.md` ("After the final wave" section L61-72, guardrails)

- [ ] **Step 1:** SKILL.md: Architecture step — if org.json configured, dispatch `adr-checker` before finalizing local ADRs; local decisions conflicting with an applicable org ADR need a justified `superseded-by-local` + local ADR. Spec PR step — adr-checker must be green before opening the Spec PR. Step 6/7 — after final wave: automated loop `adr-checker → component-builder fixes → qa-verifier → adr-checker` until green; **the Implementation PR must not be opened before green**. Context discipline: coordinator never reads org ADR bodies.
- [ ] **Step 2:** build.md: insert the loop into "After the final wave" between the pre-PR QA dry run and the PR announcement; add guardrail "Never announce PR readiness while adr-checker is red — loop automatically, no user prompt needed between iterations."

### Task 8: Wire init

**Files:**
- Modify: `commands/init.md` (steps after 3b, post-install checklist)

- [ ] **Step 1:** New step 3c: org governance link — ask for governance repo (`owner/name`), ref, and this repo's classification (list options by fetching classification.json if reachable, else poc/dev/prod); write `.specdev/org.json`; leave REPLACE_ME (inert) if the org has no governance repo.
- [ ] **Step 2:** New step 3d: append marked block to repo CLAUDE.md (create if absent) between `<!-- specdev:org-adr-governance -->` markers: states classification, repo-of-record pointer, and the rule — before any architectural decision or PR, dispatch `adr-checker`; never open a PR while it reports violations; idempotent (replace existing block).
- [ ] **Step 3:** Checklist additions: mark `org-adr-check` a required status check; if governance repo is private, add a `GOVERNANCE_TOKEN` secret and wire it in the workflow env.

### Task 9: README + version + final verify

**Files:**
- Modify: `README.md` (What-you-get table, new "Org ADR governance" section, Layout)
- Modify: `.claude-plugin/plugin.json` (version 0.3.0 → 0.4.0)

- [ ] **Step 1:** README: new section — repo of record, classifications, applies_to scoping, checker loop (PRs held until green), deterministic hash gate, inert-until-configured; table rows for `adr-checker`, `governance/`, `org-adr-check.yml`; Layout tree updated.
- [ ] **Step 2:** Bump plugin version to 0.4.0.
- [ ] **Step 3:** Final verify: re-run `gen_adr_index.py --check` (0), re-run fixture suite for `check_org_adrs.py`, grep that every new cross-reference (`adr-checker`, `org.json`, `org-compliance.json`, `org-adr-check`) resolves consistently across skill/commands/agents/README.

## Self-review

- Spec coverage: repo-of-record (T1-3), classification scoping (T1/T2/T4), init link-back + CLAUDE.md (T8), checker loop before PRs (T6/T7), deterministic CI gate (T4/T5), automation between PRs (T7 wording), docs (T9). No gaps.
- Placeholders: none (full contents authored at execution; JSON shown verbatim above; Step-1 fix note for the org.json comment applied at write time).
- Naming consistency: `adr-checker`, `org.json`, `org-compliance.json`, `org-adr-check.yml`, `index.json` used identically across tasks.
