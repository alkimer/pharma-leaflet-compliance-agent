"""
Stateless client for OpenAI's Responses API.

Each instance pins a model, its `instructions` (system prompt) and the sampling
parameters; every `run()` is a self-contained call, with no threads and no
server-side state. Retries on 429s and transient errors are handled by the SDK
with exponential backoff (the `max_retries` parameter).

Being stateless does not mean paying for the whole leaflet on every call: the
shared prefix (instructions + leaflet) is covered by OpenAI's prompt cache, which
is billed at 25%. That is why the leaflet ALWAYS goes first in the input and the
variable part (the disposition, the rule) last: the cache requires an identical
prefix.

The prompts live versioned under `agents/prompts/*.txt` (see `agents.prompts`).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from openai import OpenAI

from core import console
from core.config import ModelConfig, settings
from core.usage import tracker

logger = logging.getLogger(__name__)

# The Responses API rejects `text.format = json_object` when the word "json" does
# not appear in the user input: `instructions` do not count towards that
# validation. Our payloads are serialised JSON, but the word itself may be
# missing from the text, so we add it when it is not there.
JSON_MODE_HINT = "Respondé únicamente con un objeto JSON válido, sin texto adicional."

# No call waits more than 5 minutes. This is a ceiling, not a suggestion: a larger
# `timeout` requested by a caller gets clamped here. Adequation used to wait 10
# minutes and, with the SDK's retries on top, a run could sit for an hour without
# saying anything.
MAX_TIMEOUT = 300.0

# How often to report that we are still waiting. Short calls emit nothing: the
# first heartbeat only lands once the interval elapses.
HEARTBEAT_INTERVAL = 15.0

# Retries for the long calls (adequation, rule generation). With MAX_RETRIES at 5,
# a 5-minute timeout turned into half an hour of waiting before failing: on calls
# that already take minutes, insisting is very expensive in time and the second
# attempt rarely fixes what broke in the first.
LONG_CALL_RETRIES = 1


class LLMClient:
    """Stateless wrapper over `client.responses.create`."""

    def __init__(
        self,
        model: str,
        instructions: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        """
        Args:
            model: OpenAI model (e.g. "gpt-4.1-mini").
            instructions: The agent's system prompt. Can be overridden per call.
            api_key: API key; defaults to the one in `settings` (OPENAI_API_KEY in .env).
            temperature: Randomness (0.0-2.0). Ignored by reasoning models.
            top_p: Nucleus sampling (0.0-1.0).
            max_output_tokens: Output token limit.
            max_retries: The SDK's automatic retries; defaults to MAX_RETRIES from .env.
        """
        self.model = model
        self.instructions = instructions
        self.temperature = temperature
        self.top_p = top_p
        self.max_output_tokens = max_output_tokens

        self.client = OpenAI(
            api_key=api_key or settings.require_api_key(),
            max_retries=max_retries if max_retries is not None else settings.max_retries,
        )
        logger.info(f"LLMClient listo (model={model})")

    @classmethod
    def from_config(
        cls,
        config: ModelConfig,
        instructions: Optional[str] = None,
        max_retries: Optional[int] = None,
    ) -> "LLMClient":
        """
        Build the client from a `ModelConfig` of `core.config`.

        Args:
            config: Model parameters.
            instructions: The agent's system prompt.
            max_retries: The SDK's retries; defaults to MAX_RETRIES from .env.
                Long calls pass `LONG_CALL_RETRIES`.
        """
        return cls(
            model=config.model,
            instructions=instructions,
            temperature=config.temperature,
            top_p=config.top_p,
            max_output_tokens=config.max_output_tokens,
            max_retries=max_retries,
        )

    def run(
        self,
        input: str,
        instructions: Optional[str] = None,
        json_mode: bool = False,
        timeout: float = MAX_TIMEOUT,
        cache_key: Optional[str] = None,
    ) -> Any:
        """
        Run one stateless call and return the SDK's Response object.

        While the call is in flight, a console heartbeat is emitted every
        `HEARTBEAT_INTERVAL` seconds so the run does not look hung.

        Args:
            input: The user message (a self-contained string).
            instructions: System prompt for this call; falls back to the instance's.
            json_mode: Force valid JSON output (`text.format = json_object`).
            timeout: Maximum wait, in seconds; clamped to MAX_TIMEOUT.
            cache_key: Groups calls sharing a prefix so they land in the same
                server-side cache. See `prompt_cache_key` below.

        Returns:
            The SDK's Response: use `.output_text` for the text and `.id` for the id.
        """
        if timeout > MAX_TIMEOUT:
            logger.debug(f"Timeout pedido de {timeout}s recortado al techo de {MAX_TIMEOUT}s")
            timeout = MAX_TIMEOUT

        params: dict = {"model": self.model, "input": input}

        effective_instructions = instructions if instructions is not None else self.instructions
        if effective_instructions is not None:
            params["instructions"] = effective_instructions
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.top_p is not None:
            params["top_p"] = self.top_p
        if self.max_output_tokens is not None:
            params["max_output_tokens"] = self.max_output_tokens
        # The prompt cache kicks in automatically past 1024 tokens of shared
        # prefix, but routing can send the call to another machine and lose it.
        # `prompt_cache_key` pins the routing: every call over the same leaflet
        # shares one cache.
        if cache_key:
            params["prompt_cache_key"] = cache_key
        if json_mode:
            params["text"] = {"format": {"type": "json_object"}}
            if "json" not in input.lower():
                params["input"] = f"{JSON_MODE_HINT}\n\n{input}"

        with console.waiting(f"esperando a {self.model}", HEARTBEAT_INTERVAL, limit=timeout):
            response = self.client.responses.create(timeout=timeout, **params)
        tracker.record(self.model, getattr(response, "usage", None))
        logger.debug(f"Respuesta recibida: {response.id}")
        return response

    def run_text(self, input: str, **kwargs) -> str:
        """Convenience: run `run()` and return `response.output_text`."""
        return self.run(input, **kwargs).output_text
