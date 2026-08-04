---
name: adr
description: Author an architecture decision record — local (.specdev/adr/) or org (governance/adr/) — through a guided interview, and prove it does not conflict with an already-accepted decision. Use when recording an architectural decision, when the specdev pipeline reaches its Architecture step, or when superseding an existing ADR. Trigger on "ADR", "architecture decision", "record this decision", "supersede ADR", "decision record".
---

# adr — guided ADR authoring with conflict detection

An ADR that lists no rejected option records no decision, and an ADR that
silently contradicts an accepted one is worse than none. This skill interviews
for the first and mechanically prevents the second.

You drive `adr.py`; you never skip it. The tool decides what is decidable —
ids, structure, supersession chains, and which accepted ADRs *could* overlap.
You judge the one thing it cannot: whether two decisions actually contradict.

## Locate the tool and the layer

`adr.py` is at `.specdev/tools/adr.py` in a product repo, or
`assets/specdev/tools/adr.py` in a governance repo. If neither exists, fall
back to `${CLAUDE_PLUGIN_ROOT}/assets/specdev/tools/adr.py`.

Mode is detected by the tool:

- **local** — `<unit>/.specdev/adr/ADR-###.md`. A product decision, linked to
  REQs. In a monorepo, run `python .specdev/tools/units.py list` first and pass
  `--unit <unit>`; the tools always live at the repo root.
- **org** — `governance/adr/ADR-####-<slug>.md`. An org-wide decision binding
  repos by classification, carrying a **Conformance** section that the
  `adr-checker` agent verifies.

If the tool reports both layers exist, ask which one this decision belongs to.
A decision that binds only this product is local; one that binds other repos is
org. Once you know, pass `--mode local|org` on every `adr.py` command below —
the tool refuses to guess when both layers are present, and every example
command in this skill accepts it.

## The interview — one question at a time

Ask these in order. Do not batch them, and do not write anything until you have
answers to all of them.

1. **What is the decision, in one present-tense sentence?**
   "Sessions are stateless JWTs", not "we should probably look at JWTs".
2. **What forces it?** Constraints, deadlines, existing architecture,
   non-functional requirements. This becomes **Context**.
3. **Local: which REQs does it serve?** Get concrete `REQ-###` ids from
   `.specdev/spec.md`.
   **Org: who does it bind?** An `applies_to` list against
   `governance/classification.json` — entries are OR, `&` is AND, `value+` is
   that rank and above on an ordered axis.
4. **What did you seriously consider and reject, and why?** Push for at least
   two options with real Pros *and* Cons. One option means the decision has not
   been made; say so once. If the user stands by it, write the ADR
   `status: proposed`, not `accepted`.
5. **What does this cost you?** Negative consequences, risks, follow-ups. An
   ADR whose Consequences are all positive has not been thought through.
6. **Org only: how would a checker prove a repo conforms?** Each answer is one
   Conformance item naming a file that exists, a config value, or a pattern
   present or absent in code. "Teams should be careful" is not checkable.

**When you are the author** (the specdev pipeline reached its Architecture step
and there is no human in the loop for this decision), answer the ladder from
the spec and the architecture context yourself, and ask the user only where the
spec is silent. Nothing else relaxes: the same lint and the same conflict gate
apply.

## Write it

In a monorepo add `--unit <unit>` to every command below; if the repo holds
both ADR layers, add `--mode local|org` too (see above) — omit either and the
tool infers what it safely can, but refuses to guess the layer.

1. `python .specdev/tools/adr.py next-id [--unit <u>] [--mode local|org]` for
   the id.
2. Copy the matching template's shape. Org: `governance/adr/TEMPLATE.md`.
   Local: whichever ADR in `.specdev/adr/` still carries the unfilled
   `**Status:** proposed | accepted | superseded` line — on a fresh unit
   that's `ADR-001.md`, until `next-id` allocates that very id to the first
   real decision; once none remain, copy the shape from the plugin's own
   `${CLAUDE_PLUGIN_ROOT}/assets/specdev/adr/ADR-001.md`. Fill every field
   from the interview. Never invent a different shape.
3. **Local ADRs carry the frontmatter AND the prose `**Status:**` /
   `**Relates to:**` lines.** Both, always, saying the same thing:
   `gen_traceability.py` and `validate_spec.py` read the prose, and `lint`
   fails on drift between the two.
4. `python .specdev/tools/adr.py lint --file <path> [--unit <u>] [--mode local|org]`.
   Fix what it reports. **Do not hand the user a file that has not passed lint.**

## Prove it does not conflict

Run `python .specdev/tools/adr.py conflicts --file <path> --json [--unit <u>] [--mode local|org]`.

**Hard errors** (exit 1) are structural: duplicate ids, a `supersedes` pointing
nowhere, a half-finished supersession. Fix them; they are never acceptable.

**The shortlist** is your reading list, and nothing else is. Open ONLY the
shortlisted ADRs, read their **Decision** sections, and rule on each:
contradiction or coexistence. Do not read the whole ADR directory — the
shortlist exists so you don't.

On a real contradiction, land on exactly one of these. Never write the ADR
anyway:

- **Revise** the new ADR to agree with the accepted one.
- **Supersede** the old one — one atomic pair of edits: `supersedes: [ADR-00X]`
  on the new ADR, and on the old one `status: superseded` +
  `superseded_by: <new id>` in the frontmatter *and* `**Status:** superseded`
  in the prose. Then re-run `conflicts` to prove the chain is reciprocal.
- **Narrow** `scopes` / `applies_to` so the overlap genuinely disappears — only
  when the two decisions really do address different things. Narrowing scope to
  dodge a check you would otherwise fail is the one move you must not make.

## Local ADRs must also respect the org layer

If `.specdev/org.json` is configured, a local decision can contradict a binding
org ADR. Check cheaply: read `.specdev/adr/org-compliance.json` and fetch the
org index once —
`curl -fsSL https://raw.githubusercontent.com/<governance_repo>/<ref>/<path>/index.json`
(add `-H "Authorization: Bearer $GOVERNANCE_TOKEN"` for a private repo). The
`summary` line of each applicable ADR is enough to spot a contradiction; do not
read org ADR bodies into this thread.

If the local decision contradicts a binding org ADR, either revise it, or — if
the deviation is deliberate and justified — write the local ADR as the
documented deviation and tell the user the `adr-checker` agent must record
`superseded-by-local` before the next PR. **Do not dispatch `adr-checker`
yourself**: authoring is this skill's job, verification is that agent's, and
the specdev pipeline dispatches it at the Architecture step and before every PR.

## Finish

- **Org mode only:** run `python governance/tools/gen_adr_index.py` and commit
  the regenerated `index.json` + `INDEX.md`. An org ADR that is not in the index
  binds nobody.
- **Local mode:** run `python .specdev/tools/validate_spec.py` and confirm the
  new ADR is counted.
- Report: the id, the file, what it decides, what it supersedes if anything,
  and any shortlisted ADR you ruled *not* in conflict — with your reason. That
  last line is the part a reviewer cannot reconstruct.

## Guardrails

- Never write an ADR that has not passed `lint`.
- Never write an ADR while `conflicts` reports a hard error.
- Never edit `org-compliance.json` — only the `adr-checker` agent writes it.
- Never let a local ADR supersede an org ADR. The only path is a documented
  deviation plus a `superseded-by-local` manifest entry.
- Renumbering an existing ADR breaks the traceability matrix. Supersede, never
  rewrite history.
