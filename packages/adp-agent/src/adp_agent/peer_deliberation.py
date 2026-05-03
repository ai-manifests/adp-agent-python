"""
Peer-to-peer deliberation state machine. Any agent with the
``initiator`` role can construct one to drive a deliberation: it
discovers peers, requests proposals, tallies, runs belief-update
rounds, and writes a complete journal trace.

This is the Python port of the TypeScript runtime's
``PeerDeliberation`` and the C# ``Adp.Agent.Deliberation.PeerDeliberation``.
The math is delegated to :mod:`adp_manifest` (weighting, tallying,
termination) and :mod:`acb_manifest` (pricing, settlement, habit memory).
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from acb_manifest.entries import BudgetCommitted, SettlementRecorded
from acb_manifest.habit_memory import HistoricalDeliberation, compute_habit_discount
from acb_manifest.pricing import (
    Tally as AcbTally,
    TerminationState as AcbTerminationState,
    compute_disagreement_magnitude,
    compute_draw,
    select_routine,
)
from acb_manifest.settlement import (
    SubstrateReport,
    build_settlement_record,
    SettlementInputs,
)
from adj_manifest import (
    ActionDescriptor,
    DeliberationClosed,
    DeliberationConfig as AdjDeliberationConfig,
    DeliberationOpened,
    JournalEntry,
    OutcomeObserved,
    ProposalData,
    ProposalEmitted,
    RoundEvent,
    TallyRecord,
    ConditionRecord,
)
from adj_manifest.entries import EntryType, EventKind, TerminationState as AdjTerminationState
from adp_manifest import (
    DeliberationOrchestrator,
    DissentConditionStatus,
    Proposal,
    ReversibilityTier,
    TerminationState as AdpTerminationState,
    Vote,
)
from adp_manifest.weighting import compute_weight

from .config import AgentConfig, PeerConfig
from .contribution import ContributionTracker, compute_load_bearing_agents
from .journal import RuntimeJournalStore
from .manifest import AgentManifest
from .transport import PeerTransport


@dataclass(frozen=True)
class PeerDeliberationOptions:
    """
    Optional run-time options for :meth:`PeerDeliberation.run`. Mirrors
    the TS ``DeliberationRunOptions`` and the C#
    ``PeerDeliberationOptions``.

    :param budget: optional ACB budget that funds this deliberation.
    :param habit_history: pre-loaded prior deliberations for ACB habit
        memory; when ``None``, the runner falls back to scanning the
        local journal if it exposes ``get_all_entries``.
    :param has_reversible_subset: ADP §7.2 / §7.3 callback. Returns
        ``True`` when the action under deliberation has a reversible
        subset that can independently commit when convergence on the
        whole action fails. Default ``None`` → treated as ``False``
        → non-converged outcomes resolve as ``DEADLOCKED``. Adopters
        with decomposable actions plug their own logic and return
        ``True`` only when (a) the action's kind is decomposable AND
        (b) the reversible subset has been verified to meet simple
        majority on its own sub-tally.
    """
    budget: BudgetCommitted | None = None
    habit_history: list[HistoricalDeliberation] | None = None
    has_reversible_subset: Callable[[ActionDescriptor, Any], bool] | None = None


@dataclass(frozen=True)
class ProposalSummary:
    agent_id: str
    vote: Vote
    current_vote: Vote
    confidence: float


@dataclass(frozen=True)
class PeerDeliberationResult:
    deliberation_id: str
    status: AdpTerminationState
    rounds: int
    weights: dict[str, float]
    tallies: list[Any]   # adp_manifest.TallyResult
    proposals: list[ProposalSummary]
    settlement: SettlementRecorded | None
    initial_disagreement_magnitude: float | None


class PeerDeliberation:
    """Peer-to-peer deliberation state machine. See module docstring."""

    def __init__(
        self,
        self_config: AgentConfig,
        journal: RuntimeJournalStore,
        peers: list[PeerConfig],
        transport: PeerTransport,
        orchestrator: DeliberationOrchestrator | None = None,
    ) -> None:
        self._self = self_config
        self._journal = journal
        self._peers = peers
        self._transport = transport
        self._orchestrator = orchestrator or DeliberationOrchestrator()

        self._manifests: dict[str, Any] = {}
        self._peer_url_map: dict[str, str] = {}
        self._weights: dict[str, float] = {}
        self._proposals: list[Proposal] = []
        self._tallies: list[Any] = []
        self._journal_entries: list[JournalEntry] = []
        self._contribution_tracker = ContributionTracker()
        self._rounds = 0

    async def run(
        self,
        action: ActionDescriptor,
        tier: ReversibilityTier = ReversibilityTier.PARTIALLY_REVERSIBLE,
        options: PeerDeliberationOptions | None = None,
    ) -> PeerDeliberationResult:
        opts = options or PeerDeliberationOptions()
        dlb_id = f"dlb_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)

        # 1. Discover peers in parallel — a slow or briefly-unavailable
        #    peer must not block discovery for the rest of the federation.
        #    Failed peers are logged and dropped from the participant set.
        async def _discover(peer_url: str) -> tuple[str, AgentManifest | None, BaseException | None]:
            try:
                m = await self._transport.fetch_manifest(peer_url)
                return (peer_url, m, None)
            except BaseException as ex:  # noqa: BLE001 — broad catch is intentional
                return (peer_url, None, ex)

        discovery_results = await asyncio.gather(
            *(_discover(p.url) for p in self._peers),
            return_exceptions=False,
        )
        for url, manifest, err in discovery_results:
            if manifest is not None:
                self._manifests[manifest.agent_id] = manifest
                self._peer_url_map[manifest.agent_id] = url
            else:
                print(f"[deliberation] peer discovery failed for {url}: {err}", file=sys.stderr)

        # Self-manifest. The initiator never fetches its own manifest, so
        # register_agent is the only path that binds the self URL to the
        # self agent id in the transport. Without this, outgoing
        # self-proposal and self-journal calls fall back to wildcard '*'
        # peer-token lookup, which produces no Authorization header,
        # which makes the agent's own auth middleware reject the call
        # with 401.
        self_url = f"http://{self._self.domain}:{self._self.port}"
        self._peer_url_map[self._self.agent_id] = self_url
        self._transport.register_agent(self_url, self._self.agent_id)

        participants = list(self._manifests.keys()) + [self._self.agent_id]

        # ACB budget commit precedes deliberation_opened. ACB entries are
        # different envelopes from Adj entries; we surface the settlement
        # in the result rather than appending to the Adj store.
        if opts.budget is not None:
            constraints = opts.budget.constraints
            if constraints is not None and constraints.max_participants is not None:
                if len(participants) > constraints.max_participants:
                    raise ValueError(
                        f"Budget {opts.budget.budget_id} maxParticipants={constraints.max_participants} "
                        f"exceeded by deliberation with {len(participants)} participants"
                    )

        # Journal: deliberation_opened
        self._journal_entries.append(DeliberationOpened(
            entry_id=_new_entry_id(),
            entry_type=EntryType.DELIBERATION_OPENED,
            deliberation_id=dlb_id,
            timestamp=now,
            prior_entry_hash=None,
            decision_class=self._self.decision_classes[0] if self._self.decision_classes else "default",
            action=action,
            participants=tuple(participants),
            config=AdjDeliberationConfig(max_rounds=3, participation_floor=0.50),
        ))

        # 2. Request proposals from peers in parallel.
        #    Sequential proposal collection is the worst hotspot for
        #    LLM-evaluator peers: each request blocks on the peer's LLM
        #    call (5–30s), so 8 sequential peers take 2+ minutes. Fanning
        #    out cuts wall-clock cost to max(peer time), not sum.
        #    A peer that times out, returns 5xx, or otherwise errors is
        #    logged and dropped; the deliberation continues with whoever
        #    produced a valid proposal (participation_floor enforces
        #    quorum downstream).
        async def _request_one(agent_id: str, manifest: AgentManifest):
            try:
                resp = await self._transport.request_proposal(self._peer_url_map[agent_id], dlb_id, action, tier)
                domain = next(iter(manifest.domain_authorities.keys()), self._self.decision_classes[0])
                domain_auth = manifest.domain_authorities.get(domain)
                authority = domain_auth.authority if domain_auth else 0.5
                cal = await self._transport.fetch_calibration(manifest.journal_endpoint, agent_id, domain)
                return (agent_id, resp, domain, authority, cal, None)
            except BaseException as ex:  # noqa: BLE001
                return (agent_id, None, "", 0.0, None, ex)

        proposal_results = await asyncio.gather(
            *(_request_one(aid, mf) for aid, mf in list(self._manifests.items())),
            return_exceptions=False,
        )
        for agent_id, resp, domain, authority, cal, err in proposal_results:
            if err is not None or resp is None or cal is None:
                print(
                    f"[deliberation] peer proposal failed for {agent_id}: {err}",
                    file=sys.stderr,
                )
                continue
            self._proposals.append(resp.proposal)
            self._contribution_tracker.record_proposal(agent_id)
            self._weights[agent_id] = compute_weight(authority, cal, domain, resp.proposal.stake.magnitude)
            self._journal_entries.append(_build_proposal_emitted(dlb_id, resp.proposal, domain))

        # Self-proposal — same path as peers, exercises auth round-trip
        self_resp = await self._transport.request_proposal(self_url, dlb_id, action, tier)
        self._proposals.append(self_resp.proposal)
        self._contribution_tracker.record_proposal(self._self.agent_id)

        self_domain = self._self.decision_classes[0] if self._self.decision_classes else "default"
        self_authority = self._self.authorities.get(self_domain, 0.5)
        self_journal_endpoint = f"http://{self._self.domain}:{self._self.port}/adj/v0"
        self_cal = await self._transport.fetch_calibration(self_journal_endpoint, self._self.agent_id, self_domain)
        self._weights[self._self.agent_id] = compute_weight(
            self_authority, self_cal, self_domain, self_resp.proposal.stake.magnitude
        )
        self._journal_entries.append(_build_proposal_emitted(dlb_id, self_resp.proposal, self_domain))

        # 3. Round 0 tally
        proposals_by_agent = {p.agent_id: p for p in self._proposals}
        tally = self._orchestrator.tally(proposals_by_agent, self._weights, tier)
        self._tallies.append(tally)
        initial_tally = tally
        initial_magnitude = compute_disagreement_magnitude(
            AcbTally(tally.approve_weight, tally.reject_weight, tally.abstain_weight)
        )

        # 4. Belief-update rounds
        max_rounds = (
            opts.budget.constraints.max_rounds
            if opts.budget and opts.budget.constraints and opts.budget.constraints.max_rounds is not None
            else 3
        )
        for round_n in range(1, max_rounds + 1):
            if tally.converged:
                break
            self._rounds = round_n
            revised = False

            rejecters = [p for p in self._proposals if _current_vote(p) == Vote.REJECT]
            approvers = [p for p in self._proposals if _current_vote(p) == Vote.APPROVE]

            evidence_agent: Proposal | None = None
            if approvers:
                evidence_agent = max(approvers, key=lambda p: self._weights.get(p.agent_id, 0.0))

            for rejecter in list(rejecters):
                active = [dc for dc in rejecter.dissent_conditions if dc.status == DissentConditionStatus.ACTIVE]
                all_falsified = len(active) > 0

                for condition in active:
                    if evidence_agent is None:
                        all_falsified = False
                        continue

                    self._journal_entries.append(_build_round_event(
                        dlb_id, round_n, EventKind.FALSIFICATION_EVIDENCE,
                        evidence_agent.agent_id, rejecter.agent_id, condition.id,
                    ))
                    self._contribution_tracker.record_falsification_evidence(
                        evidence_agent.agent_id, rejecter.agent_id, condition.id
                    )

                    response = await self._transport.send_falsification(
                        self._peer_url_map[rejecter.agent_id], condition.id, round_n, evidence_agent.agent_id
                    )

                    response_kind = {
                        "acknowledge": EventKind.ACKNOWLEDGE,
                        "reject": EventKind.REJECT,
                        "amend": EventKind.AMEND,
                    }.get(response.action, EventKind.REJECT)

                    self._journal_entries.append(_build_round_event(
                        dlb_id, round_n, response_kind, rejecter.agent_id, None, condition.id,
                    ))

                    if response.action == "acknowledge":
                        self._mark_falsified(rejecter, condition.id, round_n, evidence_agent.agent_id)
                        self._contribution_tracker.record_acknowledgement(
                            evidence_agent.agent_id, rejecter.agent_id, condition.id
                        )
                    else:
                        all_falsified = False

                if all_falsified:
                    self._revise_to_abstain(rejecter, round_n, f"All dissent conditions falsified in round {round_n}.")
                    revised = True
                    self._journal_entries.append(_build_round_event(
                        dlb_id, round_n, EventKind.REVISE, rejecter.agent_id, None, None,
                    ))

            if not revised:
                break

            proposals_by_agent = {p.agent_id: p for p in self._proposals}
            tally = self._orchestrator.tally(proposals_by_agent, self._weights, tier)
            self._tallies.append(tally)

        # 5. Close
        # ADP §7.2 / §7.3: reversibility-subset detection is the caller's
        # responsibility because decomposition is action-kind specific.
        # Default-None → treated as False → atomic-action non-convergence
        # resolves as DEADLOCKED. Adopters with decomposable actions plug
        # their own callback in PeerDeliberationOptions.
        reversible_subset = (
            opts.has_reversible_subset(action, tally)
            if opts.has_reversible_subset is not None
            else False
        )
        status = self._orchestrator.determine_termination(tally, has_reversible_subset=reversible_subset)
        self._journal_entries.append(DeliberationClosed(
            entry_id=_new_entry_id(),
            entry_type=EntryType.DELIBERATION_CLOSED,
            deliberation_id=dlb_id,
            timestamp=datetime.now(timezone.utc),
            prior_entry_hash=None,
            termination=_to_adj_termination(status),
            round_count=self._rounds,
            tier=_tier_to_string(tier),
            final_tally=_build_tally_record(tally, tier),
            weights=dict(self._weights),
            committed_action=None if status == AdpTerminationState.DEADLOCKED else action,
        ))

        # 5.5 ACB settlement (immediate-mode here; deferred/two_phase wait for outcome)
        settlement_entry: SettlementRecorded | None = None
        if opts.budget is not None:
            budget = opts.budget
            routine = select_routine(
                budget.pricing,
                AcbTally(initial_tally.approve_weight, initial_tally.reject_weight, initial_tally.abstain_weight),
                self._rounds,
                _to_acb_termination(status),
            )
            unlock_triggered = routine.value == "expensive" if hasattr(routine, "value") else str(routine) == "Routine.EXPENSIVE"

            history = opts.habit_history if opts.habit_history is not None else self._find_habit_history(action, dlb_id)
            habit_discount = compute_habit_discount(history)

            draw_total = compute_draw(budget.pricing, routine, len(self._proposals), self._rounds, habit_discount)

            threshold = self._orchestrator.get_threshold(tier) if hasattr(self._orchestrator, "get_threshold") else 0.60
            load_bearing = compute_load_bearing_agents(tally, self._weights, threshold, self._proposals)
            brier_deltas: dict[str, float] = {}  # immediate mode
            contributions = self._contribution_tracker.build(load_bearing, brier_deltas)

            settlement_entry = build_settlement_record(SettlementInputs(
                entry_id=_new_entry_id(),
                deliberation_id=dlb_id,
                timestamp=datetime.now(timezone.utc),
                prior_entry_hash=None,
                budget_id=budget.budget_id,
                amount_total=budget.amount_total,
                draw_total=draw_total,
                settlement=budget.settlement,
                contributions=contributions,
                substrate_reports=[],
                habit_discount_applied=habit_discount,
                unlock_triggered=unlock_triggered,
                disagreement_magnitude_initial=initial_magnitude,
                outcome_referenced=None,
                signature="self",
            ))

        # 6. Persist + gossip
        for entry in self._journal_entries:
            self._journal.append(entry)
        # Push to all peers + self in parallel — best-effort. A peer that's
        # gone away post-proposal must not block journal distribution to the
        # others (or to self, which writes locally).
        all_urls = [p.url for p in self._peers] + [self_url]
        await asyncio.gather(
            *(self._transport.push_journal_entries(url, self._journal_entries) for url in all_urls),
            return_exceptions=True,
        )

        return PeerDeliberationResult(
            deliberation_id=dlb_id,
            status=status,
            rounds=self._rounds,
            weights=dict(self._weights),
            tallies=list(self._tallies),
            proposals=[
                ProposalSummary(
                    agent_id=p.agent_id, vote=p.vote, current_vote=_current_vote(p), confidence=p.confidence
                )
                for p in self._proposals
            ],
            settlement=settlement_entry,
            initial_disagreement_magnitude=initial_magnitude,
        )

    # ---------- Helpers ----------

    def _mark_falsified(self, rejecter: Proposal, condition_id: str, round_n: int, by_agent: str) -> None:
        idx = next((i for i, p in enumerate(self._proposals) if p.agent_id == rejecter.agent_id), -1)
        if idx < 0:
            return
        proposal = self._proposals[idx]
        new_conditions = []
        for dc in proposal.dissent_conditions:
            if dc.id == condition_id:
                # dataclass(frozen=True) — replace via dataclasses.replace
                from dataclasses import replace as _replace
                new_conditions.append(_replace(
                    dc,
                    status=DissentConditionStatus.FALSIFIED,
                    tested_in_round=round_n,
                    tested_by=by_agent,
                ))
            else:
                new_conditions.append(dc)
        from dataclasses import replace as _replace
        self._proposals[idx] = _replace(proposal, dissent_conditions=tuple(new_conditions))

    def _revise_to_abstain(self, rejecter: Proposal, round_n: int, reason: str) -> None:
        idx = next((i for i, p in enumerate(self._proposals) if p.agent_id == rejecter.agent_id), -1)
        if idx < 0:
            return
        from adp_manifest import VoteRevision
        from dataclasses import replace as _replace
        proposal = self._proposals[idx]
        revision = VoteRevision(
            round=round_n,
            prior_vote=_current_vote(proposal),
            new_vote=Vote.ABSTAIN,
            prior_confidence=proposal.confidence,
            new_confidence=None,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
        )
        self._proposals[idx] = _replace(proposal, revisions=proposal.revisions + (revision,))

    def _find_habit_history(self, action: ActionDescriptor, exclude_dlb_id: str) -> list[HistoricalDeliberation]:
        """
        Default habit-history lookup: scan local journal for prior
        ``DeliberationClosed`` + ``OutcomeObserved`` pairs, score similarity
        on action.kind + action.target. Mirrors the TS ``findHabitHistory``.
        """
        get_all = getattr(self._journal, "get_all_entries", None)
        if get_all is None:
            return []
        entries = [e for e in get_all() if e.deliberation_id != exclude_dlb_id]
        closed_by_dlb: dict[str, DeliberationClosed] = {}
        outcome_by_dlb: dict[str, OutcomeObserved] = {}
        for e in entries:
            if isinstance(e, DeliberationClosed):
                closed_by_dlb[e.deliberation_id] = e
            elif isinstance(e, OutcomeObserved):
                existing = outcome_by_dlb.get(e.deliberation_id)
                if existing is None or e.timestamp > existing.timestamp:
                    outcome_by_dlb[e.deliberation_id] = e

        history: list[HistoricalDeliberation] = []
        for dlb_id, closed in closed_by_dlb.items():
            committed = closed.committed_action
            if committed is None:
                continue
            similarity = 0.0
            if committed.kind == action.kind:
                similarity = 0.5
                if committed.target == action.target:
                    similarity = 1.0
                elif committed.target.split("/")[0] == action.target.split("/")[0]:
                    similarity = 0.85
            if similarity == 0:
                continue
            outcome = outcome_by_dlb.get(dlb_id)
            success = outcome is not None and float(outcome.success) >= 0.5
            history.append(HistoricalDeliberation(similarity=similarity, successful_outcome=success))
        return history


# ---------- module-level helpers ----------

def _new_entry_id() -> str:
    return f"adj_{uuid.uuid4().hex}"


def _current_vote(p: Proposal) -> Vote:
    return p.revisions[-1].new_vote if p.revisions else p.vote


def _tier_to_string(tier: ReversibilityTier) -> str:
    return {
        ReversibilityTier.REVERSIBLE: "reversible",
        ReversibilityTier.PARTIALLY_REVERSIBLE: "partially_reversible",
        ReversibilityTier.IRREVERSIBLE: "irreversible",
    }.get(tier, "partially_reversible")


def _to_adj_termination(s: AdpTerminationState) -> AdjTerminationState:
    return {
        AdpTerminationState.CONVERGED: AdjTerminationState.CONVERGED,
        AdpTerminationState.PARTIAL_COMMIT: AdjTerminationState.PARTIAL_COMMIT,
        AdpTerminationState.DEADLOCKED: AdjTerminationState.DEADLOCKED,
    }[s]


def _to_acb_termination(s: AdpTerminationState) -> AcbTerminationState:
    return {
        AdpTerminationState.CONVERGED: AcbTerminationState.CONVERGED,
        AdpTerminationState.PARTIAL_COMMIT: AcbTerminationState.PARTIAL_COMMIT,
        AdpTerminationState.DEADLOCKED: AcbTerminationState.DEADLOCKED,
    }[s]


def _build_proposal_emitted(dlb_id: str, proposal: Proposal, domain: str) -> ProposalEmitted:
    return ProposalEmitted(
        entry_id=_new_entry_id(),
        entry_type=EntryType.PROPOSAL_EMITTED,
        deliberation_id=dlb_id,
        timestamp=datetime.now(timezone.utc),
        prior_entry_hash=None,
        proposal=ProposalData(
            proposal_id=proposal.proposal_id,
            agent_id=proposal.agent_id,
            vote=proposal.vote.value if hasattr(proposal.vote, "value") else str(proposal.vote).lower(),
            confidence=proposal.confidence,
            domain=domain,
            calibration_at_stake=proposal.stake.calibration_at_stake,
            dissent_conditions=tuple(
                ConditionRecord(
                    id=dc.id,
                    condition=dc.condition,
                    status=dc.status.value if hasattr(dc.status, "value") else str(dc.status).lower(),
                    amendment_count=len(dc.amendments) if hasattr(dc, "amendments") else 0,
                    tested_in_round=dc.tested_in_round,
                )
                for dc in proposal.dissent_conditions
            ),
        ),
    )


def _build_round_event(
    dlb_id: str,
    round_n: int,
    kind: EventKind,
    agent_id: str,
    target_agent_id: str | None,
    target_condition_id: str | None,
) -> RoundEvent:
    return RoundEvent(
        entry_id=_new_entry_id(),
        entry_type=EntryType.ROUND_EVENT,
        deliberation_id=dlb_id,
        timestamp=datetime.now(timezone.utc),
        prior_entry_hash=None,
        round=round_n,
        event_kind=kind,
        agent_id=agent_id,
        target_agent_id=target_agent_id,
        target_condition_id=target_condition_id,
        payload={},
    )


def _build_tally_record(tally: Any, tier: ReversibilityTier) -> TallyRecord:
    threshold = {
        ReversibilityTier.REVERSIBLE: 0.501,
        ReversibilityTier.PARTIALLY_REVERSIBLE: 0.60,
        ReversibilityTier.IRREVERSIBLE: 2.0 / 3.0,
    }.get(tier, 0.60)
    return TallyRecord(
        approve_weight=tally.approve_weight,
        reject_weight=tally.reject_weight,
        abstain_weight=tally.abstain_weight,
        total_weight=tally.total_deliberation_weight,
        approval_fraction=tally.approval_fraction,
        participation_fraction=tally.participation_fraction,
        threshold=threshold,
    )
