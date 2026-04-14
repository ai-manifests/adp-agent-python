"""Agent manifest served at /.well-known/adp-manifest.json."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .config import AgentConfig


@dataclass(frozen=True)
class DomainAuthority:
    authority: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"authority": self.authority, "source": self.source}


@dataclass(frozen=True)
class AgentManifest:
    agent_id: str
    identity: str
    compliance_level: int
    decision_classes: tuple[str, ...]
    domain_authorities: dict[str, DomainAuthority]
    journal_endpoint: str
    public_key: str | None
    trust_level: str

    @classmethod
    def from_config(cls, config: AgentConfig) -> AgentManifest:
        return cls(
            agent_id=config.agent_id,
            identity=f"did:web:{config.domain}",
            compliance_level=3,
            decision_classes=config.decision_classes,
            domain_authorities={
                k: DomainAuthority(
                    authority=v,
                    source=f"mcp-manifest:{config.agent_id}#authorities",
                )
                for k, v in config.authorities.items()
            },
            journal_endpoint=f"http://{config.domain}:{config.port}/adj/v0",
            public_key=config.auth.public_key if config.auth else None,
            trust_level="open",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "identity": self.identity,
            "complianceLevel": self.compliance_level,
            "decisionClasses": list(self.decision_classes),
            "domainAuthorities": {
                k: v.to_dict() for k, v in self.domain_authorities.items()
            },
            "journalEndpoint": self.journal_endpoint,
            "publicKey": self.public_key,
            "trustLevel": self.trust_level,
        }
