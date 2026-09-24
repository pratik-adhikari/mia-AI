"""Provider-neutral bounded Jev decisions with strict probability validation."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Literal, Protocol

import httpx
from pydantic import AliasChoices, BaseModel, Field, field_validator

from mia_dpp.domain.base import WireModel

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MAX_CHOICE_OPTIONS = 255


class ChoiceDecision(WireModel):
    """One validated bounded classification result."""

    question_id: str = Field(min_length=1)
    choice: str = Field(min_length=1)
    probabilities: dict[str, float]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class JevDecisionClient(Protocol):
    async def choose(
        self,
        *,
        question_id: str,
        state: Mapping[str, object],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> ChoiceDecision: ...


class _JevChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    probabilities: dict[str, float]

    @field_validator("probabilities")
    @classmethod
    def probabilities_are_finite(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("Jev must return the complete probability distribution")
        if any(not math.isfinite(probability) for probability in value.values()):
            raise ValueError("Jev probabilities must be finite")
        if any(probability < 0.0 or probability > 1.0 for probability in value.values()):
            raise ValueError("Jev probabilities must be between zero and one")
        return value


class _JevUsage(BaseModel):
    input_tokens: int | None = Field(
        default=None,
        validation_alias=AliasChoices("inputTokens", "input_tokens"),
        ge=0,
    )
    output_tokens: int | None = Field(
        default=None,
        validation_alias=AliasChoices("outputTokens", "output_tokens"),
        ge=0,
    )


class _JevResponse(BaseModel):
    answers: dict[str, _JevChoiceAnswer]
    usage: _JevUsage = Field(default_factory=_JevUsage)


class OpenRouterJevClient:
    """Call OpenRouter's Decisions API without embedding mapping policy."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "typesafe/jev-1.13",
        endpoint: str = OPENROUTER_DECISIONS_URL,
        max_concurrency: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenRouter API key is required for Jev decisions")
        if max_concurrency < 1:
            raise ValueError("Jev max concurrency must be at least one")
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint
        self._client = client
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def choose(
        self,
        *,
        question_id: str,
        state: Mapping[str, object],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> ChoiceDecision:
        if not question_id:
            raise ValueError("question_id must not be empty")
        if not criteria:
            raise ValueError("Jev choice requires at least one option")
        if len(criteria) > MAX_CHOICE_OPTIONS:
            raise ValueError(
                f"Jev choice has {len(criteria)} options; maximum is {MAX_CHOICE_OPTIONS}"
            )
        if any(not identifier for identifier in criteria):
            raise ValueError("Jev criteria identifiers must not be empty")

        payload = {
            "model": self._model,
            "state": dict(state),
            "questions": {
                question_id: {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": dict(criteria),
                }
            },
        }
        async with self._semaphore:
            if self._client is not None:
                return await self._post(self._client, question_id, payload, criteria)
            async with httpx.AsyncClient(timeout=30.0) as client:
                return await self._post(client, question_id, payload, criteria)

    async def _post(
        self,
        client: httpx.AsyncClient,
        question_id: str,
        payload: dict[str, object],
        criteria: Mapping[str, str],
    ) -> ChoiceDecision:
        response = await client.post(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://mia-dpp.vercel.app",
                "X-Title": "MIA Digital Product Passport",
            },
            json=payload,
        )
        response.raise_for_status()
        parsed = _JevResponse.model_validate(response.json())
        answer = parsed.answers.get(question_id)
        if answer is None:
            raise ValueError(f"Jev response omitted answer {question_id!r}")

        allowed = set(criteria)
        returned = set(answer.probabilities)
        if answer.choice not in allowed:
            raise ValueError(f"Jev returned unknown choice: {answer.choice}")
        if returned != allowed:
            missing = sorted(allowed - returned)
            unknown = sorted(returned - allowed)
            raise ValueError(
                "Jev probability keys must exactly match criteria; "
                f"missing={missing}, unknown={unknown}"
            )
        total = sum(answer.probabilities.values())
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=0.02):
            raise ValueError(f"Jev probabilities sum to {total:.6f}, expected approximately 1.0")

        return ChoiceDecision(
            question_id=question_id,
            choice=answer.choice,
            probabilities=answer.probabilities,
            input_tokens=parsed.usage.input_tokens,
            output_tokens=parsed.usage.output_tokens,
        )
