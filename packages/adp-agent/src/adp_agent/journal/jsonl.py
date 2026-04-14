"""JSONL-backed journal store. One file per deliberation, append-only."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from adj_manifest import (
    BrierScorer,
    CalibrationScore,
    ConditionQualityMetrics,
    ConditionQualityScorer,
    JournalEntry,
    OutcomeObserved,
    ProposalEmitted,
    ScoringPair,
)

from ._serialize import from_json_line, to_json_line
from .store import DeliberationSlice


class JsonlJournalStore:
    """
    JSONL-backed journal store. One file per deliberation under the
    configured journal directory; each line is one serialized entry.
    """

    def __init__(self, journal_dir: str) -> None:
        self._root = Path(journal_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # In-memory index rebuilt from disk on startup and updated on every append.
        self._index: dict[str, list[JournalEntry]] = {}
        self._load_from_disk()

    # ---------- write ----------

    def append(self, entry: JournalEntry) -> None:
        with self._lock:
            self._append_locked(entry)

    def append_batch(self, entries: Iterable[JournalEntry]) -> None:
        with self._lock:
            for entry in entries:
                self._append_locked(entry)

    def _append_locked(self, entry: JournalEntry) -> None:
        path = self._path_for(entry.deliberation_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(to_json_line(entry))
            f.write("\n")
        self._index.setdefault(entry.deliberation_id, []).append(entry)

    # ---------- query ----------

    def get_deliberation(self, deliberation_id: str) -> tuple[JournalEntry, ...]:
        return tuple(self._index.get(deliberation_id, ()))

    def get_outcome(self, deliberation_id: str) -> OutcomeObserved | None:
        entries = self._index.get(deliberation_id, [])
        outcomes = [e for e in entries if isinstance(e, OutcomeObserved)]
        if not outcomes:
            return None
        return max(outcomes, key=lambda o: o.timestamp)

    def get_calibration(self, agent_id: str, domain: str) -> CalibrationScore:
        pairs: list[ScoringPair] = []
        for entries in self._index.values():
            proposals = [
                e for e in entries
                if isinstance(e, ProposalEmitted)
                and e.proposal is not None
                and e.proposal.agent_id == agent_id
                and e.proposal.domain == domain
                and e.proposal.calibration_at_stake
            ]
            outcomes = [e for e in entries if isinstance(e, OutcomeObserved)]
            if not outcomes:
                continue
            latest = max(outcomes, key=lambda o: o.timestamp)
            for p in proposals:
                assert p.proposal is not None  # narrowed above
                pairs.append(ScoringPair(
                    confidence=p.proposal.confidence,
                    outcome=latest.outcome_value,
                    timestamp=latest.observed_at or latest.timestamp,
                ))

        if not pairs:
            return BrierScorer.get_default()
        return BrierScorer.compute(pairs, datetime.now(timezone.utc))

    def get_condition_trace(self, agent_id: str, window: timedelta) -> ConditionQualityMetrics:
        cutoff = datetime.now(timezone.utc) - window
        conditions = []
        for entries in self._index.values():
            for e in entries:
                if (
                    isinstance(e, ProposalEmitted)
                    and e.proposal is not None
                    and e.proposal.agent_id == agent_id
                    and e.timestamp >= cutoff
                ):
                    conditions.extend(e.proposal.dissent_conditions)
        return ConditionQualityScorer.compute(conditions)

    def list_deliberations(self) -> tuple[str, ...]:
        items = sorted(
            self._index.items(),
            key=lambda kv: kv[1][0].timestamp if kv[1] else datetime.min.replace(tzinfo=timezone.utc),
        )
        return tuple(k for k, _ in items)

    def list_deliberations_since(self, since: datetime, limit: int) -> tuple[DeliberationSlice, ...]:
        items = [
            (k, v) for k, v in self._index.items()
            if v and v[0].timestamp >= since
        ]
        items.sort(key=lambda kv: kv[1][0].timestamp)
        items = items[:limit]
        return tuple(DeliberationSlice(k, tuple(v)) for k, v in items)

    def get_all_entries_since(self, since: datetime) -> tuple[JournalEntry, ...]:
        all_entries: list[JournalEntry] = []
        for entries in self._index.values():
            all_entries.extend(e for e in entries if e.timestamp >= since)
        all_entries.sort(key=lambda e: e.timestamp)
        return tuple(all_entries)

    # ---------- private helpers ----------

    def _path_for(self, deliberation_id: str) -> Path:
        for c in ("/", "\\", "..", ":"):
            if c in deliberation_id:
                raise ValueError(f"illegal character {c!r} in deliberation_id")
        return self._root / f"{deliberation_id}.jsonl"

    def _load_from_disk(self) -> None:
        for path in sorted(self._root.glob("*.jsonl")):
            deliberation_id = path.stem
            entries: list[JournalEntry] = []
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entries.append(from_json_line(line))
            entries.sort(key=lambda e: e.timestamp)
            self._index[deliberation_id] = entries


__all__ = ["JsonlJournalStore"]
