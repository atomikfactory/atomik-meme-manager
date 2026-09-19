"""HTTP client for a local Ollama server: version/tags/pull/chat with structured output.

All model access from the rest of the codebase goes through `ChatBackend`
(`chat_structured`), so unit tests can inject a fake and never touch the
network.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

BACKOFF_SCHEDULE = (1.0, 3.0)


class OllamaError(Exception):
    """Base class for all Ollama client errors."""


class OllamaUnavailable(OllamaError):
    """The server could not be reached, or a transport/5xx error persisted."""


class ModelMissing(OllamaError):
    """The requested model is not installed on the server."""


class ChatBackend(Protocol):
    """The subset of OllamaClient the analyser/categoriser depend on. Fakeable in tests."""

    def chat_structured(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        options: dict[str, Any] | None = None,
        think: bool | None = None,
        timeout_override: float | None = None,
    ) -> dict[str, Any]: ...


class OllamaClient:
    def __init__(
        self,
        host: str,
        timeout_s: float = 180,
        retries: int = 2,
        keep_alive: str = "10m",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = max(0, retries)
        self.keep_alive = keep_alive
        self._think_unsupported: set[str] = set()
        # `transport` lets tests point this client at an `httpx.MockTransport` instead of a
        # real socket - no new dependency, no network in unit tests.
        self._client = httpx.Client(base_url=self.host, timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- Introspection ---

    def version(self, timeout: float | None = None) -> str:
        probe_timeout = timeout if timeout is not None else min(self.timeout_s, 10)
        try:
            resp = self._client.get("/api/version", timeout=probe_timeout)
            resp.raise_for_status()
            return str(resp.json().get("version", ""))
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Cannot reach Ollama at {self.host}: {exc}") from exc

    def list_models(self, timeout: float | None = None) -> list[str]:
        probe_timeout = timeout if timeout is not None else min(self.timeout_s, 10)
        try:
            resp = self._client.get("/api/tags", timeout=probe_timeout)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Cannot reach Ollama at {self.host}: {exc}") from exc
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def has_model(self, name: str) -> bool:
        installed = self.list_models()
        if name in installed:
            return True
        if ":" not in name and f"{name}:latest" in installed:
            return True
        return False

    def pull(self, name: str, progress_cb=None) -> None:
        try:
            with self._client.stream(
                "POST", "/api/pull", json={"name": name}, timeout=None
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if progress_cb is not None:
                        progress_cb(data)
                    if data.get("error"):
                        raise OllamaError(f"Pull failed for {name}: {data['error']}")
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Cannot reach Ollama at {self.host}: {exc}") from exc

    # --- Chat ---

    def chat_structured(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        options: dict[str, Any] | None = None,
        think: bool | None = None,
        timeout_override: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/chat with structured output, retrying transport/5xx/bad-JSON errors.

        `think` is included only when not None (and never once the model has
        been observed to reject it with HTTP 400).
        """
        timeout = timeout_override if timeout_override is not None else self.timeout_s
        payload_base: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": schema,
            "stream": False,
            "keep_alive": self.keep_alive,
        }
        if options:
            payload_base["options"] = options

        use_think = think if model not in self._think_unsupported else None

        last_exc: Exception | None = None
        attempt = 0
        max_attempts = self.retries + 1
        while attempt < max_attempts:
            attempt += 1
            payload = dict(payload_base)
            if use_think is not None:
                payload["think"] = use_think

            try:
                resp = self._client.post("/api/chat", json=payload, timeout=timeout)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "Ollama transport error (attempt %d/%d): %s", attempt, max_attempts, exc
                )
                if attempt < max_attempts:
                    time.sleep(BACKOFF_SCHEDULE[min(attempt - 1, len(BACKOFF_SCHEDULE) - 1)])
                    continue
                raise OllamaUnavailable(f"Transport error calling {self.host}: {exc}") from exc

            if resp.status_code == 400 and use_think is not None and "think" in resp.text.lower():
                logger.info("Model %s does not support 'think'; retrying without it", model)
                self._think_unsupported.add(model)
                use_think = None
                attempt -= 1  # doesn't consume a retry
                continue

            if resp.status_code >= 500:
                last_exc = OllamaError(f"HTTP {resp.status_code} from Ollama: {resp.text[:200]}")
                logger.warning(
                    "Ollama server error (attempt %d/%d): %s", attempt, max_attempts, last_exc
                )
                if attempt < max_attempts:
                    time.sleep(BACKOFF_SCHEDULE[min(attempt - 1, len(BACKOFF_SCHEDULE) - 1)])
                    continue
                raise last_exc

            if resp.status_code == 404:
                raise ModelMissing(f"Model not found on server: {model}")

            if resp.status_code >= 400:
                raise OllamaError(f"HTTP {resp.status_code} from Ollama: {resp.text[:200]}")

            try:
                data = resp.json()
                content = data["message"]["content"]
                parsed = json.loads(content)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                last_exc = exc
                logger.warning(
                    "Invalid JSON from model (attempt %d/%d): %s", attempt, max_attempts, exc
                )
                if attempt < max_attempts:
                    time.sleep(BACKOFF_SCHEDULE[min(attempt - 1, len(BACKOFF_SCHEDULE) - 1)])
                    continue
                raise OllamaError(f"Invalid JSON content from model: {exc}") from exc

            return parsed

        raise OllamaError(f"Exhausted retries calling Ollama: {last_exc}")
