"""
adp-agent — Python reference implementation of the Agent Deliberation Protocol runtime.

Public API surface. Import from here, not from submodules.
"""

from .config import (
    AgentConfig,
    JournalBackend,
    PeerConfig,
    PeerTransport,
    AuthConfig,
    AcbDefaultsConfig,
    CalibrationAnchorConfig,
    EvaluatorConfig,
)
from .manifest import AgentManifest, DomainAuthority
from .evaluator import (
    Evaluator,
    EvaluationRequest,
    EvaluationResult,
    ShellEvaluator,
    StaticEvaluator,
)
from .llm_evaluator import LlmEvaluator, render_template
from .signing import (
    generate_key_pair,
    canonicalize,
    canonicalize_value,
    sign_proposal,
    verify_proposal,
)
from .snapshot import (
    CalibrationSnapshotRecord,
    CalibrationSnapshotEnvelope,
    canonical_snapshot_message,
    sign_snapshot,
    verify_snapshot,
    build_snapshot,
    build_envelope,
)
from .journal import (
    RuntimeJournalStore,
    DeliberationSlice,
    JsonlJournalStore,
    SqliteJournalStore,
)
from .deliberation import RuntimeDeliberation, SignedProposal
from .transport import (
    PeerTransport as PeerTransportProtocol,
    HttpTransport,
    PeerProposalResponse,
    FalsificationResponse,
    get_peer_token,
    peer_auth_headers,
)
from .contribution import ContributionTracker, compute_load_bearing_agents
from .peer_deliberation import (
    PeerDeliberation,
    PeerDeliberationOptions,
    PeerDeliberationResult,
    ProposalSummary,
)
from .host import AdpAgentHost

__version__ = "0.6.2"

__all__ = [
    "AgentConfig",
    "JournalBackend",
    "PeerConfig",
    "PeerTransport",
    "AuthConfig",
    "AcbDefaultsConfig",
    "CalibrationAnchorConfig",
    "EvaluatorConfig",
    "AgentManifest",
    "DomainAuthority",
    "Evaluator",
    "EvaluationRequest",
    "EvaluationResult",
    "ShellEvaluator",
    "StaticEvaluator",
    "LlmEvaluator",
    "render_template",
    "generate_key_pair",
    "canonicalize",
    "canonicalize_value",
    "sign_proposal",
    "verify_proposal",
    "CalibrationSnapshotRecord",
    "CalibrationSnapshotEnvelope",
    "canonical_snapshot_message",
    "sign_snapshot",
    "verify_snapshot",
    "build_snapshot",
    "build_envelope",
    "RuntimeJournalStore",
    "DeliberationSlice",
    "JsonlJournalStore",
    "SqliteJournalStore",
    "RuntimeDeliberation",
    "SignedProposal",
    "PeerTransportProtocol",
    "HttpTransport",
    "PeerProposalResponse",
    "FalsificationResponse",
    "get_peer_token",
    "peer_auth_headers",
    "ContributionTracker",
    "compute_load_bearing_agents",
    "PeerDeliberation",
    "PeerDeliberationOptions",
    "PeerDeliberationResult",
    "ProposalSummary",
    "AdpAgentHost",
]
