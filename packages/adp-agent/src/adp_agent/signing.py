"""
Ed25519 proposal signing + canonical JSON.

Wire-compatible with the TypeScript reference implementation
``@ai-manifests/adp-agent@^0.3.0`` and the C# reference implementation
``Adp.Agent@^0.1.0``. The canonicalize algorithm is a simplified RFC 8785
(JCS) variant: objects get their keys sorted alphabetically at every level
of nesting, arrays keep insertion order, primitives serialize as standard
compact JSON, and no whitespace is emitted anywhere.

**Byte-for-byte parity is required.** Cross-language golden-vector tests
enforce that a proposal signed in any one language verifies in every
other language. If this module ever disagrees with the TS or C# reference
on a byte, file an issue; this is a spec conformance bug.
"""
from __future__ import annotations

import dataclasses
import json
import secrets
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from adp_manifest import Proposal


def generate_key_pair() -> tuple[str, str]:
    """
    Generate a fresh Ed25519 key pair. Returns ``(public_key_hex, private_key_hex)``.
    Both keys are 32 bytes encoded as 64-char lowercase hex strings.
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return public_bytes.hex(), private_bytes.hex()


def canonicalize(proposal: Proposal) -> str:
    """
    Canonicalize a proposal to the exact bytes that will be Ed25519-signed.

    Steps:
    1. Serialize the proposal to a plain dict tree (dataclass → dict, enum → str value,
       datetime → ISO8601, tuples → lists).
    2. Remove the top-level ``signature`` field if present.
    3. Recursively canonicalize the tree.

    Returns a compact JSON string with sorted keys at every level.
    """
    tree = _proposal_to_tree(proposal)
    if isinstance(tree, dict):
        tree.pop("signature", None)
    return canonicalize_value(tree)


def canonicalize_value(value: Any) -> str:
    """
    Recursive canonical JSON serializer. Exported for golden-vector tests
    and cross-language parity validation; most callers use
    :func:`canonicalize(proposal)` directly.

    Output rules (must match TS/C# byte-for-byte):
    - ``None`` → ``"null"``
    - ``bool`` → ``"true"`` / ``"false"``
    - ``int`` / ``float`` → standard JSON number (raises on NaN / Infinity)
    - ``str`` → JSON-escaped string
    - ``list`` / ``tuple`` → insertion order, no trailing comma
    - ``dict`` → keys sorted with ``str`` natural ordering (Python's default),
      which matches JavaScript ``Array.prototype.sort()`` on string keys and
      .NET ``StringComparer.Ordinal``.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        # bool before int — Python's bool is a subclass of int.
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ValueError(f"Cannot canonicalize non-finite number: {value}")
        return json.dumps(value)
    if isinstance(value, str):
        # json.dumps with ensure_ascii=False leaves non-ASCII chars as-is,
        # matching ECMAScript JSON.stringify. ensure_ascii=True (default)
        # would produce \u00XX escapes for non-ASCII, which is a different
        # byte sequence than TS / C# and would break cross-language parity.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize_value(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value.keys())
        parts = [
            json.dumps(k, ensure_ascii=False) + ":" + canonicalize_value(value[k])
            for k in keys
        ]
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"Cannot canonicalize value of type {type(value).__name__}")


def sign_proposal(proposal: Proposal, private_key_hex: str) -> str:
    """
    Sign a proposal. Returns a 128-char lowercase hex Ed25519 signature.
    """
    message = canonicalize(proposal).encode("utf-8")
    return _sign_bytes(message, private_key_hex)


def verify_proposal(proposal: Proposal, signature_hex: str, public_key_hex: str) -> bool:
    """
    Verify a proposal's signature against a public key. Returns ``False`` on
    any error (malformed key, malformed signature, verification failure) —
    matches the forgiving behavior of the TS reference.
    """
    try:
        message = canonicalize(proposal).encode("utf-8")
        return _verify_bytes(message, signature_hex, public_key_hex)
    except Exception:
        return False


def _sign_bytes(message: bytes, private_key_hex: str) -> str:
    private_bytes = bytes.fromhex(private_key_hex)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    signature = private_key.sign(message)
    return signature.hex()


def _verify_bytes(message: bytes, signature_hex: str, public_key_hex: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    public_bytes = bytes.fromhex(public_key_hex)
    signature = bytes.fromhex(signature_hex)
    public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
    try:
        public_key.verify(signature, message)
        return True
    except InvalidSignature:
        return False


def _proposal_to_tree(value: Any) -> Any:
    """
    Convert a typed proposal (dataclass tree with enums, datetimes, tuples)
    into a plain dict/list/primitive tree suitable for canonicalization.
    Field names are converted to camelCase to match the wire format.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result: dict[str, Any] = {}
        for f in dataclasses.fields(value):
            key = _snake_to_camel(f.name)
            result[key] = _proposal_to_tree(getattr(value, f.name))
        return result
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        # ISO 8601 with 'Z' suffix for UTC — matches the TS runtime's
        # Date.prototype.toISOString() output exactly.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        iso = value.astimezone(timezone.utc).isoformat()
        # Python emits '+00:00', TS emits 'Z' — normalize.
        if iso.endswith("+00:00"):
            iso = iso[:-6] + "Z"
        return iso
    if isinstance(value, dict):
        return {k: _proposal_to_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_proposal_to_tree(v) for v in value]
    return value


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase. ``agent_id`` → ``agentId``."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


__all__ = [
    "generate_key_pair",
    "canonicalize",
    "canonicalize_value",
    "sign_proposal",
    "verify_proposal",
]
