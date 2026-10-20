# Implementation Plan

Timelines assume one experienced engineer. They are estimates. Each phase ends with a demo
and acceptance criteria that someone else can check.

| Phase | Weeks | Outcome | Status |
|---|---|---|---|
| 0 Advisor + lockdown | 1 | Smallest fitting setup picked; only that runtime and model installed; local chat; outbound blocked | **Built** (advisor, configs, Compose, egress test, browser version in `site/`) |
| 1 Gateway + history | 2–3 | Every conversation stored locally, encrypted, searchable by keyword and meaning | **Built** (`gateway/app.py`, `store/`, auth modes, audit) |
| 2 Recall (RAG) | 3–5 | Documents ingested; answers cite file and date; eval set | **Built** (`ingest.py`, `recall.py`, `eval.py`) |
| 3 Memory & control | 5–7 | Memory cards, retention, one-command export and delete, redaction | **Built** (`extensions.py`, `exporter.py`, web UI) |
| 4 Multi-user | 7–9 | SSO, RBAC, collection ACLs in the DB, backups, monitoring; k3s for Tier 3 | **Built** (Postgres RLS tested; restic, Prometheus/Grafana/Loki, `deploy/k3s/`); SSO not tested against a live IdP |
| 5 Optional | later | Read-only connectors, SearXNG with injection defenses, voice, cloud burst (off by default) | **Built** (`connectors.py`, `fetcher.py`, `phase5.py`) |
| 6 Customize (optional) | 10+ | LoRA post-training, distillation, versioned promotion; abliteration only through ADR-007 | **Built** (`customize.py`); training needs a GPU and was not run here |

---

## Phase 0: Right-Size Advisor and egress lockdown (Week 1)

- **Objectives.** Pick the smallest setup that meets the profile; install only that; prove
  zero egress.
- **Tasks (done):**
  - profile schema and the 5 starter profiles;
  - `config/rules.yaml`, `runtimes.yaml`, `model_catalog.yaml`, `pricing.yaml`,
    `hardware_reference.yaml`;
  - the engine (capabilities, runtime, fit, procurement, licenses, customization gates, TCO);
  - the wizard, plus `intake` and `explain` with a local LLM;
  - generated `.env`, override, `policy.yaml`, pull lists and the security packet;
  - the Compose skeleton, `make egress-test` and `update-catalog`.
- **Acceptance:**
  - `make test` passes;
  - each starter profile meets its `expected:` block;
  - `make up` on a real host starts only the recommended services;
  - `make egress-test` passes.
- **Demo.** Run `make advise` on two different machines and compare the recommendations and
  the diff.
- **Risks.** Catalog and price staleness (mitigated by `update-catalog`, `last_verified` and the
  stale flags). Real performance differing from the estimates (mitigated by the Week-2 pilot
  measurements).

## Phase 1: Gateway and conversation history (Weeks 2–3)

- **Objectives.** All traffic goes through the Gateway; every conversation is stored and
  searchable.
- **Tasks:**
  - a FastAPI OpenAI-compatible proxy (`/v1/chat/completions`, `/v1/models`);
  - authentication pass-through;
  - the audit log;
  - the `conversations`, `messages` and `audit_log` tables (SQLite for T1, Postgres for T2+);
  - an FTS5/tsvector history index plus embeddings;
  - a redaction pass before indexing;
  - `/history/search` (keyword, meaning and date filters);
  - a health page;
  - pointing Open WebUI at the Gateway.
- **Acceptance:**
  - "What did I decide about X last month?" returns the right conversation in at least 8 of 10
    seeded tests;
  - every request has an audit row with the model digest;
  - no request reaches the runtime without going through the Gateway (network test).
- **Demo.** Chat, then search the history by topic and by date.
- **Risks.** UI compatibility (keep the API OpenAI-compatible); redaction misses (add tests
  with synthetic secrets).

## Phase 2: Recall, document RAG (Weeks 3–5)

- **Objectives.** Answers come from the user's own files, with citations.
- **Tasks:**
  - a watched-folder worker, dedupe by hash, and Docling plus OCR (and whisper.cpp where enabled);
  - heading-aware chunking (code-aware for code);
  - Presidio PII tagging, with collection sensitivity defaults;
  - hybrid BM25 + vector search, with ACLs and sensitivity in the `WHERE` clause;
  - the reranker;
  - citations (path and timestamp) and an "I don't know" threshold;
  - the golden set and an eval runner (recall@k, faithfulness, citation accuracy).
- **Acceptance:**
  - recall@5 of at least 0.8 on the golden set;
  - citation accuracy of at least 0.9;
  - Restricted chunks are never returned without a session unlock (test).
- **Demo.** Drop a folder in, ask questions, open the cited files.
- **Risks.** Scanned-PDF quality (OCR tuning); chunking edge cases (eval-driven).

## Phase 3: Memory and control (Weeks 5–7)

- **Tasks:**
  - rolling summaries;
  - memory cards (propose, approve, edit, delete);
  - the strictest-label rule for derived data;
  - retention jobs;
  - `make export` (Markdown and JSON) and `make delete` (rows, vectors, then backups on rotation);
  - a timeline UI in the Gateway.
- **Acceptance:**
  - deleting a document removes its chunks, vectors and summaries;
  - a Restricted source never appears in a non-Restricted summary (test).
- **Demo.** Export everything, delete one collection, show that it is gone from search.

## Phase 4: Multi-user (Weeks 7–9)

- **Tasks:**
  - Authentik OIDC with MFA, and RBAC from groups;
  - collection ACLs with Postgres row-level security;
  - departmental collections;
  - restic backups and a restore drill;
  - Prometheus, Grafana and Loki (Tier 3);
  - k3s manifests for Tier 3;
  - an optional paired prefill/decode setup (advanced).
- **Acceptance.** A hand-crafted API call by user A cannot retrieve collection B's chunks. A
  restore drill to a spare disk succeeds.
- **Demo.** Two users in different departments get different results for the same query.

## Phase 5: Optional capabilities (later)

- Read-only scoped connectors, each with a kill switch and a "last accessed" log.
- SearXNG research. Fetched pages are sanitized and isolated, and no tools are triggered by
  retrieved text.
- Voice.
- Cloud burst, off by default. It needs per-request opt-in, PII redaction, a zero-retention
  provider and an audit entry.
- **Acceptance.** Injection test pages cannot trigger tool calls; connector writes always ask
  for confirmation.

## Phase 6: Customize track (optional, Weeks 10+)

- **Tasks:**
  - the RAG-first gate, applied from golden-set evidence;
  - a dataset sensitivity review;
  - LoRA post-training with versioned adapters;
  - distillation from a license-compatible teacher into the serving model;
  - before-and-after evals, and promotion through ADR-008;
  - abliteration only through ADR-007 (admin approval, a justification, never for households
    or children's data, guardrails kept on).
- **Acceptance.** The adapter beats the base model on the domain eval with no regression on the
  general eval. The promotion is audited.
- **Risks.** Overfitting, data leakage into weights (the sensitivity review and Restricted
  exclusion) and license violations (`allows_fine_tuning` / `allows_distillation`).
