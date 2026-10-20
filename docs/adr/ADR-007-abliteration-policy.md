# ADR-007: Abliteration policy and guardrails

Status: Accepted (2026-10-19)

## Context
Abliteration removes a model's internal refusal pattern without retraining. It can unblock
legitimate security, legal or medical research, and it can also remove useful safety behavior.

## Decision
- **Off by default, and never recommended automatically.**
- It is allowed only if **all** of the following hold:
  - `customization.abliteration.requested: true`;
  - `admin_approved: true`;
  - a non-empty justification;
  - the profile is not a household and holds no children's data.
- **Guardrails that always stay on:**
  - Gateway content filters, the audit log of every request, and RBAC limits on who can use the model;
  - a separate model id, never the default;
  - an entry in the threat model.

## Consequences
- Requests that fail a condition are shown as "refused" with the reason.
- Tests assert that no starter profile gets abliteration.
