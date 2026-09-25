"""Strict Jev Decisions API boundary tests."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mia_dpp.semantic.jev import OpenRouterJevClient


def test_jev_client_preserves_complete_probability_distribution() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "technical_data",
                        "probabilities": {
                            "technical_data": 0.91,
                            "digital_nameplate": 0.07,
                            "__unresolved__": 0.02,
                        },
                    }
                },
                "usage": {"inputTokens": 42, "outputTokens": 3},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            decider = OpenRouterJevClient(api_key="test", client=client)
            return await decider.choose(
                question_id="route",
                state={"label": "Repeat accuracy"},
                instructions="Choose a route.",
                criteria={
                    "technical_data": "Technical Data",
                    "digital_nameplate": "Digital Nameplate",
                    "__unresolved__": "Insufficient context",
                },
            )

    result = asyncio.run(run())

    assert len(seen) == 1
    assert result.choice == "technical_data"
    assert result.probabilities == {
        "technical_data": 0.91,
        "digital_nameplate": 0.07,
        "__unresolved__": 0.02,
    }
    assert result.input_tokens == 42
    assert result.output_tokens == 3


@pytest.mark.parametrize(
    ("probabilities", "message"),
    [
        (
            {"technical_data": 0.9, "__unresolved__": 0.1},
            "exactly match criteria",
        ),
        (
            {
                "technical_data": 0.5,
                "digital_nameplate": 0.2,
                "__unresolved__": 0.1,
            },
            "sum to",
        ),
    ],
)
def test_jev_client_rejects_incomplete_or_invalid_distributions(
    probabilities: dict[str, float],
    message: str,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "technical_data",
                        "probabilities": probabilities,
                    }
                }
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            decider = OpenRouterJevClient(api_key="test", client=client)
            return await decider.choose(
                question_id="route",
                state={},
                instructions="Choose.",
                criteria={
                    "technical_data": "Technical Data",
                    "digital_nameplate": "Digital Nameplate",
                    "__unresolved__": "Unresolved",
                },
            )

    with pytest.raises(ValueError, match=message):
        asyncio.run(run())
