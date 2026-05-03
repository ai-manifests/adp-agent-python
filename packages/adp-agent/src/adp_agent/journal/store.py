"""
Runtime journal store protocol and supporting types.

The ``adj-manifest`` ref lib ships ``InMemoryJournalStore`` as its Level-3
reference implementation. The runtime extends that read-only query surface
with write operations and batch-query operations it needs during live
deliberation: append, append_batch, list_deliberations,
list_deliberations_since, get_all_entries_since.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Protocol, runtime_checkable

from adj_manifest import (
    CalibrationScore,
    ConditionQualityMetrics,
    JournalEntry,
    OutcomeObserved,
)


@dataclass(frozen=True)
class DeliberationSlice:
    deliberation_id: str
    entries: tuple[JournalEntry, ...]


@runtime_checkable
class RuntimeJournalStore(Protocol):
    """
    Runtime journal store — the interface the rest of the runtime codes
    against. Implementations: :class:`JsonlJournalStore`,
    :class:`SqliteJournalStore`. Adopters can implement their own and pass
    it to :class:`AdpAgentHost` via the ``journal`` parameter.
    """

    def append(self, entry: JournalEntry) -> None: ...

    def append_batch(self, entries: Iterable[JournalEntry]) -> None: ...

    def get_deliberation(self, deliberation_id: str) -> tuple[JournalEntry, ...]: ...

    def get_outcome(self, deliberation_id: str) -> OutcomeObserved | None: ...

    def get_calibration(self, agent_id: str, domain: str) -> CalibrationScore: ...

    def get_condition_trace(
        self, agent_id: str, window: timedelta
    ) -> ConditionQualityMetrics: ...

    def list_deliberations(self) -> tuple[str, ...]: ...

    def list_deliberations_since(
        self, since: datetime, limit: int
    ) -> tuple[DeliberationSlice, ...]: ...

    def get_all_entries_since(self, since: datetime) -> tuple[JournalEntry, ...]: ...


__all__ = ["RuntimeJournalStore", "DeliberationSlice"]
