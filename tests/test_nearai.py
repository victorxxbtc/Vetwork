"""The NEAR AI path against a mocked OpenAI-compatible endpoint (no network, no key)."""

import json

import httpx

from swarm.agents.llm import NearAI, cost_usd, parse_quotes
from swarm.engine import Swarm, run_job
from swarm.evaluate import load_decimals, load_snapshot


def mock_client(reply: str) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/chat/completions")
        assert req.headers["Authorization"] == "Bearer test-key"
        body = json.loads(req.content)
        assert body["model"] and body["messages"][0]["role"] == "system"
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}],
                                         "usage": {"prompt_tokens": 400, "completion_tokens": 50}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_quotes_tolerates_fences():
    assert parse_quotes('```json\n[{"quote": "a"}, "b"]\n```') == ["a", "b"]
    assert parse_quotes("no json here") == []


def test_cost_uses_listed_price():
    assert abs(cost_usd("Qwen/Qwen3.8-27B", 1_000_000, 1_000_000) - 1.39) < 1e-9


def test_llm_backend_meters_real_usage_and_reviewer_checks_llm_quotes():
    snap = load_snapshot("marketing.sputnik-dao.near")
    llm = NearAI("test-key", "https://example.invalid/v1", client=mock_client('[{"quote": "this text is not in the proposal"}]'))
    r = run_job(Swarm.seeded(), snap, 1005, load_decimals(), job_id=3, llm=llm)
    paid = [e for e in r.events if e["type"] == "inference_paid"]
    assert paid and all(e["metered"] and e["tokens_in"] == 400 for e in paid)
    # milestone-llm bids cheapest, invents a quote, gets sent back
    sent_back = [e for e in r.events if e["type"] == "reviewed" and not e["accepted"]]
    assert any(e["agent"].startswith("milestone-llm.") for e in sent_back)
    assert r.backend == "nearai"
