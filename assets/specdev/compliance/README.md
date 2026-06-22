# SpecDev compliance layer

Build software that can be **attested against control frameworks** — ISO/IEC
27001, ISO/IEC 42001, SOC 2 Type II, NIST 800-53 — without bolting governance on
at the end. Compliance is modelled as a **second traceability axis** on top of
the one SpecDev already keeps:

```
requirements axis :  REQ  ──▶ test ──▶ commit ──▶ release ──▶ deploy   (gen_traceability.py)
compliance  axis :  CONTROL ──▶ evidence (REQ / ADR / test / commit / config / gate)   (gen_compliance.py)
```

Nothing here lives in the skill — it's all files and one tool, so it stays
auditable and diffable.

## Pieces

| File | Role |
|------|------|
| `compliance.config.json` | which frameworks are in scope, 800-53 baseline, SOC 2 freshness window |
| `frameworks/<id>.json` | control catalogs — IDs + short titles (no copyrighted text) |
| `frameworks/crosswalk.json` | cross-framework equivalence — implement once, attest many |
| `control-mapping.json` | **the artifact you maintain**: per control → applicable? status? evidence? owner? |
| `statement-of-applicability-<id>.md` | *generated* SoA (required for ISO 27001 / 42001) |
| `compliance-matrix.md` | *generated* full matrix across all in-scope frameworks |
| `risk-register.md` | risks → treatment (4Ts) → linked controls (required by ISO/SOC 2) |
| `system-security-plan.md` | SSP-lite (NIST/FedRAMP shape) |
| `dpia.md` | privacy impact (when PII is processed) |
| `ai-risk-assessment.md` | AI risk (when it's an AI system — ISO 42001 / NIST AI RMF) |
| `evidence/evidence-index.md` | **SOC 2 Type II**: evidence collected *across the period* |
| `../tools/gen_compliance.py` | generates the matrix + SoA; `--scaffold`, `--check-gaps` |

## How controls get evidence (the hooks)

A control is "satisfied" when something concrete points at it. Four signals are
discovered automatically, plus whatever you list by hand in `control-mapping.json`:

1. **ADR** — add `Satisfies controls: A.8.24, CC6.1` to an ADR. The decision
   record becomes evidence for those controls.
2. **Commit** — end a commit body with a trailer `Controls: A.8.24` (exactly
   like `Refs: REQ-###`). The commit (and its date — used for SOC 2 freshness)
   becomes evidence.
3. **Requirement** — add a `Controls:` line under a `REQ-###` in the spec; that
   requirement (and its tests, transitively) becomes evidence.
4. **Test** — any control ID appearing in a test file links that test.

So the same `Refs:`-style discipline that powers requirement traceability also
powers control traceability — no separate bookkeeping.

## Workflow

1. **Scope.** Set `frameworks` (and `is_ai_system`, `nist_baseline`) in
   `compliance.config.json`. Run `gen_compliance.py --scaffold` to seed
   `control-mapping.json` with a stub per control.
2. **Decide applicability.** For each control set `applicable` true/false. Every
   exclusion needs a `justification` (ISO SoA requires it; the gate enforces it).
3. **Map evidence as you build.** Tag ADRs/commits/REQs (above) and/or list
   evidence in `control-mapping.json`. Set `status`:
   `implemented | partial | planned | not-applicable`.
4. **Generate.** `gen_compliance.py` writes `compliance-matrix.md` and the
   SoA(s). Review them like any artifact.
5. **Gate.** `gen_compliance.py --check-gaps` fails if any *applicable* control
   is unmapped, claims implemented/partial with no evidence, or (SOC 2) has no
   recent evidence. Wire it in `compliance.yml`.

## Framework notes

- **ISO 27001 / 42001** are management-system standards → the **SoA** and
  **risk-register** are the headline artifacts; most controls are organizational
  and satisfied by policy/process evidence, not code.
- **SOC 2 Type II** audits **operating effectiveness over a period** — design
  alone is not enough. Keep `evidence/evidence-index.md` current; the gate flags
  controls whose newest dated evidence is older than `evidence_freshness_days`.
- **NIST 800-53** is public-domain and published in **OSCAL**. Don't transcribe
  it — set `oscal_import.enabled` in `frameworks/nist-800-53r5.json` and drop the
  official catalog + baseline JSON in `frameworks/oscal/`; the tool expands the
  controls scoped to your baseline.

## Honest scope

The catalogs ship **control IDs + short titles only** — full normative text for
ISO and AICPA standards is copyrighted and must come from the purchased
standard. ISO 42001 and SOC 2 catalogs are seeded but marked *scaffold*: verify
titles against the source before audit use. This layer organizes evidence and
catches gaps; it does not make you compliant, and it is not a substitute for an
assessor.
