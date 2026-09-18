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

## Status

Early. Nothing runs yet.

## License

[MIT](LICENSE)
