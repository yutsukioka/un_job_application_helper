from __future__ import annotations

import gzip
import io
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))

from jobagg.http import JobAggHTTPClient, ResponseTooLargeError  # noqa: E402


class _FakeHeaders(dict):
    def get_content_charset(self) -> str:
        return "utf-8"


class _GzipBombResponse:
    status = 200

    def __init__(self, compressed_body: bytes) -> None:
        self.headers = _FakeHeaders({"Content-Encoding": "gzip"})
        self._body = io.BytesIO(compressed_body)

    def __enter__(self) -> _GzipBombResponse:
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def read(self, amt: int = -1) -> bytes:
        if amt < 0:
            raise AssertionError("HTTP responses must be read with a finite chunk size")
        return self._body.read(amt)

    def geturl(self) -> str:
        return "https://jobs.example.org/list"


class _GzipBombOpener:
    def __init__(self, compressed_body: bytes) -> None:
        self._compressed_body = compressed_body

    def open(self, request, timeout) -> _GzipBombResponse:
        return _GzipBombResponse(self._compressed_body)


def test_C1_fetch_text_streams_gzip_bomb_with_hard_decompressed_cap(monkeypatch) -> None:
    compressed = gzip.compress(b"x" * 4096)

    def forbidden_one_shot_decompress(data: bytes) -> bytes:
        raise AssertionError("gzip.decompress would inflate the whole response")

    monkeypatch.setattr("jobagg.http.gzip.decompress", forbidden_one_shot_decompress)
    client = JobAggHTTPClient(max_retries=0, max_response_bytes=1024)
    client._opener = _GzipBombOpener(compressed)

    with pytest.raises(ResponseTooLargeError):
        client.get("https://jobs.example.org/list")
