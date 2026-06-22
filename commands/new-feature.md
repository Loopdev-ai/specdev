---
description: Start a new SpecDev feature — create a spec/<name> branch and a draft spec from the template.
argument-hint: <feature-name>
---

Start a new SpecDev feature for: **$ARGUMENTS**

Prerequisite: the repo must already contain `.specdev/` (run `/specdev:init`
otherwise).

Do this:

1. Pick the next `FEAT-###` by scanning `.specdev/spec.md`, `.specdev/specs/*.md`,
   and branches for the highest used number; if none, start at `FEAT-001`.
2. Create and switch to a branch `spec/<kebab-name>` from the default branch.
3. **Spec lifecycle (one active feature in `spec.md`):** `.specdev/spec.md` always
   holds the *active* feature; finished features live in `.specdev/specs/`.
   - If `.specdev/spec.md` is the unfilled template, draft the new feature there.
   - If it holds a *previous, completed* feature, first archive it to
     `.specdev/specs/<FEAT-###>-<name>.md` (if not already archived), then draft
     the new feature in `.specdev/spec.md`.
   Keep `spec.md` as the single working spec — do not draft a new feature into a
   `specs/` file directly. Traceability stays whole because
   `gen_traceability.py` scans `spec.md` + every archived `specs/*.md` together.
4. **Brainstorm before writing.** Ask the user the minimum questions needed to
   fill: users, problem, mode (new vs. extend), and the first requirements. For
   an extension, also capture current behavior per requirement.
5. Fill the spec: set `FEAT-###`, `Status: draft`, assign sequential `REQ-###`
   IDs each with a concrete, testable **Acceptance** line, and a real
   **Out of Scope** list.
6. Run `python .specdev/tools/validate_spec.py --strict` and fix what it flags.
7. Do **not** open the PR automatically — tell the user the spec is ready, that
   the next step is to push and open a Spec PR (Gate 1), and remind them not to
   start the build until that PR is merged.

Then for non-trivial architecture, offer to draft an `ADR-###` under
`.specdev/adr/` with `Relates to:` the affected REQs.
