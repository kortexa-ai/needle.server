"""The worker pool: toolset affinity, a memory cap, and a global compute throttle."""

import asyncio
import hashlib
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .worker import CommandFactory, Worker, WorkerStartError, runner_command

log = logging.getLogger("needle.pool")


class PoolError(Exception):
    status = 503
    code = "unavailable"


class QueueFull(PoolError):
    status = 429
    code = "queue_full"


class DeadlineExceeded(PoolError):
    status = 504
    code = "deadline_exceeded"


class ToolsRejected(PoolError):
    status = 400
    code = "tools_rejected"


class WorkerFailed(PoolError):
    status = 503
    code = "worker_failed"


@dataclass
class Result:
    envelope: dict
    worker: str  # "warm", "spawned" or "replaced"
    queue_ms: float
    engine_ms: float


def toolset_key(tools_json: str, system: str) -> str:
    return hashlib.sha256(f"{tools_json}\n{system}".encode("utf-8")).hexdigest()[:16]


class Pool:
    def __init__(self, command: CommandFactory = runner_command, *,
                 concurrency: int = config.CONCURRENCY,
                 memory_cap_mb: float = config.MEMORY_CAP_MB,
                 max_queue: int = config.MAX_QUEUE,
                 queue_timeout_s: float = config.QUEUE_TIMEOUT_S,
                 deadline_s: float = config.REQUEST_DEADLINE_S,
                 spawn_timeout_s: float = config.SPAWN_TIMEOUT_S,
                 idle_stop_s: float = config.IDLE_STOP_S):
        self._command = command
        self._memory_cap_mb = memory_cap_mb
        self._max_queue = max_queue
        self._queue_timeout_s = queue_timeout_s
        self._deadline_s = deadline_s
        self._spawn_timeout_s = spawn_timeout_s
        self._idle_stop_s = idle_stop_s
        self._permits = asyncio.Semaphore(concurrency)
        self._workers: list[Worker] = []
        self._waiting = 0
        self._active = 0
        self.peak_active = 0
        self._state_dir: Path | None = None
        self._reaper: asyncio.Task | None = None

    async def start(self) -> None:
        self._state_dir = Path(tempfile.mkdtemp(prefix="needle-server-"))
        self._reaper = asyncio.create_task(self._reap_forever())

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        workers, self._workers = self._workers, []
        await asyncio.gather(*(w.stop() for w in workers), return_exceptions=True)
        if self._state_dir is not None:
            shutil.rmtree(self._state_dir, ignore_errors=True)
            self._state_dir = None

    def stats(self) -> dict:
        return {
            "workers": len(self._workers),
            "busy": sum(w.busy for w in self._workers),
            "toolsets": len({w.key for w in self._workers}),
            "memory_mb": round(self._memory_mb(), 1),
            "memory_cap_mb": self._memory_cap_mb,
            "queued": self._waiting,
            "peak_active": self.peak_active,
        }

    async def run(self, tools_json: str, system: str, turns: list[str]) -> Result:
        """Replay `turns` into a fresh session on a worker for this toolset; return the last answer."""
        if self._waiting >= self._max_queue:
            raise QueueFull("too many requests are waiting")
        queued_at = time.monotonic()
        self._waiting += 1
        try:
            await asyncio.wait_for(self._permits.acquire(), self._queue_timeout_s)
        except asyncio.TimeoutError:
            raise QueueFull("no engine became free in time") from None
        finally:
            self._waiting -= 1
        self._active += 1
        self.peak_active = max(self.peak_active, self._active)
        try:
            queue_ms = (time.monotonic() - queued_at) * 1000
            worker, how = await self._checkout(toolset_key(tools_json, system), tools_json, system)
            started_at = time.monotonic()
            try:
                async with asyncio.timeout(self._deadline_s):
                    await worker.reset()
                    for turn in turns[:-1]:
                        await worker.complete(turn)
                    envelope = await worker.complete(turns[-1])
            except TimeoutError:
                log.warning("deadline passed; stopping worker pid=%s key=%s", worker.pid, worker.key)
                await self._discard(worker)
                raise DeadlineExceeded(f"the engine did not answer within {self._deadline_s:g} s") from None
            except asyncio.CancelledError:
                # The runner is mid-conversation and cannot be handed to anyone else.
                await self._discard(worker)
                raise
            except Exception as exc:
                log.warning("worker pid=%s key=%s failed: %r", worker.pid, worker.key, exc)
                await self._discard(worker)
                raise WorkerFailed("the engine worker failed; retry the request") from exc
            worker.busy = False
            worker.last_used = time.monotonic()
            return Result(envelope, how, queue_ms, (time.monotonic() - started_at) * 1000)
        finally:
            self._active -= 1
            self._permits.release()

    def _memory_mb(self) -> float:
        return sum(w.ram_mb for w in self._workers)

    async def _checkout(self, key: str, tools_json: str, system: str) -> tuple[Worker, str]:
        for worker in [w for w in self._workers if w.key == key and not w.busy]:
            if worker.alive:
                worker.busy = True
                return worker, "warm"
            await self._discard(worker)
        how = "spawned"
        while self._memory_mb() + config.WORKER_ESTIMATE_MB > self._memory_cap_mb:
            idle = [w for w in self._workers if not w.busy]
            if not idle:
                raise PoolError("the memory cap leaves no room for another worker")
            await self._discard(min(idle, key=lambda w: w.last_used))
            how = "replaced"
        assert self._state_dir is not None
        worker = Worker(key, tools_json, system, self._state_dir, self._command)
        worker.busy = True
        self._workers.append(worker)  # counted against the cap while it starts
        try:
            await worker.start(self._spawn_timeout_s)
        except WorkerStartError as exc:
            self._workers.remove(worker)
            if exc.rejected:
                raise ToolsRejected(f"the engine refused this toolset ({exc})") from exc
            raise WorkerFailed(str(exc)) from exc
        except BaseException:
            self._workers.remove(worker)
            await worker.stop()
            raise
        log.info("%s worker pid=%s key=%s (%d workers, %.0f MB)",
                 how, worker.pid, key, len(self._workers), self._memory_mb())
        return worker, how

    async def _discard(self, worker: Worker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        await worker.stop()

    async def reap(self) -> None:
        cutoff = time.monotonic() - self._idle_stop_s
        for worker in [w for w in self._workers if not w.busy and w.last_used < cutoff]:
            log.info("stopping idle worker pid=%s key=%s", worker.pid, worker.key)
            await self._discard(worker)

    async def _reap_forever(self) -> None:
        while True:
            await asyncio.sleep(config.REAP_INTERVAL_S)
            await self.reap()
