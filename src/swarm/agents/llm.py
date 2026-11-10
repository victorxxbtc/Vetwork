"""NEAR AI Cloud client (OpenAI-compatible) and the inference price table.

Prices are per million tokens in USD as listed by
https://cloud-api.near.ai/v1/model/list on 2026-11-10.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = os.environ.get("NEAR_AI_BASE_URL", "https://cloud-api.near.ai/v1")

PRICES_PER_M = {  # (input, output) USD per 1M tokens
    "Qwen/Qwen3.8-27B": (0.04, 1.35),
    "deepseek-ai/DeepSeek-V4.1-Flash-1017": (0.30, 1.20),
    "moonshotai/kimi-k3": (3.30, 16.50),
}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = PRICES_PER_M.get(model, (1.0, 1.0))
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int


class NearAI:
    def __init__(self, api_key: str, base_url: str = BASE_URL, client: httpx.Client | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=60)

    def chat(self, model: str, system: str, user: str, max_tokens: int = 300) -> ChatResult:
        r = self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
        )
        r.raise_for_status()
        d = r.json()
        usage = d.get("usage") or {}
        text = d["choices"][0]["message"]["content"] or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        return ChatResult(text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))


def parse_quotes(text: str) -> list[str]:
    """Pull a JSON list of strings out of a model reply; tolerate code fences."""
    m = re.search(r"\[.*\]", text, flags=re.S)
    if not m:
        return []
    try:
        data: Any = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        q = item.get("quote") if isinstance(item, dict) else item
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
    return out[:5]


def client_from_env() -> NearAI | None:
    key = os.environ.get("NEAR_AI_API_KEY", "").strip()
    return NearAI(key) if key else None
