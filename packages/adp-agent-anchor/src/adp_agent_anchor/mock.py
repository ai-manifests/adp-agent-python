"""In-memory blockchain calibration store for tests and dev."""
from __future__ import annotations

import asyncio

from .blockchain import BlockchainCalibrationStore, CalibrationRecord


class MockBlockchainStore(BlockchainCalibrationStore):
    """
    Thread-safe in-memory implementation of
    :class:`BlockchainCalibrationStore`. Stores records keyed by
    ``(agent_id, domain)`` and returns synthetic tx hashes derived from a
    counter. Used in unit tests and for the scheduler's ``target: mock``
    mode.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], CalibrationRecord] = {}
        self._tx_counter = 0
        self._lock = asyncio.Lock()

    async def get_calibration(self, agent_id: str, domain: str) -> CalibrationRecord | None:
        async with self._lock:
            return self._records.get((agent_id, domain))

    async def publish_calibration(self, record: CalibrationRecord) -> str:
        async with self._lock:
            self._records[(record.agent_id, record.domain)] = record
            self._tx_counter += 1
            return f"0xmock{self._tx_counter:016x}"

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def publish_count(self) -> int:
        return self._tx_counter
