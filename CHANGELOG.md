# Changelog

All notable changes to `adp-agent` (Python) and `adp-agent-anchor` (Python) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.1] - 2026-05-02 (anchor only)

### Fixed (packaging) — `adp-agent-anchor`
- **`adp-agent` dependency tightened from `>=0.1.0` to `~=0.5.0`** (i.e.
  `>=0.5.0,<0.6.0`). The previous unbounded floor allowed a future major
  bump of `adp-agent` to silently satisfy the anchor's resolver, even
  when the runtime contract had changed. The two packages are released in
  lockstep across language ports; the dependency spec now reflects that
  contract. `adp-agent` itself is unchanged at `0.5.0`.

### Migration
- Consumers pinning both packages to `0.5.0` are unaffected — pip resolves
  `adp-agent==0.5.0` against `~=0.5.0` cleanly. Consumers who relied on
  pulling an older `adp-agent` (`0.1.x` / `0.4.x`) under a current anchor
  must now upgrade `adp-agent` to `0.5.x`.

## [0.5.0] - 2026-05-02

### Fixed (breaking default change) — ADP §7.2 / §7.3 terminal state classification

`0.4.x` and earlier hardcoded `determine_termination(tally, has_reversible_subset=True)`
in `PeerDeliberation.run`, which meant **every non-converged deliberation
was classified as `partial_commit`**, regardless of whether the action was
actually decomposable. ADP §7.2 explicitly requires both that the action have
independently-executable sub-actions AND that a reversible sub-action meet
simple majority on its own sub-tally; without those, the spec-correct terminal
state is `deadlocked` (§7.3).

The misclassification meant federation-health metrics (notably any "deadlock
rate" derived metric) read zero against federations that were in fact
deadlocking, and any downstream escalation logic that fired on `deadlocked`
(per §7.3 — "the deliberation is escalated with the full debate trace")
never triggered.

### Added
- New optional callback on `PeerDeliberationOptions`:
  ```python
  has_reversible_subset: Callable[[ActionDescriptor, TallyResult], bool] | None = None
  ```
  The runner invokes this with the final tally before classification. When
  omitted (or returns `False`), non-converged outcomes resolve as
  `deadlocked`. When the callback returns `True`, they resolve as
  `partial_commit`. Decomposition is action-kind-specific, so the decision
  belongs to the caller — the runner does not attempt to recompute a
  sub-tally on its own.

### Changed (breaking default)
- Without an explicit `has_reversible_subset` callback, non-converged
  deliberations now resolve as **`deadlocked`** (was `partial_commit`).
  This is the spec-correct default for atomic actions
  (`merge_pull_request`, `deploy`, `revoke_token`, …) which is the vast
  majority of real-world deliberations.

### Migration
- Adopters whose actions are genuinely decomposable
  (`apply_terraform_plan` with per-resource sub-actions, batched-config-change
  PRs with per-file sub-actions, etc.) must add `has_reversible_subset` to
  their `PeerDeliberationOptions` and return `True` only when both
  conditions in §7.2 hold.
- Adopters relying on the `partial_commit` label without actually having a
  reversible subset were already in spec violation; the new default surfaces
  this explicitly. Their `deliberation_closed.termination` values will flip
  from `partial_commit` to `deadlocked` for any deliberation that hits the
  non-converged path. If escalation handlers were keyed on `partial_commit`,
  rewire them to fire on `deadlocked`.

### Tests
- `tests/test_peer_deliberation_termination.py` — covers default-deadlocked,
  explicit-partial-commit, and callback argument shape.

## [0.4.0] - 2026-05-02

> **Version alignment.** This release jumps the Python library from
> `0.1.x` straight to `0.4.0` to align language ports across the family
> (`@ai-manifests/adp-agent@0.4.0` TS, `Adp.Agent@0.4.0` C#,
> `adp-agent==0.4.0` Python). All three publish the same feature surface
> for the distributed deliberation runtime; the version number is now a
> single feature-level marker across all language ports rather than three
> independent counters. Python 0.2.0 / 0.3.0 were never published;
> consumers move from `0.1.0` directly to `0.4.0`.

### Added — Distributed deliberation runtime (feature parity with `@ai-manifests/adp-agent` 0.4.0)

The `0.1.x` Python port shipped the single-agent proposal path only. This
release brings full feature parity with the TypeScript reference runtime's
peer-to-peer deliberation state machine.

**New modules in `adp_agent`:**
- `transport` — `PeerTransport` Protocol with `register_agent`,
  `fetch_manifest`, `fetch_calibration`, `request_proposal`,
  `send_falsification`, `push_journal_entries`. `register_agent` is the
  structural fix for the self-URL → self-agent-id binding bug described
  below. `HttpTransport` is the `httpx`-backed implementation; outbound
  auth headers are resolved via `peer_auth_headers`.
- `contribution` — `ContributionTracker` records per-agent
  participation, falsification acknowledgements, and dissent-quality
  flags. Builds the per-agent `ParticipantContribution` list the
  `acb_manifest.settlement.build_settlement_record` consumes for
  `default-v0` distribution. `compute_load_bearing_agents` matches the
  TS runtime's counterfactual.
- `peer_deliberation` — `PeerDeliberation` is the full state machine
  driver. Discovers peers, registers self, requests proposals (peers +
  self), tallies via `adp_manifest.DeliberationOrchestrator`, runs
  belief-update rounds, emits `RoundEvent` entries (`FalsificationEvidence`,
  `Acknowledge`, `Reject`, `Amend`, `Revise`), produces a
  `DeliberationClosed` entry, and optionally produces an ACB
  `SettlementRecorded` via `acb_manifest.settlement.build_settlement_record`.
  Returns `PeerDeliberationResult` with the full transcript.
  `PeerDeliberationOptions` accepts an optional `BudgetCommitted` and a
  pre-loaded habit-history list.

### Fixed — Initiator self-proposal 401 under bearer-token auth

This is the architectural bug `0.2.0` exists to fix in the Python library
(it shipped untouched in `0.1.x` because the distributed deliberation
runtime wasn't ported yet).

A deliberation runner that authenticates outbound peer calls with
per-peer bearer tokens needs a URL → agent-id map so each call resolves
the right token from `AuthConfig.peer_tokens`. The map is populated as
a side-effect of `fetch_manifest` for peers, but the initiator never
fetches its own manifest — it already knows what's in it. So the self
URL stayed unbound, outgoing self-proposal calls (and the self-journal
calibration fetch, and the journal gossip push) fell back to the
wildcard `'*'` lookup, which produced no `Authorization` header, which
made the agent's own auth middleware reject the call with `401`. The
deliberation aborted with `fetch failed` before any journal entries
were written.

The fix: `PeerDeliberation.run` now calls
`self._transport.register_agent(self_url, self._self.agent_id)`
immediately after binding the self URL in its internal `peer_url_map`,
so subsequent self-proposal and self-journal calls resolve
`peer_tokens[self.agent_id]` correctly. Regression test:
`tests/test_peer_transport.py`.

### Changed (note)

- ACB `BudgetCommitted` and `SettlementRecorded` entries are returned
  out-of-band in `PeerDeliberationResult.settlement` rather than
  written to the `RuntimeJournalStore` (which only accepts
  `adj_manifest.entries.JournalEntry`). Callers that want a unified
  Adj+Acb journal wire the settlement entry to a separate ACB store
  or to a unified persistence layer of their choice. The TS runtime
  appends ACB entries to the same journal because its `JournalStore`
  interface is type-agnostic; the Python port keeps the Adj-only
  interface and surfaces ACB entries explicitly.

### Migration

- Adopters who relied on the `0.1.x` single-agent path (`POST /api/propose`
  + `POST /api/record-outcome`) need no changes — that path is unchanged.
- Adopters who want the distributed path now wire `PeerDeliberation` into
  their own `POST /api/deliberate` handler. The single-agent
  `RuntimeDeliberation` and existing routing remain backward-compatible.

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
