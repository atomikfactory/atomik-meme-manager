"""Direct tests of OllamaClient against httpx.MockTransport - no real network, no fakes."""

from __future__ import annotations

import json

import httpx
import pytest

from atomik_meme.ollama_client import ModelMissing, OllamaClient, OllamaError, OllamaUnavailable


def _chat_response(content: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json={"message": {"content": json.dumps(content)}})


def _client(handler, **kwargs) -> OllamaClient:
    transport = httpx.MockTransport(handler)
    return OllamaClient(host="http://fake-ollama", transport=transport, **kwargs)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # The real backoff schedule is 1s/3s; tests shouldn't actually wait for it.
    monkeypatch.setattr("atomik_meme.ollama_client.time.sleep", lambda *_: None)


def test_retry_then_succeed_on_503():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, text="server busy")
        return _chat_response({"ok": True})

    client = _client(handler, retries=2)
    result = client.chat_structured("m", [{"role": "user", "content": "hi"}], schema={})
    assert result == {"ok": True}
    assert calls["n"] == 2


def test_retries_exhausted_raises_ollama_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still busy")

    client = _client(handler, retries=2)
    with pytest.raises(OllamaError):
        client.chat_structured("m", [{"role": "user", "content": "hi"}], schema={})


def test_think_400_fallback_is_remembered_per_model():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if payload.get("think") is not None:
            return httpx.Response(400, text="unknown parameter: think")
        return _chat_response({"ok": True})

    client = _client(handler, retries=1)
    result = client.chat_structured(
        "m", [{"role": "user", "content": "hi"}], schema={}, think=False
    )
    assert result == {"ok": True}
    assert len(calls) == 2  # one rejected-with-think, one retried-without

    # A later call for the SAME model must not send `think` at all - it's remembered.
    calls.clear()
    result2 = client.chat_structured(
        "m", [{"role": "user", "content": "hi"}], schema={}, think=False
    )
    assert result2 == {"ok": True}
    assert len(calls) == 1
    assert "think" not in calls[0]


def test_404_raises_model_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    client = _client(handler)
    with pytest.raises(ModelMissing):
        client.chat_structured("missing-model", [{"role": "user", "content": "hi"}], schema={})


def test_invalid_json_content_is_retried_then_raises():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json={"message": {"content": "not valid json"}})

    client = _client(handler, retries=1)
    with pytest.raises(OllamaError):
        client.chat_structured("m", [{"role": "user", "content": "hi"}], schema={})
    assert attempts["n"] == 2  # initial attempt + 1 retry


def test_transport_error_raises_ollama_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client(handler, retries=1)
    with pytest.raises(OllamaUnavailable):
        client.chat_structured("m", [{"role": "user", "content": "hi"}], schema={})


def test_version_list_models_and_pull():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.5"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})
        if request.url.path == "/api/pull":
            return httpx.Response(200, content=b'{"status": "success"}\n')
        return httpx.Response(404)

    client = _client(handler)
    assert client.version() == "0.32.5"
    assert client.list_models() == ["qwen3.5:4b"]
    assert client.has_model("qwen3.5:4b") is True
    assert client.has_model("nope") is False

    progress: list[dict] = []
    client.pull("qwen3.5:4b", progress_cb=progress.append)
    assert progress and progress[0]["status"] == "success"
