"""LLM clients. Anything that speaks the OpenAI chat-completions API works: Ollama,
vLLM, llama.cpp server, LM Studio, or a hosted provider.

Model output is never trusted to be well-formed, so JSON is extracted defensively and
every call records latency and token usage for the benchmark.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

Message = dict[str, str]


class LLMError(RuntimeError):
    pass


class _ClientError(LLMError):
    """A 4xx response: retrying the same request will not help."""


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def add(self, other: Usage) -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.seconds += other.seconds


class LLMClient(Protocol):
    usage: Usage

    @property
    def name(self) -> str: ...

    def complete(self, messages: Sequence[Message]) -> str: ...


@dataclass
class OpenAICompatibleClient:
    """Chat-completions client with JSON mode, retries and usage accounting."""

    model: str
    base_url: str = "http://localhost:11434/v1"
    api_key: str | None = None
    temperature: float = 0.0
    timeout: float = 180.0
    max_retries: int = 2
    json_mode: bool = True
    usage: Usage = field(default_factory=Usage)
    transport: httpx.BaseTransport | None = None

    @property
    def name(self) -> str:
        return self.model

    @classmethod
    def from_env(cls, model: str | None = None, base_url: str | None = None) -> OpenAICompatibleClient:
        return cls(
            model=model or os.environ.get("PATCHPROOF_MODEL", "qwen2.5-coder:7b"),
            base_url=base_url or os.environ.get("PATCHPROOF_BASE_URL", "http://localhost:11434/v1"),
            api_key=os.environ.get("PATCHPROOF_API_KEY"),
        )

    def complete(self, messages: Sequence[Message]) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url.rstrip("/") + "/chat/completions"

        last: Exception | None = None
        with httpx.Client(timeout=self.timeout, transport=self.transport) as http:
            for attempt in range(self.max_retries + 1):
                start = time.perf_counter()
                try:
                    resp = http.post(url, json=payload, headers=headers)
                    if resp.status_code >= 500 or resp.status_code == 429:
                        raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    if resp.status_code >= 400:
                        raise _ClientError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"] or ""
                    u = data.get("usage") or {}
                    self.usage.add(
                        Usage(
                            calls=1,
                            prompt_tokens=int(u.get("prompt_tokens", 0)),
                            completion_tokens=int(u.get("completion_tokens", 0)),
                            seconds=time.perf_counter() - start,
                        )
                    )
                    return str(text)
                except _ClientError as exc:
                    last = exc
                    break
                except (httpx.TransportError, LLMError, KeyError, ValueError) as exc:
                    last = exc
                    if attempt < self.max_retries:
                        time.sleep(min(2**attempt, 8))
        raise LLMError(f"LLM call failed after retries: {last}")


Responder = Callable[[Sequence[Message]], str]


@dataclass
class ScriptedClient:
    """Deterministic client for tests and baselines: replies come from a function."""

    responder: Responder
    name: str = "scripted"
    usage: Usage = field(default_factory=Usage)
    transcript: list[list[Message]] = field(default_factory=list)

    def complete(self, messages: Sequence[Message]) -> str:
        self.transcript.append(list(messages))
        self.usage.add(Usage(calls=1))
        return self.responder(messages)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse the first JSON object in a model reply (handles code fences and chatter)."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for m in _FENCE.finditer(text):
        try:
            return json.loads(m.group(1))
        except ValueError:
            continue
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise LLMError(f"no JSON object in model reply: {text[:200]!r}")
