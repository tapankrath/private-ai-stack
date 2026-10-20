# ADR-005: Memory model

Status: Accepted (2026-10-19)

## Context
Assistants that remember too much leak; assistants that remember nothing are less useful.

## Decision
- **Three layers:**
  - session context;
  - rolling summaries;
  - **memory cards**, which the user approves and can view, edit and delete.
- A redaction pass runs before indexing, and secrets are never auto-saved.
- **Derived data inherits the strictest label of its sources.** This applies to summaries, memory cards, history embeddings and chunks.
- Retention is set per collection, and hard delete covers rows, vectors and backups on rotation.

## Consequences
- Summaries cannot carry Restricted content into a less-restricted view.
