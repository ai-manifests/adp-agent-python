# adp-agent-anchor

[![PyPI](https://img.shields.io/pypi/v/adp-agent-anchor.svg?label=PyPI)](https://pypi.org/project/adp-agent-anchor/)
[![Downloads](https://img.shields.io/pypi/dm/adp-agent-anchor.svg)](https://pypi.org/project/adp-agent-anchor/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/adp-agent-anchor/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Spec](https://img.shields.io/badge/spec-adp--manifest.dev-informational)](https://adp-manifest.dev)

Optional Neo3 blockchain anchor for [`adp-agent`](https://github.com/ai-manifests/adp-agent-python) (Python runtime). Commits signed calibration snapshots to a Neo3-compatible chain on a schedule for third-party tamper evidence.

```bash
pip install adp-agent adp-agent-anchor
```

## Why it's optional

The always-on [signed calibration snapshot](https://adp-manifest.dev) at `/.well-known/adp-calibration.json` (ADJ §7.4) is the primary trust mechanism — peers and registries verify against it with one HTTPS fetch plus a signature check, no chain required. The chain anchor is a **strictly optional** overlay that adds:

1. **Third-party verification** without routing through the registry
2. **Evidence that survives agent disappearance** — the anchored record stays on-chain even if the agent's HTTPS endpoint goes offline
3. **Anti-rewrite defense** — on-chain records are mechanically detectable if an agent later rewrites its journal

## Supported targets

All four targets use the same `Neo3BlockchainStore` client and the same `CalibrationStore.cs` smart contract — only the RPC URL, contract hash, and signing wallet change.

| Target | Use case |
|---|---|
| `mock` | Unit tests (in-memory, no network) |
| `neo-express` | Local dev chain |
| `neo-custom` | Operator's existing private Neo3 chain |
| `neo-testnet` | Public Neo N3 testnet |
| `neo-mainnet` | Public Neo N3 mainnet |

## Usage

```python
from adp_agent import AdpAgentHost
from adp_agent_anchor import BlockchainStoreFactory, CalibrationAnchorScheduler

host = AdpAgentHost(config)

if config.calibration_anchor and config.calibration_anchor.enabled:
    store = BlockchainStoreFactory.create(config.calibration_anchor)
    if store:
        scheduler = CalibrationAnchorScheduler(config, host.journal, store)
        host.after_start(scheduler.start)
        host.before_stop(scheduler.stop)

await host.run()
```

## Status

**v0.1.0: `Neo3BlockchainStore` is stubbed.** The interface is defined and
`MockBlockchainStore` + `CalibrationAnchorScheduler` are fully functional, but
the actual Neo3 RPC client wiring is deferred to v0.2.0. Adopters who need
Neo3 anchoring today should use the TypeScript runtime's
`@ai-manifests/adp-agent-anchor` package, which has a working implementation
built on `@cityofzion/neon-js`.

## License

Apache-2.0 — see [`LICENSE`](LICENSE) for the full license text and [`NOTICE`](NOTICE) for attribution.
