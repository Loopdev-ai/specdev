---
description: Start a new SpecDev feature — create a spec/<name> branch and a draft spec from the template.
argument-hint: <feature-name>
---

Start a new SpecDev feature for: **$ARGUMENTS**

**This command dispatches `spec-reviewer` subagents by design. Running it is
your authorization to spawn them — do it; never review the spec inline, which
collapses the reviewer's independence in a way the gate cannot detect.**

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
   an extension, also capture current behavior per requirement. **Record the
   user's request verbatim** — word for word, never paraphrased — plus each
   brainstorm answer that shaped it. They become the spec's `## Original
   Request`, which the reviewers judge intent against.
5. Fill the spec: the `## Original Request`, `FEAT-###`, `Status: draft`,
   sequential `REQ-###` IDs each with a concrete, testable **Acceptance**
   line, and a real **Out of Scope** list.
6. Run `python .specdev/tools/validate_spec.py --strict` and fix what it flags.
7. **Pre-PR review loop (spec phase)** — skip only when
   `python .specdev/tools/profile.py show --unit <unit> --key spec_pr` prints
   `false` (a poc unit has no Spec PR). Follow the specdev skill's *Pre-PR
   review loop*, starting from the pass
   `python .specdev/tools/review_ledger.py --root <unit> status --phase spec`
   names:
   - dispatch a **fresh `spec-reviewer`** each pass with the unit root and the
     dismissed list;
   - record the pass: `review_ledger.py --root <unit> record --phase spec
     --findings-json <file>`;
   - on blocking findings, revise the spec and re-run `validate_spec.py
     --strict`; batch the `needs_human` findings into one question to the
     user per pass; dismiss a deferral (`review_ledger.py --root <unit>
     dismiss --phase spec --id <id> --reason "deferred by the user"`) and list it under
     `## Open Questions`;
   - stop on a clean pass, or at `max_review_iterations` — then list every
     open finding id under `## Open Questions`.
   - any later edit to `spec.md` — an ADR you draft next, an `adr-checker` fix,
     a reviewer's comment on the open PR — needs another pass (`new-run
     --phase spec` first if `status` says `stale-at-cap`); `check --phase
     spec` fails otherwise.
   Commit `.specdev/review.json` with the spec, then
   `review_ledger.py --root <unit> check --phase spec` must pass —
   `spec-validate.yml` runs the same check on the Spec PR.
8. Do **not** open the PR automatically — tell the user the spec is ready
   (and which findings, if any, are unresolved), that the next step is to push
   and open a Spec PR (Gate 1), and remind them not to start the build until
   that PR is merged.

Then for non-trivial architecture, offer to draft an ADR — invoke the `adr`
skill (or `/specdev:adr`) rather than writing the file directly, so the
decision is interviewed, linted, and checked against the ADRs already accepted.
