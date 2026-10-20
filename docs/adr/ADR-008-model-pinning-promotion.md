# ADR-008: Model pinning and promotion

Status: Accepted (2026-10-19)

## Context
Availability is one of local AI's main benefits: a validated model should behave the same next
month. Silent upgrades break that, and continuously running agents are hit hardest.

## Decision
- Model files are pinned by digest or revision (`generated/model-pins.txt`, `pin_revision` from `update-catalog`), and image digests are pinned in `.env`.
- **No auto-upgrade.** Downloads happen only in the explicit pull step or by offline import. Ollama `:cloud` tags are refused.
- **Promotion:** a candidate must pass the golden-set eval (recall@k, faithfulness, citation accuracy, latency) and be no worse than the current version. Keep the previous version for rollback.
- **Always-on agents:** pinned models only, plus a watchdog (health checks, restart policy, alerts).

## Consequences
- Every model change leaves an audit entry with old and new digests and the eval run id.
