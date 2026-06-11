#!/usr/bin/env python3
"""Create and update a v2 ensemble run manifest."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SERVERS = (
    "P0a",
    "P0b",
    "S1",
    "S2",
    "S3",
    "C1",
    "D1",
    "D2",
    "D3",
    "C2",
    "E1",
    "E2",
    "R1",
    "R2",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def manifest_path(outdir: Path) -> Path:
    return outdir / "_discussion" / "run_manifest.json"


def init_manifest(args: argparse.Namespace) -> int:
    path = manifest_path(args.outdir)
    servers = [server.strip() for server in (args.servers or ",".join(DEFAULT_SERVERS)).split(",") if server.strip()]
    manifest = {
        "job_slug": args.job_slug,
        "target_system": args.target_system,
        "run_start_utc": now(),
        "run_end_utc": None,
        "servers_launched": servers,
        "skills_invoked": [],
        "skills_not_invoked": [],
        "unexpected_invocations": [],
        "quality_gates": {},
    }
    write_manifest(path, manifest)
    print(f"RUN_MANIFEST_INITIALIZED: {path}")
    return 0


def add_skill(args: argparse.Namespace) -> int:
    path = manifest_path(args.outdir)
    manifest = read_manifest(path)
    if not manifest:
        raise SystemExit(f"run manifest does not exist: {path}")
    manifest.setdefault("skills_invoked", []).append(
        {
            "skill": args.skill,
            "server": args.server,
            "artifact": args.artifact,
            "recorded_at_utc": now(),
        }
    )
    write_manifest(path, manifest)
    print(f"RUN_MANIFEST_SKILL_RECORDED: {args.skill}")
    return 0


def finalize(args: argparse.Namespace) -> int:
    path = manifest_path(args.outdir)
    manifest = read_manifest(path)
    if not manifest:
        raise SystemExit(f"run manifest does not exist: {path}")
    manifest["run_end_utc"] = now()
    if args.skills_not_invoked:
        manifest["skills_not_invoked"] = [s.strip() for s in args.skills_not_invoked.split(",") if s.strip()]
    if args.unexpected_invocations:
        manifest["unexpected_invocations"] = [
            s.strip() for s in args.unexpected_invocations.split(",") if s.strip()
        ]
    write_manifest(path, manifest)
    print(f"RUN_MANIFEST_FINALIZED: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--outdir", type=Path, required=True)
    init.add_argument("--job-slug", required=True)
    init.add_argument("--target-system", required=True)
    init.add_argument("--servers")
    init.set_defaults(func=init_manifest)

    add = sub.add_parser("add-skill")
    add.add_argument("--outdir", type=Path, required=True)
    add.add_argument("--skill", required=True)
    add.add_argument("--server", required=True)
    add.add_argument("--artifact", required=True)
    add.set_defaults(func=add_skill)

    done = sub.add_parser("finalize")
    done.add_argument("--outdir", type=Path, required=True)
    done.add_argument("--skills-not-invoked")
    done.add_argument("--unexpected-invocations")
    done.set_defaults(func=finalize)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
