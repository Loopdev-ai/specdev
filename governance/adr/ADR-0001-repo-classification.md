---
id: ADR-0001
title: Every repo declares a classification and links to the repo of record
status: accepted
applies_to: [all]
scopes: [governance]
summary: Each SpecDev-managed repo declares its classification (one value per axis, e.g. maturity poc/dev/prod and audience internal/customer) in .specdev/org.json and enables the org-adr-check gate; org ADRs bind repos by classification.
---

# ADR-0001 — Every repo declares a classification and links to the repo of record

## Context

Org ADRs are only enforceable if every repo (a) says what class of repo it is
and (b) points back at this repo of record so tooling can fetch the decisions
that bind it. Without a declared classification there is no way to scope a
decision to "production repos only" vs "everything including spikes".

## Options

1. **Per-repo declaration in `.specdev/org.json` (chosen)**
   - Pros: machine-readable, versioned with the repo, validated in CI, written
     once at `/specdev:init`.
   - Cons: can drift from reality (a POC that quietly became prod) — mitigated
     by PR review of any change to `org.json`.
2. **Central registry of repos in the governance repo**
   - Pros: single inventory.
   - Cons: push-model, goes stale, requires the governance repo to know every
     repo; contradicts the pull-based trust model.

## Decision

Each repo initialized with SpecDev declares its classification in
`.specdev/org.json` (`classification`), together with the governance repo
(`governance_repo`), `ref`, and `path`. The scheme is **multi-axis**
(`governance/classification.json`): each axis is one dimension of the repo's
identity, and a repo declares one value per axis — seeded axes are `maturity`
(`poc < dev < prod`, ordered) and `audience` (`internal`, `customer`);
both axes and values are extensible. Org ADRs declare `applies_to` in
frontmatter: list entries are OR, `&` within an entry is AND, and `<value>+`
means that rank and above on an ordered axis — e.g. `[prod]`, `[customer]`,
or `[customer & dev+]` — binding exactly the repos whose classification
matches.

## Consequences

- Positive: org ADRs can be scoped; tooling stays pull-based and deterministic.
- Negative / risks: misclassification weakens governance — reclassification is
  a PR-reviewed change like any other.
- Follow-ups: none.

## Conformance

- [ ] `.specdev/org.json` exists with no `REPLACE_ME` values and a
      `classification` declaring a defined value for every axis of the org
      scheme.
- [ ] `.github/workflows/org-adr-check.yml` is present and not weakened
      (still runs `check_org_adrs.py` on pull requests).
- [ ] `.specdev/adr/org-compliance.json` exists and passes
      `python .specdev/tools/check_org_adrs.py`.
