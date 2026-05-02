"""Tests for the LLM evaluator. No live API calls — httpx is mocked."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from adj_manifest import ActionDescriptor
from adp_manifest import ReversibilityTier, Vote

from adp_agent import (
    AgentConfig,
    AuthConfig,
    EvaluatorConfig,
    LlmEvaluator,
    render_template,
)
from adp_agent.evaluator import EvaluationRequest


def make_config(provider: str, prompt: str = "You evaluate code correctness.", template: str = "Vote on {action.target} (you are {agent.id})") -> AgentConfig:
    return AgentConfig(
        agent_id="did:adp:claude-tester-v1",
        port=3011,
        domain="claude-tester.adp-federation.dev",
        decision_classes=("code.correctness",),
        authorities={"code.correctness": 0.8},
        stake_magnitude="high",
        default_vote=Vote.ABSTAIN,
        default_confidence=0.5,
        dissent_conditions=(),
        journal_dir="/tmp/x",
        auth=AuthConfig(bearer_token="x"),
        evaluator=EvaluatorConfig(
            kind="llm",
            provider=provider,
            model="claude-opus-4-7" if provider == "anthropic" else "gpt-5",
            system_prompt=prompt,
            user_template=template,
            timeout_ms=30_000,
        ),
    )


def make_request(target: str = "ai-manifests/adp-dogfood#5", parameters: dict[str, str] | None = None) -> EvaluationRequest:
    return EvaluationRequest(
        deliberation_id="dlb_test_1",
        action=ActionDescriptor(kind="merge_pull_request", target=target, parameters=parameters),
        tier=ReversibilityTier.PARTIALLY_REVERSIBLE,
        decision_class="code.correctness",
    )


def fake_response(json_body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status, json=json_body)


class TestRenderTemplate:
    def test_substitutes_all_placeholders(self) -> None:
        config = make_config("anthropic")
        request = make_request(parameters={"branch": "main"})
        result = render_template(
            "Action {action.kind} on {action.target} with {action.parameters}; you are {agent.id} judging {agent.decisionClass}.",
            request,
            config,
        )
        assert result == (
            "Action merge_pull_request on ai-manifests/adp-dogfood#5 with branch=main; "
            "you are did:adp:claude-tester-v1 judging code.correctness."
        )

    def test_handles_missing_parameters(self) -> None:
        config = make_config("anthropic")
        request = make_request()
        assert render_template("{action.parameters}", request, config) == ""

    def test_replaces_repeated_placeholders(self) -> None:
        config = make_config("anthropic")
        request = make_request(target="t")
        assert render_template("{action.target} - {action.target}", request, config) == "t - t"


class TestConfigValidation:
    @pytest.mark.asyncio
    async def test_unsupported_provider_abstains(self) -> None:
        config = make_config("ollama")  # type: ignore[arg-type]
        result = await LlmEvaluator(config).evaluate(make_request())
        assert result.vote == Vote.ABSTAIN
        assert "unsupported provider" in result.rationale

    @pytest.mark.asyncio
    async def test_missing_system_prompt_abstains(self) -> None:
        config = make_config("anthropic", prompt="")
        result = await LlmEvaluator(config).evaluate(make_request())
        assert result.vote == Vote.ABSTAIN
        assert "system_prompt" in result.rationale


class TestAnthropic:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    @pytest.mark.asyncio
    async def test_happy_path_parses_tool_use(self) -> None:
        config = make_config("anthropic")
        captured: dict[str, Any] = {}

        async def post(url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return fake_response({
                "content": [
                    {"type": "text", "text": "thinking"},
                    {
                        "type": "tool_use",
                        "name": "submit_vote",
                        "input": {
                            "vote": "reject",
                            "confidence": 0.91,
                            "summary": "Tests are missing.",
                            "dissent_conditions": ["no test added"],
                            "evidence_refs": ["ci.log"],
                        },
                    },
                ],
            })

        client = httpx.AsyncClient()
        with patch.object(client, "post", new=AsyncMock(side_effect=post)):
            result = await LlmEvaluator(config, client=client).evaluate(make_request())

        assert result.vote == Vote.REJECT
        assert pytest.approx(result.confidence, abs=0.001) == 0.91
        assert result.rationale == "Tests are missing."
        assert result.evidence_refs == ("ci.log",)

        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        body = captured["json"]
        assert body["tool_choice"] == {"type": "tool", "name": "submit_vote"}
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_no_tool_use_block_abstains(self) -> None:
        config = make_config("anthropic")
        client = httpx.AsyncClient()
        with patch.object(client, "post", new=AsyncMock(return_value=fake_response({
            "content": [{"type": "text", "text": "I refuse."}],
        }))):
            result = await LlmEvaluator(config, client=client).evaluate(make_request())
        assert result.vote == Vote.ABSTAIN
        assert "tool_use" in result.rationale

    @pytest.mark.asyncio
    async def test_http_error_abstains_with_status(self) -> None:
        config = make_config("anthropic")
        client = httpx.AsyncClient()
        with patch.object(client, "post", new=AsyncMock(return_value=httpx.Response(status_code=429, text="rate limited"))):
            result = await LlmEvaluator(config, client=client).evaluate(make_request())
        assert result.vote == Vote.ABSTAIN
        assert "anthropic 429" in result.rationale

    @pytest.mark.asyncio
    async def test_missing_api_key_abstains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        config = make_config("anthropic")
        result = await LlmEvaluator(config).evaluate(make_request())
        assert result.vote == Vote.ABSTAIN
        assert "ANTHROPIC_API_KEY" in result.rationale


class TestOpenAi:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    @pytest.mark.asyncio
    async def test_happy_path_parses_content_json(self) -> None:
        config = make_config("openai")
        inner = json.dumps({
            "vote": "approve",
            "confidence": 0.78,
            "summary": "Looks good",
            "dissent_conditions": [],
            "evidence_refs": [],
        })
        captured: dict[str, Any] = {}

        async def post(url: str, **kwargs: Any) -> httpx.Response:
            captured["json"] = kwargs.get("json")
            return fake_response({
                "choices": [{"message": {"content": inner}}],
            })

        client = httpx.AsyncClient()
        with patch.object(client, "post", new=AsyncMock(side_effect=post)):
            result = await LlmEvaluator(config, client=client).evaluate(make_request())
        assert result.vote == Vote.APPROVE
        assert pytest.approx(result.confidence, abs=0.001) == 0.78
        body = captured["json"]
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True

    @pytest.mark.asyncio
    async def test_invalid_json_content_abstains(self) -> None:
        config = make_config("openai")
        client = httpx.AsyncClient()
        with patch.object(client, "post", new=AsyncMock(return_value=fake_response({
            "choices": [{"message": {"content": "not json"}}],
        }))):
            result = await LlmEvaluator(config, client=client).evaluate(make_request())
        assert result.vote == Vote.ABSTAIN
        assert "not valid JSON" in result.rationale
