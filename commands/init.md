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
4. **Determine the deploy target:** run `python .specdev/tools/detect_deploy.py`.
   It writes `.specdev/deploy.profile.json`. For an **existing** repo it detects
   the current platform; show the target, rollback strategy, and any
   `REPLACE_ME`/placeholder values to confirm. For a **new** product nothing is
   detected (target `manual`) — that is expected: the platform is an architecture
   decision. Point the user to `.specdev/deploy-platforms.md` and
   `adr/ADR-deployment-platform.md`, help them pick the simplest fit (not
   Kubernetes by default), then set `target` + `params` + `"locked": true`.
5. Do **not** commit. Leave the new files for the user to review.
6. Verify the tools run: `python .specdev/tools/validate_spec.py` and
   `python .specdev/tools/gen_traceability.py` should execute (Gate 1 will fail
   on the placeholder spec — that's expected). Also
   `python .specdev/tools/deploy.py url --env staging` should print the URL.

Then print this post-install checklist:

- Replace the build/test `TODO:`s in `.github/workflows/` with your stack's
  commands (deploy/rollback/health are already wired to the profile).
- Fill real URLs and any `REPLACE_ME` params in `.specdev/deploy.profile.json`,
  and add the platform-CLI install/auth step noted in `deploy.yml`.
- Branch protection on `main`: require the `post-dev-qa` checks + PR review.
- Require `spec-validate` on `spec/**` branches.
- Create `staging` and `production` Environments (no required reviewer on
  production — promotion is automatic).

Finish by summarizing what was installed and what the user must configure.
