"""
Signing tests for the Python runtime. Mirror the C# SigningTests and the
TypeScript signing.test.ts regression set — including the v0.3.0 nested
tamper detection test that proves the recursive canonicalize closes the
integrity hole the old replacer-array filter left open.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from adj_manifest import ActionDescriptor  # noqa: F401
from adp_manifest import (
    BlastRadius,
    DomainClaim,
    Justification,
    Proposal,
    ProposalAction,
    ReversibilityTier,
    Stake,
    StakeMagnitude,
    Vote,
)

from adp_agent.signing import (
    canonicalize,
    canonicalize_value,
    generate_key_pair,
    sign_proposal,
    verify_proposal,
)


def _build_proposal() -> Proposal:
    return Proposal(
        proposal_id="prp_test_001",
        deliberation_id="dlb_test_001",
        agent_id="did:adp:test-agent",
        timestamp=datetime(2026, 4, 11, 14, 32, 9, 221000, tzinfo=timezone.utc),
        action=ProposalAction(
            kind="merge_pull_request",
            target="github.com/acme/api#4471",
            parameters={},
        ),
        vote=Vote.APPROVE,
        confidence=0.86,
        domain_claim=DomainClaim(domain="code.correctness", authority_source="test"),
        reversibility_tier=ReversibilityTier.PARTIALLY_REVERSIBLE,
        blast_radius=BlastRadius(
            scope=("service:api",),
            estimated_users_affected=12000,
            rollback_cost_seconds=90,
        ),
        justification=Justification(
            summary="All tests pass",
            evidence_refs=(),
        ),
        stake=Stake(
            declared_by="self",
            magnitude=StakeMagnitude.HIGH,
            calibration_at_stake=True,
        ),
        dissent_conditions=(),
        revisions=(),
    )


def test_generate_key_pair_returns_valid_hex_keys():
    pub, priv = generate_key_pair()
    assert len(pub) == 64
    assert len(priv) == 64
    assert all(c in "0123456789abcdef" for c in pub)
    assert all(c in "0123456789abcdef" for c in priv)


def test_canonicalize_is_deterministic():
    proposal = _build_proposal()
    assert canonicalize(proposal) == canonicalize(proposal)


def test_canonicalize_value_sorts_keys_recursively():
    # Two objects that are semantically equal but have keys in different
    # insertion order. Canonical form must match byte-for-byte.
    a = {"b": 2, "a": {"y": 20, "x": 10}, "c": [1, 2]}
    b = {"a": {"x": 10, "y": 20}, "c": [1, 2], "b": 2}
    assert canonicalize_value(a) == canonicalize_value(b)
    assert canonicalize_value(a) == '{"a":{"x":10,"y":20},"b":2,"c":[1,2]}'


def test_sign_and_verify_roundtrip():
    proposal = _build_proposal()
    pub, priv = generate_key_pair()
    signature = sign_proposal(proposal, priv)
    assert len(signature) == 128
    assert verify_proposal(proposal, signature, pub)


def test_verify_rejects_tampered_top_level_confidence():
    proposal = _build_proposal()
    pub, priv = generate_key_pair()
    signature = sign_proposal(proposal, priv)
    tampered = replace(proposal, confidence=0.99)
    assert not verify_proposal(tampered, signature, pub)


def test_verify_rejects_tampered_nested_justification():
    """
    Regression test for the canonicalize fix in 0.3.0. Under the pre-0.3.0
    TypeScript implementation, mutating justification.summary did NOT
    invalidate the signature because the replacer-array canonicalize
    dropped nested fields. Python's recursive canonicalize closes that.
    """
    proposal = _build_proposal()
    pub, priv = generate_key_pair()
    signature = sign_proposal(proposal, priv)
    tampered = replace(
        proposal,
        justification=Justification(
            summary="Tests fail, refusing to merge",
            evidence_refs=(),
        ),
    )
    assert not verify_proposal(tampered, signature, pub)


def test_verify_rejects_wrong_public_key():
    proposal = _build_proposal()
    _, priv1 = generate_key_pair()
    pub2, _ = generate_key_pair()
    signature = sign_proposal(proposal, priv1)
    assert not verify_proposal(proposal, signature, pub2)


def test_canonicalize_non_finite_number_raises():
    with pytest.raises(ValueError):
        canonicalize_value(float("nan"))
    with pytest.raises(ValueError):
        canonicalize_value(float("inf"))
