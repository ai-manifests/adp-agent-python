"""Scheduler + mock store tests for the anchor package."""
from __future__ import annotations

import pytest

from adp_agent_anchor import (
    BlockchainStoreFactory,
    CalibrationRecord,
    MockBlockchainStore,
    Neo3BlockchainStore,
)


@pytest.mark.asyncio
async def test_mock_store_roundtrips_a_published_record():
    store = MockBlockchainStore()
    record = CalibrationRecord(
        agent_id="did:adp:test-runner-v2",
        domain="code.correctness",
        value=0.7812,
        sample_size=42,
        timestamp=1_700_000_000_000,
        journal_hash="a1b2c3",
    )
    tx = await store.publish_calibration(record)
    assert tx.startswith("0xmock")

    retrieved = await store.get_calibration("did:adp:test-runner-v2", "code.correctness")
    assert retrieved is not None
    assert retrieved.value == 0.7812
    assert retrieved.sample_size == 42
    assert retrieved.journal_hash == "a1b2c3"


@pytest.mark.asyncio
async def test_mock_store_returns_none_for_unknown_agent():
    store = MockBlockchainStore()
    result = await store.get_calibration("did:adp:nobody", "code.correctness")
    assert result is None


@pytest.mark.asyncio
async def test_mock_store_overwrites_on_republish():
    store = MockBlockchainStore()
    r1 = CalibrationRecord("did:adp:a", "d", 0.5, 10, 0, "h1")
    r2 = CalibrationRecord("did:adp:a", "d", 0.7, 20, 1, "h2")
    await store.publish_calibration(r1)
    await store.publish_calibration(r2)
    retrieved = await store.get_calibration("did:adp:a", "d")
    assert retrieved is not None
    assert retrieved.value == 0.7
    assert retrieved.journal_hash == "h2"
    assert store.publish_count == 2


def test_factory_returns_mock_for_mock_target():
    class Config:
        enabled = True
        target = "mock"
    store = BlockchainStoreFactory.create(Config())
    assert isinstance(store, MockBlockchainStore)


def test_factory_returns_none_when_disabled():
    class Config:
        enabled = False
        target = "mock"
    assert BlockchainStoreFactory.create(Config()) is None


def test_factory_returns_none_for_neo_without_rpc_url():
    class Config:
        enabled = True
        target = "neo-custom"
        rpc_url = None
        contract_hash = None
    assert BlockchainStoreFactory.create(Config()) is None
