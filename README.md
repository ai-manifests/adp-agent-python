# adp-agent-python

[![adp-agent](https://img.shields.io/pypi/v/adp-agent.svg?label=adp-agent)](https://pypi.org/project/adp-agent/)
[![adp-agent-anchor](https://img.shields.io/pypi/v/adp-agent-anchor.svg?label=adp-agent-anchor)](https://pypi.org/project/adp-agent-anchor/)
[![Downloads](https://img.shields.io/pypi/dm/adp-agent.svg?label=adp-agent%20downloads)](https://pypi.org/project/adp-agent/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Spec](https://img.shields.io/badge/spec-adp--manifest.dev-informational)](https://adp-manifest.dev)

Python / FastAPI reference implementation of the [Agent Deliberation Protocol](https://adp-manifest.dev). Monorepo for two PyPI packages, sister project to the TypeScript [`@ai-manifests/adp-agent`](https://github.com/ai-manifests/adp-agent) and the C# [`Adp.Agent`](https://github.com/ai-manifests/adp-agent-csharp) runtimes.

| Package | Description |
|---|---|
| [`adp-agent`](packages/adp-agent) | Protocol runtime — `AdpAgentHost` class, deliberation state machine, journal (JSONL + SQLite), Ed25519 signing, signed calibration snapshots (ADJ §7.4), ACB budget endpoint, FastAPI middleware. |
| [`adp-agent-anchor`](packages/adp-agent-anchor) | Optional Neo3 blockchain anchor — periodically commits signed calibration snapshots to a Neo3-compatible chain for third-party tamper evidence. |

Both packages depend on the Python reference libraries:
- [`adj-manifest`](https://github.com/ai-manifests/adj-ref-lib-py)
- [`adp-manifest`](https://github.com/ai-manifests/adp-ref-lib-py)
- [`acb-manifest`](https://github.com/ai-manifests/acb-ref-lib-py)

## Install

```bash
pip install adp-agent
```

Packages are published to the Gitea PyPI feed at `https://git.marketally.com/api/packages/ai-manifests/pypi/simple`. Configure pip (or `uv`) to use that feed; see each package's README for the exact snippet.

## Minimal use

```python
from adp_agent import AgentConfig, AdpAgentHost
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

See [`adp-agent-template-python`](https://github.com/ai-manifests/adp-agent-template-python) for a forkable starter.

## Dev

```bash
# Install both packages editable with dev deps
pip install -e 'packages/adp-agent[dev]' -e 'packages/adp-agent-anchor[dev]'

# Run tests
pytest packages/adp-agent/tests packages/adp-agent-anchor/tests
```

Requires Python 3.11+. `adj-manifest`, `adp-manifest`, and `acb-manifest` must be resolvable from either PyPI or the Gitea PyPI feed before the editable install works.

## Cross-language parity

Python signatures are bit-compatible with the TypeScript and C# runtimes. A proposal signed in one language verifies in any other. The canonicalize algorithm is a simplified RFC 8785 (JCS) variant: objects get keys sorted alphabetically at every level, arrays keep insertion order, primitives use standard compact JSON, no whitespace. See `packages/adp-agent/src/adp_agent/signing.py` and compare against the TS `signing.ts` / C# `JsonCanonicalizer.cs`.

## License

Apache-2.0 — see [`LICENSE`](LICENSE) for the full license text and [`NOTICE`](NOTICE) for attribution.
