"""
FastAPI routes for the ADP runtime.

- /healthz, /.well-known/adp-manifest.json, /.well-known/adp-calibration.json
- /api/propose, /api/respond-falsification, /api/deliberate, /api/record-outcome
- /api/budget
- /adj/v0/calibration, /adj/v0/deliberation/{id}, /adj/v0/deliberations,
  /adj/v0/outcome/{id}, /adj/v0/entries
- /mcp (stub; 501)
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from adj_manifest import ActionDescriptor
from adj_manifest.entries import OutcomeClass
from adp_manifest import ReversibilityTier

from .config import AgentConfig
from .deliberation import RuntimeDeliberation
from .journal import RuntimeJournalStore
from .manifest import AgentManifest
from .snapshot import build_envelope


def _json(value: Any) -> Any:
    """Best-effort dataclass/enum/datetime → plain JSON tree."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {_camel(f.name): _json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        iso = value.astimezone(timezone.utc).isoformat()
        return iso.replace("+00:00", "Z") if iso.endswith("+00:00") else iso
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


# ---------- Request / response models ----------

class ActionRequest(BaseModel):
    kind: str
    target: str
    parameters: dict[str, str] | None = None


class ProposeRequest(BaseModel):
    deliberationId: str
    action: ActionRequest
    tier: str | None = None
    decisionClass: str | None = None


class RecordOutcomeRequest(BaseModel):
    deliberationId: str
    success: float
    reporterId: str
    reporterConfidence: float
    groundTruth: bool = False
    evidenceRefs: list[str] | None = None
    outcomeClass: str | None = None


class BudgetRequest(BaseModel):
    deliberationId: str
    amountTotal: float | None = None


# ---------- Route registration ----------

def register_routes(
    app: FastAPI,
    config: AgentConfig,
    journal: RuntimeJournalStore,
    runtime: RuntimeDeliberation,
) -> None:
    _register_manifest(app, config, journal)
    _register_journal(app, journal)
    _register_deliberation(app, config, runtime)
    _register_acb(app, config)
    _register_mcp(app)


def _register_manifest(
    app: FastAPI, config: AgentConfig, journal: RuntimeJournalStore,
) -> None:
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "agentId": config.agent_id}

    @app.get("/.well-known/adp-manifest.json")
    async def manifest():
        return AgentManifest.from_config(config).to_dict()

    @app.get("/.well-known/adp-calibration.json")
    async def calibration_snapshot():
        if config.auth is None or not config.auth.private_key:
            raise HTTPException(
                status_code=503,
                detail="Agent has no signing key configured; cannot publish signed calibration",
            )
        try:
            return build_envelope(config, journal).to_dict()
        except Exception as ex:
            raise HTTPException(status_code=500, detail=f"failed to build calibration snapshot: {ex}")


def _register_journal(app: FastAPI, journal: RuntimeJournalStore) -> None:
    @app.get("/adj/v0/calibration")
    async def get_calibration(agentId: str, domain: str):
        score = journal.get_calibration(agentId, domain)
        return _json(score)

    @app.get("/adj/v0/deliberation/{id}")
    async def get_deliberation(id: str):
        entries = journal.get_deliberation(id)
        if not entries:
            raise HTTPException(status_code=404, detail={"error": "deliberation_not_found", "deliberationId": id})
        return {"deliberationId": id, "entries": [_json(e) for e in entries]}

    @app.get("/adj/v0/deliberations")
    async def list_deliberations(since: str | None = None, limit: int = 100):
        dt = _parse_since(since)
        lim = max(1, min(limit, 10_000))
        slices = journal.list_deliberations_since(dt, lim)
        return {
            "deliberations": [
                {"deliberationId": s.deliberation_id, "entries": [_json(e) for e in s.entries]}
                for s in slices
            ]
        }

    @app.get("/adj/v0/outcome/{id}")
    async def get_outcome(id: str):
        outcome = journal.get_outcome(id)
        if outcome is None:
            raise HTTPException(status_code=404, detail={"error": "outcome_not_found", "deliberationId": id})
        return _json(outcome)

    @app.get("/adj/v0/entries")
    async def get_entries(since: str | None = None):
        dt = _parse_since(since)
        entries = journal.get_all_entries_since(dt)
        return {"entries": [_json(e) for e in entries]}


def _register_deliberation(
    app: FastAPI, config: AgentConfig, runtime: RuntimeDeliberation,
) -> None:
    @app.post("/api/propose")
    async def propose(body: ProposeRequest):
        action = ActionDescriptor(
            kind=body.action.kind,
            target=body.action.target,
            parameters=dict(body.action.parameters or {}),
        )
        tier = _parse_tier(body.tier)
        decision_class = body.decisionClass or (
            config.decision_classes[0] if config.decision_classes else "default"
        )
        signed = await runtime.run_proposal(body.deliberationId, action, tier, decision_class)
        return {
            "proposal": _json(signed.proposal),
            "signature": signed.signature,
        }

    @app.post("/api/respond-falsification")
    async def respond_falsification():
        raise HTTPException(
            status_code=501,
            detail={
                "error": "not_implemented",
                "message": "Falsification response handling is not yet ported to the Python runtime (v0.2.0).",
            },
        )

    @app.post("/api/deliberate")
    async def deliberate():
        raise HTTPException(
            status_code=501,
            detail={
                "error": "not_implemented",
                "message": (
                    "Distributed deliberation initiation is not yet ported to the Python runtime (v0.2.0). "
                    "Until then, the Python runtime supports single-agent proposal emission via POST /api/propose."
                ),
            },
        )

    @app.post("/api/record-outcome")
    async def record_outcome(body: RecordOutcomeRequest):
        outcome_class = OutcomeClass(body.outcomeClass) if body.outcomeClass else OutcomeClass.BINARY
        runtime.record_outcome(
            deliberation_id=body.deliberationId,
            success=body.success,
            reporter_id=body.reporterId,
            reporter_confidence=body.reporterConfidence,
            ground_truth=body.groundTruth,
            evidence_refs=tuple(body.evidenceRefs or ()),
            outcome_class=outcome_class,
        )
        return {"status": "recorded"}


def _register_acb(app: FastAPI, config: AgentConfig) -> None:
    @app.post("/api/budget")
    async def budget(body: BudgetRequest):
        if config.acb is None:
            raise HTTPException(status_code=503, detail={"error": "acb_not_configured"})
        # acb_manifest types — imported lazily to keep the Adp.Agent module's
        # cold-import cost low for adopters who don't use ACB.
        from acb_manifest import BudgetCommitted, BudgetConstraints  # type: ignore
        import uuid
        amount = body.amountTotal if body.amountTotal is not None else config.acb.default_amount_total
        now = datetime.now(timezone.utc)
        committed = BudgetCommitted(
            entry_id=f"acb_{uuid.uuid4().hex}",
            deliberation_id=body.deliberationId,
            timestamp=now,
            prior_entry_hash=None,
            budget_id=f"bgt_{uuid.uuid4().hex}",
            budget_authority=config.acb.budget_authority,
            posted_at=now,
            denomination=config.acb.denomination,
            amount_total=amount,
            pricing=config.acb.pricing,
            settlement=config.acb.settlement,
            constraints=config.acb.constraints or BudgetConstraints(
                max_participants=8, max_rounds=4, irrevocable=False,
            ),
            signature="unsigned-v0",  # TODO v0.2.0: ACB entry signing
        )
        return {"budget": _json(committed)}


def _register_mcp(app: FastAPI) -> None:
    @app.get("/mcp")
    async def mcp_stub():
        raise HTTPException(
            status_code=501,
            detail={
                "error": "not_implemented",
                "message": "MCP tool server is not yet ported to the Python runtime (v0.2.0). Use the TypeScript runtime @ai-manifests/adp-agent if you need MCP integration today.",
            },
        )


def _parse_since(since: str | None) -> datetime:
    if not since:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        iso = since.replace("Z", "+00:00") if since.endswith("Z") else since
        return datetime.fromisoformat(iso)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _parse_tier(tier: str | None) -> ReversibilityTier:
    if not tier:
        return ReversibilityTier.PARTIALLY_REVERSIBLE
    try:
        return ReversibilityTier(tier)
    except ValueError:
        return ReversibilityTier.PARTIALLY_REVERSIBLE


__all__ = ["register_routes"]
