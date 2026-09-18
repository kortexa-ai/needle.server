# Plan

Design for the first working version. Evidence for every number here is in
[issue #2](https://github.com/kortexa-ai/needle.server/issues/2).

## Shape

A small Python front server (FastAPI + uvicorn, Python 3.12, uv) in front of a
pool of **upstream native `needle` runner processes**. We write no inference
code and no worker code: a worker *is* upstream's binary in `--serve` mode,
listening on a private localhost port.

Why the native runner and not the shared library: it starts in ~90 ms, reports
real context overflow (`--fail-input-overflow`), does its own grounding
validation, and can be killed when a request overruns. The shared library has
none of that, cannot be cancelled, and picks its own thread count.

## API

Native, not OpenAI-compatible. It is upstream's `--serve` contract plus the
fields a multi-client server needs. The server is stateless: the client owns
the conversation.

`POST /complete`

```json
{
  "input": "dim the living room to 30",
  "tools": [{"name": "set_lights", "description": "...", "parameters": {}}],
  "system": "locale: en-US; device: speaker",
  "history": ["turn on the living room lights", "[{\"ok\": true}]"]
}
```

- `input` - required. The latest user text, or a JSON list of tool results.
- `tools` - required. Compact or OpenAI-style schemas; the engine takes both.
- `system` - optional environment *facts*, never instructions. The server adds
  a `date:` fact when the client sends none.
- `history` - optional earlier inputs, oldest first. The server replays them
  into a fresh engine session before `input`. Replay is deterministic.

The response is the engine envelope, unchanged (`function_calls`,
`suppressed_calls`, `confidence`, `reasoning`, `validation`, ...), plus one
`server` object: how the worker was obtained, replayed turns, dropped history
turns, queue time. Confidence gating is the client's job; `llms.txt` teaches it.

Also: `GET /health`, `GET /models`, `GET /llms.txt`. FastAPI serves
`/openapi.json` for free.

Errors: `400` a limit was passed or the tools are malformed, `429` +
`Retry-After` queue full, `503` a worker failed, `504` deadline passed (the
worker is killed and replaced). History that does not fit is dropped, not refused.

## Limits

Constants, not configuration, until a real client needs otherwise.

- Latest `input`: 2,000 characters. Over the limit is a `400`, never a silent cut.
- `input` plus replayed `history`: 4,000 characters, about 1.3 s of engine time
  at worst. Without this, six full-size turns would pass the deadline.
- `history`: at most 6 replayed turns. Older turns drop first, in whole
  exchanges, so a replay never starts on a tool result.
- `tools`: 1 to 20, at most 32,000 characters of JSON. Starting an engine costs
  about 1 s for 20 tools and grows faster than the tool count.
- `system`: 500 characters.
- Request deadline: 5 s. `--fail-input-overflow` is set, but it never fired in
  tests: 16 KB of input still answered after 11 s. Latency is the real limit.

## Pool

- Key: hash of the canonical `tools` JSON plus the `system` facts. Workers
  belong to toolsets, not to clients.
- Starts empty. A worker cannot exist before its toolset is known, and a cold
  start is ~90 ms for 3 tools (~1 s for 20), so pre-warming buys nothing.
- A request takes a worker exclusively: `POST /reset`, replay `history`, then
  `input`. The runner holds a single conversation, so it is never shared.
- Miss: spawn a worker if memory allows; otherwise stop the least recently
  used idle worker and spawn in its place; otherwise wait in the queue.
- Memory cap 1.5 GB, counted from the `peak_ram_mb` each worker reports
  (~100 MB each, so roughly 14 workers).
- Idle workers stop after 10 minutes.
- A popular toolset may hold two workers, because two can compute at once.

## Throttle

At most **2** engine operations at a time across the whole server (spawn,
replay, complete). Measured: throughput peaks at 2 and falls beyond it. The rest
wait in a bounded FIFO queue; a full queue answers `429`.

## Service

- `setup.sh` creates `.venv/` and fetches the runner and weights for this
  platform from a pinned Hugging Face revision, with checksums.
- `run.sh` starts the front server on port 4007. `launchd/*.plist` and `systemd/*.service`
  follow the layout the other Kortexa servers use, so `ktxsvc` finds them.
- Workers always run with `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1`.
- The systemd unit sets no `CPUAffinity` and no `CPUQuota`: confining the
  engine made it 40-80x slower.

## Code map

- `needle_server/worker.py` - one runner process: spawn, ready, reset, complete, stop.
- `needle_server/pool.py` - toolset key, LRU replacement, memory cap, idle stop, throttle, queue.
- `needle_server/app.py` - the HTTP API, the limits and the history fitting.
- `tests/stub_runner.py` - a stand-in runner that copies the real parser's quirks.
- `tests/test_integration.py` - the same API against the real runner.

## What the runner taught us

- Its request parser is hand-rolled. `{"input": "..."}` with a space after the
  colon is read as an empty input, and `\uXXXX` escapes are not decoded, both
  without any error. `worker.encode()` sends the one form it reads correctly,
  and the integration tests guard it.
- It loads a malformed toolset as "no tools" and then refuses everything. The
  server checks the tool shape and answers `tools_invalid` instead.
- A `date:` fact without a time of day works: "tomorrow" resolves correctly.
  Without any date fact the model invents a date, so the server always adds one.

## Still open

- Whether `--threads` does anything. It made no difference on an M4 Pro. This
  must be known before running on a shared Linux machine.
- Whether a long-lived worker's memory grows. Reported `peak_ram_mb` crept up
  by about 1 MB per worker over 100 requests. If it keeps growing, recycle
  workers after a fixed number of requests.
- Validation on Linux and on a Raspberry Pi 5.

## Not now

- OpenAI `/v1/chat/completions` compatibility. If it is ever wanted it is a
  thin translation in front of `/complete`.
- Structured extraction and embeddings, which the engine also offers.
- Authentication. The front server listens on the LAN; the public API gateway
  owns auth when this is exposed.
- Skipping the replay when a worker already holds the same conversation.
