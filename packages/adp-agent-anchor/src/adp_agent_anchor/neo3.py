"""
Neo3 JSON-RPC calibration store.

**Status: stub in v0.1.0.** The interface is defined and the options
record is stable, but the actual RPC wiring (build transaction, sign,
broadcast, poll application log for inclusion) is deferred to v0.2.0
alongside distributed deliberation. Adopters who want Neo3 anchoring
today should use the TypeScript runtime's
``@ai-manifests/adp-agent-anchor`` package, which has a working
``Neo3BlockchainStore`` built on ``@cityofzion/neon-js``.

The four chain targets (``neo-express``, ``neo-custom``, ``neo-testnet``,
``neo-mainnet``) all use the same client code and smart contract — only
the RPC URL, contract hash, and signing wallet differ.
"""
from __future__ import annotations

from dataclasses import dataclass

from .blockchain import BlockchainCalibrationStore, CalibrationRecord


@dataclass(frozen=True)
class Neo3StoreOptions:
    rpc_url: str
    contract_hash: str
    private_key: str | None = None
    network_magic: int = 0x334F454E  # Neo3 MainNet
    publish_timeout_seconds: int = 30


class Neo3BlockchainStore(BlockchainCalibrationStore):
    def __init__(self, options: Neo3StoreOptions) -> None:
        self._options = options

    async def get_calibration(self, agent_id: str, domain: str) -> CalibrationRecord | None:
        raise NotImplementedError(
            "Neo3BlockchainStore.get_calibration is a v0.2.0 deliverable. "
            "Use MockBlockchainStore in tests, or the TypeScript adp-agent-anchor runtime "
            "for real chain integration today."
        )

    async def publish_calibration(self, record: CalibrationRecord) -> str:
        raise NotImplementedError(
            "Neo3BlockchainStore.publish_calibration is a v0.2.0 deliverable. "
            "Use MockBlockchainStore in tests, or the TypeScript adp-agent-anchor runtime "
            "for real chain integration today."
        )
