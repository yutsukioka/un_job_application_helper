from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from jobagg.models import JobRecord, OrganizationSource
from jobagg.pipelines.inventory_checks import verify_listing


def census(tmp_path, pages, *, offsets=None, total_paths=None):
    source = OrganizationSource(
        "example",
        "Example",
        "workday",
        "https://x.example/External",
        extra={"cxs_base_url": "https://x.example/cxs", "page_size": 2},
    )
    paths = []
    observed = []
    for n, (total, rows) in enumerate(pages):
        request = {
            "appliedFacets": {},
            "limit": 2,
            "offset": offsets[n] if offsets else n * 2,
            "searchText": "",
        }
        body = json.dumps(
            {"total": total, "jobPostings": [{"externalPath": row} for row in rows]}
        ).encode()
        artifact = tmp_path / f"{n}.body.gz"
        artifact.write_bytes(gzip.compress(body))
        meta = {
            "phase": {"kind": "listing"},
            "url": "https://x.example/cxs/jobs",
            "response_url": "https://x.example/cxs/jobs",
            "status_code": 200,
            "method": "POST",
            "started_at": f"2026-09-30T00:{n:02d}:00+00:00",
            "finished_at": f"2026-09-30T00:{n:02d}:01+00:00",
            "artifact": str(artifact),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "public_pagination_request": request,
            "request_body_sha256": hashlib.sha256(
                json.dumps(request, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        path = tmp_path / f"{n}.json"
        path.write_text(json.dumps(meta))
        paths.append(path)
        observed.extend(rows)
    jobs = [
        JobRecord(
            source_id="example",
            org_id="example",
            ats_family="workday",
            external_id=str(n),
            title="Officer",
            apply_url="https://x.example" + value,
            raw={"externalPath": value},
        )
        for n, value in enumerate(total_paths if total_paths is not None else observed)
    ]
    return source, jobs, paths


def test_later_cxs_total_zero_is_omission_not_zero_jobs(tmp_path):
    args = census(tmp_path, [(3, ["/a", "/b"]), (0, ["/c"])])
    result = verify_listing(*args)
    assert (
        result["complete"] is True and result["reported_total"] == 3 and result["page_count"] == 2
    )


@pytest.mark.parametrize(
    "pages,offsets,observed",
    [
        ([(5, ["/a", "/b"])], None, None),
        ([(3, ["/a", "/b"]), (0, ["/c"])], [0, 4], None),
        ([(3, ["/a", "/b"]), (0, ["/b"])], None, None),
        ([(3, ["/a", "/b"]), (4, ["/c"])], None, None),
        ([(2, ["/a", "/b"])], None, ["/a"]),
    ],
)
def test_max_pages_skips_duplicates_count_drift_and_missing_database_ids_are_unknown(
    tmp_path, pages, offsets, observed
):
    assert (
        verify_listing(*census(tmp_path, pages, offsets=offsets, total_paths=observed))["complete"]
        is False
    )


def test_scope_filter_and_capture_hash_mutations_fail(tmp_path):
    args = census(tmp_path, [(1, ["/a"])])
    source, jobs, paths = args
    meta = json.loads(paths[0].read_text())
    original = deepcopy(meta)
    meta["public_pagination_request"]["searchText"] = "restricted"
    paths[0].write_text(json.dumps(meta))
    assert not verify_listing(*args)["complete"]
    original["body_sha256"] = "0" * 64
    paths[0].write_text(json.dumps(original))
    assert not verify_listing(source, jobs, paths)["complete"]


def test_verified_zero_needs_actual_empty_response(tmp_path):
    source, jobs, paths = census(tmp_path, [(0, [])])
    assert verify_listing(source, jobs, paths)["verified_zero"] is True
    assert verify_listing(source, jobs, [])["complete"] is False


@pytest.mark.parametrize("damage", ["missing", "naive", "reversed"])
def test_invalid_capture_interval_cannot_reconcile_vacancy_denial(tmp_path, damage):
    source, jobs, paths = census(tmp_path, [(1, ["/a"])])
    meta = json.loads(paths[0].read_text())
    if damage == "missing":
        del meta["started_at"]
    elif damage == "naive":
        meta["started_at"] = "2026-09-30T00:00:00"
    else:
        meta["started_at"] = "2026-09-30T00:00:02+00:00"
    paths[0].write_text(json.dumps(meta))
    assert not verify_listing(source, jobs, paths)["complete"]


def test_complete_workday_census_carries_entire_capture_interval(tmp_path):
    result = verify_listing(*census(tmp_path, [(3, ["/a", "/b"]), (0, ["/c"])]))
    assert result["started_at"] == "2026-09-30T00:00:00+00:00"
    assert result["finished_at"] == "2026-09-30T00:01:01+00:00"
