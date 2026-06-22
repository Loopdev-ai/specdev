# Evidence Index — <SYSTEM NAME>

_For **SOC 2 Type II** especially: an auditor tests **operating effectiveness
across a period**, so a control needs evidence produced **repeatedly over time**,
not a single design artifact. This index points at where that recurring evidence
lives. SpecDev's automatic pipeline already produces most of it — link it here._

## Recurring (automated) evidence

| Control(s) | Evidence | Source / where | Cadence |
|-----------|----------|----------------|---------|
| CC8.1 / A.8.32 | PR reviews + required status checks | GitHub PRs, `post-dev-qa` runs | per change |
| CC7.1 / A.8.8 | dependency + secret scans | `post-dev-qa.yml` (gitleaks, SAST) | per change |
| A.8.29 / CC8.1 | test + coverage results | CI artifacts | per change |
| CC7.2 / A.8.16 | deploy + health checks | `deploy.yml`, `post-deploy-qa.yml` | per deploy |
| CC7.4 / A.5.26 | incident records + rollbacks | auto-rollback + incident issues | per incident |
| A.8.13 / A1.2 | backup runs | <backup job / platform> | <daily> |

## Point-in-time evidence

| Control(s) | Evidence | Location | Last collected | Owner |
|-----------|----------|----------|----------------|-------|
| A.5.1 | Information security policy | <link> |  |  |
| A.6.3 | Security awareness training records | <link> |  |  |
| A.5.18 / CC6.3 | Access review | <link> |  |  |
| A.8.8 | Penetration test report | <link> |  |  |

## Notes

- Prefer evidence the pipeline emits automatically (CI runs, deploy logs, PR
  history) — it is timestamped, tamper-evident, and continuous, which is exactly
  what a Type II window needs.
- `gen_compliance.py` reads commit dates from `Controls:` trailers to flag
  controls whose newest evidence is older than `evidence_freshness_days`.
- Keep collected exports under `.specdev/compliance/evidence/<control>/<date>/`
  or link to the immutable CI/run URL.
