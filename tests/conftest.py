import sys
from pathlib import Path

import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from needle_server.pool import Pool  # noqa: E402

STUB = Path(__file__).with_name("stub_runner.py")


def stub_command(tools_path, system_path, port):
    return [sys.executable, str(STUB), "--model", "stub.cact", "--tools", str(tools_path),
            "--system", str(system_path), "--serve", "--port", str(port), "--fail-input-overflow"]


def tools(*names):
    return [{"name": name, "description": f"Do {name}",
             "parameters": {"type": "object", "properties": {}, "required": []}} for name in names]


@pytest_asyncio.fixture
async def make_pool():
    pools = []

    async def factory(**options):
        pool = Pool(stub_command, **options)
        await pool.start()
        pools.append(pool)
        return pool

    yield factory
    for pool in pools:
        await pool.close()
