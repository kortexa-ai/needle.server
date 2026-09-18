"""A stand-in for upstream's `needle --serve`, for tests. It copies the real
runner's contract, including its request-parsing quirks, so a test fails if the
server ever sends a form the real runner would silently misread."""

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--model")
parser.add_argument("--tools")
parser.add_argument("--system")
parser.add_argument("--serve", action="store_true")
parser.add_argument("--port", type=int)
parser.add_argument("--fail-input-overflow", action="store_true")
args = parser.parse_args()

tools = json.load(open(args.tools, encoding="utf-8"))
system = open(args.system, encoding="utf-8").read()
if any(tool.get("name") == "REJECT" for tool in tools):
    sys.exit(2)
time.sleep(float(os.environ.get("STUB_START_DELAY", "0")))
turns: list[str] = []


def read_input(raw: bytes) -> str:
    text = raw.decode("utf-8")
    if '"input":"' not in text:  # the real parser misses "input": "..." with a space
        return ""
    value = json.loads(text)["input"]
    if "\\u" in text:  # the real parser does not decode \uXXXX escapes
        return value.encode("ascii", "backslashreplace").decode().replace("\\", "")
    return value


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/reset":
            turns.clear()
            body = {}
        else:
            text = read_input(raw)
            if text == "CRASH":
                os._exit(1)
            if text.startswith("SLEEP:"):
                time.sleep(float(text.split(":", 1)[1]))
            turns.append(text)
            body = {"type": "call", "success": True, "confidence": 1.0, "peak_ram_mb": 100.0,
                    "function_calls": [{"name": "echo", "arguments": {
                        "input": text, "turns": list(turns), "system": system,
                        "tools": [tool.get("name") for tool in tools], "pid": os.getpid()}}]}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
