"""Pluggable blockchain calibration store interface."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CalibrationRecord:
    """The shape of a calibration record as it lives on-chain."""
    agent_id: str
    domain: str
    value: float
    sample_size: int
    timestamp: int  # unix millis
    journal_hash: str


@runtime_checkable
class BlockchainCalibrationStore(Protocol):
    """
    Pluggable interface for committing calibration records to a blockchain
    anchor and reading them back. Implementations: :class:`MockBlockchainStore`
    (in-memory), :class:`Neo3BlockchainStore` (Neo3 RPC client, stubbed in
    v0.1.0). Adopters with a non-Neo3 chain implement this themselves.
    """

    async def get_calibration(self, agent_id: str, domain: str) -> CalibrationRecord | None: ...

    async def publish_calibration(self, record: CalibrationRecord) -> str: ...


__all__ = ["BlockchainCalibrationStore", "CalibrationRecord"]
