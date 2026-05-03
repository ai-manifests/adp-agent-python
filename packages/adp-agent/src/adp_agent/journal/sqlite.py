"""SQLite-backed journal store."""
from __future__ import annotations

import sqlite3
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


class SqliteJournalStore:
    """
    SQLite-backed journal store. One row per entry under a single
    ``journal.db`` file at the configured journal directory.
    """

    def __init__(self, journal_dir: str) -> None:
        root = Path(journal_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        db_path = root / "journal.db"
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS entries (
                    entry_id TEXT PRIMARY KEY,
                    deliberation_id TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    json_payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_entries_deliberation
                    ON entries (deliberation_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_entries_timestamp
                    ON entries (timestamp);
            """)

    # ---------- write ----------

    def append(self, entry: JournalEntry) -> None:
        with self._lock:
            self._insert(entry)

    def append_batch(self, entries: Iterable[JournalEntry]) -> None:
        with self._lock:
            self._conn.execute("BEGIN;")
            try:
                for entry in entries:
                    self._insert(entry)
                self._conn.execute("COMMIT;")
            except Exception:
                self._conn.execute("ROLLBACK;")
                raise

    def _insert(self, entry: JournalEntry) -> None:
        ts = entry.timestamp.isoformat()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO entries
                (entry_id, deliberation_id, entry_type, timestamp, json_payload)
            VALUES (?, ?, ?, ?, ?);
            """,
            (
                entry.entry_id,
                entry.deliberation_id,
                entry.entry_type.value,
                ts,
                to_json_line(entry),
            ),
        )

    # ---------- query ----------

    def get_deliberation(self, deliberation_id: str) -> tuple[JournalEntry, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT json_payload FROM entries WHERE deliberation_id = ? ORDER BY timestamp ASC;",
                (deliberation_id,),
            ).fetchall()
        return tuple(from_json_line(r[0]) for r in rows)

    def get_outcome(self, deliberation_id: str) -> OutcomeObserved | None:
        entries = self.get_deliberation(deliberation_id)
        outcomes = [e for e in entries if isinstance(e, OutcomeObserved)]
        if not outcomes:
            return None
        return max(outcomes, key=lambda o: o.timestamp)

    def get_calibration(self, agent_id: str, domain: str) -> CalibrationScore:
        with self._lock:
            rows = self._conn.execute(
                "SELECT deliberation_id, json_payload FROM entries ORDER BY deliberation_id, timestamp;"
            ).fetchall()

        by_dlb: dict[str, tuple[list[ProposalEmitted], OutcomeObserved | None]] = {}
        for dlb_id, payload in rows:
            entry = from_json_line(payload)
            proposals, outcome = by_dlb.get(dlb_id, ([], None))
            if (
                isinstance(entry, ProposalEmitted)
                and entry.proposal is not None
                and entry.proposal.agent_id == agent_id
                and entry.proposal.domain == domain
                and entry.proposal.calibration_at_stake
            ):
                proposals.append(entry)
            elif isinstance(entry, OutcomeObserved):
                if outcome is None or entry.timestamp > outcome.timestamp:
                    outcome = entry
            by_dlb[dlb_id] = (proposals, outcome)

        pairs: list[ScoringPair] = []
        for proposals, outcome in by_dlb.values():
            if outcome is None:
                continue
            for p in proposals:
                assert p.proposal is not None
                pairs.append(ScoringPair(
                    confidence=p.proposal.confidence,
                    outcome=outcome.outcome_value,
                    timestamp=outcome.observed_at or outcome.timestamp,
                ))

        if not pairs:
            return BrierScorer.get_default()
        return BrierScorer.compute(pairs, datetime.now(timezone.utc))

    def get_condition_trace(self, agent_id: str, window: timedelta) -> ConditionQualityMetrics:
        cutoff = (datetime.now(timezone.utc) - window).isoformat()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT json_payload FROM entries
                WHERE entry_type = 'proposal_emitted' AND timestamp >= ?
                ORDER BY timestamp ASC;
                """,
                (cutoff,),
            ).fetchall()
        conditions = []
        for (payload,) in rows:
            entry = from_json_line(payload)
            if isinstance(entry, ProposalEmitted) and entry.proposal is not None and entry.proposal.agent_id == agent_id:
                conditions.extend(entry.proposal.dissent_conditions)
        return ConditionQualityScorer.compute(conditions)

    def list_deliberations(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT deliberation_id FROM entries
                GROUP BY deliberation_id
                ORDER BY MIN(timestamp) ASC;
                """
            ).fetchall()
        return tuple(r[0] for r in rows)

    def list_deliberations_since(self, since: datetime, limit: int) -> tuple[DeliberationSlice, ...]:
        with self._lock:
            id_rows = self._conn.execute(
                """
                SELECT deliberation_id FROM entries
                GROUP BY deliberation_id
                HAVING MIN(timestamp) >= ?
                ORDER BY MIN(timestamp) ASC
                LIMIT ?;
                """,
                (since.isoformat(), limit),
            ).fetchall()
        return tuple(
            DeliberationSlice(r[0], self.get_deliberation(r[0])) for r in id_rows
        )

    def get_all_entries_since(self, since: datetime) -> tuple[JournalEntry, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT json_payload FROM entries
                WHERE timestamp >= ?
                ORDER BY timestamp ASC;
                """,
                (since.isoformat(),),
            ).fetchall()
        return tuple(from_json_line(r[0]) for r in rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["SqliteJournalStore"]
