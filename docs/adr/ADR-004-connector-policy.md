# ADR-004: Connector policy

Status: Accepted (2026-10-19)

## Context
Users refuse standing access to email, banking and social accounts.

## Decision
- **No connectors by default.** Exported files (`.mbox`, notes exports) are preferred.
- If a connector is enabled:
  - OAuth with minimal read-only scopes, with tokens in the vault;
  - a kill switch per connector and a visible "last accessed" log;
  - every write action shows its exact payload and needs confirmation.
- Never in air-gapped profiles. Passwords are never entered into the assistant.

## Consequences
- Connectors arrive in Phase 5 behind the Gateway, and each is audited.
