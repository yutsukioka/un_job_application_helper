"""Independent proof for OSCE's public same-session server document census."""

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

from jobagg.adapters.osce_inventory import SEARCH_URL, pagination_state, reconcile
from jobagg.osce_fragments import SERVER_NAVIGATION, page_url, session_parts
from jobagg.pipelines.inventory_api_contracts import require

TRANSPORT = "chromium_cdp_native_v1"
SESSION_SCOPE = "one task; fresh anonymous browser context; never persisted"


def _interval(meta):
    stamps = [datetime.fromisoformat(str(meta.get(key, "")).replace("Z", "+00:00"))
              for key in ("started_at", "finished_at")]
    require(all(stamp.tzinfo is not None for stamp in stamps) and stamps[0] <= stamps[1],
            "OSCE document capture interval invalid")
    return tuple(stamp.astimezone(timezone.utc) for stamp in stamps)


def _capture(source, path):
    encoded = path.read_bytes()
    meta = json.loads(encoded)
    require(isinstance(meta, dict), "OSCE document metadata invalid")
    binding = meta.get("source_binding") or {}
    diagnostics = meta.get("transport_diagnostics") or {}
    require(isinstance(binding, dict) and isinstance(diagnostics, dict)
            and isinstance(meta.get("phase"), dict), "OSCE document metadata context invalid")
    require(binding.get("source_id") == source.id and binding.get("ats_family") == source.ats_family,
            "OSCE document capture source differs")
    require(meta.get("phase", {}).get("kind") == "listing"
            and meta.get("phase", {}).get("job_id") is None and meta.get("external_id") is None,
            "OSCE document capture phase differs")
    require(meta.get("method") == "GET" and meta.get("transport") == TRANSPORT
            and diagnostics.get("resource_type") == "Document"
            and diagnostics.get("request_body_bytes") == 0
            and not meta.get("request_body_sha256") and not meta.get("error_type")
            and not meta.get("failure_category") and meta.get("state") == "response_captured"
            and meta.get("body_captured") is True,
            "OSCE requires successful native document GET captures")
    require(meta.get("response_url") == meta.get("url")
            and hashlib.sha256(str(meta.get("url", "")).encode()).hexdigest()
            == meta.get("request_url_sha256"), "OSCE document request/response URL differs")
    artifact = Path(meta["artifact"]).resolve()
    require(artifact == path.with_suffix(".body.gz"), "OSCE document artifact outside capture")
    raw = gzip.decompress(artifact.read_bytes())
    require(hashlib.sha256(raw).hexdigest() == meta.get("body_sha256")
            and len(raw) == meta.get("body_bytes"), "OSCE document body hash/size differs")
    return meta, raw, hashlib.sha256(encoded).hexdigest(), _interval(meta)


def verify_server_captures(source, bundle, capture_paths, receipt, receipt_path):
    """Bind every enumerated page and initial redirect to raw native captures.

    Only robots checks may accompany the document walk. Replaying a subset,
    another source/session, or a synthetic bundle cannot establish completeness.
    Historical UI/POST proofs use their existing verifier separately.
    """
    require(receipt.get("transport") == TRANSPORT and receipt.get("session_scope") == SESSION_SCOPE
            and receipt.get("contract", {}).get("navigation") == SERVER_NAVIGATION,
            "OSCE server-document receipt differs")
    target = Path(receipt_path).resolve().parent.parent
    require(Path(receipt["html_path"]).resolve() == Path(receipt_path).resolve().parent / "rendered.html",
            "OSCE rendered bundle outside browser receipt")
    match = re.fullmatch(r'<script type="application/json" id="jobagg-osce-inventory">(.*)</script>',
                         bundle, re.S)
    require(match is not None, "OSCE server inventory bundle missing")
    pages = json.loads(match[1])
    require(isinstance(pages, list) and 1 <= len(pages) <= 50
            and all(isinstance(page, dict) for page in pages), "OSCE server page bound differs")
    _, total = reconcile(pages)
    session, _ = session_parts(pages[0]["url"])
    allowed = [Path(path).resolve() for path in capture_paths]
    require(len(set(allowed)) == len(allowed), "OSCE repeated supplied capture")
    documents = {}
    for path in allowed:
        require(path.parent == target / "http", "OSCE capture outside browser task")
        meta = json.loads(path.read_bytes())
        require(isinstance(meta, dict) and isinstance(meta.get("phase"), dict),
                "OSCE document metadata context invalid")
        if meta["phase"].get("kind") == "robots":
            continue
        documents[path] = _capture(source, path)
    redirects = [(path, value) for path, value in documents.items() if value[0].get("url") == SEARCH_URL]
    require(len(redirects) == 1, "OSCE initial public search redirect missing/ambiguous")
    redirect_path, (redirect, _, redirect_hash, interval) = redirects[0]
    first_url = pages[0]["request_url"]
    require(redirect.get("status_code") in {301, 302, 303, 307, 308}
            and redirect.get("transport_diagnostics", {}).get("redirect_url") == first_url,
            "OSCE initial redirect does not bind discovered session")
    verified = [{"path": str(redirect_path), "sha256": redirect_hash}]
    used, intervals = {redirect_path}, [interval]
    for number, page in enumerate(pages, 1):
        expected = page_url(session, number)
        require(type(page.get("number")) is int and page["number"] == number
                and page.get("navigation") == SERVER_NAVIGATION and page.get("url") == expected
                and page.get("request_url") in {expected, expected + "/"},
                "OSCE server document route/page differs")
        path = Path(page["capture_path"]).resolve()
        require(path in documents and path not in used, "OSCE server document capture missing/reused")
        meta, raw, metadata_hash, interval = documents[path]
        require(meta.get("status_code") == 200 and meta.get("url") == page["request_url"],
                "OSCE server page was not its exact successful response")
        require(metadata_hash == page.get("capture_sha256")
                and hashlib.sha256(raw).hexdigest() == page.get("response_sha256")
                and raw.decode("utf-8", errors="replace") == page.get("html"),
                "OSCE server page differs from raw capture")
        current, count = pagination_state(page["html"])
        require(current == number and type(page.get("advertised_pages")) is int
                and page["advertised_pages"] == count and type(page.get("reported_total")) is int
                and page["reported_total"] == total,
                "OSCE server saved pagination/total differs from raw document")
        require(interval[0] >= intervals[-1][1], "OSCE document captures overlap or precede session")
        used.add(path)
        intervals.append(interval)
        verified.append({"path": str(path), "sha256": metadata_hash})
    require(used == set(documents), "OSCE server walk contains extra or repeated document captures")
    require(receipt.get("final_url") == pages[-1]["request_url"], "OSCE final session page differs")
    return verified, {"started_at": intervals[0][0].isoformat(),
                      "finished_at": intervals[-1][1].isoformat(), "page_count": len(pages)}
