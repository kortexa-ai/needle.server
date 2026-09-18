"""HTTP API: POST /complete plus /health, /models and /llms.txt."""

import datetime
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from . import config
from .pool import Pool, PoolError


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input: str
    tools: list[dict[str, Any]]
    system: str | None = None
    history: list[str] = []


class RequestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def error_response(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def is_tool_result(text: str) -> bool:
    """Tool results are fed to the engine as JSON; anything else is user text."""
    if not text.lstrip().startswith(("[", "{")):
        return False
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def fit_history(history: list[str], input_text: str) -> tuple[list[str], int]:
    """Keep the newest history that fits. Oldest turns drop first, and the
    replay never starts on a tool result whose request was dropped."""
    kept = list(history[-config.MAX_HISTORY_TURNS:]) if history else []
    budget = config.MAX_TOTAL_CHARS - len(input_text)
    while kept and sum(len(turn) for turn in kept) > budget:
        kept.pop(0)
    while kept and is_tool_result(kept[0]):
        kept.pop(0)
    return kept, len(history) - len(kept)


def check_tools(tools: list[dict[str, Any]]) -> None:
    """The runner loads a malformed toolset as "no tools" and then refuses
    every request without saying why, so the shape is checked here."""
    for index, tool in enumerate(tools):
        spec = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not spec["name"].strip():
            raise RequestError("tools_invalid", f"tools[{index}] needs a non-empty string name")
        if not isinstance(spec.get("parameters", {}), dict):
            raise RequestError("tools_invalid", f"tools[{index}].parameters must be a JSON-schema object")


def with_date_fact(system: str | None) -> str:
    """Without a date fact the model invents one. Day granularity keeps a
    toolset's workers reusable all day; clients needing more send their own."""
    system = (system or "").strip()
    if "date:" in system:
        return system
    fact = datetime.datetime.now().strftime("date: %Y-%m-%d %a")
    return f"{fact}; {system}" if system else fact


def prepare(request: CompleteRequest) -> tuple[str, str, list[str], int]:
    if not request.input.strip():
        raise RequestError("input_empty", "input must not be empty")
    if len(request.input) > config.MAX_INPUT_CHARS:
        raise RequestError("input_too_long",
                           f"input is {len(request.input)} characters; the limit is {config.MAX_INPUT_CHARS}")
    if not request.tools:
        raise RequestError("tools_required", "tools must list at least one tool")
    if len(request.tools) > config.MAX_TOOLS:
        raise RequestError("too_many_tools", f"{len(request.tools)} tools; the limit is {config.MAX_TOOLS}")
    check_tools(request.tools)
    tools_json = json.dumps(request.tools, separators=(",", ":"), ensure_ascii=False)
    if len(tools_json) > config.MAX_TOOLS_CHARS:
        raise RequestError("tools_too_large",
                           f"tools are {len(tools_json)} characters of JSON; the limit is {config.MAX_TOOLS_CHARS}")
    if request.system and len(request.system) > config.MAX_SYSTEM_CHARS:
        raise RequestError("system_too_long",
                           f"system is {len(request.system)} characters; the limit is {config.MAX_SYSTEM_CHARS}")
    history, dropped = fit_history(request.history, request.input)
    if is_tool_result(request.input) and not history:
        raise RequestError("tool_result_without_history",
                           "input is a tool result, but no history that fits the limits precedes it")
    return tools_json, with_date_fact(request.system), [*history, request.input], dropped


def create_app(pool: Pool | None = None) -> FastAPI:
    owned = pool is None
    pool = pool or Pool()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if owned:
            await pool.start()
        try:
            yield
        finally:
            if owned:
                await pool.close()

    app = FastAPI(title="needle.server", version="0.1.0", lifespan=lifespan)

    @app.post("/complete")
    async def complete(request: CompleteRequest):
        try:
            tools_json, system, turns, dropped = prepare(request)
        except RequestError as exc:
            return error_response(400, exc.code, str(exc))
        try:
            result = await pool.run(tools_json, system, turns)
        except PoolError as exc:
            headers = {"Retry-After": "1"} if exc.status in (429, 503) else None
            return error_response(exc.status, exc.code, str(exc), headers)
        return {**result.envelope, "server": {
            "worker": result.worker,
            "replayed_turns": len(turns) - 1,
            "dropped_history": dropped,
            "queue_ms": round(result.queue_ms, 1),
            "engine_ms": round(result.engine_ms, 1),
        }}

    @app.get("/health")
    async def health():
        ready = config.RUNNER.is_file() and config.WEIGHTS.is_file()
        body = {"status": "ok" if ready else "error", "model": config.MODEL_ID, **pool.stats()}
        if not ready:
            body["message"] = "engine files are missing; run setup.sh"
        return JSONResponse(body, status_code=200 if ready else 503)

    @app.get("/models")
    async def models():
        return {"object": "list", "data": [
            {"id": config.MODEL_ID, "object": "model", "owned_by": "cactus-compute"}]}

    @app.get("/llms.txt", response_class=PlainTextResponse)
    async def llms_txt():
        return config.LLMS_TXT.read_text(encoding="utf-8")

    return app


app = create_app()
