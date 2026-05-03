"""
Peer-to-peer transport for the ADP distributed deliberation runtime.

This module mirrors the TypeScript runtime's ``deliberation.ts``
PeerTransport / HttpTransport surface and the C# runtime's
``Adp.Agent.Deliberation.IPeerTransport`` / ``HttpPeerTransport``. The
three implementations are kept aligned so adopters porting code between
languages don't have to relearn the contract.

The transport owns the URL → agent-id mapping that ``peer_auth_headers``
consumes to build outgoing Authorization headers. The map is populated
both as a side-effect of ``fetch_manifest`` and explicitly via
``register_agent`` — see :class:`PeerTransport` for why the explicit
hook is required for the initiator's own self URL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

import httpx
from adj_manifest import ActionDescriptor, JournalEntry
from adp_manifest import (
    CalibrationScore as AdpCalibrationScore,
    Proposal,
    ReversibilityTier,
)

from .config import AuthConfig
from .manifest import AgentManifest


def get_peer_token(auth: AuthConfig | None, peer_agent_id: str) -> str | None:
    """
    Return the bearer token to use when calling ``peer_agent_id``'s
    protected endpoints. Falls back to a wildcard ``"*"`` entry if no
    peer-specific token is configured; returns ``None`` when no auth
    is configured at all.
    """
    if auth is None or not auth.peer_tokens:
        return None
    direct = auth.peer_tokens.get(peer_agent_id)
    if direct is not None:
        return direct
    return auth.peer_tokens.get("*")


def peer_auth_headers(auth: AuthConfig | None, peer_agent_id: str) -> dict[str, str]:
    """
    Build the outgoing header dictionary for a peer call. Always
    includes ``Content-Type: application/json``; conditionally adds
    ``Authorization: Bearer <token>`` when a peer token is resolved.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    token = get_peer_token(auth, peer_agent_id)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@dataclass(frozen=True)
class PeerProposalResponse:
    """
    Envelope returned by :meth:`PeerTransport.request_proposal`. Wraps
    the rich :class:`adp_manifest.Proposal` with the optional Ed25519
    signature so the caller can verify without re-fetching.
    """
    proposal: Proposal
    signature: str | None


@dataclass(frozen=True)
class FalsificationResponse:
    """
    Response shape from a falsification request. ``action`` is one of
    ``"acknowledge"``, ``"reject"``, or ``"amend"``. When ``"amend"``,
    ``new_condition`` carries the narrowed condition string.
    """
    action: str
    reason: str | None = None
    new_condition: str | None = None


@runtime_checkable
class PeerTransport(Protocol):
    """
    Peer-to-peer transport contract. Implementations own the URL →
    agent-id mapping that outbound calls need to look up the right
    peer-token in :attr:`AuthConfig.peer_tokens`.

    The map is populated automatically as a side-effect of
    :meth:`fetch_manifest`, and explicitly via :meth:`register_agent`
    for paths that don't go through manifest fetch — most importantly
    the initiator's own self URL, since a deliberation's initiator
    never fetches its own manifest. Without :meth:`register_agent`,
    the self URL stays unbound and outgoing calls fall back to the
    wildcard ``"*"`` peer-token lookup, which produces no
    Authorization header and a 401 from the agent's own auth
    middleware.
    """

    def register_agent(self, peer_url: str, agent_id: str) -> None:
        """
        Bind ``peer_url`` to ``agent_id`` in the transport's internal
        map so subsequent outgoing calls to ``peer_url`` use the
        correct peer-token from :attr:`AuthConfig.peer_tokens`.
        """
        ...

    async def fetch_manifest(self, peer_url: str) -> AgentManifest: ...

    async def fetch_calibration(
        self, journal_endpoint: str, agent_id: str, domain: str
    ) -> AdpCalibrationScore: ...

    async def request_proposal(
        self,
        peer_url: str,
        deliberation_id: str,
        action: ActionDescriptor,
        tier: ReversibilityTier,
    ) -> PeerProposalResponse: ...

    async def send_falsification(
        self,
        peer_url: str,
        condition_id: str,
        round: int,
        evidence_agent_id: str,
    ) -> FalsificationResponse: ...

    async def push_journal_entries(
        self, peer_url: str, entries: Iterable[JournalEntry]
    ) -> None: ...


class HttpTransport:
    """
    HTTP implementation of :class:`PeerTransport`. Sends peer calls
    over HTTP using an :class:`httpx.AsyncClient`, with outbound auth
    headers resolved from :attr:`AuthConfig.peer_tokens` via
    :func:`peer_auth_headers`.

    The transport keeps an internal URL → agent-id map so the helper
    can resolve the right peer-token for each outgoing call. The map
    is populated automatically as a side-effect of
    :meth:`fetch_manifest` and explicitly via :meth:`register_agent`.
    """

    # Per-call timeout for slow peer responses (proposal requests block on
    # the peer's evaluator, which may be a 5–30s LLM call). Without these,
    # an unresponsive peer hangs the deliberation indefinitely. Mirrors the
    # equivalent TS / C# defaults.
    _PROPOSAL_TIMEOUT = 60.0
    _FAST_TIMEOUT = 10.0

    def __init__(self, client: httpx.AsyncClient, auth: AuthConfig | None = None) -> None:
        self._client = client
        self._auth = auth
        self._peer_agent_ids: dict[str, str] = {}

    def register_agent(self, peer_url: str, agent_id: str) -> None:
        self._peer_agent_ids[peer_url] = agent_id

    def _headers_for(self, peer_url: str) -> dict[str, str]:
        agent_id = self._peer_agent_ids.get(peer_url, "*")
        return peer_auth_headers(self._auth, agent_id)

    async def fetch_manifest(self, peer_url: str) -> AgentManifest:
        res = await self._client.get(
            f"{peer_url}/.well-known/adp-manifest.json",
            timeout=self._FAST_TIMEOUT,
        )
        if not res.is_success:
            raise RuntimeError(f"Manifest fetch failed: {peer_url} → {res.status_code}")
        body = res.json()
        manifest = _manifest_from_dict(body)
        # Populate the URL → agent-id map as a side-effect, mirroring the
        # TS HttpTransport behavior. The same binding is established by
        # register_agent for paths that bypass manifest discovery.
        self._peer_agent_ids[peer_url] = manifest.agent_id
        return manifest

    async def fetch_calibration(
        self, journal_endpoint: str, agent_id: str, domain: str
    ) -> AdpCalibrationScore:
        try:
            res = await self._client.get(
                f"{journal_endpoint}/calibration",
                params={"agent_id": agent_id, "domain": domain},
                timeout=self._FAST_TIMEOUT,
            )
            if res.is_success:
                body = res.json()
                # The wire shape uses staleness in milliseconds; AdpCalibrationScore
                # uses a timedelta or float depending on the ref-lib's version. Try
                # the common shapes.
                from datetime import timedelta
                staleness_ms = float(body.get("staleness", 0) or 0)
                return AdpCalibrationScore(
                    value=float(body["value"]),
                    sample_size=int(body["sampleSize"]),
                    staleness=timedelta(milliseconds=staleness_ms),
                )
        except Exception:
            pass
        from datetime import timedelta
        return AdpCalibrationScore(value=0.5, sample_size=0, staleness=timedelta())

    async def request_proposal(
        self,
        peer_url: str,
        deliberation_id: str,
        action: ActionDescriptor,
        tier: ReversibilityTier,
    ) -> PeerProposalResponse:
        body: dict[str, Any] = {
            "deliberationId": deliberation_id,
            "action": {
                "kind": action.kind,
                "target": action.target,
                "parameters": dict(action.parameters) if action.parameters else None,
            },
            "tier": tier.value if hasattr(tier, "value") else str(tier),
        }
        res = await self._client.post(
            f"{peer_url}/api/propose",
            json=body,
            headers=self._headers_for(peer_url),
            timeout=self._PROPOSAL_TIMEOUT,
        )
        if not res.is_success:
            raise RuntimeError(f"Proposal request failed: {peer_url} → {res.status_code}")
        envelope = res.json()
        # Server returns either {proposal: {...}, signature: "..."} or just
        # the proposal. Tolerate both shapes.
        proposal_dict = envelope.get("proposal") if "proposal" in envelope else envelope
        signature = envelope.get("signature") if isinstance(envelope, dict) else None
        return PeerProposalResponse(
            proposal=_proposal_from_dict(proposal_dict),
            signature=signature,
        )

    async def send_falsification(
        self,
        peer_url: str,
        condition_id: str,
        round: int,
        evidence_agent_id: str,
    ) -> FalsificationResponse:
        body = {
            "conditionId": condition_id,
            "round": round,
            "evidenceAgentId": evidence_agent_id,
        }
        try:
            res = await self._client.post(
                f"{peer_url}/api/respond-falsification",
                json=body,
                headers=self._headers_for(peer_url),
                timeout=self._PROPOSAL_TIMEOUT,
            )
            if not res.is_success:
                # Per the spec, a non-responding peer's vote stands unchanged.
                # We treat a non-2xx as an implicit reject so the deliberation
                # continues rather than failing.
                return FalsificationResponse(action="reject", reason=f"peer returned {res.status_code}")
            data = res.json()
            return FalsificationResponse(
                action=data.get("action", "reject"),
                reason=data.get("reason"),
                new_condition=data.get("newCondition"),
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResponse(action="reject", reason=f"transport error: {exc}")

    async def push_journal_entries(
        self, peer_url: str, entries: Iterable[JournalEntry]
    ) -> None:
        # Best-effort gossip — peers that reject the push (revoked, suspended,
        # validating) don't break the initiator's transcript.
        try:
            from adj_manifest.entries import journal_entry_to_dict  # type: ignore
            payload = [journal_entry_to_dict(e) for e in entries]
        except Exception:
            payload = [_journal_entry_to_dict(e) for e in entries]
        try:
            await self._client.post(
                f"{peer_url}/adj/v0/entries",
                json=payload,
                headers=self._headers_for(peer_url),
                timeout=self._FAST_TIMEOUT,
            )
        except Exception:
            pass


# ----- helpers (defensive deserialization shared with manifest serving) ----

def _manifest_from_dict(body: dict[str, Any]) -> AgentManifest:
    """Best-effort deserialization of an AgentManifest off the wire."""
    from .manifest import DomainAuthority

    domain_authorities = {
        k: DomainAuthority(authority=float(v["authority"]), source=str(v.get("source", "")))
        for k, v in (body.get("domainAuthorities") or {}).items()
    }
    return AgentManifest(
        agent_id=str(body["agentId"]),
        identity=str(body.get("identity", body.get("agentId"))),
        compliance_level=int(body.get("complianceLevel", 1)),
        decision_classes=tuple(body.get("decisionClasses", ())),
        domain_authorities=domain_authorities,
        journal_endpoint=str(body.get("journalEndpoint", "")),
        public_key=body.get("publicKey"),
        trust_level=str(body.get("trustLevel", "open")),
    )


def _proposal_from_dict(body: Any) -> Proposal:
    """Defensive Proposal deserialization. Imports are deferred so
    optional ref-lib fields don't break the transport at module load time."""
    from datetime import datetime
    from adp_manifest import (
        BlastRadius, DissentCondition, DissentConditionStatus, DomainClaim,
        Justification, ProposalAction, Stake, StakeMagnitude as SM,
        VoteRevision, Vote as V,
    )

    raw = body
    action_raw = raw["action"]
    action = ProposalAction(
        kind=action_raw["kind"],
        target=action_raw["target"],
        parameters=dict(action_raw.get("parameters") or {}),
    )
    stake_raw = raw["stake"]
    stake = Stake(
        declared_by=stake_raw.get("declaredBy", "self"),
        magnitude=SM(stake_raw["magnitude"]) if isinstance(stake_raw["magnitude"], str) else stake_raw["magnitude"],
        calibration_at_stake=bool(stake_raw.get("calibrationAtStake", True)),
    )
    dissent_conditions = tuple(
        DissentCondition(
            id=dc["id"],
            condition=dc["condition"],
            status=DissentConditionStatus(dc.get("status", "active")) if isinstance(dc.get("status"), str) else dc.get("status"),
            amendments=tuple(),
            tested_in_round=dc.get("testedInRound"),
            tested_by=dc.get("testedBy"),
        )
        for dc in (raw.get("dissentConditions") or [])
    )
    revisions: tuple[VoteRevision, ...] = tuple()  # peer-side never carries revisions on round-0 proposal

    return Proposal(
        proposal_id=raw["proposalId"],
        deliberation_id=raw["deliberationId"],
        agent_id=raw["agentId"],
        timestamp=datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
            if isinstance(raw["timestamp"], str) else raw["timestamp"],
        action=action,
        vote=V(raw["vote"]) if isinstance(raw["vote"], str) else raw["vote"],
        confidence=float(raw["confidence"]),
        domain_claim=DomainClaim(
            domain=raw["domainClaim"]["domain"],
            authority_source=raw["domainClaim"]["authoritySource"],
        ),
        reversibility_tier=ReversibilityTier(raw["reversibilityTier"])
            if isinstance(raw["reversibilityTier"], str) else raw["reversibilityTier"],
        blast_radius=BlastRadius(
            scope=tuple(raw.get("blastRadius", {}).get("scope") or ()),
            estimated_users_affected=int(raw.get("blastRadius", {}).get("estimatedUsersAffected", 0)),
            rollback_cost_seconds=int(raw.get("blastRadius", {}).get("rollbackCostSeconds", 0)),
        ),
        justification=Justification(
            summary=raw.get("justification", {}).get("summary", ""),
            evidence_refs=tuple(raw.get("justification", {}).get("evidenceRefs") or ()),
        ),
        stake=stake,
        dissent_conditions=dissent_conditions,
        revisions=revisions,
    )


def _journal_entry_to_dict(entry: JournalEntry) -> dict[str, Any]:
    """Last-resort serializer when the ref lib doesn't expose one."""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(entry):
        return asdict(entry)
    return {"raw": str(entry)}
