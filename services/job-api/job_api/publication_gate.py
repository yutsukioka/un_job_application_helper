"""Suppress database-backed responses that cross a publication generation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from starlette.responses import JSONResponse

_READ_PATHS = {"/api/health", "/api/search", "/api/facets", "/api/taxonomies",
               "/api/updates", "/api/sources", "/api/sync/runs",
               "/api/job-detail", "/api/job-attachment", "/api/listing-inventory"}

def database_read(scope):
    path = scope.get("path", "")
    return (path in _READ_PATHS or path.startswith("/api/jobs/")
            or (path.startswith("/api/saved-searches/") and path.endswith("/run")))

def publication_token(path: Path):
    """Absent means legacy generation; malformed or incomplete means unavailable."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
    except FileNotFoundError:
        return True, None
    except OSError:
        return False, None
    if len(raw) > 65536:
        return False, None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return False, None
    if not isinstance(value, dict):
        return False, None
    valid = (value.get("state") == "complete"
             and isinstance(value.get("generation_id"), str)
             and bool(value["generation_id"]))
    return valid, hashlib.sha256(raw).hexdigest()

class PublicationGateMiddleware:
    """Buffer finite JSON/file responses before checking the generation again.

    The spool bounds RAM for large documents. A request started before publication
    must not release mixed results even when the publisher finishes during it.
    This gate does not govern external programs reading CSV/SQLite directly.
    """
    def __init__(self, app, *, state_path):
        self.app = app
        self.state_path = Path(state_path)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not database_read(scope):
            return await self.app(scope, receive, send)
        ready, token = publication_token(self.state_path)
        if not ready:
            return await self.unavailable(scope, receive, send)
        import tempfile
        messages = []
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            async def collect(message):
                message = dict(message)
                if message["type"] == "http.response.body":
                    chunk = message.pop("body", b"")
                    message["_offset"] = body.tell()
                    message["_length"] = len(chunk)
                    body.write(chunk)
                messages.append(message)
            await self.app(scope, receive, collect)
            ready, after = publication_token(self.state_path)
            if not ready or after != token:
                return await self.unavailable(scope, receive, send)
            for message in messages:
                if "_offset" in message:
                    body.seek(message.pop("_offset"))
                    message["body"] = body.read(message.pop("_length"))
                await send(message)

    @staticmethod
    async def unavailable(scope, receive, send):
        response = JSONResponse(
            {"detail": "Job updates are being published. Please retry shortly.",
             "publication_status": "updating"},
            status_code=503,
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
