# ADR-002: Vector and metadata store

Status: Accepted (2026-10-19)

## Context
The prompt suggests Qdrant or Chroma for Tiers 1–2. Our hard requirement is that ACLs and
sensitivity labels apply inside the query, never as a filter in the UI afterwards.

## Decision
- **Tier 1:** SQLite with FTS5 and sqlite-vec, running in-process. No extra service, and the whole store is one file to back up and encrypt.
- **Tiers 2–3:** Postgres with pgvector and tsvector. Metadata, full-text search, vectors and ACLs live in one database, and row-level security enforces ACLs in the same query that ranks results.
- **Alternatives:** Qdrant (payload filters) when vector volume or latency outgrows pgvector; Chroma and LanceDB for Tier 1 experiments.

## Consequences
- There is one fewer service to run and secure.
- Moving to Qdrant at scale means carrying ACL payloads on every point, plus a parity test.
