import hashlib
import json
import time

import pytest

from jobagg.historical_plans import read_plan


def read(path, **kw):
    return read_plan(
        path,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        deadline_at=time.monotonic() + 10,
        max_bytes=32 * 1024**2,
        **kw,
    )


def test_large_beforeimage_is_validated_and_hashed_without_retaining_it(tmp_path):
    path = tmp_path / "plan.json"
    value = {
        "version": "incremental-live-publication-1",
        "changes": [
            {
                "worker_row": {"source_id": "s", "external_id": "1", "description": '雪 " \\ \n'},
                "proof": {"captures": [{"sha256": "a" * 64}]},
                "publication_key": "key",
                "destinations": [
                    {"path": "/exact/store", "before": {"description": "x" * 17 * 1024**2}}
                ],
            }
        ],
    }
    path.write_text(json.dumps(value))
    projection, record = read(path)
    assert path.stat().st_size > 16 * 1024**2
    assert projection["changes"][0]["worker_row"] == value["changes"][0]["worker_row"]
    assert projection["changes"][0]["destinations"] == [{"path": "/exact/store"}]
    assert record["bytes"] == path.stat().st_size and record["retained_characters"] < 1000


@pytest.mark.parametrize(
    "payload",
    [
        '{"changes":[],"changes":[]}',
        '{"ignored":{"x":1,"x":2}}',
        '{"ignored":NaN}',
        '{"ignored":1e999}',
        '{"ignored":01}',
        '{"ignored":"bad\\q"}',
        '{"ignored":"\\u0gg0"}',
        '{"ignored":"bad\n"}',
        '{"changes":[],}',
        '{"ignored":[1,]}',
        "{}{}",
        '{"ignored":',
    ],
)
def test_invalid_even_skipped_json_cannot_become_a_plan(tmp_path, payload):
    path = tmp_path / "plan.json"
    path.write_text(payload)
    with pytest.raises(ValueError):
        read(path)


def test_deadline_hash_alias_and_retained_limits_are_enforced(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text('{"changes":[],"version":"long value"}')
    with pytest.raises(ValueError, match="retained"):
        read(path, retained_bytes=1)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(TimeoutError):
        read_plan(path, expected_sha256=sha, deadline_at=time.monotonic() - 1, max_bytes=1024)
    with pytest.raises(ValueError, match="hash_or_identity"):
        read_plan(path, expected_sha256="a" * 64, deadline_at=time.monotonic() + 1, max_bytes=1024)
    with pytest.raises(ValueError, match="oversized"):
        read_plan(path, expected_sha256=sha, deadline_at=time.monotonic() + 1, max_bytes=1)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="aliased"):
        read(alias)


def test_utf8_and_escape_sequences_cross_chunk_boundaries(tmp_path):
    path = tmp_path / "plan.json"
    for n in range(65515, 65540):
        value = {"version": "x" * n + '雪"\\\n', "changes": [], "listing_frames": []}
        path.write_text(json.dumps(value, ensure_ascii=False))
        projected, _ = read(path)
        assert projected == value


def test_retained_container_count_is_bounded_even_without_string_payload(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text('{"changes":[' + ",".join("{}" for _ in range(200000)) + "]}")
    with pytest.raises(ValueError, match="retained_node_bound"):
        read(path)
