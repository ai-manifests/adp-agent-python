"""
The adopter-provided hook that produces votes. Everything else in the
runtime is framework; this is where the agent's actual decision logic lives.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Protocol

from adj_manifest import ActionDescriptor
from adp_manifest import ReversibilityTier, Vote

from .config import AgentConfig, EvaluatorConfig


@dataclass(frozen=True)
class EvaluationRequest:
    deliberation_id: str
    action: ActionDescriptor
    tier: ReversibilityTier
    decision_class: str


@dataclass(frozen=True)
class EvaluationResult:
    vote: Vote
    confidence: float
    rationale: str
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def approve(cls, confidence: float = 0.75, rationale: str = "stub approval") -> EvaluationResult:
        return cls(Vote.APPROVE, confidence, rationale)

    @classmethod
    def reject(cls, confidence: float, rationale: str) -> EvaluationResult:
        return cls(Vote.REJECT, confidence, rationale)

    @classmethod
    def abstain(cls, rationale: str) -> EvaluationResult:
        return cls(Vote.ABSTAIN, 0.0, rationale)


class Evaluator(Protocol):
    """
    The interface adopters implement. Runtime hands an EvaluationRequest in
    and expects an EvaluationResult out. Registered via
    :class:`AdpAgentHost`'s ``evaluator`` parameter.
    """

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


class StaticEvaluator:
    """
    Trivial evaluator that returns the agent's configured default vote at
    its configured default confidence for every request. Used when no
    :class:`EvaluatorConfig` is supplied, and as a drop-in stub during
    development.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return EvaluationResult(
            vote=self._config.default_vote,
            confidence=self._config.default_confidence,
            rationale="static default vote",
        )


class ShellEvaluator:
    """
    Default runtime evaluator that shells out to an external command.

    Two parse modes selected by :attr:`EvaluatorConfig.parse_output`:

    - ``exit-code``: exit 0 → Approve, any non-zero → Reject. Stdout captured
      into the rationale. Useful for wrapping existing test scripts.
    - ``json``: expects stdout to contain a JSON object with ``vote`` and
      ``confidence``; optional ``rationale`` and ``evidenceRefs`` preserved.

    The command runs with a timeout of :attr:`EvaluatorConfig.timeout_ms`.
    Timeout produces an abstain with rationale ``"evaluator timed out"``.
    """

    def __init__(self, config: AgentConfig) -> None:
        if config.evaluator is None or config.evaluator.kind != "shell":
            raise ValueError("ShellEvaluator requires EvaluatorConfig with kind='shell'")
        if not config.evaluator.command:
            raise ValueError("ShellEvaluator requires EvaluatorConfig.command to be set")
        self._config = config
        self._eval_config: EvaluatorConfig = config.evaluator

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        env = {
            **os.environ,
            "ADP_DELIBERATION_ID": request.deliberation_id,
            "ADP_ACTION_KIND": request.action.kind,
            "ADP_ACTION_TARGET": request.action.target,
            "ADP_REVERSIBILITY_TIER": request.tier.value,
            "ADP_DECISION_CLASS": request.decision_class,
        }
        timeout_s = self._eval_config.timeout_ms / 1000.0
        try:
            proc = await asyncio.create_subprocess_shell(
                self._eval_config.command or "",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as ex:
            return EvaluationResult.abstain(f"evaluator failed to start: {ex}")

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return EvaluationResult.abstain("evaluator timed out")

        stdout_s = (stdout or b"").decode("utf-8", errors="replace")
        stderr_s = (stderr or b"").decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        if self._eval_config.parse_output == "exit-code":
            return self._parse_exit_code(exit_code, stdout_s, stderr_s)
        if self._eval_config.parse_output == "json":
            return self._parse_json(stdout_s, stderr_s)
        return EvaluationResult.abstain(
            f"unknown parse_output mode '{self._eval_config.parse_output}'"
        )

    def _parse_exit_code(self, exit_code: int, stdout: str, stderr: str) -> EvaluationResult:
        rationale = stdout.strip() or stderr.strip() or f"exit {exit_code}"
        if exit_code == 0:
            return EvaluationResult(Vote.APPROVE, self._config.default_confidence, rationale)
        return EvaluationResult(Vote.REJECT, self._config.default_confidence, rationale)

    def _parse_json(self, stdout: str, stderr: str) -> EvaluationResult:
        if not stdout.strip():
            return EvaluationResult.abstain(
                stderr.strip() or "evaluator produced empty output"
            )
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as ex:
            return EvaluationResult.abstain(f"evaluator output was not valid JSON: {ex}")

        vote_str = data.get("vote")
        if not isinstance(vote_str, str):
            return EvaluationResult.abstain("evaluator JSON missing 'vote' field")
        try:
            vote = Vote(vote_str.lower())
        except ValueError:
            return EvaluationResult.abstain(f"evaluator JSON had unknown vote '{vote_str}'")

        confidence = data.get("confidence", self._config.default_confidence)
        if not isinstance(confidence, (int, float)):
            confidence = self._config.default_confidence

        rationale = data.get("rationale", "evaluator output")
        if not isinstance(rationale, str):
            rationale = "evaluator output"

        evidence_refs: tuple[str, ...] = ()
        raw_refs = data.get("evidenceRefs")
        if isinstance(raw_refs, list):
            evidence_refs = tuple(r for r in raw_refs if isinstance(r, str))

        return EvaluationResult(
            vote=vote,
            confidence=float(confidence),
            rationale=rationale,
            evidence_refs=evidence_refs,
        )


__all__ = [
    "Evaluator",
    "EvaluationRequest",
    "EvaluationResult",
    "StaticEvaluator",
    "ShellEvaluator",
]
