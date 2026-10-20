import json

import httpx
import pytest

from patchproof.llm import LLMError, OpenAICompatibleClient, ScriptedClient, extract_json


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": [1, 2]}\n```\nDone') == {"a": [1, 2]}
    assert extract_json('noise {"a": "}"} trailing') == {"a": "}"}
    assert extract_json('{bad} then {"ok": true}') == {"ok": True}
    with pytest.raises(LLMError):
        extract_json("no json here")


def _reply(content, usage=None):
    return {"choices": [{"message": {"content": content}}], "usage": usage or {}}


def test_openai_client_payload_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_reply('{"x": 1}', {"prompt_tokens": 10, "completion_tokens": 3}))

    c = OpenAICompatibleClient("m", base_url="http://h/v1/", api_key="k",
                               transport=httpx.MockTransport(handler))
    assert c.complete([{"role": "user", "content": "hi"}]) == '{"x": 1}'
    assert seen["url"] == "http://h/v1/chat/completions"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["temperature"] == 0.0
    assert seen["auth"] == "Bearer k"
    assert (c.usage.calls, c.usage.prompt_tokens, c.usage.completion_tokens) == (1, 10, 3)


def test_retries_server_errors_but_not_client_errors(monkeypatch):
    monkeypatch.setattr("patchproof.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json=_reply("ok"))

    c = OpenAICompatibleClient("m", transport=httpx.MockTransport(flaky))
    assert c.complete([]) == "ok" and calls["n"] == 3

    calls["n"] = 0

    def bad(request):
        calls["n"] += 1
        return httpx.Response(400, text="bad model")

    c = OpenAICompatibleClient("m", transport=httpx.MockTransport(bad))
    with pytest.raises(LLMError, match="400"):
        c.complete([])
    assert calls["n"] == 1


def test_scripted_client_records_transcript():
    c = ScriptedClient(lambda m: m[-1]["content"].upper())
    assert c.complete([{"role": "user", "content": "hey"}]) == "HEY"
    assert c.usage.calls == 1 and c.transcript[0][0]["content"] == "hey"
