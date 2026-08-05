# Build Plan — <FEATURE NAME>

**Feature ID:** FEAT-XXX
**Status:** planning | building | qa | deployed
**Phase:**
**Spec PR:** #   **Impl PR:** #
**Started:**   **Updated:**

---

## Product Spec

_See [spec.md](spec.md). Approved <date>, Spec PR #._

## Architecture Decision

_See [adr/](adr/). Chosen: <ADR-### title>._

## Build Log

### Scaffold

| File | Purpose |
|------|---------|
|      |         |

### Wave ledger

_Filled by `/specdev:build`. One row per component per wave; a wave is done only
when every row is built and its `qa-verifier` verdict is green. This is the
resume point after a compact — a fresh session continues from the first
unfinished wave._

| Wave | Component | REQs covered | Tests (pass/fail) | qa-verifier | Commit |
|------|-----------|--------------|-------------------|-------------|--------|
| 1    |           |              |                   | green/red   |        |

### Open Items

- [ ]

## Deployment Facts

_Source of truth is `deploy.profile.json`; this table documents the destination
facts as they're discovered or decided during the build. Every fact must be
resolved before merge (the `preflight` job blocks deploy otherwise)._

| Fact | Value | Source (discovered/declared) | Verified |
|------|-------|------------------------------|----------|
| target |  |  |  |
| staging URL |  |  |  |
| production URL |  |  |  |
| <param e.g. app/namespace/image> |  |  |  |

## Security Notes

<auth / PII / network calls / secrets — findings from the security checkpoint>

## Environment Variables

| Var | Purpose | Set where |
|-----|---------|-----------|
|     |         |           |

## Findings

> **Required to finish a `poc` build.** A spike's deliverable is what it
> taught you, because the code itself is reverse-mapped and rebuilt rather
> than promoted. An empty section fails the build's terminal-state
> assertion, so `deploy-poc` never runs on a spike that left nothing behind.

- **What we set out to answer:** <from CHARTER.md>
- **What we learned:** <the answer, including "it doesn't work" — that is a result, not a failure>
- **What surprised us:** <anything the real spec must account for>
- **What the rebuild must do differently:** <concrete guidance for the dev/prod unit that replaces this>
