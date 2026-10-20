# Your Private AI Setup

> All memory, speed, disk and price figures are estimates, not benchmarks. Model names are catalog examples; check licenses before use.

## In One Sentence

One small general AI model on one server on your network handles your questions and writing; it also searches your own files and past conversations and answers questions about photos. Nothing leaves your control.

- *Expertise*: Use the simplest stack (Ollama + Open WebUI) and follow the numbered setup steps; no command line needed after install.

## What You Need

| Component | What it does for you | Model / tool | Why this one |
|---|---|---|---|
| General assistant | Answers questions, writes and summarizes | Qwen3 8B | Q5_K_M, 8,192-token context, 7.7 GB, ~33.9 tok/s (estimate) |
| Photo and chart questions | Answers questions about photos, charts and diagrams | Qwen2.5-VL 7B Instruct | Q4_K_M, 8,192-token context, 6.1 GB, ~39.3 tok/s (estimate); loaded on demand |
| Search by meaning | Finds documents and past chats by meaning, not just keywords | nomic-embed-text v1.5 | 0.3 GB, small and local |
| Better search ranking | Puts the most relevant passages first | BGE Reranker Base | 0.6 GB, small and local |
| Ollama | Non-technical owners: Ollama + Open WebUI is the easiest stack to run and update. | ollama | Default. Alternatives: llama.cpp server. |
| Open WebUI | Chat UI bound to localhost/LAN, telemetry disabled. | open-webui | Default. Alternatives: AnythingLLM, custom thin UI on the Gateway. |
| Caddy reverse proxy | Caddy (only container with a published port; local CA TLS) | caddy | Default. Alternatives: Traefik. |
| Gateway | The only path to the model: policy, access rules, audit log, Recall, memory and searchable history. | gateway | Default. |
| Encrypted backups (restic) | restic (encrypted) to local disk + optional offline drive | backups | Default. Alternatives: borg. |
| Model pinning | Model files pinned by checksum; no auto-upgrade; a new version must pass the golden-set eval before promotion. | model-pinning | Default. |
| Postgres | Shared conversation, metadata and ACL store for multiple users. | postgres | Default. |
| Per-user accounts | Every person has their own login and history. | accounts | Default. |
| Postgres + pgvector + tsvector | Semantic + keyword (hybrid) search over documents and history. | vector-store | Default. Alternatives: Qdrant + Postgres, Chroma. |
| Docling document parser | Parses PDFs, Office files and email exports locally. | docling | Default. Alternatives: Unstructured, Apache Tika. |
| Local OCR pipeline (Tesseract via Docling) | Scans and images need text extraction. | ocr | Default. Alternatives: PaddleOCR. |

## What You Don't Need

- vLLM / SGLang — Only 2 concurrent session(s), or no NVIDIA GPU; the simpler runtime is enough.
- Speech-to-text (whisper.cpp) — No meetings, voice notes or audio to process.
- SearXNG web research — Web research is off (features.web_research: false).
- SSO (Authentik/Keycloak) — Built-in per-user accounts are enough for this group (features.sso: false).
- Prometheus / Grafana / Loki — Too heavy for this tier; the Gateway health page covers basics.
- Live account connectors (email, social, banking) — None requested. Use exported files (.mbox, notes exports) instead of account access.
- Cloud burst mode — Off by default. The advisor never turns it on.
- Per-user desktop installs — One shared server is simpler to secure, back up and update.
- Code model — Coding is not a primary job; the general model handles occasional code questions.
- Separate small routing/summary model — The general model handles summaries; saves memory at this tier.
- Post-training on your data — Not requested.

## Why Local (Beyond Price)

- **Privacy**: Prompts, documents and answers never leave your network. That is a simpler guarantee than any contract a provider can offer, and it matters for the regulated data you hold.
- **Security**: You control the whole chain: the model, its exact version, the serving software and who can reach it. No outside service joins your attack surface.
- **Availability**: No rate limits, no outages caused by someone else's demand, no surprise deprecations. A validated, pinned model behaves the same next month.

## Your Hardware Fit

- Machine: nvidia (rtx_3060), compute class medium; market tier: consumer ai workstation
- Runtime: Ollama. Non-technical owners: Ollama + Open WebUI is the easiest stack to run and update.
- Memory: needs 8.6 GB of 12.0 GB usable (12 GB VRAM (GPU); models kept fully on GPU); headroom 28.3% (at least 20% required)
- Writing speed: ~33.9 tokens/sec (decode, estimate); target for 'few seconds' is 8 tok/s
- Reading a typical prompt before answering (prefill): ~0.8s (estimate)
- Context: 8,192 tokens; sessions in parallel: 2
- Disk: ~104.8 GB including a year of growth (estimate)
- Setup effort: half a day (estimate)

## Privacy Settings

- Outbound traffic: Deny all outbound by default. Model downloads happen only in a separate `make pull` step.
- Encryption: Full-disk encryption required (FileVault / LUKS / BitLocker); encrypted backups. In transit: TLS on the LAN via Caddy's local CA.
- Remote access: None by default; if needed, WireGuard/Tailscale only. Never port-forward.
- Retention: conversations 365 days; documents keep until deleted; memory cards user managed
- Restricted tier: on (excluded from search unless unlocked for the session)
- Access control: Per-user and per-collection ACLs enforced in the database query (Postgres row-level security), not only in the UI.
- Connectors: none; web research: off; cloud burst: off
- Regulated data present. These controls support privacy obligations but do not by themselves make you compliant with HIPAA, PCI, GDPR or similar; review with your compliance advisor.

## Cost Comparison (Honest View)

| | Local | API (open-weight model) | API (frontier model) | Subscription |
|---|---|---|---|---|
| 3-year cost | $782 | $28 | $428 | $2,700 |
| Low estimate | $782 | $11 | $214 | $2,700 |
| Router fee (included) | – | $1 | – | – |
| Where your prompts go | stay on your machine | sent to the router and whichever provider it routes to | sent to the model provider | sent to the subscription provider |

3-year estimates. On these estimates, a hosted open-weight model API would be cheaper than local by about $754 over three years. Local is recommended here for privacy, security, availability, not price. The saving has a privacy cost: every question, the chat history sent with it and the document passages Recall finds would go to the router and whichever provider it routes to. For this profile that is not just a preference: its regulated data makes it a compliance question.

Local breakdown: hardware $0, power $482, admin time $0, networking $300.
The open-weight API figure includes $1 of router fees (5.5%).
Assumptions: 3 user(s) x 10 queries/day x 22 days/month; hosted APIs bill ~6,500 input tokens per question (2,000 question and instructions + 1,500 chat history resent + 3,000 Recall passages) and 500 output; the low figure counts the question only, at the cheapest provider; 0 agent(s); admin time at $0/hour (your own time is not priced); electricity $0.17/kWh; High-memory machines may hold resale value while memory prices are high; resale is not counted.
Prices not verified in the last 60 days: running costs, subscription price. Update config/pricing.yaml.

## Setup Steps

1. Turn on disk encryption (FileVault on Mac, BitLocker on Windows).
2. Install Docker Desktop.
3. Open a terminal in this folder and run `make secrets`.
4. Run `make pull` to download only the listed models (the only step that goes online).
5. Run `make up`, then open https://localhost:8443 in your browser.

## Things to Double-Check

- Assumption: Children's data present: Restricted tier enabled for kids' school/medical folders.
- Exact chip/GPU not given; speed assumes a conservative rtx_3060.
