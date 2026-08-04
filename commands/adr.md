---
description: Author an architecture decision record (local or org) with a guided interview and conflict checks.
argument-hint: [decision in one sentence]
---

Author an ADR for: **$ARGUMENTS**

Use the `adr` skill. In short:

1. Locate `adr.py` (`.specdev/tools/` in a product repo, `assets/specdev/tools/`
   in a governance repo) and let it detect the layer; in a monorepo, pick the
   unit with `python .specdev/tools/units.py list` and pass `--unit <unit>`.
2. Run the interview — decision, forces, REQs (or `applies_to`), rejected
   options, costs, and Conformance items for an org ADR. One question at a
   time. If `$ARGUMENTS` already answers a question, confirm rather than re-ask.
3. Allocate the id with `adr.py next-id`, fill the matching template, and pass
   `adr.py lint --file <path>` before showing the user anything.
4. Run `adr.py conflicts --file <path> --json`, read only the shortlisted ADRs,
   and resolve any real contradiction by revising, superseding, or narrowing
   scope — never by writing it anyway.
5. Org mode: regenerate the index with
   `python governance/tools/gen_adr_index.py` and commit it.

Do not open a PR automatically. Report the id, the file, and any shortlisted
ADR you ruled not in conflict, with the reason.
