"""One worker is one upstream `needle --serve` process bound to one toolset."""

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Callable

import httpx

from . import config

log = logging.getLogger("needle.worker")

CommandFactory = Callable[[Path, Path, int], list[str]]


class WorkerStartError(Exception):
    """The runner did not come up. `rejected` means it exited on its own."""

    def __init__(self, message: str, rejected: bool):
        super().__init__(message)
        self.rejected = rejected


def runner_command(tools_path: Path, system_path: Path, port: int) -> list[str]:
    return [
        str(config.RUNNER),
        "--model", str(config.WEIGHTS),
        "--tools", str(tools_path),
        "--system", str(system_path),
        "--serve", "--port", str(port),
        "--fail-input-overflow",
    ]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def encode(body: dict) -> bytes:
    # The runner's request parser is hand-rolled: it does not find "input" when
    # a space follows the colon, and it does not decode \uXXXX escapes. Compact
    # separators and raw UTF-8 are the only form it reads correctly.
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Worker:
    def __init__(self, key: str, tools_json: str, system: str, state_dir: Path,
                 command: CommandFactory = runner_command):
        self.key = key
        self.busy = False
        self.ram_mb = float(config.WORKER_ESTIMATE_MB)
        self.last_used = time.monotonic()
        self._tools_json = tools_json
        self._system = system
        self._state_dir = state_dir
        self._command = command
        self._process: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    async def start(self, timeout: float = config.SPAWN_TIMEOUT_S) -> None:
        tools_path = self._state_dir / f"{self.key}.tools.json"
        system_path = self._state_dir / f"{self.key}.system.txt"
        tools_path.write_text(self._tools_json, encoding="utf-8")
        system_path.write_text(self._system, encoding="utf-8")
        port = _free_port()
        env = {**os.environ, "NEEDLE_TELEMETRY": "0", "DO_NOT_TRACK": "1"}
        self._process = await asyncio.create_subprocess_exec(
            *self._command(tools_path, system_path, port), env=env,
            stdin=asyncio.subprocess.DEVNULL)
        # The runner answers one request per connection; never reuse one.
        self._client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=None,
            limits=httpx.Limits(max_keepalive_connections=0))
        deadline = time.monotonic() + timeout
        while True:
            if self._process.returncode is not None:
                code = self._process.returncode
                await self.stop()
                raise WorkerStartError(f"runner exited with code {code} during startup", rejected=True)
            try:
                await self.reset()
                return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                await self.stop()
                raise WorkerStartError(f"runner was not ready after {timeout:.0f} s", rejected=False)
            await asyncio.sleep(0.01)

    async def _post(self, path: str, body: dict) -> dict:
        assert self._client is not None
        response = await self._client.post(
            path, content=encode(body),
            headers={"Content-Type": "application/json", "Connection": "close"})
        response.raise_for_status()
        return response.json() if response.content else {}

    async def reset(self) -> None:
        await self._post("/reset", {})

    async def complete(self, text: str) -> dict:
        envelope = await self._post("/complete", {"input": text})
        peak = envelope.get("peak_ram_mb")
        if isinstance(peak, (int, float)) and peak > 0:
            self.ram_mb = float(peak)
        return envelope

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            # A runner inside an engine call ignores SIGTERM until it returns.
            process.kill()
            await process.wait()
