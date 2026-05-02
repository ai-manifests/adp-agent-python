"""
Per-deliberation contribution tracker. Records which agents proposed,
who acknowledged whose falsifications, and dissent-quality flags; at
close, builds the per-agent ParticipantContribution list the
:func:`acb_manifest.settlement.build_settlement_record` consumes for
the ``default-v0`` distribution.

Mirrors the TypeScript runtime's ``ContributionTracker`` in ``acb.ts``
and the C# ``Adp.Agent.Deliberation.ContributionTracker``. The methods
are called by :class:`PeerDeliberation` at well-defined points in the
state machine; everything in here is runtime-mutable state, intentionally
separate from the immutable :mod:`acb_manifest` records.
"""
from __future__ import annotations

from acb_manifest.settlement import ParticipantContribution
from adp_manifest import Proposal, Vote


class ContributionTracker:
    """Runtime contribution tracker. See module docstring."""

    def __init__(self) -> None:
        self._participants: set[str] = set()
        self._acknowledged: dict[str, int] = {}
        self._flagged: set[str] = set()

    def record_proposal(self, agent_id: str) -> None:
        """Mark ``agent_id`` as having submitted a proposal."""
        self._participants.add(agent_id)

    def record_falsification_evidence(
        self, evidence_agent_id: str, target_agent_id: str, condition_id: str
    ) -> None:
        """
        Record a falsification event. The runner calls this for every
        outgoing falsification regardless of the peer's response, but
        only acknowledged falsifications count toward the bonus (see
        :meth:`record_acknowledgement`).
        """
        # No-op — counted only when acknowledged. The shape of the call
        # is preserved for symmetry with the TS / C# runtimes.
        _ = (evidence_agent_id, target_agent_id, condition_id)

    def record_acknowledgement(
        self, evidence_agent_id: str, target_agent_id: str, condition_id: str
    ) -> None:
        """
        Record that the targeted agent acknowledged the falsification
        raised by ``evidence_agent_id``. ACB §6.2 — only acknowledged
        falsifications count toward the falsification bonus, to
        discourage spam.
        """
        _ = (target_agent_id, condition_id)
        self._acknowledged[evidence_agent_id] = self._acknowledged.get(evidence_agent_id, 0) + 1

    def flag_dissent_quality(self, agent_id: str) -> None:
        """Flag an agent's contribution as low-quality. Triggers the dissent-quality penalty."""
        self._flagged.add(agent_id)

    def build(
        self,
        load_bearing_agents: set[str],
        brier_deltas: dict[str, float],
    ) -> list[ParticipantContribution]:
        """
        Build the final per-agent contribution list. ``load_bearing_agents``
        is the set whose votes were load-bearing (their removal would have
        changed the termination state); the runner computes this by replay
        after the final tally. ``brier_deltas`` carries
        ``(confidence − outcome)²`` per-agent when the outcome is known
        at settlement time; pass an empty dict for immediate-mode settlement.
        """
        result: list[ParticipantContribution] = []
        for agent_id in self._participants:
            result.append(ParticipantContribution(
                agent_id=agent_id,
                participated=True,
                acknowledged_falsifications=self._acknowledged.get(agent_id, 0),
                load_bearing=agent_id in load_bearing_agents,
                outcome_brier_delta=brier_deltas.get(agent_id),
                dissent_quality_flagged=agent_id in self._flagged,
            ))
        return result


def compute_load_bearing_agents(
    final_tally,
    weights: dict[str, float],
    threshold: float,
    proposals: list[Proposal],
) -> set[str]:
    """
    Counterfactual load-bearing computation: an agent's vote is
    load-bearing if removing their weight would have dropped approval
    fraction below the convergence threshold. Only computed for agents
    whose final vote was ``approve`` — the load-bearing direction in a
    converged deliberation. Mirrors the TS runtime's
    ``computeLoadBearingAgents`` helper.
    """
    if not getattr(final_tally, "threshold_met", False):
        return set()

    load_bearing: set[str] = set()
    for p in proposals:
        current = p.revisions[-1].new_vote if p.revisions else p.vote
        if current != Vote.APPROVE:
            continue
        w = weights.get(p.agent_id, 0.0)
        if w == 0:
            continue
        new_approve = final_tally.approve_weight - w
        new_non_abstaining = new_approve + final_tally.reject_weight
        new_approval_fraction = (new_approve / new_non_abstaining) if new_non_abstaining > 0 else 0
        if new_approval_fraction < threshold:
            load_bearing.add(p.agent_id)
    return load_bearing
