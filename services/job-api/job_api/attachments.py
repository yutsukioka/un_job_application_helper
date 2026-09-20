"""Public attachment metadata and verified downloads from published SQLite."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.responses import Response

def _connection(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)

def list_job_attachments(path, job_key):
    with _connection(path) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_attachments'").fetchone():
            return []
        rows = conn.execute(
            """SELECT a.attachment_id,a.url,a.final_url,a.label,a.category,
                      a.required_for_complete_text,a.status,a.content_sha256,
                      a.extracted_text,b.media_type,b.size_bytes
               FROM job_attachments a LEFT JOIN attachment_blobs b
                 ON b.content_sha256=a.content_sha256
               WHERE a.job_key=? ORDER BY a.attachment_id""",
            (job_key,),
        ).fetchall()
    return [
        {**dict(row), "required_for_complete_text": bool(row["required_for_complete_text"]),
         "download_url": ("/api/job-attachment?" + urlencode(
             {"job_key": job_key, "attachment_id": row["attachment_id"]}))
         if row["content_sha256"] and row["size_bytes"] is not None else None,
         "completeness_certified": False}
        for row in rows
    ]

def download_job_attachment(path, job_key, attachment_id):
    with _connection(path) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_attachments'").fetchone():
            raise HTTPException(status_code=404, detail="Attachment not found")
        row = conn.execute(
            """SELECT b.content_sha256,b.content,b.media_type,b.size_bytes
               FROM job_attachments a JOIN attachment_blobs b
                 ON b.content_sha256=a.content_sha256
               JOIN jobs j ON j.job_key=a.job_key
               WHERE a.job_key=? AND a.attachment_id=?""",
            (job_key, attachment_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found for this job")
    content = bytes(row["content"])
    digest = hashlib.sha256(content).hexdigest()
    if digest != row["content_sha256"] or len(content) != row["size_bytes"]:
        raise HTTPException(status_code=503, detail="Stored attachment integrity check failed")
    media = row["media_type"] or ""
    if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media):
        media = "application/octet-stream"
    suffix = {"application/pdf": ".pdf", "text/plain": ".txt",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}.get(media, ".bin")
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="job-document-{digest[:16]}{suffix}"',
        "ETag": f'"{digest}"', "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600, immutable",
    })
