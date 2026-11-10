"""Document fixed fifteen-second playback (v0.12.1) in the reviewed contract.

Idempotent: new attempts use 15000 ms; existing attempts retain their recorded
durationMs (5000-15000) during an upgrade. Every response
example that embeds an attempt receives the documented default, and the packaged
byte copy plus the hash/version pins in test_http_contracts.py are refreshed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.12.1"
DURATION = {
    "type": "integer",
    "minimum": 5000,
    "maximum": 15000,
    "description": (
        "Full-screen milliseconds. Every new attempt is fixed at 15000 regardless of "
        "queue length or deployment settings. Attempts created before this policy change "
        "retain their recorded duration (5000-15000), including idempotent command replay. "
        "complete is accepted only after firstFrameAt + durationMs."
    ),
}


def _is_attempt(value):
    return isinstance(value, dict) and "attemptId" in value and "firstFrameAt" in value


def _add_to_examples(node):
    if isinstance(node, dict):
        if _is_attempt(node):
            # Keep key order readable: right after firstFrameAt.
            items = list(node.items())
            if "durationMs" not in node:
                index = [key for key, _ in items].index("firstFrameAt") + 1
                items.insert(index, ("durationMs", 15000))
                node.clear()
                node.update(items)
        for value in node.values():
            _add_to_examples(value)
    elif isinstance(node, list):
        for value in node:
            _add_to_examples(value)


def build(contract):
    contract["info"]["version"] = max(
        VERSION, contract["info"]["version"], key=lambda version: tuple(map(int, version.split(".")))
    )
    contract["info"]["description"] = contract["info"]["description"].replace(
        "then reports completion after the server-chosen durationMs (15 seconds, down to 8 when a queue builds up).",
        "then reports completion after at least 15 seconds.",
    )
    schema = contract["components"]["schemas"]["PlaybackAttempt"]
    properties = list(schema["properties"].items())
    if "durationMs" not in schema["properties"]:
        index = [key for key, _ in properties].index("firstFrameAt") + 1
        properties.insert(index, ("durationMs", DURATION))
        schema["properties"] = dict(properties)
    else:
        schema["properties"]["durationMs"] = DURATION
    if "durationMs" not in schema["required"]:
        schema["required"].insert(schema["required"].index("firstFrameAt") + 1, "durationMs")
    for path in contract["paths"].values():
        for endpoint in path.values():
            if isinstance(endpoint, dict):
                _add_to_examples(endpoint.get("responses", {}))
                description = endpoint.get("description")
                if isinstance(description, str):
                    endpoint["description"] = description.replace("firstFrameAt+15s", "firstFrameAt+durationMs")
    _add_to_examples(contract["components"].get("examples", {}))
    _add_to_examples(contract["components"].get("responses", {}))
    return contract


def main():
    path = ROOT / "docs/openapi.json"
    contract = build(json.loads(path.read_text(encoding="utf-8")))
    output = (json.dumps(contract, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(output)
    (ROOT / "backend/app/resources/openapi.json").write_bytes(output)
    digest = hashlib.sha256(output).hexdigest()
    test = ROOT / "backend/tests/test_http_contracts.py"
    text_value = test.read_text(encoding="utf-8")
    text_value = re.sub(r'CONTRACT_SHA256 = "[a-f0-9]+"', f'CONTRACT_SHA256 = "{digest}"', text_value)
    version = contract["info"]["version"]
    text_value = re.sub(
        r'CONTRACT\["info"\]\["version"\] == "[0-9.]+"', f'CONTRACT["info"]["version"] == "{version}"', text_value
    )
    test.write_text(text_value, encoding="utf-8", newline="\n")
    print(f"Contract v{version} SHA256:", digest)


if __name__ == "__main__":
    main()
