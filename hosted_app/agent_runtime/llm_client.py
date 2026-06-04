"""OpenAI-compatible chat client used by the hosted agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    chat_path: str = "/chat/completions"
    timeout: int = 120

    @classmethod
    def from_env(cls) -> "LLMConfig":
        base_url = os.environ.get("CODEX_BASE_URL", "").strip().rstrip("/")
        api_key = os.environ.get("CODEX_API_KEY", "").strip()
        model = os.environ.get("CODEX_MODEL", "").strip()
        chat_path = os.environ.get("CODEX_CHAT_PATH", "/chat/completions").strip()
        timeout = int(os.environ.get("CODEX_TIMEOUT", "120"))
        missing = [
            name
            for name, value in (
                ("CODEX_BASE_URL", base_url),
                ("CODEX_API_KEY", api_key),
                ("CODEX_MODEL", model),
            )
            if not value
        ]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Missing hosted agent secret(s): {joined}")
        if not chat_path.startswith("/"):
            chat_path = "/" + chat_path
        return cls(base_url=base_url, api_key=api_key, model=model, chat_path=chat_path, timeout=timeout)


class ChatClient:
    """Small client for OpenAI-compatible chat-completions relays."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        url = f"{self.config.base_url}{self.config.chat_path}"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM request failed: HTTP {resp.status_code}: {resp.text[:1000]}")
        data = resp.json()
        if "choices" in data and data["choices"]:
            message = data["choices"][0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        raise RuntimeError(f"Unsupported LLM response shape: {str(data)[:1000]}")
