# Changelog

All notable changes to `adp-agent` (Python) and `adp-agent-anchor` (Python) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-14

### Added

Initial Python / .NET 11+ port of the TypeScript `@ai-manifests/adp-agent` runtime. Ships alongside the C# `Adp.Agent@0.1.0` port and wire-compatible with both.

**`adp-agent` (PyPI package):**
- `AdpAgentHost` class — entry point adopters instantiate
- `AgentConfig` dataclass with runtime configuration
- `Evaluator` protocol + `ShellEvaluator` + `StaticEvaluator` implementations
- `RuntimeJournalStore` protocol + `JsonlJournalStore` + `SqliteJournalStore` backends
- Ed25519 proposal signing via `cryptography.hazmat` with recursive canonical JSON matching the TypeScript `@ai-manifests/adp-agent@^0.3.0` algorithm byte-for-byte (simplified RFC 8785 / JCS variant)
- Signed calibration snapshot builder + verifier per ADJ §7.4
- HTTP endpoints via FastAPI: `/healthz`, `/.well-known/adp-manifest.json`, `/.well-known/adp-calibration.json`, `/api/propose`, `/api/record-outcome`, `/api/budget`, `/adj/v0/calibration`, `/adj/v0/deliberation/{id}`, `/adj/v0/deliberations`, `/adj/v0/outcome/{id}`, `/adj/v0/entries`
- Bearer-token auth middleware with `hmac.compare_digest` constant-time comparison
- Fixed-window rate limiter middleware
- `JournalEntryValidator` for runtime-side entry validation (embedded in the deliberation path)

**`adp-agent-anchor` (PyPI package):**
- `BlockchainCalibrationStore` protocol + `CalibrationRecord` dataclass
- `MockBlockchainStore` — in-memory implementation for tests and dev
- `CalibrationAnchorScheduler` — async periodic publish loop with status history
- `BlockchainStoreFactory.create(config)` for wire-up

### Feature parity matrix vs TypeScript `@ai-manifests/adp-agent@0.3.0`

| Feature | TS 0.3.0 | Py 0.1.0 | Notes |
|---|:---:|:---:|---|
| Agent manifest serving                      | ✓ | ✓ | |
| Signed calibration snapshots (ADJ §7.4)     | ✓ | ✓ | |
| Ed25519 proposal signing                    | ✓ | ✓ | Bit-identical canonicalize |
| JSONL journal                               | ✓ | ✓ | |
| SQLite journal                              | ✓ | ✓ | |
| Single-agent proposal emission              | ✓ | ✓ | |
| `POST /api/record-outcome`                  | ✓ | ✓ | |
| ADJ §7.1 query endpoints                    | ✓ | ✓ | |
| Bearer-token auth                           | ✓ | ✓ | |
| Rate limiting                               | ✓ | ✓ | |
| `POST /api/budget` (ACB defaults)           | ✓ | ✓ | Budget not persisted in v0.1.0 |
| Distributed deliberation (belief update)    | ✓ | ✗ | Deferred to v0.2.0 |
| Peer-to-peer HTTP transport                 | ✓ | ✗ | Deferred to v0.2.0 |
| MCP tool server                             | ✓ | ✗ | Deferred to v0.2.0 |
| `Neo3BlockchainStore` actual chain calls    | ✓ | ✗ | Stub; deferred to v0.2.0 |
| `MockBlockchainStore`                       | ✓ | ✓ | |
| Calibration anchor scheduler                | ✓ | ✓ | |
| Shell evaluator                             | ✓ | ✓ | |

### Known limitations

- Distributed deliberation, MCP tool server, and Neo3 RPC client are scheduled for v0.2.0. Adopters who need those features today should use the TypeScript runtime — all three implementations share the same wire format and can coexist in a mixed federation (once TS is at v0.3.0+).
- Cross-language golden-vector parity tests are on the backlog; v0.1.0 ships self-consistent signing and the same recursive canonicalizer as TS/C#, but no test fixture yet pins specific signature bytes that must match across languages.
