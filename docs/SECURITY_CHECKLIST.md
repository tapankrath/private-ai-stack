# Pre-go-live security checklist

Tick every box before real data goes in.

## Device
- [ ] Full-disk encryption is on (FileVault / LUKS / BitLocker). Docker volumes and backups live on this disk.
- [ ] OS and Docker are up to date; automatic security updates enabled.
- [ ] Screen lock and a strong login password on the host.

## Network and egress
- [ ] Host firewall denies **outbound** traffic by default (macOS: LuLu or Little Snitch; Linux: nftables/ufw `default deny outgoing`; Windows: Defender Firewall outbound block).
- [ ] Outbound is allowed only temporarily for `make pull` / updates, then closed again.
- [ ] `make egress-test` passes (no container outside the allowlist reaches the internet).
- [ ] Only Caddy publishes a port; `docker compose ps` shows no other `0.0.0.0` bindings.
- [ ] `BIND_ADDRESS` is `127.0.0.1` (tier 1) or a LAN IP (tier 2+), never a public IP.
- [ ] No router port-forwarding. Remote access, if needed, is WireGuard/Tailscale only.
- [ ] macOS with native Ollama: `OLLAMA_HOST` is `127.0.0.1` (the default); host firewall blocks Ollama outbound.

## Supply chain and model pinning
- [ ] Every image in `.env` is pinned by digest, and the digest is recorded.
- [ ] Model licenses checked (`scripts/update-catalog --allow-egress`, then read the mismatches).
- [ ] Model revisions pinned (`pin_revision` in `config/catalog_verification.yaml`).
- [ ] Telemetry flags verified against the pinned Open WebUI / vLLM / Authentik versions.
- [ ] No Ollama `:cloud` model tags anywhere (they run prompts on a third party's servers).
- [ ] `generated/model-pins.txt` recorded; auto-upgrade off; golden-set eval passes before any model promotion (ADR-008).
- [ ] Runtime is not HF TGI (maintenance mode); experimental or closed-source runtimes only with the profile flag set.

## Agents (if any)
- [ ] Always-on agents use pinned models only; watchdog health checks and restart policy are active.
- [ ] Agent write actions require human confirmation with the exact payload.

## Customization (if any)
- [ ] Post-training / distillation: dataset sensitivity review done; base/teacher license allows it; before/after eval recorded.
- [ ] Abliteration: OFF unless ADR-007 conditions are recorded (request, justification, admin approval; never household or children's data).

## Identity and secrets (tier 2+)
- [ ] Open WebUI signup disabled; admin account created with a unique password.
- [ ] Regulated offices: SSO with MFA (Authentik) enforced for every user.
- [ ] `secrets/` and `.env.secrets` are mode 600 and not in git.
- [ ] No passwords for email, banking or social accounts are stored anywhere in this stack.

## Data
- [ ] Sensitivity labels assigned per collection (e.g. everything under `/Clients` is Confidential).
- [ ] Restricted collections stay out of retrieval unless unlocked for the session.
- [ ] Summaries, memory cards and history embeddings inherit the strictest label of their sources (`derived_data` in policy.yaml).
- [ ] Retention set per collection; test a hard delete (rows, vectors, then backup rotation).

## Backups
- [ ] restic repository is encrypted; its password is stored offline, not on this machine.
- [ ] A restore drill was done to a spare disk and the result checked.
- [ ] Offline/rotated copy exists for ransomware recovery.

## Regulated data
- [ ] Your compliance advisor has reviewed the setup. This stack supports privacy controls but does not by itself make you HIPAA, PCI or GDPR compliant.
