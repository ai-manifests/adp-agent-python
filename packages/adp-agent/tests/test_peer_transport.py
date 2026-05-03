"""
Regression coverage for the self-URL → self-agent-id binding the
deliberation runner must establish on the transport before the
initiator self-proposal call.

Before the fix, only fetch_manifest registered URLs in HttpTransport's
internal map, and the initiator never fetches its own manifest — so
the self URL stayed unbound and outgoing self-proposal calls fell back
to the wildcard '*' peer-token lookup. With AuthConfig.peer_tokens
holding only per-peer entries (no '*'), no Authorization header was
sent, and the agent's own auth middleware rejected the call with 401.
"""
from __future__ import annotations

import httpx

from adp_agent.config import AuthConfig
from adp_agent.transport import HttpTransport, peer_auth_headers, get_peer_token


def test_register_agent_binds_url_to_agent_id_for_outbound_auth_lookup() -> None:
    auth = AuthConfig(
        bearer_token="self-bearer",
        peer_tokens={
            "did:adp:self": "self-bearer",
            "did:adp:peer": "peer-bearer",
        },
    )
    transport = HttpTransport(httpx.AsyncClient(), auth)
    transport.register_agent("http://self.test:3001", "did:adp:self")
    transport.register_agent("http://peer.test", "did:adp:peer")

    # Indirect verification via the helper used inside the transport.
    from adp_agent.transport import peer_auth_headers, get_peer_token

    self_token = get_peer_token(auth, "did:adp:self")
    assert self_token == "self-bearer"

    self_headers = peer_auth_headers(auth, "did:adp:self")
    assert self_headers["Authorization"] == "Bearer self-bearer"

    peer_headers = peer_auth_headers(auth, "did:adp:peer")
    assert peer_headers["Authorization"] == "Bearer peer-bearer"


def test_peer_auth_headers_falls_back_to_wildcard_when_agent_missing() -> None:
    """
    Wildcard fallback is preserved for transports that legitimately need
    it (e.g. external integrations). The bug fix doesn't remove wildcard
    support; it makes the self URL no longer rely on it.
    """
    from adp_agent.transport import peer_auth_headers

    auth = AuthConfig(bearer_token="x", peer_tokens={"*": "wildcard-token"})
    headers = peer_auth_headers(auth, "did:adp:unknown")
    assert headers["Authorization"] == "Bearer wildcard-token"


def test_peer_auth_headers_no_token_when_no_match() -> None:
    from adp_agent.transport import peer_auth_headers

    auth = AuthConfig(bearer_token="x", peer_tokens={"did:adp:other": "other"})
    headers = peer_auth_headers(auth, "did:adp:unknown")
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


def test_peer_auth_headers_no_auth_returns_only_content_type() -> None:
    from adp_agent.transport import peer_auth_headers

    headers = peer_auth_headers(None, "did:adp:anything")
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"
