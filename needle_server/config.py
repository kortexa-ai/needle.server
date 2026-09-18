"""Constants. Deliberately not configuration: see PLAN.md, "Limits"."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "engine"
RUNNER = ENGINE_DIR / "needle"
WEIGHTS = ENGINE_DIR / "needle3.cact"
LLMS_TXT = ROOT / "llms.txt"
MODEL_ID = "needle3"

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "4007"))

# Request limits. Engine time grows faster than input length and a call cannot
# be cancelled, so these bound how long one request can hold an engine.
MAX_INPUT_CHARS = 2000
MAX_TOTAL_CHARS = 4000  # input plus the history that is replayed
MAX_HISTORY_TURNS = 6
MAX_SYSTEM_CHARS = 500
MAX_TOOLS = 20
MAX_TOOLS_CHARS = 32000

# Throughput peaks at two concurrent engine operations and falls beyond that.
CONCURRENCY = 2
MAX_QUEUE = 32
QUEUE_TIMEOUT_S = 10.0
REQUEST_DEADLINE_S = 5.0
SPAWN_TIMEOUT_S = 30.0

MEMORY_CAP_MB = 1500
WORKER_ESTIMATE_MB = 100  # used until a worker reports its own peak_ram_mb
IDLE_STOP_S = 600.0
REAP_INTERVAL_S = 30.0
