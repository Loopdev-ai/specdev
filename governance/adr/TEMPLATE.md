---
id: ADR-0000
title: <decision title>
status: proposed        # proposed | accepted | superseded
applies_to: [all]       # who this binds. Entries are OR; '&' inside an entry is AND. Values come from ../classification.json (any axis): all | prod | customer | dev+ ('+' = that rank and above, ordered axes only) | customer & dev+
scopes: []              # free tags, e.g. [deployment, auth, data-handling]
summary: <one sentence — this line is what repos read first, keep it self-contained>
---

# <id> — <title>

> Org-wide ADR. Copy this template to `governance/adr/ADR-####-<slug>.md`,
> fill every frontmatter key, then run
> `python governance/tools/gen_adr_index.py` and commit the regenerated index.
> `applies_to` decides which product repos must conform (see
> `../classification.json`); the **Conformance** section is what their
> `adr-checker` agent verifies, so make each item concretely checkable.

## Context

<forces and constraints driving the decision — org standards, compliance,
existing platforms, cost, team capacity>

## Options

1. **<Option A>**
   - Pros:
   - Cons:
2. **<Option B>**
   - Pros:
   - Cons:

## Decision

<the chosen option and the rationale tied to the Context>

## Consequences

- Positive:
- Negative / risks:
- Follow-ups:

## Conformance

A repo in scope conforms when: (each item must be objectively checkable — a
file that exists, a config value, a pattern present/absent in code)

- [ ] <checkable statement 1>
- [ ] <checkable statement 2>

Exceptions: a repo that cannot conform records `superseded-by-local` in its
`.specdev/adr/org-compliance.json` with a justification **and** a local ADR
explaining the deviation; both are reviewed on its next PR.
