"""Against the real upstream runner. Skipped until setup.sh has fetched it."""

import json

import httpx
import pytest
import pytest_asyncio

from needle_server import config
from needle_server.app import create_app
from needle_server.pool import Pool

pytestmark = pytest.mark.skipif(not (config.RUNNER.is_file() and config.WEIGHTS.is_file()),
                                reason="engine files are missing; run setup.sh")


def tool(name, description, properties, required):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}


HOME = [tool("set_lights", "Turn a room's lights on or off and set brightness",
             {"room": {"type": "string"}, "on": {"type": "boolean"},
              "brightness": {"type": "integer", "minimum": 0, "maximum": 100}}, ["room", "on"]),
        tool("lock_door", "Lock a door", {"door": {"type": "string"}}, ["door"])]
MEDIA = [tool("play_music", "Play music by genre, artist or title", {"query": {"type": "string"}}, ["query"])]
NOTES = [tool("create_note", "Create a note with a title and body text",
              {"title": {"type": "string"}, "body": {"type": "string"}}, ["title"]),
         tool("delete_note", "Delete a note by its numeric id", {"note_id": {"type": "integer"}}, ["note_id"])]
REMINDERS = [tool("create_reminder", "Create a reminder for a date",
                  {"message": {"type": "string"}, "date": {"type": "string", "description": "ISO date YYYY-MM-DD"}},
                  ["message", "date"])]


@pytest_asyncio.fixture
async def client():
    pool = Pool()
    await pool.start()
    transport = httpx.ASGITransport(app=create_app(pool))
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as http:
        yield http
    await pool.close()


async def complete(client, **body):
    response = await client.post("/complete", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def calls(body):
    return [(call["name"], call["arguments"]) for call in body["function_calls"]]


async def test_the_engine_sees_the_whole_input(client):
    body = await complete(client, input="lock the front door", tools=HOME)
    assert calls(body) == [("lock_door", {"door": "front door"})]
    assert body["confidence"] >= 0.7


async def test_unicode_survives_the_trip(client):
    body = await complete(client, input="create a note titled café crème", tools=NOTES)
    assert calls(body)[0][1]["title"].lower() == "café crème"


async def test_two_clients_with_different_toolsets_share_the_server(client):
    home = await complete(client, input="play some jazz", tools=HOME)
    media = await complete(client, input="play some jazz", tools=MEDIA)
    again = await complete(client, input="lock the back door", tools=HOME)
    assert calls(home) == []  # the home toolset refuses a media request
    assert calls(media) == [("play_music", {"query": "jazz"})]
    assert calls(again) == [("lock_door", {"door": "back door"})]
    assert (home["server"]["worker"], media["server"]["worker"], again["server"]["worker"]) == \
        ("spawned", "spawned", "warm")


async def test_replayed_history_carries_a_tool_result_forward(client):
    history = ["create a note titled groceries", json.dumps([{"note_id": 48213, "title": "groceries"}])]
    body = await complete(client, input="actually delete that note", tools=NOTES, history=history)
    assert calls(body) == [("delete_note", {"note_id": 48213})]
    assert body["server"]["replayed_turns"] == 2
    forgotten = await complete(client, input="actually delete that note", tools=NOTES)
    assert calls(forgotten) != [("delete_note", {"note_id": 48213})]  # stateless: nothing leaked


async def test_tomorrow_resolves_against_the_date_fact(client):
    body = await complete(client, input="remind me tomorrow to call mom", tools=REMINDERS,
                          system="date: 2026-09-17 Thu")
    assert calls(body)[0][1]["date"] == "2026-09-18"


async def test_openai_style_tool_schemas_work(client):
    openai_tools = [{"type": "function", "function": t} for t in MEDIA]
    body = await complete(client, input="play some jazz", tools=openai_tools)
    assert calls(body) == [("play_music", {"query": "jazz"})]
