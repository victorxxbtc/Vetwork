"""Read-only NEAR JSON-RPC client (stdlib only, no keys, no transactions)."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Any

import os

MAINNET_RPC = os.environ.get("NEAR_RPC_URL", "https://free.rpc.fastnear.com")
TESTNET_RPCS = [
    os.environ.get("TESTNET_RPC_URL", ""),
    "https://near-testnet.drpc.org",
    "https://test.rpc.fastnear.com",
    "https://rpc.testnet.near.org",
]
TESTNET_RPCS = [u for u in TESTNET_RPCS if u]
TESTNET_RPC = TESTNET_RPCS[0]


class RpcError(RuntimeError):
    pass


class NearRpc:
    """Calls view methods over JSON-RPC. Never signs anything."""

    def __init__(self, url: str = MAINNET_RPC, timeout: float = 60.0, retries: int = 4):
        self.url = url
        self.timeout = timeout
        self.retries = retries

    def _post(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"jsonrpc": "2.0", "id": "vetwork", "method": method, "params": params}).encode()
        urls = [self.url] + [u for u in TESTNET_RPCS if u != self.url]
        last: Exception | None = None
        for attempt in range(self.retries):
            target_url = urls[attempt % len(urls)]
            req = urllib.request.Request(
                target_url,
                body,
                {"Content-Type": "application/json", "User-Agent": "Vetwork/1.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.load(resp)
                if "error" in payload:
                    raise RpcError(json.dumps(payload["error"])[:400])
                return payload["result"]
            except (urllib.error.URLError, TimeoutError, RpcError) as exc:
                last = exc
                time.sleep(1.0 + attempt * 1.5)
        raise RpcError(f"{method} failed after {self.retries} attempts: {last}")

    def view(self, account_id: str, method: str, args: dict[str, Any] | None = None) -> Any:
        params = {
            "request_type": "call_function",
            "finality": "final",
            "account_id": account_id,
            "method_name": method,
            "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode(),
        }
        result = self._post("query", params)
        return json.loads(bytes(result["result"]).decode())

    def view_account(self, account_id: str) -> dict[str, Any]:
        return self._post(
            "query", {"request_type": "view_account", "finality": "final", "account_id": account_id}
        )
