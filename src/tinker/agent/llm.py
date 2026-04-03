"""LiteLLM wrapper — single entry point for all LLM calls.

Supports any model string that LiteLLM understands:

  Provider          Model string example
  ─────────────     ───────────────────────────────────────────────
  Anthropic direct  anthropic/claude-sonnet-4-6
  OpenRouter        openrouter/anthropic/claude-opus-4-6
  OpenRouter        openrouter/openai/gpt-4o
  OpenRouter        openrouter/meta-llama/llama-3.1-70b-instruct
  OpenAI direct     openai/gpt-4o
  Groq              groq/llama-3.1-70b-versatile
  Mistral           mistral/mistral-large-latest
  Ollama (local)    ollama/llama3

Set the model in config:
  TINKER_DEFAULT_MODEL=openrouter/anthropic/claude-sonnet-4-6
  TINKER_DEEP_RCA_MODEL=openrouter/anthropic/claude-opus-4-6

Set the API key for the provider you use:
  ANTHROPIC_API_KEY    for anthropic/* models
  OPENROUTER_API_KEY   for openrouter/* models
  OPENAI_API_KEY       for openai/* models
  GROQ_API_KEY         for groq/* models
  MISTRAL_API_KEY      for mistral/* models

LiteLLM picks up provider keys from env automatically.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import litellm
import structlog

log = structlog.get_logger(__name__)

# Suppress LiteLLM's verbose logging — Tinker uses structlog
litellm.suppress_debug_info = True
litellm.set_verbose = False


def _is_anthropic(model: str) -> bool:
    """True if the model routes to Anthropic (direct or via OpenRouter)."""
    return "claude" in model.lower()


def _supports_thinking(model: str) -> bool:
    """Extended thinking is only available on Anthropic Claude Opus/Sonnet via direct API."""
    return model.startswith("anthropic/") and (
        "opus" in model.lower() or "sonnet" in model.lower()
    )


# ── Completion (non-streaming) ────────────────────────────────────────────────

def complete(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    thinking: bool = False,
    max_tokens: int = 8192,
) -> litellm.ModelResponse:
    """Run a single completion. Returns a LiteLLM ModelResponse (OpenAI-compatible)."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    # Extended thinking — Anthropic direct only
    if thinking and _supports_thinking(model):
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8000}
        log.debug("llm.thinking_enabled", model=model)
    elif thinking:
        log.debug("llm.thinking_not_supported", model=model)

    log.debug("llm.complete", model=model, n_messages=len(messages))
    return litellm.completion(**kwargs)


# ── Streaming completion ──────────────────────────────────────────────────────

async def stream_complete(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 8192,
) -> AsyncIterator[str]:
    """Stream text tokens as they arrive. Yields str chunks."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    log.debug("llm.stream", model=model)
    response = await litellm.acompletion(**kwargs)
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


# ── Response parsing helpers ──────────────────────────────────────────────────

def extract_text(response: litellm.ModelResponse) -> str:
    """Pull the assistant text from a ModelResponse."""
    choice = response.choices[0]
    content = choice.message.content
    return content or ""


def extract_tool_calls(response: litellm.ModelResponse) -> list[dict[str, Any]]:
    """Return a list of {id, name, arguments} dicts from a ModelResponse."""
    choice = response.choices[0]
    tool_calls = getattr(choice.message, "tool_calls", None) or []
    result = []
    for tc in tool_calls:
        try:
            arguments = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        result.append({
            "id": tc.id,
            "name": tc.function.name,
            "arguments": arguments,
        })
    return result


def is_tool_call(response: litellm.ModelResponse) -> bool:
    return bool(extract_tool_calls(response))


def finish_reason(response: litellm.ModelResponse) -> str:
    return response.choices[0].finish_reason or "stop"


def tool_result_message(tool_call_id: str, content: Any) -> dict[str, Any]:
    """Build a tool result message in OpenAI format."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content if isinstance(content, str) else json.dumps(content, default=str),
    }


def assistant_message_from_response(response: litellm.ModelResponse) -> dict[str, Any]:
    """Convert a ModelResponse into an assistant message for the next turn."""
    choice = response.choices[0]
    msg: dict[str, Any] = {"role": "assistant"}
    if choice.message.content:
        msg["content"] = choice.message.content
    if getattr(choice.message, "tool_calls", None):
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in choice.message.tool_calls
        ]
    return msg
