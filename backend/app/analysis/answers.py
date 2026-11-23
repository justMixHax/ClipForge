"""Reading the JSON an AI writes into its answer.

An answer can hold text around its JSON, braces in that text, a draft followed by a correction ("Correction,
here is the real answer:"), or times written as a clock (28:35) where seconds were asked for. All of these were
seen for real, and each time the good picks in the answer were thrown away. Here every JSON block is found and
the last one written comes first (a correction comes after its draft). A block that does not read as written is
tried once more with its clock times quoted; a block that reads as written is never changed."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from typing import Any

_CLOCK_TEXT = r"\d{1,2}(?::\d{2}){1,2}(?:\.\d+)?"
# a clock time where a JSON value goes, unquoted: after a key's colon, or in a list
_BARE_CLOCK = re.compile(rf'("\s*:\s*|[\[,]\s*)({_CLOCK_TEXT})(?=\s*[,\]}}])')
_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def _block_end(text: str, start: int) -> int:
    """The index just after the brace that closes the block opened at start (quoted text respected), or -1."""
    depth, quoted, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _read(block: str) -> Any:
    try:
        return json.loads(block)
    except ValueError:
        pass
    try:
        return json.loads(_BARE_CLOCK.sub(r'\1"\2"', block))
    except ValueError:
        return None


def json_objects(text: str) -> list[dict[str, Any]]:
    """Every JSON object written in the text, in the order written."""
    found: list[dict[str, Any]] = []
    text = text or ""
    start = text.find("{")
    while start != -1:
        end = _block_end(text, start)
        value = _read(text[start:end]) if end != -1 else None
        if isinstance(value, dict):
            found.append(value)
            start = text.find("{", end)
        else:  # not JSON (a brace in a sentence): a block may still start inside it
            start = text.find("{", start + 1)
    return found


def json_answers(text: str) -> Iterator[dict[str, Any]]:
    """The JSON objects in an AI's answer, the last one written first."""
    yield from reversed(json_objects(text))


def seconds(value: Any) -> float | None:
    """A time in seconds from 12.5, "12.5", "28:35" or "1:02:15.5"; None when it is not one."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    text = value.strip().removesuffix("s").strip()
    if not text:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        pass
    match = _CLOCK.match(text)
    if not match:
        return None
    hours, minutes, secs = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + float(secs)
