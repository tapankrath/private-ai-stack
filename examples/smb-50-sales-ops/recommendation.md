# Your Private AI Setup

> All memory, speed, disk and price figures are estimates, not benchmarks. Model names are catalog examples; check licenses before use.

## In One Sentence

One mid-size general AI model on your own servers handles your questions and writing; it also searches your own files and past conversations and transcribes meetings. Nothing leaves your control.

Cost is a concern you raised, so start with **Cost Comparison (Honest View)** below.

- *Cost*: Start with the honest cost comparison below and the smallest pilot; only buy hardware after the pilot proves value.
- *Security review*: A security review packet (data flows, egress policy, threat model, model provenance and licenses, pinning and update process) is generated next to this file.

## What You Need

| Component | What it does for you | Model / tool | Why this one |
|---|---|---|---|
| General assistant | Answers questions, writes and summarizes | Qwen3.6 35B-A3B | AWQ, 16,384-token context, 26.8 GB, ~298.4 tok/s (estimate); MoE: 35B total sets memory, 3B active sets speed |
| Search by meaning | Finds documents and past chats by meaning, not just keywords | BGE-M3 | 1.2 GB, small and local |
| Better search ranking | Puts the most relevant passages first | BGE Reranker v2 M3 | 1.2 GB, small and local |
| Speech to text | Turns meetings and voice notes into text | Whisper large-v3-turbo (whisper.cpp) | 2.0 GB, small and local |
| Quick summaries and routing | Summarizes conversations so the main model stays free | Qwen3 1.7B | Q8_0, 8,192-token context, 3.4 GB, ~285 tok/s (estimate) |
| vLLM (OpenAI-compatible) | 8+ concurrent sessions on NVIDIA: continuous batching gives far higher throughput. | vllm | Default. Alternatives: SGLang. |
| Ollama sidecar | Serves embeddings and any small or specialist models. | ollama | Default. |
| Open WebUI | Chat UI bound to localhost/LAN, telemetry disabled. | open-webui | Default. Alternatives: AnythingLLM, custom thin UI on the Gateway. |
| Caddy reverse proxy | Caddy (only container with a published port; local CA TLS) | caddy | Default. Alternatives: Traefik. |
| Gateway | The only path to the model: policy, access rules, audit log, Recall, memory and searchable history. | gateway | Default. |
| Encrypted backups (restic) | restic (encrypted) to local disk + optional offline drive | backups | Default. Alternatives: borg. |
| Model pinning | Model files pinned by checksum; no auto-upgrade; a new version must pass the golden-set eval before promotion. | model-pinning | Default. |
| Postgres | Shared conversation, metadata and ACL store for multiple users. | postgres | Default. |
| Per-user accounts | Every person has their own login and history. | accounts | Default. |
| Postgres + pgvector + tsvector (row-level security for ACLs) | Semantic + keyword (hybrid) search over documents and history. | vector-store | Default. Alternatives: Qdrant (payload filters) + Postgres. |
| Docling document parser | Parses PDFs, Office files and email exports locally. | docling | Default. Alternatives: Unstructured, Apache Tika. |
| whisper.cpp server | Meetings and voice notes need local speech-to-text. | whisper | Default. |
| SearXNG (private web research) | Private metasearch: searches are not tied to your identity. The only service allowed outbound, and only to search engines. Regulated data is present, so this is a named, audited egress exception you opted into. | searxng | Default. |
| Authentik (OIDC SSO + MFA) | Central login with MFA (requested) | authentik | Default. Alternatives: Keycloak. |
| Role-based access control | Roles from SSO groups decide which collections each user can search. | rbac | Default. |
| Prometheus + Grafana + Loki | Capacity, latency, queue depth and ingestion monitoring. | observability | Default. |
| Agent watchdog | Always-on agents: health checks, automatic restart, alerts; runs only pinned models. | agent-watchdog | Default. |
| Scoped read-only connectors: calendar_readonly | Requested. OAuth with minimal read-only scopes, tokens in the vault, per-connector kill switch, 'last accessed' log. (Phase 5) | connectors | Default. |

**Hardware to buy:** Workstation with two RTX 3090 24 GB (48 GB total, used GPUs) ($2,600-$3,600, estimate; price last verified: never). It is the smallest listed option that meets this profile.

### Licenses (commercial use)

- **Qwen3.6 35B-A3B**: Apache-2.0 (OSI open source); commercial use: yes.
- **BGE-M3**: MIT (OSI open source); commercial use: yes.
- **BGE Reranker v2 M3**: Apache-2.0 (OSI open source); commercial use: yes.
- **Whisper large-v3-turbo (whisper.cpp)**: MIT (OSI open source); commercial use: yes.
- **Qwen3 1.7B**: Apache-2.0 (OSI open source); commercial use: yes.

### Customize Track (optional)

- **Post-training (LoRA) on Qwen3.6 35B-A3B**: RAG first. Start only when the golden-set eval shows RAG alone misses domain terms or behavior. Requires: dataset sensitivity review, versioned LoRA adapters, before/after eval on the golden set, runs locally; weights and data never leave.
- **Distillation** from GLM-5.3-Flash (MIT, distillation allowed: yes) into Qwen3.6 35B-A3B. The teacher can run temporarily on rented or borrowed hardware you control; the student serves users. Requires: teacher license permits distillation, before/after eval, versioned promotion.

## What You Don't Need

- OCR pipeline — No scans or images to read.
- Cloud burst mode — Off by default. The advisor never turns it on.
- Per-user desktop installs — One shared server is simpler to secure, back up and update.
- Vision-language model — No job needs to understand photos or charts.
- Code model — Coding is not a primary job; the general model handles occasional code questions.

## Why Local (Beyond Price)

- **Security**: You control the whole chain: the model, its exact version, the serving software and who can reach it. No outside service joins your attack surface.
- **Availability**: No rate limits, no outages caused by someone else's demand, no surprise deprecations. A validated, pinned model behaves the same next month, which matters most for your always-on agents.
- **Privacy**: Prompts, documents and answers never leave your network. That is a simpler guarantee than any contract a provider can offer, and it matters for the regulated data you hold.
- **Customization**: Open weights let you train on your own material or distill a faster model, locally, without handing data or weights to a provider.

## Your Hardware Fit

- Machine: nvidia (rtx_3090), compute class medium; market tier: consumer ai workstation
- Runtime: vLLM. 8+ concurrent sessions on NVIDIA: continuous batching gives far higher throughput.
- Memory: needs 34.5 GB of 48.0 GB usable (48 GB usable for models (option spec, estimate)); headroom 28.1% (at least 20% required)
- Writing speed: ~298.4 tokens/sec (decode, estimate); target for 'instant' is 20 tok/s
- Reading a typical prompt before answering (prefill): ~0.8s (estimate)
- Context: 16,384 tokens; sessions in parallel: 18
- Disk: ~1211.3 GB including a year of growth (estimate)
- Setup effort: 2-5 days incl. SSO and backups (estimate)

## Privacy Settings

- Outbound traffic: Deny all outbound by default. Model downloads happen only in a separate `make pull` step. SearXNG may reach search engines (named exception).
- Encryption: Full-disk encryption required (FileVault / LUKS / BitLocker); encrypted backups. In transit: TLS on the LAN via Caddy's local CA.
- Remote access: None by default; if needed, WireGuard/Tailscale only. Never port-forward.
- Retention: conversations 365 days; documents keep until deleted; memory cards admin and user managed
- Restricted tier: on (excluded from search unless unlocked for the session)
- Access control: Per-user and per-collection ACLs enforced in the database query (Postgres row-level security), not only in the UI.
- Connectors: calendar_readonly (read-only); web research: on; cloud burst: off
- Regulated data present. These controls support privacy obligations but do not by themselves make you compliant with HIPAA, PCI, GDPR or similar; review with your compliance advisor.

## Cost Comparison (Honest View)

| | Local | API (open-weight model) | API (frontier model) | Subscription |
|---|---|---|---|---|
| 3-year cost | $50,576 | $2,774 | $43,200 | $46,333 |
| Low estimate | $50,576 | $1,447 | $36,072 | $46,333 |
| Router fee (included) | – | $145 | – | – |
| Where your prompts go | stay on your machine | sent to the router and whichever provider it routes to | sent to the model provider | sent to the subscription provider |

3-year estimates. On these estimates, a hosted open-weight model API would be cheaper than local by about $47,802 over three years. Local is recommended here for security, availability, privacy, not price. The saving has a privacy cost: every question, the chat history sent with it and the document passages Recall finds would go to the router and whichever provider it routes to. For this profile that is not just a preference: its regulated data makes it a compliance question.

Local breakdown: hardware $3,100, power $1,776, admin time $43,200, networking $2,500.
The open-weight API figure includes $145 of router fees (5.5%).
Subscription includes ~$1,333 of API usage for agents (subscriptions rarely cover them).
Assumptions: 50 user(s) x 20 queries/day x 22 days/month; hosted APIs bill ~12,500 input tokens per question (8,000 question and instructions + 1,500 chat history resent + 3,000 Recall passages) and 500 output; the low figure counts the question only, at the cheapest provider; 3 agent(s) always on; admin time at $75/hour; electricity $0.17/kWh; High-memory machines may hold resale value while memory prices are high; resale is not counted.
Prices not verified in the last 60 days: running costs, subscription price, price of Workstation with two RTX 3090 24 GB (48 GB total, used GPUs). Update config/pricing.yaml.

## Setup Steps

Start with a two-week pilot before buying, on a machine you already have if possible:

- Days 1-2: run the advisor, install only the recommended runtime and model, confirm outbound traffic is blocked (make egress-test).
- Days 3-4: write 20-30 real questions your team asks (the golden set) and record today's answers and time spent.
- Days 5-7: daily use by one or two people; log what worked and what did not.
- Days 8-10: if documents matter, ingest one folder and test search with citations against the golden set.
- Days 11-12: review the audit log, memory use and speed against the estimates here.
- Days 13-14: decide go / adjust / stop, using the golden-set results and the cost comparison.

1. Full-disk encryption on the host; nftables/ufw `default deny outgoing`; record image digests in .env.
2. Review generated/.env (COMPOSE_PROFILES, runtime settings) and generated/config/policy.yaml.
3. `make secrets` (writes ./secrets/* and .env.secrets, mode 600).
4. Run `make pull` to download only the listed models (the only step that goes online); record model digests (generated/model-pins.txt).
5. Bind Caddy to the LAN IP (BIND_ADDRESS); only port 8443 is published. Ollama 11434, vLLM 8000, Postgres 5432 stay on the internal network.
6. `make up`; once it is running, `make egress-test` must pass before real data goes in.

## Things to Double-Check

- Assumption: Departments: sales, operations, finance, leadership — one collection each.
- Context reduced from 32,768 to 16,384 tokens to fit memory.
- KV cache quantized to q8_0 to fit memory (needs flash attention; small quality cost).
- A security review packet was generated: SECURITY_REVIEW_PACKET.md.
