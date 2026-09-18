import asyncio
import json
import os
import time

import pytest

from needle_server.pool import DeadlineExceeded, QueueFull, ToolsRejected, WorkerFailed

from conftest import tools

LIGHTS = json.dumps(tools("set_lights"))
MUSIC = json.dumps(tools("play_music"))
SYSTEM = "date: 2026-09-17 Thu"


def echo(result):
    return result.envelope["function_calls"][0]["arguments"]


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


async def test_same_toolset_reuses_the_warm_worker(make_pool):
    pool = await make_pool()
    first = await pool.run(LIGHTS, SYSTEM, ["lights on"])
    second = await pool.run(LIGHTS, SYSTEM, ["lights off"])
    assert (first.worker, second.worker) == ("spawned", "warm")
    assert echo(first)["pid"] == echo(second)["pid"]
    assert pool.stats()["workers"] == 1


async def test_each_toolset_gets_its_own_worker_with_its_own_tools(make_pool):
    pool = await make_pool()
    lights = await pool.run(LIGHTS, SYSTEM, ["hello"])
    music = await pool.run(MUSIC, SYSTEM, ["hello"])
    assert echo(lights)["tools"] == ["set_lights"]
    assert echo(music)["tools"] == ["play_music"]
    assert echo(lights)["system"] == SYSTEM
    assert pool.stats()["toolsets"] == 2


async def test_system_facts_are_part_of_the_toolset_key(make_pool):
    pool = await make_pool()
    await pool.run(LIGHTS, "date: 2026-09-17 Thu", ["hello"])
    other = await pool.run(LIGHTS, "date: 2026-09-18 Fri", ["hello"])
    assert other.worker == "spawned"


async def test_history_is_replayed_into_a_fresh_session_every_time(make_pool):
    pool = await make_pool()
    await pool.run(LIGHTS, SYSTEM, ["a stale conversation"])
    result = await pool.run(LIGHTS, SYSTEM, ["first", '[{"ok": true}]', "latest"])
    assert result.worker == "warm"
    assert echo(result)["turns"] == ["first", '[{"ok": true}]', "latest"]


async def test_input_reaches_the_runner_in_the_only_form_it_parses(make_pool):
    pool = await make_pool()
    result = await pool.run(LIGHTS, SYSTEM, ['dim the "café" lights'])
    assert echo(result)["input"] == 'dim the "café" lights'


async def test_memory_cap_replaces_the_least_recently_used_idle_worker(make_pool):
    pool = await make_pool(memory_cap_mb=250)
    await pool.run(LIGHTS, SYSTEM, ["hello"])
    await pool.run(MUSIC, SYSTEM, ["hello"])
    third = await pool.run(json.dumps(tools("lock_door")), SYSTEM, ["hello"])
    assert third.worker == "replaced"
    assert pool.stats()["workers"] == 2
    assert (await pool.run(MUSIC, SYSTEM, ["hello"])).worker == "warm"  # LIGHTS was the oldest
    assert (await pool.run(LIGHTS, SYSTEM, ["hello"])).worker == "replaced"


async def test_at_most_two_engine_operations_run_at_once(make_pool):
    pool = await make_pool(concurrency=2)
    toolsets = [json.dumps(tools(f"tool_{i}")) for i in range(6)]
    started = time.monotonic()
    await asyncio.gather(*(pool.run(t, SYSTEM, ["SLEEP:0.3"]) for t in toolsets))
    assert pool.peak_active == 2
    assert time.monotonic() - started >= 0.85  # three waves of two, not one wave of six


async def test_busy_toolset_gets_a_second_worker(make_pool):
    pool = await make_pool(concurrency=2)
    results = await asyncio.gather(pool.run(LIGHTS, SYSTEM, ["SLEEP:0.3"]), pool.run(LIGHTS, SYSTEM, ["SLEEP:0.3"]))
    assert len({echo(r)["pid"] for r in results}) == 2


async def test_deadline_stops_the_worker(make_pool):
    pool = await make_pool(deadline_s=0.3)
    warm = await pool.run(LIGHTS, SYSTEM, ["hello"])
    with pytest.raises(DeadlineExceeded):
        await pool.run(LIGHTS, SYSTEM, ["SLEEP:5"])
    assert pool.stats()["workers"] == 0
    assert gone(echo(warm)["pid"])
    assert (await pool.run(LIGHTS, SYSTEM, ["hello"])).worker == "spawned"


async def test_full_queue_is_refused(make_pool):
    pool = await make_pool(concurrency=1, max_queue=1)
    outcomes = await asyncio.gather(*(pool.run(LIGHTS, SYSTEM, ["SLEEP:0.3"]) for _ in range(3)),
                                    return_exceptions=True)
    assert sum(isinstance(o, QueueFull) for o in outcomes) == 1


async def test_queue_wait_is_bounded(make_pool):
    pool = await make_pool(concurrency=1, queue_timeout_s=0.1)
    outcomes = await asyncio.gather(pool.run(LIGHTS, SYSTEM, ["SLEEP:0.5"]), pool.run(LIGHTS, SYSTEM, ["hello"]),
                                    return_exceptions=True)
    assert isinstance(outcomes[1], QueueFull)


async def test_crashed_worker_is_dropped_and_replaced(make_pool):
    pool = await make_pool()
    with pytest.raises(WorkerFailed):
        await pool.run(LIGHTS, SYSTEM, ["CRASH"])
    assert pool.stats()["workers"] == 0
    assert (await pool.run(LIGHTS, SYSTEM, ["hello"])).worker == "spawned"


async def test_worker_that_died_while_idle_is_replaced(make_pool):
    pool = await make_pool()
    first = await pool.run(LIGHTS, SYSTEM, ["hello"])
    os.kill(echo(first)["pid"], 9)
    await asyncio.sleep(0.2)
    assert (await pool.run(LIGHTS, SYSTEM, ["hello"])).worker == "spawned"


async def test_toolset_the_runner_refuses_is_a_client_error(make_pool):
    pool = await make_pool()
    with pytest.raises(ToolsRejected):
        await pool.run(json.dumps(tools("REJECT")), SYSTEM, ["hello"])
    assert pool.stats() ["workers"] == 0


async def test_idle_workers_stop(make_pool):
    pool = await make_pool(idle_stop_s=0.1)
    first = await pool.run(LIGHTS, SYSTEM, ["hello"])
    await asyncio.sleep(0.2)
    await pool.reap()
    assert pool.stats()["workers"] == 0
    assert gone(echo(first)["pid"])


async def test_close_stops_every_worker(make_pool):
    pool = await make_pool()
    pids = [echo(await pool.run(t, SYSTEM, ["hello"]))["pid"] for t in (LIGHTS, MUSIC)]
    await pool.close()
    assert all(gone(pid) for pid in pids)
