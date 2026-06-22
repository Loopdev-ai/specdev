# System Security Plan (SSP-lite) — <SYSTEM NAME>

_NIST 800-53 / FedRAMP shape, trimmed for a single product. The control-by-control
implementation lives in `control-mapping.json` + the generated matrix; this
document carries the system context an assessor reads first._

## 1. System description

- **Purpose:**
- **Owner / authorizing official:**
- **Data types processed:** <none / PII / PHI / payment / secrets>
- **FIPS 199 categorization:** Confidentiality __ / Integrity __ / Availability __ → **overall: low | moderate | high**
- **Selected 800-53 baseline:** <low | moderate | high>

## 2. Authorization boundary

_What is in scope: components, data stores, external services, trust boundaries.
Reference `.specdev/components.md` and the deployment profile._

```
<diagram or bullet list of components, data flows, and the boundary>
```

## 3. Environments & separation

| Environment | Hosting | Purpose | Separation from prod |
|-------------|---------|---------|----------------------|
| dev |  |  |  |
| staging |  |  |  |
| production |  |  |  |

## 4. Control implementation summary

_Per-control detail is generated into `compliance-matrix.md`. Summarize only the
inherited vs. system-specific split and any common controls here._

- **Inherited (from platform / cloud provider):**
- **System-specific:**
- **Hybrid:**

## 5. Roles

| Role | Responsibility | Person/team |
|------|----------------|-------------|
| System owner |  |  |
| Security lead |  |  |
| Incident response |  |  |
