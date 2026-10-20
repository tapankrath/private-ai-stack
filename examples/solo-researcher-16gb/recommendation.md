# Your Private AI Setup

> All memory, speed, disk and price figures are estimates, not benchmarks. Model names are catalog examples; check licenses before use.

## In One Sentence

One small general AI model on your computer handles your questions and writing; it also searches your own files and past conversations. Nothing leaves your control.

- *Where to begin*: Follow the two-week pilot plan on the hardware you already have.

**Changes since last run:**

- 3-year local cost: 97 -> 46

## What You Need

| Component | What it does for you | Model / tool | Why this one |
|---|---|---|---|
| General assistant | Answers questions, writes and summarizes | Qwen3 8B | Q4_K_M, 8,192-token context, 6.8 GB, ~11.1 tok/s (estimate) |
| Search by meaning | Finds documents and past chats by meaning, not just keywords | nomic-embed-text v1.5 | 0.3 GB, small and local |
| Better search ranking | Puts the most relevant passages first | BGE Reranker Base | 0.6 GB, small and local |
| Ollama (installed natively on macOS) | Ollama for easy model management; MLX is the faster option for power users. | ollama-native | Default. Alternatives: MLX (mlx-lm server), llama.cpp server. |
| Open WebUI | Chat UI bound to localhost/LAN, telemetry disabled. | open-webui | Default. Alternatives: AnythingLLM, custom thin UI on the Gateway. |
| Caddy reverse proxy | Caddy (only container with a published port; local CA TLS) | caddy | Default. Alternatives: Traefik. |
| Gateway | The only path to the model: policy, access rules, audit log, Recall, memory and searchable history. | gateway | Default. |
| Encrypted backups (restic) | restic (encrypted) to local disk + optional offline drive | backups | Default. Alternatives: borg. |
| Model pinning | Model files pinned by checksum; no auto-upgrade; a new version must pass the golden-set eval before promotion. | model-pinning | Default. |
| sqlite-vec + SQLite FTS5 (in-process, no extra service) | Semantic + keyword (hybrid) search over documents and history. | vector-store | Default. Alternatives: LanceDB, Chroma, Qdrant. |
| Docling document parser | Parses PDFs, Office files and email exports locally. | docling | Default. Alternatives: Unstructured, Apache Tika. |
| SearXNG (private web research) | Private metasearch: searches are not tied to your identity. The only service allowed outbound, and only to search engines. | searxng | Default. |

## What You Don't Need

- vLLM / SGLang — Only 1 concurrent session(s), or no NVIDIA GPU; the simpler runtime is enough.
- Postgres server — Single user: SQLite on the encrypted disk is enough.
- OCR pipeline — No scans or images to read.
- Speech-to-text (whisper.cpp) — No meetings, voice notes or audio to process.
- SSO (Authentik/Keycloak) — Single user on localhost.
- Prometheus / Grafana / Loki — Too heavy for this tier; the Gateway health page covers basics.
- Live account connectors (email, social, banking) — None requested. Use exported files (.mbox, notes exports) instead of account access.
- Cloud burst mode — Off by default. The advisor never turns it on.
- Dedicated GPU server — One user on their own machine does not need a server.
- Vision-language model — No job needs to understand photos or charts.
- Code model — Coding is not a primary job; the general model handles occasional code questions.
- Separate small routing/summary model — The general model handles summaries; saves memory at this tier.
- Post-training on your data — Not requested.

## Why Local (Beyond Price)

- **Privacy**: Prompts, documents and answers never leave this computer. That is a simpler guarantee than any contract a provider can offer.
- **Availability**: No rate limits, no outages caused by someone else's demand, no surprise deprecations. A validated, pinned model behaves the same next month.
- **Security**: You control the whole chain: the model, its exact version, the serving software and who can reach it. No outside service joins your attack surface.

## Your Hardware Fit

- Machine: apple (apple_m2), compute class low; market tier: consumer ai workstation
- Runtime: Ollama (native). Ollama for easy model management; MLX is the faster option for power users.
- Memory: needs 7.7 GB of 10.7 GB usable (67% of 16 GB unified memory (macOS GPU limit, default)); headroom 27.9% (at least 20% required)
- Writing speed: ~11.1 tokens/sec (decode, estimate); target for 'few seconds' is 8 tok/s
- Reading a typical prompt before answering (prefill): ~11.4s (estimate)
- Context: 8,192 tokens; sessions in parallel: 1
- Disk: ~29.0 GB including a year of growth (estimate)
- Setup effort: about 1 hour (estimate)

## Privacy Settings

- Outbound traffic: Deny all outbound by default. Model downloads happen only in a separate `make pull` step. SearXNG may reach search engines (named exception).
- Encryption: Full-disk encryption required (FileVault / LUKS / BitLocker); encrypted backups. In transit: localhost only.
- Remote access: None by default; if needed, WireGuard/Tailscale only. Never port-forward.
- Retention: conversations 730 days; documents keep until deleted; memory cards user managed
- Restricted tier: available, not required
- Access control: n/a (single user)
- Connectors: none; web research: on; cloud burst: off

## Cost Comparison (Honest View)

| | Local | API (open-weight model) | API (frontier model) | Subscription |
|---|---|---|---|---|
| 3-year cost | $46 | $38 | $570 | $900 |
| Low estimate | $46 | $15 | $285 | $900 |
| Router fee (included) | – | $2 | – | – |
| Where your prompts go | stay on your machine | sent to the router and whichever provider it routes to | sent to the model provider | sent to the subscription provider |

3-year estimates. On these estimates, a hosted open-weight model API would be cheaper than local by about $8 over three years. Local is recommended here for privacy, availability, security, not price. The saving has a privacy cost: every question, the chat history sent with it and the document passages Recall finds would go to the router and whichever provider it routes to.

Local breakdown: hardware $0, power $46, admin time $0, networking $0.
The open-weight API figure includes $2 of router fees (5.5%).
Assumptions: 1 user(s) x 40 queries/day x 22 days/month; hosted APIs bill ~6,500 input tokens per question (2,000 question and instructions + 1,500 chat history resent + 3,000 Recall passages) and 500 output; the low figure counts the question only, at the cheapest provider; 0 agent(s); admin time at $0/hour (your own time is not priced); electricity $0.17/kWh; High-memory machines may hold resale value while memory prices are high; resale is not counted.
Prices not verified in the last 60 days: running costs, subscription price. Update config/pricing.yaml.

## Setup Steps

Start with a two-week pilot on the hardware you have:

- Days 1-2: run the advisor, install only the recommended runtime and model, confirm outbound traffic is blocked (make egress-test).
- Days 3-4: write 20-30 real questions your team asks (the golden set) and record today's answers and time spent.
- Days 5-7: daily use by one or two people; log what worked and what did not.
- Days 8-10: if documents matter, ingest one folder and test search with citations against the golden set.
- Days 11-12: review the audit log, memory use and speed against the estimates here.
- Days 13-14: decide go / adjust / stop, using the golden-set results and the cost comparison.

1. Enable full-disk encryption and a default-deny outbound firewall.
2. Install Ollama natively (Docker on macOS cannot use the GPU); install Docker + Compose.
3. `cp generated/.env .env && make secrets`.
4. Run `make pull-native` to download only the listed models.
5. `make up` (UI on https://localhost:8443).

## Things to Double-Check

- Offline operation is required: web research works only while a connection is available.
- Assumption: Occasional code questions handled by general model.
- Exact chip/GPU not given; speed assumes a conservative apple_m2.
- Reading a typical 2,000-token prompt takes ~11.4s before the answer starts (prefill, estimate). Prompt-heavy work favors higher-compute GPUs.
