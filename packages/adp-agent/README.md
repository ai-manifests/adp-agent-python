# adp-agent

Python / FastAPI reference implementation of the [Agent Deliberation Protocol](https://adp-manifest.dev). Sister project to the TypeScript [`@ai-manifests/adp-agent`](https://github.com/ai-manifests/adp-agent) and the C# [`Adp.Agent`](https://github.com/ai-manifests/adp-agent-csharp) runtimes — same wire format, same cross-language signing interop.

## Install

```bash
pip install adp-agent
```

Packages are published to the Gitea PyPI feed. Configure pip once:

```toml
# pyproject.toml
[tool.pip]
extra-index-url = "https://git.marketally.com/api/packages/ai-manifests/pypi/simple"
```

Or with `uv`:

```toml
# pyproject.toml
[[tool.uv.index]]
name = "ai-manifests"
url = "https://git.marketally.com/api/packages/ai-manifests/pypi/simple"
```

## Minimal use

```python
from adp_agent import AgentConfig, AdpAgentHost, JournalBackend
from adp_manifest import StakeMagnitude, Vote


config = AgentConfig(
    agent_id="did:adp:my-agent-v1",
    port=3000,
    domain="my-agent.example.com",
    decision_classes=("code.correctness",),
    authorities={"code.correctness": 0.7},
    stake_magnitude=StakeMagnitude.MEDIUM,
    default_vote=Vote.APPROVE,
    default_confidence=0.65,
    dissent_conditions=("if any test marked critical regresses",),
    journal_dir="./journal",
)

host = AdpAgentHost(config)
await host.run()
```

The `AdpAgentHost` class serves:

- `GET /healthz`
- `GET /.well-known/adp-manifest.json`
- `GET /.well-known/adp-calibration.json` (signed, ADJ §7.4)
- `POST /api/propose`
- `POST /api/respond-falsification`
- `POST /api/deliberate`
- `POST /api/record-outcome`
- `GET  /adj/v0/calibration`
- `GET  /adj/v0/deliberation/{id}`
- `GET  /adj/v0/deliberations`
- `GET  /adj/v0/outcome/{id}`
- `GET  /adj/v0/entries`
- `POST /api/budget`

The adopter implements an `Evaluator` (the function that produces votes) and hands it to the host. See [`adp-agent-template-python`](https://github.com/ai-manifests/adp-agent-template-python) for the full starter pattern.

## With optional chain anchoring

```python
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

Targets: `mock`, `neo-express`, `neo-custom`, `neo-testnet`, `neo-mainnet`. Same code, same smart contract, only the RPC URL, contract hash, and signing wallet differ.

## Feature parity vs TypeScript `@ai-manifests/adp-agent@0.3.0`

See [CHANGELOG.md](../../CHANGELOG.md) for the full feature parity matrix. Short version: everything except distributed deliberation, MCP tool server, and the Neo3 RPC client itself ships in 0.1.0. Those three are v0.2.0 deliverables, stubbed with 501 responses / `NotImplementedError` so adopters get honest errors instead of silent failures.

## License

Apache-2.0 — see [`LICENSE`](LICENSE) for the full license text and [`NOTICE`](NOTICE) for attribution.
