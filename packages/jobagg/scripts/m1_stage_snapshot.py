#!/usr/bin/env python3
"""Copy a fenced legacy boundary for rehearsal; never open production SQLite.

The only production descriptor opened for writing is the existing owner lock.
Historical evidence locators remain unchanged and read-only; no proof is rebased.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import ctypes
import sys
import time


def sha(path: Path, check=lambda: None) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            check()
            h.update(block)
    return h.hexdigest()


def stamp(path: Path) -> tuple[int, ...]:
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError(f'nonregular source: {path}')
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def disjoint(destination: Path, sources: list[Path]) -> None:
    if destination != destination.resolve():
        raise ValueError('snapshot destination is aliased')
    for source in sources:
        source = source.resolve()
        if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
            raise ValueError('snapshot destination overlaps a production root')


@contextmanager
def fenced_owner(config: dict, expected_fence: str):
    marker, owner = Path(config['maintenance_file']), Path(config['shared_lock_path'])
    if not marker.is_absolute() or not owner.is_absolute() or owner.resolve() != owner:
        raise ValueError('exact absolute owner and maintenance paths required')
    if marker.is_symlink() or sha(marker) != expected_fence:
        raise ValueError('maintenance fence differs')
    before = stamp(owner)
    marker_before = stamp(marker)
    fd = os.open(owner, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if stamp(owner) != before or (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != before[:2]:
            raise ValueError('owner pathname identity changed')
        if marker.is_symlink() or stamp(marker) != marker_before or sha(marker) != expected_fence:
            raise ValueError('maintenance fence changed before ownership')
        yield fd
        if marker.is_symlink() or stamp(marker) != marker_before or sha(marker) != expected_fence or stamp(owner) != before:
            raise ValueError('production fence/owner changed during rehearsal')
    finally:
        os.close(fd)


def files_in(root: Path):
    if not root.is_dir() or root.is_symlink():
        raise ValueError('source directory missing or aliased')
    for directory, directories, names in os.walk(root, followlinks=False):
        directories.sort()
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError('source directory alias')
        for name in sorted(names):
            path = Path(directory) / name
            stamp(path)
            yield path


def snapshot(config_path: Path, destination: Path, expected_fence: str, *, max_bytes: int,
             max_files: int, seconds: float) -> dict:
    config_path = config_path.absolute()
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    worker = Path(config['publication_worker_database'])
    publication = config['publication_argv']
    output = Path(publication[publication.index('--output-dir') + 1])
    owner = Path(config['shared_lock_path'])
    production_roots = [worker.parent, output, owner.parent, Path(config['state_dir']),
                        Path(config['attempt_state_dir'])]
    disjoint(destination, production_roots)
    if max_bytes <= 0 or max_files <= 0 or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('finite positive snapshot bounds required')
    deadline = time.monotonic() + seconds
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('bounded snapshot deadline')
    rows = []
    total_bytes = 0
    def add(source: Path, relative: Path):
        nonlocal total_bytes
        check()
        signature = stamp(source)
        rows.append((source, relative, signature))
        total_bytes += signature[2]
        if len(rows) > max_files or total_bytes > max_bytes:
            raise ValueError('snapshot exceeds declared scratch budget')
    manifest = None
    try:
        with fenced_owner(config, expected_fence) as owner_fd:
            if config_path.read_bytes() != config_bytes:
                raise ValueError('production configuration changed')
            for source in [worker, *sorted(output.glob('*_jobs.sqlite3'))]:
                for suffix in ('', '-wal', '-shm', '-journal'):
                    member = Path(str(source) + suffix)
                    if member.exists():
                        add(member, Path('worker' if source == worker else 'output') / member.name)
            policy_root = Path(str(owner) + '.worker-policy')
            for source in files_in(policy_root):
                add(source, Path('policy') / source.relative_to(policy_root))
            add(Path(str(owner) + '.worker-policy.json'), Path('policy-marker.json'))
            for name in ('state.json', 'concurrency.json', 'health.json'):
                source = Path(config['state_dir']) / name
                if source.is_file():
                    add(source, Path('dispatcher') / name)
            for name in ('health.json',):
                source = Path(config['attempt_state_dir']) / name
                add(source, Path('status') / name)
            add(output / '.jobagg-publication-state.json', Path('output/.jobagg-publication-state.json'))
            destination.mkdir(mode=0o700, parents=True, exist_ok=False)
            manifest = {'schema': 'jobagg-fenced-rehearsal-snapshot-1', 'complete': False,
                        'created_at_utc': datetime.now(timezone.utc).isoformat(),
                        'production_config': str(config_path), 'production_config_sha256': hashlib.sha256(config_bytes).hexdigest(),
                        'fence_sha256': expected_fence, 'owner': str(owner), 'owner_identity': stamp(owner)[:2],
                        'snapshot_root': str(destination), 'max_bytes': max_bytes, 'max_files': max_files,
                        'historical_artifacts': 'original absolute locators, read-only under production fence/owner; not copied or rewritten',
                        'files': []}
            def journal():
                temporary = destination / 'snapshot.pending.json'
                temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
                temporary.replace(destination / 'snapshot.json')
            journal()
            (destination / 'production-config.original.json').write_bytes(config_bytes)
            for name in ('', 'attempts', 'hosts'):
                if (policy_root / name).is_dir():
                    (destination / 'policy' / name).mkdir(parents=True, exist_ok=True, mode=0o700)
            for source, relative, before in rows:
                check()
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # APFS clone if supported, ordinary independent copy otherwise.
                cloned = False
                if sys.platform == 'darwin':
                    libc = ctypes.CDLL(None, use_errno=True)
                    cloned = libc.clonefile(os.fsencode(source), os.fsencode(target), 0) == 0
                if not cloned:
                    if target.exists():
                        target.unlink()
                    with source.open('rb') as incoming, target.open('xb') as outgoing:
                        while block := incoming.read(1024 * 1024):
                            check()
                            outgoing.write(block)
                    shutil.copystat(source, target)
                if stamp(source) != before or os.path.samefile(source, target):
                    raise ValueError('source changed or snapshot shares its inode')
                digest = sha(source, check)
                if sha(target, check) != digest or stamp(source) != before:
                    raise ValueError('snapshot content differs')
                manifest['files'].append({'source': str(source), 'copy': str(relative),
                                          'source_identity': before, 'sha256': digest, 'cloned': cloned})
                if len(manifest['files']) % 1000 == 0:
                    journal()
            for row in manifest['files']:
                if stamp(Path(row['source'])) != tuple(row['source_identity']):
                    raise ValueError('source boundary changed before snapshot completion')
            manifest.update(complete=True, file_count=len(rows), logical_bytes=sum(row[2][2] for row in rows),
                            elapsed_seconds=seconds-(deadline-time.monotonic()), owner_fd_verified=os.fstat(owner_fd).st_ino)
            journal()
            return manifest
    except BaseException as error:
        if manifest is not None:
            manifest.update(complete=False, failure=type(error).__name__ + ': ' + str(error))
            journal()
        raise



def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--fence-sha256', required=True)
    p.add_argument('--max-bytes', type=int, required=True)
    p.add_argument('--max-files', type=int, required=True)
    p.add_argument('--seconds', type=float, required=True)
    a = p.parse_args()
    result = snapshot(a.config, a.destination, a.fence_sha256, max_bytes=a.max_bytes,
                      max_files=a.max_files, seconds=a.seconds)
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
