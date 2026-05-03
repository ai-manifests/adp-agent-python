"""
LLM evaluator — calls Anthropic or OpenAI with structured-output forcing
to produce a guaranteed-shape `EvaluationResult`.

- Anthropic uses tool_use forced output (``tool_choice: {type: "tool",
  name: "submit_vote"}``), which is the supported pattern for guaranteed
  JSON. The system prompt is marked ``cache_control: {type: "ephemeral"}``
  so identical system prompts across actions hit the prompt cache.
- OpenAI uses Structured Outputs (``response_format: {type: "json_schema",
  strict: true}``).

Provider keys come from environment (``ANTHROPIC_API_KEY`` /
``OPENAI_API_KEY``) — not config — so config files can be committed
without secrets.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from adp_manifest import Vote

from .config import AgentConfig, EvaluatorConfig
from .evaluator import EvaluationRequest, EvaluationResult


VOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "vote": {"type": "string", "enum": ["approve", "reject", "abstain"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string"},
        "dissent_conditions": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["vote", "confidence", "summary", "dissent_conditions", "evidence_refs"],
    "additionalProperties": False,
}


class LlmEvaluator:
    """
    Default runtime evaluator for ``EvaluatorConfig.kind == "llm"``. Calls
    Anthropic or OpenAI with structured-output forcing and returns a typed
    :class:`EvaluationResult`. Network failures, missing keys, and shape
    mismatches all surface as ``Vote.ABSTAIN`` with an explanatory rationale
    so the deliberation runner never crashes on transient LLM errors.
    """

    def __init__(self, config: AgentConfig, client: httpx.AsyncClient | None = None) -> None:
        if config.evaluator is None or config.evaluator.kind != "llm":
            raise ValueError("LlmEvaluator requires EvaluatorConfig with kind='llm'")
        self._config = config
        self._eval_config: EvaluatorConfig = config.evaluator
        self._client = client
        self._owns_client = client is None

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        cfg = self._eval_config
        provider = cfg.provider
        if provider not in ("anthropic", "openai"):
            return EvaluationResult.abstain(
                f"llm evaluator: unsupported provider '{provider or '<missing>'}'"
            )
        if not cfg.model:
            return EvaluationResult.abstain("llm evaluator: model is required")
        if not cfg.system_prompt:
            return EvaluationResult.abstain("llm evaluator: system_prompt is required")
        if not cfg.user_template:
            return EvaluationResult.abstain("llm evaluator: user_template is required")

        user_message = render_template(cfg.user_template, request, self._config)
        timeout = cfg.timeout_ms / 1000.0
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            if provider == "anthropic":
                return await self._call_anthropic(client, user_message, timeout)
            return await self._call_openai(client, user_message, timeout)
        except Exception as ex:  # noqa: BLE001 — provider errors must always abstain
            return EvaluationResult.abstain(f"llm evaluator ({provider}) failed: {ex}")
        finally:
            if self._owns_client:
                await client.aclose()

    async def _call_anthropic(
        self,
        client: httpx.AsyncClient,
        user_message: str,
        timeout: float,
    ) -> EvaluationResult:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

        cfg = self._eval_config
        body: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "system": [
                {"type": "text", "text": cfg.system_prompt, "cache_control": {"type": "ephemeral"}}
            ],
            "tools": [
                {
                    "name": "submit_vote",
                    "description": "Submit your judgement on this action with confidence and dissent conditions.",
                    "input_schema": VOTE_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": "submit_vote"},
            "messages": [{"role": "user", "content": user_message}],
        }
        if cfg.temperature is not None:
            body["temperature"] = cfg.temperature

        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"anthropic {response.status_code}: {response.text[:240]}")

        data = response.json()
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                return _shape_from_raw(block["input"])
        raise RuntimeError("anthropic: response had no tool_use block")

    async def _call_openai(
        self,
        client: httpx.AsyncClient,
        user_message: str,
        timeout: float,
    ) -> EvaluationResult:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set in environment")

        cfg = self._eval_config
        body: dict[str, Any] = {
            "model": cfg.model,
            "max_completion_tokens": cfg.max_tokens,
            "messages": [
                {"role": "system", "content": cfg.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "submit_vote", "schema": VOTE_SCHEMA, "strict": True},
            },
        }
        if cfg.temperature is not None:
            body["temperature"] = cfg.temperature

        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json=body,
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"openai {response.status_code}: {response.text[:240]}")

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("openai: response had no choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError("openai: response had no message.content string")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as ex:
            raise RuntimeError(f"openai: response was not valid JSON: {content[:240]}") from ex
        if not isinstance(parsed, dict):
            raise RuntimeError("openai: parsed content is not an object")
        return _shape_from_raw(parsed)


def render_template(template: str, request: EvaluationRequest, config: AgentConfig) -> str:
    """Substitute ``{action.kind}``/``{action.target}``/``{action.parameters}``/
    ``{agent.id}``/``{agent.decisionClass}`` placeholders in *template*.
    """
    params = request.action.parameters or {}
    params_str = ", ".join(f"{k}={v}" for k, v in params.items())
    return (
        template.replace("{action.kind}", request.action.kind)
        .replace("{action.target}", request.action.target)
        .replace("{action.parameters}", params_str)
        .replace("{agent.id}", config.agent_id)
        .replace("{agent.decisionClass}", request.decision_class)
    )


def _shape_from_raw(raw: dict[str, Any]) -> EvaluationResult:
    vote = _normalise_vote(raw.get("vote"))
    confidence = raw.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))
    summary = raw.get("summary", "")
    if not isinstance(summary, str):
        summary = ""
    refs_raw = raw.get("evidence_refs") or []
    evidence_refs = tuple(r for r in refs_raw if isinstance(r, str)) if isinstance(refs_raw, list) else ()
    return EvaluationResult(vote=vote, confidence=confidence, rationale=summary, evidence_refs=evidence_refs)


def _normalise_vote(value: Any) -> Vote:
    if value == "approve":
        return Vote.APPROVE
    if value == "reject":
        return Vote.REJECT
    return Vote.ABSTAIN


__all__ = ["LlmEvaluator", "render_template"]
