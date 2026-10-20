# ADR-006: Customization track: post-training and distillation

Status: Accepted (2026-10-19)

## Context
Open weights allow customization that hosted models do not, but training is costly and risky
if done early.

## Decision
- **Post-training (LoRA)** happens only when it is requested with a justification **and** the golden-set eval shows RAG alone misses domain terms or behavior.
  - It requires a dataset sensitivity review, versioned adapters, before-and-after evals, and a base model whose license allows fine-tuning (`allows_fine_tuning`).
- **Distillation** happens only when requested with a justification. Use it for larger-model quality on modest hardware or for high concurrency.
  - The teacher's license must allow distillation (`allows_distillation`).
  - The teacher may run temporarily on hardware the owner controls.
- Everything runs locally, and the data and weights never go to a provider.

## Consequences
- This is Phase 6; nothing is trained in Phases 0–5.
