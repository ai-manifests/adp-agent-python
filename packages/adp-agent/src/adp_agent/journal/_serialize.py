"""
Polymorphic JSON serializer/deserializer for :class:`JournalEntry` subclasses.
Used by both the JSONL and SQLite backends.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from adj_manifest import (
    ActionDescriptor,
    ConditionRecord,
    DeliberationClosed,
    DeliberationConfig,
    DeliberationOpened,
    JournalEntry,
    OutcomeObserved,
    ProposalData,
    ProposalEmitted,
    RoundEvent,
    TallyRecord,
)
# Enums are defined in adj_manifest.entries but not re-exported from the
# package __init__; import them directly from the submodule.
from adj_manifest.entries import (
    EntryType,
    EventKind,
    OutcomeClass,
    TerminationState,
)


def to_json_line(entry: JournalEntry) -> str:
    """Serialize an entry to a single JSON line suitable for JSONL storage."""
    tree = _to_tree(entry)
    return json.dumps(tree, ensure_ascii=False, separators=(",", ":"))


def from_json_line(line: str) -> JournalEntry:
    """Parse a JSONL line into the correct JournalEntry subclass via discriminator."""
    raw = json.loads(line)
    if not isinstance(raw, dict):
        raise ValueError("journal entry must be a JSON object")
    entry_type = raw.get("entryType") or raw.get("entry_type")
    if not isinstance(entry_type, str):
        raise ValueError("journal entry missing entryType discriminator")

    if entry_type == "deliberation_opened":
        return _build_deliberation_opened(raw)
    if entry_type == "proposal_emitted":
        return _build_proposal_emitted(raw)
    if entry_type == "round_event":
        return _build_round_event(raw)
    if entry_type == "deliberation_closed":
        return _build_deliberation_closed(raw)
    if entry_type == "outcome_observed":
        return _build_outcome_observed(raw)
    raise ValueError(f"unknown journal entryType: {entry_type!r}")


def _to_tree(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result: dict[str, Any] = {}
        for f in dataclasses.fields(value):
            result[_snake_to_camel(f.name)] = _to_tree(getattr(value, f.name))
        return result
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        iso = value.astimezone(timezone.utc).isoformat()
        if iso.endswith("+00:00"):
            iso = iso[:-6] + "Z"
        return iso
    if isinstance(value, dict):
        return {k: _to_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_tree(v) for v in value]
    return value


def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camel_to_snake(name: str) -> str:
    out = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0:
            out.append("_")
        out.append(c.lower())
    return "".join(out)


def _parse_dt(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        raise ValueError(f"expected ISO8601 string, got {type(raw).__name__}")
    iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    return datetime.fromisoformat(iso)


def _base_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": raw["entryId"],
        "entry_type": EntryType(raw["entryType"]),
        "deliberation_id": raw["deliberationId"],
        "timestamp": _parse_dt(raw["timestamp"]),
        "prior_entry_hash": raw.get("priorEntryHash"),
    }


def _build_action(raw: Any) -> ActionDescriptor | None:
    if raw is None:
        return None
    return ActionDescriptor(
        kind=raw.get("kind", ""),
        target=raw.get("target", ""),
        parameters=dict(raw.get("parameters") or {}),
    )


def _build_deliberation_opened(raw: dict[str, Any]) -> DeliberationOpened:
    base = _base_kwargs(raw)
    cfg_raw = raw.get("config")
    config = None
    if cfg_raw is not None:
        config = DeliberationConfig(
            max_rounds=cfg_raw.get("maxRounds", 3),
            participation_floor=cfg_raw.get("participationFloor", 0.5),
        )
    return DeliberationOpened(
        **base,
        decision_class=raw.get("decisionClass", ""),
        action=_build_action(raw.get("action")),
        participants=tuple(raw.get("participants") or ()),
        config=config,
    )


def _build_proposal_data(raw: Any) -> ProposalData | None:
    if raw is None:
        return None
    conditions_raw = raw.get("dissentConditions") or ()
    conditions = tuple(
        ConditionRecord(
            id=c["id"],
            condition=c["condition"],
            status=c["status"],
            amendment_count=c.get("amendmentCount", 0),
            tested_in_round=c.get("testedInRound"),
        )
        for c in conditions_raw
    )
    return ProposalData(
        proposal_id=raw["proposalId"],
        agent_id=raw["agentId"],
        vote=raw["vote"],
        confidence=float(raw["confidence"]),
        domain=raw["domain"],
        calibration_at_stake=bool(raw["calibrationAtStake"]),
        dissent_conditions=conditions,
    )


def _build_proposal_emitted(raw: dict[str, Any]) -> ProposalEmitted:
    return ProposalEmitted(
        **_base_kwargs(raw),
        proposal=_build_proposal_data(raw.get("proposal")),
    )


def _build_round_event(raw: dict[str, Any]) -> RoundEvent:
    return RoundEvent(
        **_base_kwargs(raw),
        round=int(raw.get("round", 0)),
        event_kind=EventKind(raw.get("eventKind", "timeout")),
        agent_id=raw.get("agentId", ""),
        target_agent_id=raw.get("targetAgentId"),
        target_condition_id=raw.get("targetConditionId"),
        payload=raw.get("payload"),
    )


def _build_tally(raw: Any) -> TallyRecord | None:
    if raw is None:
        return None
    return TallyRecord(
        approve_weight=float(raw["approveWeight"]),
        reject_weight=float(raw["rejectWeight"]),
        abstain_weight=float(raw["abstainWeight"]),
        total_weight=float(raw["totalWeight"]),
        approval_fraction=float(raw["approvalFraction"]),
        participation_fraction=float(raw["participationFraction"]),
        threshold=float(raw["threshold"]),
    )


def _build_deliberation_closed(raw: dict[str, Any]) -> DeliberationClosed:
    return DeliberationClosed(
        **_base_kwargs(raw),
        termination=TerminationState(raw.get("termination", "deadlocked")),
        round_count=int(raw.get("roundCount", 0)),
        tier=raw.get("tier", ""),
        final_tally=_build_tally(raw.get("finalTally")),
        weights=dict(raw.get("weights") or {}),
        committed_action=_build_action(raw.get("committedAction")),
    )


def _build_outcome_observed(raw: dict[str, Any]) -> OutcomeObserved:
    return OutcomeObserved(
        **_base_kwargs(raw),
        observed_at=_parse_dt(raw["observedAt"]),
        outcome_class=OutcomeClass(raw.get("outcomeClass", "binary")),
        success=float(raw.get("success", 0.0)),
        evidence_refs=tuple(raw.get("evidenceRefs") or ()),
        reporter_id=raw.get("reporterId", ""),
        reporter_confidence=float(raw.get("reporterConfidence", 0.0)),
        ground_truth=bool(raw.get("groundTruth", False)),
        supersedes=raw.get("supersedes"),
    )


__all__ = ["to_json_line", "from_json_line"]
