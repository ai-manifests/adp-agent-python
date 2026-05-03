"""
Runtime glue between :class:`Evaluator`, :class:`Proposal` construction,
proposal signing, and journal persistence.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from adj_manifest import (
    ActionDescriptor as AdjActionDescriptor,
    ConditionRecord,
    OutcomeObserved,
    ProposalData,
    ProposalEmitted,
)
from adj_manifest.entries import EntryType, OutcomeClass
from adp_manifest import (
    BlastRadius,
    DissentCondition,
    DomainClaim,
    Justification,
    Proposal,
    ProposalAction as AdpProposalAction,
    ReversibilityTier,
    Stake,
)

from .config import AgentConfig
from .evaluator import EvaluationRequest, Evaluator
from .journal import RuntimeJournalStore
from .signing import sign_proposal


@dataclass(frozen=True)
class SignedProposal:
    proposal: Proposal
    signature: str | None


class RuntimeDeliberation:
    """
    v0.1.0 single-agent proposal path. Runs the evaluator, builds a
    signed proposal, writes :class:`ProposalEmitted` to the journal,
    returns the signed proposal.

    Full distributed deliberation (belief-update rounds, peer falsification,
    termination) is deferred to v0.2.0 to match the C# port's scope.
    """

    def __init__(
        self,
        config: AgentConfig,
        journal: RuntimeJournalStore,
        evaluator: Evaluator,
    ) -> None:
        self._config = config
        self._journal = journal
        self._evaluator = evaluator

    async def run_proposal(
        self,
        deliberation_id: str,
        action: AdjActionDescriptor,
        tier: ReversibilityTier,
        decision_class: str,
    ) -> SignedProposal:
        eval_result = await self._evaluator.evaluate(EvaluationRequest(
            deliberation_id=deliberation_id,
            action=action,
            tier=tier,
            decision_class=decision_class,
        ))

        proposal_id = f"prp_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)

        proposal = Proposal(
            proposal_id=proposal_id,
            deliberation_id=deliberation_id,
            agent_id=self._config.agent_id,
            timestamp=now,
            action=AdpProposalAction(
                kind=action.kind,
                target=action.target,
                parameters=dict(action.parameters),
            ),
            vote=eval_result.vote,
            confidence=eval_result.confidence,
            domain_claim=DomainClaim(
                domain=decision_class,
                authority_source=f"mcp-manifest:{self._config.agent_id}#authorities",
            ),
            reversibility_tier=tier,
            blast_radius=BlastRadius(
                scope=(),
                estimated_users_affected=0,
                rollback_cost_seconds=0,
            ),
            justification=Justification(
                summary=eval_result.rationale,
                evidence_refs=tuple(eval_result.evidence_refs),
            ),
            stake=Stake(
                declared_by=self._config.agent_id,
                magnitude=self._config.stake_magnitude,
                calibration_at_stake=True,
            ),
            dissent_conditions=self._build_dissent_conditions(),
            revisions=(),
        )

        signature: str | None = None
        if self._config.auth and self._config.auth.private_key:
            signature = sign_proposal(proposal, self._config.auth.private_key)

        entry = ProposalEmitted(
            entry_id=f"adj_{uuid.uuid4().hex}",
            entry_type=EntryType.PROPOSAL_EMITTED,
            deliberation_id=deliberation_id,
            timestamp=now,
            prior_entry_hash=None,
            proposal=ProposalData(
                proposal_id=proposal_id,
                agent_id=self._config.agent_id,
                vote=eval_result.vote.value,
                confidence=eval_result.confidence,
                domain=decision_class,
                calibration_at_stake=True,
                dissent_conditions=self._build_condition_records(),
            ),
        )
        self._journal.append(entry)

        return SignedProposal(proposal=proposal, signature=signature)

    def record_outcome(
        self,
        deliberation_id: str,
        success: float,
        reporter_id: str,
        reporter_confidence: float,
        ground_truth: bool,
        evidence_refs: Iterable[str] = (),
        outcome_class: OutcomeClass = OutcomeClass.BINARY,
    ) -> None:
        now = datetime.now(timezone.utc)
        entry = OutcomeObserved(
            entry_id=f"adj_{uuid.uuid4().hex}",
            entry_type=EntryType.OUTCOME_OBSERVED,
            deliberation_id=deliberation_id,
            timestamp=now,
            prior_entry_hash=None,
            observed_at=now,
            outcome_class=outcome_class,
            success=success,
            evidence_refs=tuple(evidence_refs),
            reporter_id=reporter_id,
            reporter_confidence=reporter_confidence,
            ground_truth=ground_truth,
            supersedes=None,
        )
        self._journal.append(entry)

    def _build_dissent_conditions(self) -> tuple[DissentCondition, ...]:
        return tuple(
            DissentCondition.create(
                id=f"dc_{self._config.agent_id}_{i:03d}",
                condition=text,
            )
            for i, text in enumerate(self._config.dissent_conditions)
        )

    def _build_condition_records(self) -> tuple[ConditionRecord, ...]:
        return tuple(
            ConditionRecord(
                id=f"dc_{self._config.agent_id}_{i:03d}",
                condition=text,
                status="active",
                amendment_count=0,
                tested_in_round=None,
            )
            for i, text in enumerate(self._config.dissent_conditions)
        )


__all__ = ["RuntimeDeliberation", "SignedProposal"]
