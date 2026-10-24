#!/usr/bin/env python3
"""Propose exact backup evidence locators using metadata only; never an integrity seal.

No evidence payload is opened, hashed or copied. This proposal does not modify
bootstrap proofs, authorize runtime admission or turn deployment readiness on.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import stat
import time


def locate(references, *, original_root, backup_root, max_files, seconds):
    original_root, backup_root = Path(original_root), Path(backup_root)
    if (
        not original_root.is_absolute()
        or ".." in original_root.parts
        or not backup_root.is_absolute()
        or backup_root.resolve(strict=True) != backup_root
    ):
        raise ValueError("exact absolute roots required")
    if not math.isfinite(seconds) or seconds <= 0 or type(max_files) is not int or max_files <= 0:
        raise ValueError("finite positive search bounds required")
    if not references or len(references) > max_files:
        raise ValueError("reference count exceeds reviewed metadata-search bound")
    deadline = time.monotonic() + seconds
    root_identity = backup_root.stat()
    rows, seen = [], set()
    for item in references:
        if time.monotonic() >= deadline:
            raise TimeoutError("backup metadata search deadline")
        original = Path(item["path"])
        expected = item.get("expected_sha256", item.get("sha256"))
        if (
            not original.is_absolute()
            or ".." in original.parts
            or original in seen
            or not re.fullmatch(r"[a-f0-9]{64}", str(expected))
        ):
            raise ValueError("duplicate or malformed original evidence reference")
        seen.add(original)
        relative = original.relative_to(original_root)
        candidate = backup_root / relative
        if candidate.resolve(strict=True) != candidate:
            raise ValueError("backup evidence locator is aliased")
        info = candidate.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("backup evidence is not a regular file")
        rows.append(
            dict(
                original_path=str(original),
                backup_path=str(candidate),
                original_expected_sha256=expected,
                integrity_verified=False,
                identity=dict(
                    device=info.st_dev,
                    inode=info.st_ino,
                    bytes=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                    ctime_ns=info.st_ctime_ns,
                ),
            )
        )
    after = backup_root.stat()
    if (root_identity.st_dev, root_identity.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError("backup root identity changed")
    return dict(
        schema="jobagg-backup-locations-proposal-1",
        observed_at_utc=datetime.now(timezone.utc).isoformat(),
        original_root=str(original_root),
        backup_root=str(backup_root),
        backup_root_identity=dict(device=after.st_dev, inode=after.st_ino),
        references=len(rows),
        logical_bytes=sum(row["identity"]["bytes"] for row in rows),
        files_over_runtime_16MiB_audit_limit=sum(
            row["identity"]["bytes"] > 16 * 1024**2 for row in rows
        ),
        integrity_verified=False,
        runtime_supported=False,
        ready=False,
        evidence_payloads_read=0,
        evidence_payloads_copied=0,
        evidence_payloads_hashed=0,
        files=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--max-files", type=int, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    args = parser.parse_args()
    value = locate(
        json.loads(args.audit.read_text())["files"],
        original_root=args.original_root,
        backup_root=args.backup_root,
        max_files=args.max_files,
        seconds=args.seconds,
    )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
