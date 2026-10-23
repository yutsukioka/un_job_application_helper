"""Explicit offline streaming plan projection; never used by ordinary ticks.

Hash and validate every input byte while retaining only reconciliation fields.
Large beforeimages/rejected rows remain in the original file, not in memory.
"""

import codecs
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from jobagg.publication_snapshot import _stamp

_DESTINATION = {"path": True, "beforeimage_path": True, "beforeimage_sha256": True}
PLAN_FIELDS = {
    key: True
    for key in (
        "version",
        "generation_id",
        "generation_root",
        "created_at",
        "registry_sha256",
        "output_dir",
        "request_identity_sha256",
        "observation_set_sha256",
        "worker_database",
    )
}
PLAN_FIELDS.update(
    changes=[
        {
            "proof": True,
            "publication_key": True,
            "documents": True,
            "worker_row": {
                key: True for key in ("job_key", "source_id", "external_id", "description")
            },
            "destinations": [_DESTINATION],
        }
    ],
    listing_frames=[
        {
            "publication_key": True,
            "source_id": True,
            "frame_sha256": True,
            "frame_path": True,
            "proof": True,
            "destinations": [_DESTINATION],
        }
    ],
)
_SPECIAL = re.compile(r'["\\\x00-\x1f]')
_SPACE = re.compile(r"[ \r\n\t]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class _Reader:
    def __init__(self, fd, maximum, deadline, retained_limit):
        self.fd, self.maximum, self.deadline = fd, maximum, deadline
        self.retained_limit, self.retained = retained_limit, 0
        self.retained_nodes = 0
        self.buffer, self.at, self.eof, self.bytes = "", 0, False, 0
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.digest = hashlib.sha256()

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("historical_plan_deadline")

    def fill(self):
        self.check()
        if self.at < len(self.buffer):
            return True
        if self.eof:
            return False
        block = os.read(self.fd, min(65536, self.maximum + 1 - self.bytes))
        self.bytes += len(block)
        if self.bytes > self.maximum:
            raise ValueError("historical_plan_byte_bound")
        self.digest.update(block)
        self.eof = not block
        self.buffer = self.decoder.decode(block, final=self.eof)
        self.at = 0
        return bool(self.buffer) or (not self.eof and self.fill())

    def peek(self):
        return self.buffer[self.at] if self.fill() else ""

    def take(self, expected):
        if self.peek() != expected:
            raise ValueError("historical_plan_invalid_json")
        self.at += 1

    def whitespace(self):
        while self.fill():
            self.at = _SPACE.match(self.buffer, self.at).end()
            if self.at < len(self.buffer):
                break

    def string(self, keep):
        self.take('"')
        chunks, length = [], 0
        while self.fill():
            match = _SPECIAL.search(self.buffer, self.at)
            end = match.start() if match else len(self.buffer)
            length += end - self.at
            if keep and length > 16 * 1024**2:
                raise ValueError("historical_plan_string_bound")
            if keep:
                chunks.append(self.buffer[self.at : end])
                self.retained += end - self.at
                if self.retained > self.retained_limit:
                    raise ValueError("historical_plan_retained_bound")
            self.at = end
            if not match:
                continue
            char = self.buffer[self.at]
            self.at += 1
            if char == '"':
                return json.loads('"' + "".join(chunks) + '"') if keep else None
            if char != "\\":
                raise ValueError("historical_plan_unescaped_control")
            escape = self.peek()
            if not escape or escape not in '"\\/bfnrtu':
                raise ValueError("historical_plan_invalid_escape")
            self.at += 1
            sequence = "\\" + escape
            if escape == "u":
                for _ in range(4):
                    char = self.peek()
                    if not char or char not in "0123456789abcdefABCDEF":
                        raise ValueError("historical_plan_invalid_escape")
                    self.at += 1
                    sequence += char
            if keep:
                chunks.append(sequence)
                self.retained += len(sequence)
                if self.retained > self.retained_limit:
                    raise ValueError("historical_plan_retained_bound")
        raise ValueError("historical_plan_unterminated_string")

    def value(self, selector, depth=0):
        self.check()
        if selector is not None:
            self.retained_nodes += 1
            if self.retained_nodes > 200000:
                raise ValueError("historical_plan_retained_node_bound")
        if depth > 64:
            raise ValueError("historical_plan_depth_bound")
        self.whitespace()
        char = self.peek()
        if char == "{":
            self.at += 1
            result, keys = {}, set()
            self.whitespace()
            if self.peek() == "}":
                self.at += 1
                return result if selector is not None else None
            while True:
                self.whitespace()
                key = self.string(True)
                if key in keys or len(keys) >= 200000:
                    raise ValueError("historical_plan_duplicate_or_excess_keys")
                keys.add(key)
                self.whitespace()
                self.take(":")
                selected = (
                    True
                    if selector is True
                    else selector.get(key)
                    if isinstance(selector, dict)
                    else None
                )
                value = self.value(selected, depth + 1)
                if selected is not None:
                    result[key] = value
                self.whitespace()
                if self.peek() == "}":
                    self.at += 1
                    return result if selector is not None else None
                self.take(",")
        if char == "[":
            self.at += 1
            result, count = [], 0
            self.whitespace()
            if self.peek() == "]":
                self.at += 1
                return result if selector is not None else None
            while True:
                count += 1
                if count > 200000:
                    raise ValueError("historical_plan_array_bound")
                selected = (
                    True
                    if selector is True
                    else selector[0]
                    if isinstance(selector, list)
                    else None
                )
                value = self.value(selected, depth + 1)
                if selected is not None:
                    result.append(value)
                self.whitespace()
                if self.peek() == "]":
                    self.at += 1
                    return result if selector is not None else None
                self.take(",")
        if char == '"':
            return self.string(selector is not None)
        token = ""
        while self.peek() and self.peek() not in " \r\n\t,]}":
            token += self.peek()
            self.at += 1
            if len(token) > 128:
                raise ValueError("historical_plan_scalar_bound")
        if token not in ("true", "false", "null") and not _NUMBER.fullmatch(token):
            raise ValueError("historical_plan_invalid_scalar")
        value = json.loads(token)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("historical_plan_nonfinite")
        return value if selector is not None else None


def read_plan(path, *, expected_sha256, deadline_at, max_bytes, retained_bytes=64 * 1024**2):
    if (
        type(max_bytes) is not int
        or not 0 < max_bytes <= 1024**3
        or type(retained_bytes) is not int
        or not 0 < retained_bytes <= 128 * 1024**2
        or type(deadline_at) not in (int, float)
        or not math.isfinite(deadline_at)
        or not re.fullmatch("[a-f0-9]{64}", str(expected_sha256))
    ):
        raise ValueError("finite_historical_plan_contract_required")
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError("historical_plan_aliased")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise ValueError("historical_plan_not_regular_or_oversized")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if _stamp(os.fstat(fd)) != _stamp(before):
            raise ValueError("historical_plan_changed_during_open")
        reader = _Reader(fd, max_bytes, deadline_at, retained_bytes)
        value = reader.value(PLAN_FIELDS)
        reader.whitespace()
        if reader.peek():
            raise ValueError("historical_plan_trailing_content")
        reader.check()
        if (
            _stamp(os.fstat(fd)) != _stamp(before)
            or _stamp(path.lstat()) != _stamp(before)
            or path.resolve() != path
            or reader.digest.hexdigest() != expected_sha256
        ):
            raise ValueError("historical_plan_hash_or_identity_changed")
    finally:
        os.close(fd)
    if not isinstance(value, dict):
        raise ValueError("historical_plan_object_required")
    return value, {
        "path": str(path),
        "sha256": reader.digest.hexdigest(),
        "bytes": reader.bytes,
        "retained_characters": reader.retained,
        "identity": list(_stamp(before)),
    }
