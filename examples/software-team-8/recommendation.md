# Your Private AI Setup

> All memory, speed, disk and price figures are estimates, not benchmarks. Model names are catalog examples; check licenses before use.

## In One Sentence

One mid-size general AI model on one server on your network handles your questions and writing; it also searches your own files and past conversations and helps with code. Nothing leaves your control.

Cost is a concern you raised, so start with **Cost Comparison (Honest View)** below.

- *Cost*: Start with the honest cost comparison below and the smallest pilot; only buy hardware after the pilot proves value.

**Changes since last run:**

- 3-year local cost: 11582 -> 11616

## What You Need

| Component | What it does for you | Model / tool | Why this one |
|---|---|---|---|
| General assistant | Answers questions, writes and summarizes | Qwen3 14B | Q4_K_M, 8,192-token context, 10.9 GB, ~57.4 tok/s (estimate) |
| Coding help | Writes and explains code | Qwen2.5-Coder 7B Instruct | Q4_K_M, 16,384-token context, 6.2 GB, ~111.7 tok/s (estimate) |
| Search by meaning | Finds documents and past chats by meaning, not just keywords | nomic-embed-text v1.5 | 0.3 GB, small and local |
| Better search ranking | Puts the most relevant passages first | BGE Reranker Base | 0.6 GB, small and local |
| Ollama | Fallback so every required model fits. | ollama | Default. |
| Open WebUI | Chat UI bound to localhost/LAN, telemetry disabled. | open-webui | Default. Alternatives: AnythingLLM, custom thin UI on the Gateway. |
| Caddy reverse proxy | Caddy (only container with a published port; local CA TLS) | caddy | Default. Alternatives: Traefik. |
| Gateway | The only path to the model: policy, access rules, audit log, Recall, memory and searchable history. | gateway | Default. |
| Encrypted backups (restic) | restic (encrypted) to local disk + optional offline drive | backups | Default. Alternatives: borg. |
| Model pinning | Model files pinned by checksum; no auto-upgrade; a new version must pass the golden-set eval before promotion. | model-pinning | Default. |
| Postgres | Shared conversation, metadata and ACL store for multiple users. | postgres | Default. |
| Per-user accounts | Every person has their own login and history. | accounts | Default. |
| Postgres + pgvector + tsvector | Semantic + keyword (hybrid) search over documents and history. | vector-store | Default. Alternatives: Qdrant + Postgres, Chroma. |
| Docling document parser | Parses PDFs, Office files and email exports locally. | docling | Default. Alternatives: Unstructured, Apache Tika. |
| SearXNG (private web research) | Private metasearch: searches are not tied to your identity. The only service allowed outbound, and only to search engines. Regulated data is present, so this is a named, audited egress exception you opted into. | searxng | Default. |
| Authentik (OIDC SSO + MFA) | Central login with MFA (requested) | authentik | Default. Alternatives: Keycloak. |
| Role-based access control | Roles from SSO groups decide which collections each user can search. | rbac | Default. |
| Agent watchdog | Always-on agents: health checks, automatic restart, alerts; runs only pinned models. | agent-watchdog | Default. |

### Licenses (commercial use)

- **Qwen3 14B**: Apache-2.0 (OSI open source); commercial use: yes.
- **Qwen2.5-Coder 7B Instruct**: Apache-2.0 (OSI open source); commercial use: yes.
- **nomic-embed-text v1.5**: Apache-2.0 (OSI open source); commercial use: yes.
- **BGE Reranker Base**: MIT (OSI open source); commercial use: yes.

### Customize Track (optional)

- **Post-training (LoRA) on Qwen3 14B**: RAG first. Start only when the golden-set eval shows RAG alone misses domain terms or behavior. Requires: dataset sensitivity review, versioned LoRA adapters, before/after eval on the golden set, runs locally; weights and data never leave.

## What You Don't Need

- vLLM / SGLang — Only 9 concurrent session(s), or no NVIDIA GPU; the simpler runtime is enough.
- OCR pipeline — No scans or images to read.
- Speech-to-text (whisper.cpp) — No meetings, voice notes or audio to process.
- Prometheus / Grafana / Loki — Too heavy for this tier; the Gateway health page covers basics.
- Live account connectors (email, social, banking) — None requested. Use exported files (.mbox, notes exports) instead of account access.
- Cloud burst mode — Off by default. The advisor never turns it on.
- Per-user desktop installs — One shared server is simpler to secure, back up and update.
- Vision-language model — No job needs to understand photos or charts.
- Separate small routing/summary model — The general model handles summaries; saves memory at this tier.
- Distillation to a smaller model — Not requested.

## Why Local (Beyond Price)

- **Privacy**: Prompts, documents and answers never leave your network. That is a simpler guarantee than any contract a provider can offer, and it matters for the regulated data you hold.
- **Availability**: No rate limits, no outages caused by someone else's demand, no surprise deprecations. A validated, pinned model behaves the same next month, which matters most for your always-on agents.
- **Customization**: Open weights let you train on your own material or distill a faster model, locally, without handing data or weights to a provider.
- **Security**: You control the whole chain: the model, its exact version, the serving software and who can reach it. No outside service joins your attack surface.

## Your Hardware Fit

- Machine: nvidia (rtx_3090), compute class medium; market tier: consumer ai workstation
- Runtime: Ollama. Fallback so every required model fits.
- Memory: needs 18.0 GB of 24.0 GB usable (24 GB VRAM (GPU); models kept fully on GPU); headroom 25.0% (at least 20% required)
- Writing speed: ~57.4 tokens/sec (decode, estimate); target for 'instant' is 20 tok/s
- Reading a typical prompt before answering (prefill): ~4.2s (estimate)
- Context: 8,192 tokens; sessions in parallel: 4
- Disk: ~110.5 GB including a year of growth (estimate)
- Setup effort: half a day (estimate)

## Privacy Settings

- Outbound traffic: Deny all outbound by default. Model downloads happen only in a separate `make pull` step. SearXNG may reach search engines (named exception).
- Encryption: Full-disk encryption required (FileVault / LUKS / BitLocker); encrypted backups. In transit: TLS on the LAN via Caddy's local CA.
- Remote access: None by default; if needed, WireGuard/Tailscale only. Never port-forward.
- Retention: conversations 365 days; documents keep until deleted; memory cards user managed
- Restricted tier: on (excluded from search unless unlocked for the session)
- Access control: Per-user and per-collection ACLs enforced in the database query (Postgres row-level security), not only in the UI.
- Connectors: none; web research: on; cloud burst: off
- Regulated data present. These controls support privacy obligations but do not by themselves make you compliant with HIPAA, PCI, GDPR or similar; review with your compliance advisor.

## Cost Comparison (Honest View)

| | Local | API (open-weight model) | API (frontier model) | Subscription |
|---|---|---|---|---|
| 3-year cost | $11,616 | $2,469 | $37,325 | $30,577 |
| Low estimate | $11,616 | $1,395 | $33,903 | $30,577 |
| Router fee (included) | – | $129 | – | – |
| Where your prompts go | stay on your machine | sent to the router and whichever provider it routes to | sent to the model provider | sent to the subscription provider |

3-year estimates. On these estimates, a hosted open-weight model API would be cheaper than local by about $9,147 over three years. Local is recommended here for privacy, availability, customization, not price. The saving has a privacy cost: every question, the chat history sent with it and the document passages Recall finds would go to the router and whichever provider it routes to. For this profile that is not just a preference: its regulated data makes it a compliance question.

Local breakdown: hardware $0, power $516, admin time $10,800, networking $300.
The open-weight API figure includes $129 of router fees (5.5%).
Subscription includes ~$1,777 of API usage for agents (subscriptions rarely cover them).
Assumptions: 8 user(s) x 60 queries/day x 22 days/month; hosted APIs bill ~12,500 input tokens per question (8,000 question and instructions + 1,500 chat history resent + 3,000 Recall passages) and 500 output; the low figure counts the question only, at the cheapest provider; 4 agent(s) always on; admin time at $75/hour; electricity $0.17/kWh; High-memory machines may hold resale value while memory prices are high; resale is not counted.
Prices not verified in the last 60 days: running costs, subscription price. Update config/pricing.yaml.

## Options If You Want More

Not needed today; these only buy a stronger general model.
- **Good: Workstation with two RTX 3090 24 GB (48 GB total, used GPUs)** ($2,600-$3,600, estimate) -> Gemma 4 26B A4B AWQ, ~235.6 tok/s, 8,192 context
- **Better: Mac mini, M4 Pro, 48 GB unified memory** ($1,800-$2,300, estimate) -> Gemma 4 26B A4B Q4_K_M, ~65.2 tok/s, 32,768 context
- **Best: NVIDIA DGX Spark-class, 64 GB unified memory** ($4,999-$4,999, estimate) -> Qwen3.6 35B-A3B AWQ, ~87.0 tok/s, 32,768 context

## Setup Steps

Start with a two-week pilot on the hardware you have:

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

- Assumption: Source code indexed read-only from local git mirrors; no write access to repos.
- vLLM would not leave room for the required specialist model, so this plan uses Ollama (fewer parallel sessions; the rest queue).
- Exact chip/GPU not given; speed assumes a conservative rtx_3090.
- 9 sessions planned but this runtime serves 4 in parallel; the rest queue.
- Context reduced from 32,768 to 8,192 tokens to fit memory.
- KV cache quantized to q8_0 to fit memory (needs flash attention; small quality cost).
- Reading a typical 8,000-token prompt takes ~4.2s before the answer starts (prefill, estimate).
