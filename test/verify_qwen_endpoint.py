"""Check whether the live Qwen endpoint exposes the required native fields."""
import json
import sys
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import load_config
BASE = {"model": "Qwen/Qwen3-8B", "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 64}
TOOL = {"type": "function", "function": {"name": "probe_echo", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}}
CASES: dict[str, dict[str, Any]] = {
    "baseline": {},
    "tools": {"tools": [TOOL]},
    "tool_choice": {"tool_choice": "none"},
    "schema": {"response_format": {"type": "json_schema", "json_schema": {"name": "Probe", "schema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}}},
    "max_completion_tokens": {"max_completion_tokens": 64},
}

def main():
    url = load_config().qwen.base_url.rstrip("/") + "/chat/completions"
    failures = []
    for name, fields in CASES.items():
        request = urllib.request.Request(url, data=json.dumps({**BASE, **fields}).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                print(f"{name}: HTTP {response.status}")
        except urllib.error.HTTPError as error:
            # The service currently emits JSON with escaped Chinese but declares no charset.
            body = error.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("error", "")
            except json.JSONDecodeError:
                message = "non-JSON error response"
            print(f"{name}: HTTP {error.code}; {message}")
            failures.append(name)
    if failures:
        raise SystemExit("Required protocol fields failed: " + ", ".join(failures))

if __name__ == "__main__":
    main()

