"""Factory for resolving the right store from a CalibrationAnchorConfig."""
from __future__ import annotations

from .blockchain import BlockchainCalibrationStore
from .mock import MockBlockchainStore
from .neo3 import Neo3BlockchainStore, Neo3StoreOptions


class BlockchainStoreFactory:
    """
    Resolves the right :class:`BlockchainCalibrationStore` from a runtime
    :class:`adp_agent.CalibrationAnchorConfig`. Matches the
    ``createAnchorStore`` helper in the TypeScript runtime and the
    ``BlockchainStoreFactory.Create`` helper in the C# runtime.
    """

    @staticmethod
    def create(config) -> BlockchainCalibrationStore | None:
        """
        Build the store for a given config. Returns ``None`` if required
        fields are missing (and the target is not ``mock``).
        """
        if not getattr(config, "enabled", False):
            return None

        target = getattr(config, "target", "mock")
        if target == "mock":
            return MockBlockchainStore()

        rpc_url = getattr(config, "rpc_url", None)
        contract_hash = getattr(config, "contract_hash", None)
        if not rpc_url or not contract_hash:
            return None

        return Neo3BlockchainStore(Neo3StoreOptions(
            rpc_url=rpc_url,
            contract_hash=contract_hash,
            private_key=getattr(config, "private_key", None),
            network_magic=int(getattr(config, "network_magic", 0x334F454E) or 0x334F454E),
            publish_timeout_seconds=int(getattr(config, "publish_timeout_seconds", 30)),
        ))
