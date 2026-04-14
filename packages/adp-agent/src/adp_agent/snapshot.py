"""
Signed calibration snapshot per ADJ §7.4.

The canonical message format is a pipe-delimited string (NOT JSON) and
matches the TypeScript and C# references byte-for-byte:

    {agentId}|{domain}|{value:.4f}|{sampleSize}|{journalHash}|{computedAt}
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .signing import _sign_bytes, _verify_bytes

if TYPE_CHECKING:
    from .config import AgentConfig
    from .journal import RuntimeJournalStore


@dataclass(frozen=True)
class CalibrationSnapshotRecord:
    domain: str
    calibration_value: float
    sample_size: int
    journal_hash: str
    computed_at: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "calibrationValue": self.calibration_value,
            "sampleSize": self.sample_size,
            "journalHash": self.journal_hash,
            "computedAt": self.computed_at,
            "signature": self.signature,
        }


@dataclass(frozen=True)
class CalibrationSnapshotEnvelope:
    agent_id: str
    published_at: str
    snapshots: tuple[CalibrationSnapshotRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "publishedAt": self.published_at,
            "snapshots": [s.to_dict() for s in self.snapshots],
        }


def canonical_snapshot_message(
    agent_id: str,
    domain: str,
    calibration_value: float,
    sample_size: int,
    journal_hash: str,
    computed_at: str,
) -> str:
    """
    Build the exact bytes that get Ed25519-signed for a single snapshot.
    Value is formatted to 4 decimal places to match Brier-score precision
    and avoid cross-language number-format drift.
    """
    value = f"{calibration_value:.4f}"
    return f"{agent_id}|{domain}|{value}|{sample_size}|{journal_hash}|{computed_at}"


def sign_snapshot(
    agent_id: str,
    domain: str,
    calibration_value: float,
    sample_size: int,
    journal_hash: str,
    computed_at: str,
    private_key_hex: str,
) -> str:
    """Sign an unsigned snapshot. Returns a 128-char lowercase hex signature."""
    message = canonical_snapshot_message(
        agent_id, domain, calibration_value, sample_size, journal_hash, computed_at
    ).encode("utf-8")
    return _sign_bytes(message, private_key_hex)


def verify_snapshot(
    agent_id: str,
    snapshot: CalibrationSnapshotRecord,
    public_key_hex: str,
) -> bool:
    """Verify a signed snapshot against a public key. Returns False on any error."""
    try:
        message = canonical_snapshot_message(
            agent_id,
            snapshot.domain,
            snapshot.calibration_value,
            snapshot.sample_size,
            snapshot.journal_hash,
            snapshot.computed_at,
        ).encode("utf-8")
        return _verify_bytes(message, snapshot.signature, public_key_hex)
    except Exception:
        return False


def build_snapshot(
    agent_id: str,
    domain: str,
    journal: "RuntimeJournalStore",
    private_key_hex: str,
) -> CalibrationSnapshotRecord:
    """Build one snapshot for a given (agent, domain) pair by querying the journal."""
    score = journal.get_calibration(agent_id, domain)
    journal_hash = _compute_journal_hash(journal, domain)
    computed_at = _iso_now()
    signature = sign_snapshot(
        agent_id, domain, score.value, score.sample_size,
        journal_hash, computed_at, private_key_hex,
    )
    return CalibrationSnapshotRecord(
        domain=domain,
        calibration_value=score.value,
        sample_size=score.sample_size,
        journal_hash=journal_hash,
        computed_at=computed_at,
        signature=signature,
    )


def build_envelope(
    config: "AgentConfig",
    journal: "RuntimeJournalStore",
) -> CalibrationSnapshotEnvelope:
    """Build the full envelope — one signed snapshot per declared decision class."""
    if config.auth is None or not config.auth.private_key:
        raise RuntimeError(
            "Agent has no signing key configured; cannot publish signed calibration snapshot."
        )

    snapshots = tuple(
        build_snapshot(config.agent_id, domain, journal, config.auth.private_key)
        for domain in config.decision_classes
    )
    return CalibrationSnapshotEnvelope(
        agent_id=config.agent_id,
        published_at=_iso_now(),
        snapshots=snapshots,
    )


def _compute_journal_hash(journal: "RuntimeJournalStore", domain: str) -> str:
    """
    Deterministic hash of the journal state used to produce a calibration value.
    A peer replaying the same journal must get the same value and hash.
    """
    from adj_manifest import ProposalEmitted, OutcomeObserved

    relevant: list[str] = []
    for dlb_id in journal.list_deliberations():
        entries = journal.get_deliberation(dlb_id)
        proposals = [
            e for e in entries
            if isinstance(e, ProposalEmitted)
            and e.proposal is not None
            and e.proposal.domain == domain
            and e.proposal.calibration_at_stake
        ]
        outcomes = [e for e in entries if isinstance(e, OutcomeObserved)]
        latest_outcome = max(outcomes, key=lambda o: o.timestamp) if outcomes else None
        for p in proposals:
            outcome_val = (
                f"{latest_outcome.success:.4f}" if latest_outcome is not None else "-"
            )
            assert p.proposal is not None  # narrowed above
            relevant.append(
                f"{p.proposal.proposal_id}|{p.proposal.confidence:.4f}|{outcome_val}"
            )

    relevant.sort()
    joined = "\n".join(relevant)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CalibrationSnapshotRecord",
    "CalibrationSnapshotEnvelope",
    "canonical_snapshot_message",
    "sign_snapshot",
    "verify_snapshot",
    "build_snapshot",
    "build_envelope",
]
