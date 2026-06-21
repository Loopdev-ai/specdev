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
