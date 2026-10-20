# Your Private AI Setup

> All memory, speed, disk and price figures are estimates, not benchmarks. Model names are catalog examples; check licenses before use.

## In One Sentence

One mid-size general AI model on one server on your network handles your questions and writing; it also searches your own files and past conversations. Nothing leaves your control.

- *Security review*: A security review packet (data flows, egress policy, threat model, model provenance and licenses, pinning and update process) is generated next to this file.
- *Expertise*: Use the simplest stack (Ollama + Open WebUI) and follow the numbered setup steps; no command line needed after install.

## What You Need

| Component | What it does for you | Model / tool | Why this one |
|---|---|---|---|
| General assistant | Answers questions, writes and summarizes | Gemma 4 26B A4B | Q4_K_M, 16,384-token context, 17.9 GB, ~223.5 tok/s (estimate); MoE: 25.2B total sets memory, 3.8B active sets speed |
| Search by meaning | Finds documents and past chats by meaning, not just keywords | nomic-embed-text v1.5 | 0.3 GB, small and local |
| Better search ranking | Puts the most relevant passages first | BGE Reranker Base | 0.6 GB, small and local |
| Ollama | Simple model management; enough for this concurrency. | ollama | Default. Alternatives: llama.cpp server, vLLM. |
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
| Authentik (OIDC SSO + MFA) | Central login with MFA (requested) | authentik | Default. Alternatives: Keycloak. |
| Role-based access control | Roles from SSO groups decide which collections each user can search. | rbac | Default. |

**Hardware to buy:** Desktop with one RTX 3090 24 GB (used GPU) ($1,300-$1,900, estimate; price last verified: never). It is the smallest listed option that meets this profile.

### Licenses (commercial use)

- **Gemma 4 26B A4B**: Apache-2.0 (OSI open source); commercial use: yes.
- **nomic-embed-text v1.5**: Apache-2.0 (OSI open source); commercial use: yes.
- **BGE Reranker Base**: MIT (OSI open source); commercial use: yes.

## What You Don't Need

- vLLM / SGLang — Only 4 concurrent session(s), or no NVIDIA GPU; the simpler runtime is enough.
- Speech-to-text (whisper.cpp) — No meetings, voice notes or audio to process.
- SearXNG web research — Web research is off (features.web_research: false).
- Prometheus / Grafana / Loki — Too heavy for this tier; the Gateway health page covers basics.
- Live account connectors (email, social, banking) — None requested. Use exported files (.mbox, notes exports) instead of account access.
- Cloud burst mode — Off by default. The advisor never turns it on.
- Per-user desktop installs — One shared server is simpler to secure, back up and update.
- Vision-language model — Reading text from scans and images is enough (OCR); no photo or diagram questions selected.
- Code model — Coding is not a primary job; the general model handles occasional code questions.
- Separate small routing/summary model — The general model handles summaries; saves memory at this tier.
- Post-training on your data — Not requested.

## Why Local (Beyond Price)

- **Privacy**: Prompts, documents and answers never leave your network. That is a simpler guarantee than any contract a provider can offer, and it matters for the regulated data you hold.
- **Security**: You control the whole chain: the model, its exact version, the serving software and who can reach it. No outside service joins your attack surface.
- **Availability**: No rate limits, no outages caused by someone else's demand, no surprise deprecations. A validated, pinned model behaves the same next month.

## Your Hardware Fit

- Machine: nvidia (rtx_3090), compute class medium; market tier: consumer ai workstation
- Runtime: Ollama. Simple model management; enough for this concurrency.
- Memory: needs 18.8 GB of 24.0 GB usable (24 GB usable for models (option spec, estimate)); headroom 21.5% (at least 20% required)
- Writing speed: ~223.5 tokens/sec (decode, estimate); target for 'few seconds' is 8 tok/s
- Reading a typical prompt before answering (prefill): ~1.1s (estimate)
- Context: 16,384 tokens; sessions in parallel: 4
- Disk: ~496.2 GB including a year of growth (estimate)
- Setup effort: half a day (estimate)

## Privacy Settings

- Outbound traffic: None, ever. Models and updates arrive on an offline drive with checksums.
- Encryption: Full-disk encryption required (FileVault / LUKS / BitLocker); encrypted backups. In transit: TLS on the LAN via Caddy's local CA.
- Remote access: None by default; if needed, WireGuard/Tailscale only. Never port-forward.
- Retention: conversations 2555 days; documents keep until deleted; memory cards admin and user managed
- Restricted tier: on (excluded from search unless unlocked for the session)
- Access control: Per-user and per-collection ACLs enforced in the database query (Postgres row-level security), not only in the UI.
- Connectors: none; web research: off; cloud burst: off
- Regulated data present. These controls support privacy obligations but do not by themselves make you compliant with HIPAA, PCI, GDPR or similar; review with your compliance advisor.

## Cost Comparison (Honest View)

| | Local | API (open-weight model) | API (frontier model) | Subscription |
|---|---|---|---|---|
| 3-year cost | $13,638 | $360 | $5,940 | $9,000 |
| Low estimate | $13,638 | $157 | $4,158 | $9,000 |
| Router fee (included) | – | $19 | – | – |
| Where your prompts go | stay on your machine | sent to the router and whichever provider it routes to | sent to the model provider | sent to the subscription provider |

3-year estimates. On these estimates, a hosted open-weight model API would be cheaper than local by about $13,278 over three years. Local is recommended here for privacy, security, availability, not price. The saving has a privacy cost: every question, the chat history sent with it and the document passages Recall finds would go to the router and whichever provider it routes to. For this profile that is not just a preference: its air-gap requirement rules hosted options out.

Local breakdown: hardware $1,600, power $938, admin time $10,800, networking $300.
The open-weight API figure includes $19 of router fees (5.5%).
Assumptions: 10 user(s) x 25 queries/day x 22 days/month; hosted APIs bill ~12,500 input tokens per question (8,000 question and instructions + 1,500 chat history resent + 3,000 Recall passages) and 500 output; the low figure counts the question only, at the cheapest provider; 0 agent(s); admin time at $75/hour; electricity $0.17/kWh; High-memory machines may hold resale value while memory prices are high; resale is not counted.
Prices not verified in the last 60 days: running costs, subscription price, price of Desktop with one RTX 3090 24 GB (used GPU). Update config/pricing.yaml.

## Options If You Want More

Not needed today; these only buy a stronger general model.
- **Good: Mac mini, M4 Pro, 48 GB unified memory** ($1,800-$2,300, estimate) -> Qwen3.6 35B-A3B Q4_K_M, ~82.6 tok/s, 32,768 context
- **Better: AMD Ryzen AI Halo developer platform, 128 GB unified memory** ($3,999-$3,999, estimate) -> Qwen3.6 35B-A3B Q8_0, ~44.2 tok/s, 32,768 context

## Setup Steps

1. Full-disk encryption on the host; nftables/ufw `default deny outgoing`; record image digests in .env.
2. Review generated/.env (COMPOSE_PROFILES, runtime settings) and generated/config/policy.yaml.
3. `make secrets` (writes ./secrets/* and .env.secrets, mode 600).
4. Copy the models from the offline drive (see models-ollama.txt); record model digests (generated/model-pins.txt).
5. Bind Caddy to the LAN IP (BIND_ADDRESS); only port 8443 is published. Ollama 11434, vLLM 8000, Postgres 5432 stay on the internal network.
6. `make up`; once it is running, `make egress-test` must pass before real data goes in.

## Things to Double-Check

- Assumption: Scanned receipts/statements need text extraction only, not photo understanding.
- Assumption: Retention period must be confirmed against the firm's records policy.
- Context reduced from 32,768 to 16,384 tokens to fit memory.
- KV cache quantized to q8_0 to fit memory (needs flash attention; small quality cost).
- A security review packet was generated: SECURITY_REVIEW_PACKET.md.
