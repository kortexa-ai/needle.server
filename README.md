# needle.server

A small, deployable server for [Cactus Needle](https://cactuscompute.com/needle).

Needle is a tiny on-device model for tool calling. Text goes in, together with
the list of tools an app exposes; a well-formed JSON tool call comes back. The
whole model is a single 8-29 MB file and runs on hardware as small as a
Raspberry Pi.

- Model: <https://cactuscompute.com/needle>
- Weights and engines: <https://huggingface.co/Cactus-Compute/needle3>
- Upstream source: <https://github.com/cactus-compute/needle>

## Purpose

Run Needle as a long-lived network service, so that any client on the network
can turn plain language into tool calls without shipping the model itself.

The defining feature is that **the toolset belongs to the client, not to the
server**. Each client brings its own set of tools, and can change that set on
the fly. Different clients use different toolsets against the same server at
the same time. Upstream Needle binds one toolset to one engine session, so
managing the model engine on behalf of many clients is the main job of this
project.

## Targets

One server, installed as a native system service:

- macOS, as a `launchd` service
- Ubuntu and other `systemd` Linux machines, as a `systemd` service
- Raspberry Pi 5, as a `systemd` service

## Use it

```sh
./setup.sh   # Python dependencies, plus the pinned upstream runner and weights
./run.sh     # serves on port 4007
```

```sh
curl -s http://localhost:4007/complete -H 'Content-Type: application/json' -d '{
  "input": "lock the front door",
  "tools": [{"name": "lock_door", "description": "Lock a door",
             "parameters": {"type": "object", "properties": {"door": {"type": "string"}}, "required": ["door"]}}]
}'
```

[`llms.txt`](llms.txt) is the full client contract, written so that a coding
agent can generate a client from it. A running server also serves it at
`/llms.txt`, next to `/openapi.json`, `/health` and `/models`.

The service definitions are in `launchd/` and `systemd/`. The design, and the
measurements behind it, are in [`PLAN.md`](PLAN.md).

```sh
.venv/bin/python -m pytest   # the integration tests need ./setup.sh first
```

## License

[MIT](LICENSE)
