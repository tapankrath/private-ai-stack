# ADR-003: Storage and encryption

Status: Accepted (2026-10-19)

## Context
Vector stores generally do not encrypt their own files. Users want simple guarantees.

## Decision
- **Full-disk encryption is required** (FileVault, LUKS or BitLocker), and backups are encrypted with restic.
- SQLCipher is optional on Tier 1.
- Secrets live in `./secrets` (mode 600) or the OS keychain or vault. No passwords appear in configuration or the database.
- In transit: LAN TLS through Caddy's local CA, and WireGuard or Tailscale for remote access.

## Consequences
- The go-live checklist must verify that disk encryption is on.
- The backup key is stored offline.
