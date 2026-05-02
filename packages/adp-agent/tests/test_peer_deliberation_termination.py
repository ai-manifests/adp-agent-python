"""
Regression coverage for ADP §7.2 / §7.3 terminal-state classification.

The runner must default to ``DEADLOCKED`` on non-convergence (atomic
actions are the common case); ``PARTIAL_COMMIT`` requires the caller to
opt in via the ``has_reversible_subset`` callback in
:class:`PeerDeliberationOptions`.
"""
from __future__ import annotations

from adp_manifest import DeliberationOrchestrator, TerminationState, TallyResult

from adp_agent.peer_deliberation import PeerDeliberationOptions


def test_has_reversible_subset_default_is_none() -> None:
    opts = PeerDeliberationOptions()
    assert opts.has_reversible_subset is None


def test_has_reversible_subset_can_be_provided() -> None:
    cb = lambda action, tally: False
    opts = PeerDeliberationOptions(has_reversible_subset=cb)
    assert opts.has_reversible_subset is cb


def test_orchestrator_reversible_subset_false_returns_deadlocked() -> None:
    orch = DeliberationOrchestrator()
    non_converged = TallyResult(
        approve_weight=0.255,
        reject_weight=1.404,
        abstain_weight=0.0,
        total_deliberation_weight=1.659,
        approval_fraction=0.154,
        participation_fraction=1.0,
        threshold_met=False,
        participation_floor_met=True,
        domain_vetoes_clear=True,
        converged=False,
    )
    assert (
        orch.determine_termination(non_converged, has_reversible_subset=False)
        == TerminationState.DEADLOCKED
    )


def test_orchestrator_reversible_subset_true_returns_partial_commit() -> None:
    orch = DeliberationOrchestrator()
    non_converged = TallyResult(
        approve_weight=0.255,
        reject_weight=1.404,
        abstain_weight=0.0,
        total_deliberation_weight=1.659,
        approval_fraction=0.154,
        participation_fraction=1.0,
        threshold_met=False,
        participation_floor_met=True,
        domain_vetoes_clear=True,
        converged=False,
    )
    assert (
        orch.determine_termination(non_converged, has_reversible_subset=True)
        == TerminationState.PARTIAL_COMMIT
    )
