"""
:class:`CalibrationAnchorScheduler` — periodic publisher that commits the
agent's current signed calibration snapshot to a Neo3-compatible chain.
Runs as an asyncio task; wired via :meth:`AdpAgentHost.after_start` and
:meth:`AdpAgentHost.before_stop` lifecycle hooks.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .blockchain import BlockchainCalibrationStore, CalibrationRecord

if TYPE_CHECKING:
    from adp_agent import AgentConfig, RuntimeJournalStore


@dataclass(frozen=True)
class AnchorStatusEntry:
    at: datetime
    domain: str
    success: bool
    detail: str


class CalibrationAnchorScheduler:
    def __init__(
        self,
        config: "AgentConfig",
        journal: "RuntimeJournalStore",
        store: BlockchainCalibrationStore,
        interval_seconds: int | None = None,
    ) -> None:
        self._config = config
        self._journal = journal
        self._store = store
        configured = (
            config.calibration_anchor.publish_interval_seconds
            if config.calibration_anchor else 3600
        )
        self._interval = max(30, interval_seconds if interval_seconds is not None else configured)
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._status: list[AnchorStatusEntry] = []

    async def start(self) -> None:
        """Start the periodic publish loop. Idempotent."""
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Request a shutdown. Returns after the current publish cycle finishes."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            self._stop_event = None

    async def publish_now(self) -> None:
        """
        Publish every declared decision class once, immediately. Mirrors the
        TypeScript ``publishNow`` method — used by tests and by adopters who
        want an on-demand commit without waiting for the next timer tick.
        """
        from adp_agent.snapshot import build_snapshot

        auth = self._config.auth
        if auth is None or not auth.private_key:
            self._record(AnchorStatusEntry(
                at=datetime.now(timezone.utc),
                domain="(none)",
                success=False,
                detail="No signing key configured; skipping.",
            ))
            return

        for domain in self._config.decision_classes:
            try:
                snapshot = build_snapshot(
                    self._config.agent_id, domain, self._journal, auth.private_key,
                )
                record = CalibrationRecord(
                    agent_id=self._config.agent_id,
                    domain=domain,
                    value=snapshot.calibration_value,
                    sample_size=snapshot.sample_size,
                    timestamp=int(
                        datetime.fromisoformat(snapshot.computed_at.replace("Z", "+00:00")).timestamp() * 1000
                    ),
                    journal_hash=snapshot.journal_hash,
                )
                tx_hash = await self._store.publish_calibration(record)
                self._record(AnchorStatusEntry(
                    at=datetime.now(timezone.utc),
                    domain=domain,
                    success=True,
                    detail=f"tx={tx_hash}",
                ))
            except Exception as ex:
                self._record(AnchorStatusEntry(
                    at=datetime.now(timezone.utc),
                    domain=domain,
                    success=False,
                    detail=str(ex),
                ))
                print(
                    f"[anchor] Failed to publish calibration for "
                    f"{self._config.agent_id}/{domain}: {ex}"
                )

    @property
    def status(self) -> tuple[AnchorStatusEntry, ...]:
        """Snapshot of recent publish attempts for the status endpoint."""
        return tuple(self._status[-32:])

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.publish_now()
            except Exception as ex:
                print(f"[anchor] Loop iteration failed: {ex}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def _record(self, entry: AnchorStatusEntry) -> None:
        self._status.append(entry)
        if len(self._status) > 128:
            del self._status[:-128]
