---
description: Install the SpecDev pipeline (.specdev/ + GitHub workflows) into the current repository.
---

Install the SpecDev spec-driven delivery kit into the current working directory.
The plugin's templates live at `${CLAUDE_PLUGIN_ROOT}/assets/`.

Do this:

1. Confirm the current directory is the repository root (look for `.git`). If it
   isn't a git repo, tell the user and offer to `git init`, but do not proceed
   silently.
2. Copy `${CLAUDE_PLUGIN_ROOT}/assets/specdev/` → `./.specdev/` (including the
   `adr/` and `tools/` subfolders). **Do not overwrite** an existing `.specdev/`
   file — if one exists, list the conflicts and ask before replacing.
3. Copy `${CLAUDE_PLUGIN_ROOT}/assets/workflows/*.yml` → `./.github/workflows/`
   (create the directory if needed; same no-overwrite rule).
3b. Copy `${CLAUDE_PLUGIN_ROOT}/assets/gitleaks.toml` → `./.gitleaks.toml` (same
   no-overwrite rule) so the secret-scan gate allowlists test fixtures.
3a2. **Vendor the SpecDev skill + subagents for headless CI.** Copy
   `${CLAUDE_PLUGIN_ROOT}/skills/specdev/` → `./.claude/skills/specdev/` and
   `${CLAUDE_PLUGIN_ROOT}/agents/*.md` → `./.claude/agents/` (create the dirs;
   same no-overwrite rule). This lets `specdev-build.yml` run the build with
   `claude-code-action` — the runner loads the same skill and the
   component-builder / qa-verifier / adr-checker / spec-explorer subagents from
   the repo, so the coordinator can offload work and stay context-bounded.
3c. **Link to the org's architectural repo of record.** Ask the user for:
   - the governance repo (`owner/name` — the org's SpecDev repo holding
     `governance/adr/`), and the `ref` to track (`main`, or a tag to pin);
   - this repo's **classification** — fetch
     `governance/classification.json` from that repo and ask for **one value
     per axis** (e.g. seeded axes: `maturity` = poc | dev | prod, `audience` =
     internal | customer; orgs may define others — fall back to those seeds if
     the file is unreachable). This is a decision, not a detection: it
     determines which org ADRs bind this repo. Write it as an object, e.g.
     `{"maturity": "prod", "audience": "internal"}`, rewriting the template's
     axis keys to match the org's actual axes.
   Fill `.specdev/org.json` with the answers. If the org has no governance
   repo, leave the `REPLACE_ME` values — every org-ADR tool and gate is inert
   until it is configured.
3d. **Point Claude at the source of truth (only if 3c was configured).**
   Append this block to the repo's `CLAUDE.md` (create the file if absent; if
   the markers already exist, replace the block — idempotent):

   ```markdown
   <!-- specdev:org-adr-governance -->
   ## Org architecture governance
   This repo is classified **<axis: value, one per axis — e.g. maturity:
   prod, audience: internal>** and is governed by the org ADRs in
   `<owner/name>` (ref `<ref>`, path `governance/adr/`) — the architectural
   repo of record. Before making or documenting any
   architectural decision, and before opening any Spec or Implementation PR,
   dispatch the specdev `adr-checker` agent to verify the work against the
   applicable org ADRs. Never open a PR while it reports violations; fix
   automatically (builder → QA → re-check) and only stop for a genuine
   conflict that needs a human decision. Deviations require a local ADR plus
   a justified `superseded-by-local` entry — never silence.
   <!-- /specdev:org-adr-governance -->
   ```
4. **Determine the deploy target:** run `python .specdev/tools/detect_deploy.py`.
   It writes `.specdev/deploy.profile.json`. For an **existing** repo it detects
   the current platform; show the target, rollback strategy, and any
   `REPLACE_ME`/placeholder values to confirm. For a **new** product nothing is
   detected (target `manual`) — that is expected: the platform is an architecture
   decision. Point the user to `.specdev/deploy-platforms.md` and
   `adr/ADR-deployment-platform.md`, help them pick the simplest fit (not
   Kubernetes by default), then set `target` + `params` + `"locked": true`.
4b. **Single governed unit by default.** A freshly initialized repo is ONE
   governed unit rooted at the repo root — there is no `units.json` and none is
   needed. If the repo actually holds several projects at different maturities
   (some throwaway, some touching real systems), tell the user they can convert
   later with `python .specdev/tools/units.py migrate --unit <path>`, which
   splits the layout into per-unit `.specdev/` directories and writes the
   registry. Do NOT hand-create `.specdev/units.json` — an invalid registry
   fails `units.py check`, and every gate's discover job runs that check.
5. Do **not** commit. Leave the new files for the user to review.
6. Verify the tools run: `python .specdev/tools/validate_spec.py` and
   `python .specdev/tools/gen_traceability.py` should execute (Gate 1 will fail
   on the placeholder spec — that's expected). Also
   `python .specdev/tools/deploy.py url --env staging` should print the URL.

If the user wants control-framework compliance (ISO 27001/42001, SOC 2, NIST
800-53), point them at `.specdev/compliance/` (installed in step 2): set the
in-scope `frameworks` in `compliance.config.json`, run
`python .specdev/tools/gen_compliance.py --scaffold` to seed
`control-mapping.json`, and mark `compliance.yml` a required check. It's inert
until configured, so leave it alone if compliance isn't in scope.

Then print this post-install checklist:

- Replace the build/test `TODO:`s in `.github/workflows/` with your stack's
  commands (deploy/rollback/health are already wired to the profile).
- Fill real URLs and any `REPLACE_ME` params in `.specdev/deploy.profile.json`,
  and add the platform-CLI install/auth step noted in `deploy.yml`.
- Branch protection on `main`: require the `post-dev-qa` **`summary`** check +
  PR review. Require the `summary` job, never the matrix legs — leg names are
  dynamic, so branch protection cannot require them and a newly added governed
  unit would arrive as a silently unrequired check.
- Require the `spec-validate` **`summary`** check on `spec/**` branches.
- If org governance was configured (3c): mark the `org-adr-check` **`summary`**
  job a required status check; if the governance repo is private, add a read-only PAT as the
  `GOVERNANCE_TOKEN` secret (the workflow already reads it).
- Create `staging` and `production` Environments (no required reviewer on
  production — promotion is automatic).
- CI handoff (specdev-build): add the `ANTHROPIC_API_KEY` repo/org secret. Merge
  a Spec PR (`spec/**`) to hand a prod build to the runner; push a `poc/**`
  branch for a hands-off poc build. Tune `.specdev/ci.json` (`runner`,
  `max_session_minutes`) — set `runner` to a self-hosted label for long builds.
- Create a `poc` GitHub Environment (isolated from staging/production) and fill
  `environments.poc.url` in `.specdev/deploy.profile.json`; poc builds deploy
  only there and never promote to prod.
- Architecture/runtime hosting config: capture values with the `arch-config`
  skill (writes `.specdev/architecture-config.json`); make `arch-config-validate`
  a required status check if you use it.

Finish by summarizing what was installed and what the user must configure.
