# Private AI Stack

A private AI assistant that runs on your own computer or office server. Your questions,
documents and conversation history stay on your machine.

It has two parts:

- The **Right-Size Advisor** asks what you need and checks your hardware, or what you could buy.
  It then recommends the **smallest setup that does the job**, says what you do *not* need, and
  generates the configuration that starts only those pieces. There is also a
  [browser version](site/) that runs entirely on the visitor's machine.
- The **Gateway** is the only path to the model. It enforces access rules, redacts secrets,
  keeps an audit log, and adds Recall (cited answers from your documents and past chats),
  memory cards, retention, export, optional connectors and web research, and a customize track.

## Quickstart

```bash
pip install -e ".[dev]"     # Python 3.10+; PyYAML and jsonschema

make wizard                 # a few plain-language questions -> profile.yaml
make advise                 # then read generated/recommendation.md
```

Or start from an example:

```bash
cp profiles/solo-researcher-16gb.yaml profile.yaml && make advise
```

You will get `generated/recommendation.md`, with these sections: *In One Sentence · What You
Need · What You Don't Need · Why Local (Beyond Price) · Your Hardware Fit · Privacy Settings ·
Cost Comparison (Honest View) · Options If You Want More · Setup Steps · Things to Double-Check*.

## Starter profiles

These are the advisor's actual results (all estimates). See `examples/` for the full output.

| Profile | Gets | Does not get |
|---|---|---|
| `solo-researcher-16gb` (16 GB Mac) | Qwen3 8B (Q4) on native Ollama, document and history search, private web search | vision, speech, code model, server |
| `household-photos-docs` (RTX 3060 12 GB) | Qwen3 8B, OCR, a small vision model loaded on demand, per-user accounts, Restricted tier | vLLM, SSO, code model, speech |
| `law-accounting-office-10` (no hardware yet, under $5k) | Buys a 24 GB GPU desktop; Gemma 4 26B A4B (MoE); SSO + MFA; air-gapped; security review packet | web research, vision, cloud |
| `smb-50-sales-ops` (no hardware yet, under $20k) | Buys a 2-GPU workstation; Qwen3.6 35B-A3B on vLLM; SSO/RBAC; transcription; agent watchdog; customize track | vision, code model, desktop installs |
| `software-team-8` (RTX 3090-class 24 GB) | Qwen3 14B + Qwen2.5-Coder 7B, both loaded; code-aware search; SSO; agent watchdog | vision, speech, OCR |

The honest cost view: for every starter profile, a hosted open-weight API would cost less over
three years. The advisor says so, then gives the profile's reasons for running locally anyway
(privacy, security, availability, customization).

Model names, prices and engine status are **dated examples (October 2026)**. Run
`scripts/update-catalog --allow-egress` and refresh `config/pricing.yaml` before relying on them.

## Running the stack (after `make advise`)

```bash
make secrets                # local passwords/keys in ./secrets (never committed)
make pull                   # downloads ONLY the recommended models; macOS: make pull-native
make up                     # starts ONLY the recommended services
make egress-test            # proves containers cannot reach the internet
make ui-token               # sign in to the Gateway page at https://localhost:8443/gateway/ui
make ingest                 # index the folders listed in config/collections.yaml
make eval                   # golden-set check: run it before and after any model change
```

In the chat UI, pick `<model> + Recall` to answer from your documents and history (with
citations), or `<model> + Web` for private web research when it is enabled. Open WebUI keeps its
own copy of chats in its database; the Gateway's history is the one that Recall, retention and
export use, so set Open WebUI's chat retention to match your policy.

Before going live, work through `docs/SECURITY_CHECKLIST.md`.

## Optional: a local model explains or interviews

```bash
make intake                 # chat-style interview with a LOCAL model -> profile.yaml
make explain                # plain-language rewrite of the recommendation by a LOCAL model
```

These commands only talk to localhost. If the model's text names something the engine did not
pick, calls local "cheaper" when the numbers say otherwise, or drops a section, it is discarded
and the deterministic version is shown instead.

## How it decides

1. **Capabilities** (`config/rules.yaml`): only jobs, data types and explicit flags turn things on.
   Scans get OCR, not a vision model. Web research needs `features.web_research: true`.
2. **Runtime** (`config/runtimes.yaml`):
   - Ollama by default, installed natively on Macs.
   - vLLM for 8+ sessions on NVIDIA.
   - TGI is never chosen (maintenance mode). TensorFold only if experimental engines are
     allowed, and LM Studio only if closed source is accepted.
3. **Fit**: memory is sized from **total** parameters and speed from **active** parameters, so
   mixture-of-experts (MoE) models are fast for their size. The plan:
   - keeps at least 20% of memory free;
   - counts always-on agents as extra sessions;
   - matches hardware to workload: prompt-heavy work favors compute, generation-heavy work
     favors memory bandwidth.
4. **Licenses**: shows open-weight vs. open source. Blocking license triggers need a recorded
   acknowledgement before a model can be picked for commercial use.
5. **Cost** (`advisor/tco.py`, `config/pricing.yaml`): 3-year local vs. API vs. subscription.
   Stale prices are flagged.
6. **Customize track**: post-training and distillation only when requested and justified.
   Abliteration is off unless an admin approves it, and never for households.

## Browser version of the advisor

`site/` is a static page: `index.html`, the engine ported to JavaScript (`advisor-engine.js`) and
the catalog (`advisor-data.js`, built by `python scripts/build-site.py`). It needs no server and
sends nothing anywhere. `tests/test_site_parity.py` checks that it gives the same answers as the
Python engine. The included GitHub Actions workflow publishes `site/` to GitHub Pages: in the
repo settings, set Pages to deploy from GitHub Actions.

## Layout

```
advisor/            engine, sizing, TCO, renderers, CLI, wizard, local-LLM intake/explain
advisor/schema/     profile JSON Schema    advisor/prompts/  system prompt (EXPLAIN + INTAKE)
config/             rules, runtimes, model catalog, pricing, hardware reference, policy example
profiles/           _template.yaml + 5 starter profiles (with test expectations)
examples/           generated output for each starter profile
docs/               ARCHITECTURE.md, IMPLEMENTATION_PLAN.md, adr/ (001-008),
                    SECURITY_CHECKLIST.md, SECURITY_REVIEW_PACKET.md (template)
gateway/            the Gateway: proxy, auth, redaction, store (SQLite or Postgres + RLS), Recall,
                    ingest, memory, retention, export, connectors, fetcher, customize track, web UI
site/               browser version of the advisor (static; GitHub Pages)
deploy/k3s/         manifests for a small cluster, with default-deny network policies
scripts/            update-catalog (opt-in egress), egress-test.sh, backup.sh, restore-drill.sh,
                    build-site.py
tests/              advisor, Gateway (SQLite; Postgres when GATEWAY_TEST_PG is set), site parity
```

## Status

Phases 0 to 6 of `docs/IMPLEMENTATION_PLAN.md` are built and covered by tests, including row-level
security tested against a real Postgres with pgvector.

Not yet proven here: real models and GPUs (tests use a fake runtime and a hashing embedder),
Authentik/OIDC against a live identity provider, the k3s manifests on a cluster, and the full
`docker compose up` on real hardware. Treat the first run on your machine as the pilot, and run
`make egress-test` and `make restore-drill` before trusting it with real data.
