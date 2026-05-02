"""Runtime configuration for an ADP agent host."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

from adp_manifest import StakeMagnitude, Vote


class JournalBackend(Enum):
    JSONL = "jsonl"
    SQLITE = "sqlite"


class PeerTransport(Enum):
    HTTP = "http"
    MCP = "mcp"


@dataclass(frozen=True)
class PeerConfig:
    agent_id: str
    url: str
    transport: PeerTransport = PeerTransport.HTTP


@dataclass(frozen=True)
class AuthConfig:
    bearer_token: str
    peer_tokens: dict[str, str] = field(default_factory=dict)
    private_key: str | None = None
    public_key: str | None = None


@dataclass(frozen=True)
class AcbDefaultsConfig:
    """ACB defaults applied when no caller-supplied budget is provided."""
    budget_authority: str
    denomination: object  # acb_manifest.Denomination
    default_amount_total: float
    pricing: object  # acb_manifest.PricingProfile
    settlement: object  # acb_manifest.SettlementProfileConfig
    constraints: object | None = None  # acb_manifest.BudgetConstraints | None


@dataclass(frozen=True)
class CalibrationAnchorConfig:
    """Optional Neo3 calibration anchor configuration (ADJ §7.4 overlay)."""
    enabled: bool = False
    target: str = "mock"  # mock | neo-express | neo-custom | neo-testnet | neo-mainnet
    rpc_url: str | None = None
    contract_hash: str | None = None
    private_key: str | None = None
    network_magic: int | None = None
    publish_interval_seconds: int = 3600
    publish_timeout_seconds: int = 30


@dataclass(frozen=True)
class EvaluatorConfig:
    """Strategy-pattern evaluator config."""
    kind: str = "static"  # "shell" | "static" | "llm" | custom
    command: str | None = None
    timeout_ms: int = 60_000
    parse_output: str = "exit-code"  # "exit-code" | "json"

    # --- LLM evaluator fields (kind: 'llm') ---
    # Which LLM API to call.
    provider: str | None = None  # "anthropic" | "openai"
    # Provider model id (e.g. "claude-opus-4-7", "gpt-5").
    model: str | None = None
    # System prompt — the agent's identity and judging criteria. Stable
    # across actions, so providers may cache it server-side (Anthropic
    # prompt caching is enabled when this is set).
    system_prompt: str | None = None
    # User-message template. Placeholders substituted at call time:
    # {action.kind}, {action.target}, {action.parameters}, {agent.id},
    # {agent.decisionClass}.
    user_template: str | None = None
    # Max tokens for the response.
    max_tokens: int = 1024
    # Sampling temperature (default 0 — deterministic).
    temperature: float = 0.0


@dataclass(frozen=True)
class AgentConfig:
    """
    Runtime configuration for an ADP agent host. Typically deserialized from
    an ``agent.config.json`` file at startup.
    """
    agent_id: str
    port: int
    domain: str
    decision_classes: tuple[str, ...]
    authorities: dict[str, float]
    stake_magnitude: StakeMagnitude
    default_vote: Vote
    default_confidence: float
    dissent_conditions: tuple[str, ...]
    journal_dir: str
    journal_backend: JournalBackend = JournalBackend.JSONL
    falsification_responses: dict[str, str] = field(default_factory=dict)
    peers: tuple[PeerConfig, ...] = ()
    auth: AuthConfig | None = None
    acb: AcbDefaultsConfig | None = None
    calibration_anchor: CalibrationAnchorConfig | None = None
    evaluator: EvaluatorConfig | None = None
    initiator: bool = False
