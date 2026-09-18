import datetime
import json

import httpx
import pytest_asyncio

from needle_server import config
from needle_server.app import create_app, fit_history, is_tool_result

from conftest import tools


@pytest_asyncio.fixture
async def client(make_pool):
    pool = await make_pool(concurrency=1, max_queue=1)
    transport = httpx.ASGITransport(app=create_app(pool))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def echo(response):
    return response.json()["function_calls"][0]["arguments"]


async def test_complete_returns_the_engine_envelope_plus_server_block(client):
    response = await client.post("/complete", json={"input": "lights on", "tools": tools("set_lights")})
    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] == 1.0 and echo(response)["input"] == "lights on"
    assert body["server"]["worker"] == "spawned"
    assert body["server"]["replayed_turns"] == 0 and body["server"]["dropped_history"] == 0


async def test_server_adds_a_date_fact_and_keeps_client_facts(client):
    today = datetime.datetime.now().strftime("date: %Y-%m-%d %a")
    plain = await client.post("/complete", json={"input": "hi", "tools": tools("a")})
    facts = await client.post("/complete", json={"input": "hi", "tools": tools("a"), "system": "locale: en-US"})
    own = await client.post("/complete", json={"input": "hi", "tools": tools("a"), "system": "date: 2030-01-01 Tue"})
    assert echo(plain)["system"] == today
    assert echo(facts)["system"] == f"{today}; locale: en-US"
    assert echo(own)["system"] == "date: 2030-01-01 Tue"


async def test_history_is_replayed_and_reported(client):
    response = await client.post("/complete", json={
        "input": "and the kitchen", "tools": tools("set_lights"),
        "history": ["lights on in the hall", '[{"ok": true}]']})
    assert echo(response)["turns"] == ["lights on in the hall", '[{"ok": true}]', "and the kitchen"]
    assert response.json()["server"]["replayed_turns"] == 2


async def test_long_input_is_refused_not_cut(client):
    response = await client.post("/complete", json={"input": "x" * (config.MAX_INPUT_CHARS + 1), "tools": tools("a")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "input_too_long"


async def test_request_validation(client):
    cases = [
        ({"input": "   ", "tools": tools("a")}, "input_empty"),
        ({"input": "hi", "tools": []}, "tools_required"),
        ({"input": "hi", "tools": tools(*[f"t{i}" for i in range(config.MAX_TOOLS + 1)])}, "too_many_tools"),
        ({"input": "hi", "tools": tools("a"), "system": "x" * (config.MAX_SYSTEM_CHARS + 1)}, "system_too_long"),
        ({"input": '[{"ok": true}]', "tools": tools("a")}, "tool_result_without_history"),
        ({"input": "hi", "tools": [{"name": 5}]}, "tools_invalid"),
        ({"input": "hi", "tools": [{"description": "nameless"}]}, "tools_invalid"),
        ({"input": "hi", "tools": [{"name": "a", "parameters": "nonsense"}]}, "tools_invalid"),
        ({"input": "hi", "tools": [{"type": "function", "function": {"description": "nameless"}}]}, "tools_invalid"),
    ]
    for body, code in cases:
        response = await client.post("/complete", json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (400, code)
    assert (await client.post("/complete", json={"tools": tools("a")})).status_code == 422


async def test_refused_toolset_is_a_400(client):
    response = await client.post("/complete", json={"input": "hi", "tools": tools("REJECT")})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "tools_rejected")


async def test_full_queue_is_a_429_with_retry_after(client):
    import asyncio
    body = {"input": "SLEEP:0.3", "tools": tools("a")}
    responses = await asyncio.gather(*(client.post("/complete", json=body) for _ in range(3)))
    refused = [r for r in responses if r.status_code == 429]
    assert len(refused) == 1 and refused[0].headers["Retry-After"] == "1"
    assert refused[0].json()["error"]["code"] == "queue_full"


def test_history_drops_oldest_first_and_never_starts_on_a_tool_result():
    result = '[{"ok": true}]'
    history = ["one", result, "two", result, "three", result, "four", result]
    kept, dropped = fit_history(history, "now")
    assert kept == ["two", result, "three", result, "four", result] and dropped == 2
    kept, dropped = fit_history(["old", result, "recent"], "x" * (config.MAX_TOTAL_CHARS - 10))
    assert kept == ["recent"] and dropped == 2
    kept, dropped = fit_history(["a" * config.MAX_TOTAL_CHARS, result], "now")
    assert kept == [] and dropped == 2
    assert fit_history([], "now") == ([], 0)


def test_tool_results_are_json_and_user_text_is_not():
    assert is_tool_result('[{"ok": true}]') and is_tool_result(' {"temp": 21}')
    assert not is_tool_result("turn on the lights") and not is_tool_result("[unclosed")


async def test_health_models_and_llms_txt(client):
    health = await client.get("/health")
    assert health.json()["model"] == "needle3" and "workers" in health.json()
    models = (await client.get("/models")).json()
    assert models["data"][0]["id"] == "needle3"
    llms = await client.get("/llms.txt")
    assert llms.status_code == 200 and "POST /complete" in llms.text
