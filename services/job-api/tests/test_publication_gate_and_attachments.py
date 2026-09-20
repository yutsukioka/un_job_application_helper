import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fastapi import HTTPException
from job_api.publication_gate import PublicationGateMiddleware, publication_token
from job_api.attachments import list_job_attachments, download_job_attachment

class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ".jobagg-publication-state.json"

    def state(self, state="complete", generation="one"):
        self.path.write_text(json.dumps({"state": state, "generation_id": generation}))

    def request(self, app, path="/api/jobs/example"):
        async def run():
            output = []
            async def receive(): return {"type": "http.request", "body": b""}
            async def send(message): output.append(dict(message))
            await PublicationGateMiddleware(app, state_path=self.path)(
                {"type": "http", "path": path, "method": "GET"}, receive, send)
            return output
        return asyncio.run(run())

    @staticmethod
    async def response(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"old coherent data"})

    def test_legacy_database_works_without_marker(self):
        result = self.request(self.response)
        self.assertEqual(result[0]["status"], 200)
        self.assertEqual(result[-1]["body"], b"old coherent data")

    def test_active_or_corrupt_publication_stops_before_query(self):
        async def must_not_run(*args): self.fail("Queried while publication unsafe")
        for state in ("publishing", "exporting", "stopped"):
            self.state(state)
            self.assertEqual(self.request(must_not_run)[0]["status"], 503)
        self.path.write_text("malformed")
        self.assertEqual(self.request(must_not_run)[0]["status"], 503)
        self.path.write_text("[]")
        self.assertFalse(publication_token(self.path)[0])

    def test_publication_completing_during_request_discards_entire_response(self):
        self.state(generation="one")
        async def cross_generation(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"mixed data", "more_body": True})
            self.state(generation="two")
            await send({"type": "http.response.body", "body": b"new data"})
        result = self.request(cross_generation)
        self.assertEqual(result[0]["status"], 503)
        self.assertNotIn(b"mixed data", b"".join(x.get("body", b"") for x in result))

    def test_first_publication_appearing_midrequest_is_detected(self):
        async def first_generation(scope, receive, send):
            self.state()
            await self.response(scope, receive, send)
        self.assertEqual(self.request(first_generation)[0]["status"], 503)

    def test_large_multichunk_document_response_remains_exact(self):
        self.state()
        chunk = b"0123456789" * 150000
        async def streaming(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"tail"})
        result = self.request(streaming, "/api/job-attachment")
        self.assertEqual(b"".join(x.get("body", b"") for x in result), chunk + b"tail")

    def test_tracker_writes_are_not_retried_due_to_job_publication(self):
        self.state("publishing")
        self.assertEqual(self.request(self.response, "/api/tracker")[0]["status"], 200)

class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "jobs.sqlite3"
        self.content = b"%PDF-test-original-bytes"
        self.digest = hashlib.sha256(self.content).hexdigest()
        with sqlite3.connect(self.path) as c:
            c.executescript("""
            CREATE TABLE jobs(job_key TEXT PRIMARY KEY);
            CREATE TABLE attachment_blobs(content_sha256 TEXT PRIMARY KEY,content BLOB,media_type TEXT,size_bytes INTEGER);
            CREATE TABLE job_attachments(attachment_id TEXT PRIMARY KEY,job_key TEXT,url TEXT,final_url TEXT,label TEXT,category TEXT,
                required_for_complete_text INTEGER,status TEXT,content_sha256 TEXT,extracted_text TEXT);
            INSERT INTO jobs VALUES('org:one%2D'),('org:two');
            """)
            c.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)",
                      (self.digest, self.content, "application/pdf", len(self.content)))
            c.execute("INSERT INTO job_attachments VALUES(?,?,?,?,?,?,?,?,?,?)",
                      ("attachment-one", "org:one%2D", "https://example.org/jd.pdf", None,
                       "Terms of reference", "job_description", 1, "extracted_requires_review",
                       self.digest, "Original extracted public text"))

    def test_metadata_exposes_full_text_and_exact_job_bound_download(self):
        rows = list_job_attachments(self.path, "org:one%2D")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["extracted_text"], "Original extracted public text")
        self.assertIn("job_key=org%3Aone%252D", rows[0]["download_url"])
        self.assertTrue(rows[0]["required_for_complete_text"])
        self.assertFalse(rows[0]["completeness_certified"])

    def test_download_has_original_bytes_and_safe_headers(self):
        response = download_job_attachment(self.path, "org:one%2D", "attachment-one")
        self.assertEqual(response.body, self.content)
        self.assertEqual(response.headers["etag"], '"' + self.digest + '"')
        self.assertTrue(response.headers["content-disposition"].startswith("attachment;"))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_other_job_cannot_download_unassociated_blob(self):
        with self.assertRaises(HTTPException) as error:
            download_job_attachment(self.path, "org:two", "attachment-one")
        self.assertEqual(error.exception.status_code, 404)

    def test_corrupt_bytes_or_size_are_rejected(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE attachment_blobs SET content=?", (b"corrupted",))
        with self.assertRaises(HTTPException) as error:
            download_job_attachment(self.path, "org:one%2D", "attachment-one")
        self.assertEqual(error.exception.status_code, 503)

if __name__ == "__main__":
    unittest.main()
