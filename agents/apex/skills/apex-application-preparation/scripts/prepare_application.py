#!/usr/bin/env python3
"""Read-only vacancy capture and narrowly scoped context/archive updates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import datetime, timezone

REPLACE = ('JOB_DESCRIPTION_TEXT', 'JOB_REQUIREMENT_TEXT',
           'JOB_QUALIFICATION_QUESTIONS', 'LIMITS')
CLEAR = ('CCOG_CLASSIFICATION', 'JD_KEYWORD_BANK', 'TERM_EXTRACTOR')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def require_distinct_paths(*paths):
    resolved = [Path(path).resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError('Input, output and archive paths must be distinct')


def require_new_file(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError('Capture or report must be a new file: ' + str(path))


def write_new_file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def archive_name(filename):
    if not isinstance(filename, str) or re.search(r'[/\\\x00-\x1f]', filename) or filename in ('.', '..') or not filename.endswith('.md'):
        raise ValueError('Archive name must be one safe .md filename')
    return filename


def sections(data):
    """Return exact level-two sections, ignoring headings inside fenced blocks."""
    result = {}; starts = []; offset = 0; fence = None
    for line in data.splitlines(keepends=True):
        text = line.decode('utf-8').rstrip('\r\n')
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', text)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        else:
            match = re.fullmatch(r'## ([A-Z][A-Z0-9_]*)[ \t]*', text)
            # Every h2 is a boundary, even if it is not a canonical uppercase key.
            if text.startswith('## '):
                starts.append((match[1] if match else text[3:].strip(), offset, offset + len(line)))
        offset += len(line)
    if fence:
        raise ValueError('Unclosed fenced block in context')
    for i, (name, start, body) in enumerate(starts):
        if name in result:
            raise ValueError('Duplicate section: ' + name)
        result[name] = (start, body, starts[i+1][1] if i+1 < len(starts) else len(data))
    return result


def replace_sections(before, patch):
    ranges = sections(before)
    if set(patch) != set(REPLACE):
        raise ValueError('Patch must contain exactly: ' + ', '.join(REPLACE))
    if any(not isinstance(v, str) or not v.strip() for v in patch.values()):
        raise ValueError('Replacement bodies must be nonempty strings')
    for key in REPLACE + CLEAR:
        if key not in ranges:
            raise ValueError('Missing section: ' + key)
    if not re.search(r'^TARGET_SYSTEM:\s*(UNICEF|INSPIRA|IOM|OTHER)\s*$', patch['LIMITS'], re.M):
        raise ValueError('LIMITS needs a supported TARGET_SYSTEM')
    for key in ('CHAR_LIMIT', 'TARGET_LOW', 'TARGET_HIGH', 'WORD_TARGET'):
        if not re.search(r'^' + key + ':', patch['LIMITS'], re.M):
            raise ValueError('Missing LIMITS key: ' + key)
    newline = b'\r\n' if b'\r\n' in before else b'\n'
    after = before
    for name in sorted(REPLACE + CLEAR, key=lambda k:ranges[k][1], reverse=True):
        _, start, end = ranges[name]
        value = patch.get(name, '').replace('\r\n', '\n').replace('\r', '\n').strip()
        body = newline + value.encode('utf-8').replace(b'\n', newline) + newline * 2 if value else newline
        after = after[:start] + body + after[end:]
    new_ranges = sections(after)
    if set(new_ranges) != set(ranges):
        raise ValueError('Replacement introduces or removes section headings')
    for name, (start, _, end) in ranges.items():
        if name not in REPLACE + CLEAR:
            a, _, b = new_ranges[name]
            if before[start:end] != after[a:b]:
                raise ValueError('Protected section changed: ' + name)
    first = min((v[0] for v in ranges.values()), default=len(before))
    if before[:first] != after[:first]:
        raise ValueError('Context preamble changed')
    for name in CLEAR:
        _, a, b = new_ranges[name]
        assert not after[a:b].strip()
    return after


def extract(database, key, output):
    database = Path(database).resolve(strict=True)
    require_distinct_paths(database, output)
    require_new_file(output)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows = db.execute('SELECT * FROM jobs WHERE job_key=?', (key,)).fetchall()
        if len(rows) != 1:
            raise ValueError('Expected one exact job_key, found ' + str(len(rows)))
        row = dict(rows[0]); raw = row.pop('raw_json', '{}')
        row['raw_source_fields'] = json.loads(raw or '{}')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        attachments = [dict(r) for r in db.execute('SELECT * FROM job_attachments WHERE job_key=?', (key,))] if 'job_attachments' in tables else []
    if not row.get('description', '').strip():
        raise ValueError('Selected record has no description; extract source detail before preparing')
    result = {'captured_at_utc': datetime.now(timezone.utc).isoformat(),
              'database': str(database), 'job': row, 'attachments': attachments}
    payload = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    write_new_file(output, payload)
    return {'job_key': key, 'status': row.get('status'), 'source_sha256': digest(payload),
            'description_characters': len(row['description']), 'attachments': len(attachments)}


def prepare(context, source, patch_path, apply=False):
    context = Path(context); source = Path(source)
    require_distinct_paths(context, source, patch_path)
    if context.is_symlink():
        raise ValueError('Context must be a regular file, not a symlink')
    before = context.read_bytes(); source_bytes = source.read_bytes()
    patch = json.loads(Path(patch_path).read_text())
    snapshot = json.loads(source_bytes)
    if digest(source_bytes) != patch['source_sha256']:
        raise ValueError('Source capture changed; review it again')
    if snapshot['job']['job_key'] != patch['job_key']:
        raise ValueError('Patch and source vacancy identities differ')
    filename = archive_name(patch['archive_filename'])
    archive = context.parent / 'history' / filename
    require_distinct_paths(context, source, patch_path, archive)
    if archive.is_symlink() or (archive.exists() and not archive.is_file()):
        raise ValueError('Archive destination must be a regular file')
    after = replace_sections(before, patch['sections'])
    if before == after:
        if not archive.is_file() or digest(archive.read_bytes()) != patch['context_sha256']:
            raise ValueError('Identical context but original archive cannot be verified')
        return {'status': 'already_applied', 'archive': str(archive), 'archive_sha256': patch['context_sha256'], 'context_sha256': digest(after)}
    if digest(before) != patch['context_sha256']:
        raise ValueError('Context changed since review; refresh patch before applying')
    report = {'status': 'applied' if apply else 'preview', 'job_key': patch['job_key'],
              'archive': str(archive), 'archive_replaces_existing': archive.exists(),
              'archive_sha256': digest(before), 'context_sha256': digest(after),
              'replaced_sections': list(REPLACE), 'cleared_sections': list(CLEAR),
              'protected_sections_unchanged': True}
    if apply:
        if context.read_bytes() != before:
            raise ValueError('Context changed before archive; no update made')
        atomic_write(archive, before)
        if archive.read_bytes() != before:
            raise ValueError('Archive verification failed; context not changed')
        if context.read_bytes() != before:
            raise ValueError('Context changed during archive; context not overwritten')
        atomic_write(context, after)
        if context.read_bytes() != after or archive.read_bytes() != before:
            raise ValueError('Readback verification failed')
    return report


def archive_prepared(context, filename, outgoing_filename, expected_context_sha256):
    context = Path(context)
    if context.is_symlink() or not context.is_file():
        raise ValueError('Context must be a regular file, not a symlink')
    filename = archive_name(filename)
    outgoing_filename = archive_name(outgoing_filename)
    if filename == outgoing_filename:
        raise ValueError('Prepared and outgoing archive names must differ')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_context_sha256):
        raise ValueError('Expected context SHA-256 must be 64 lowercase hex characters')
    data = context.read_bytes()
    if digest(data) != expected_context_sha256:
        raise ValueError('Context changed since preparation; review it again')
    archive = context.parent / 'history' / filename
    require_distinct_paths(context, archive, context.parent / 'history' / outgoing_filename)
    if archive.is_symlink() or (archive.exists() and not archive.is_file()):
        raise ValueError('Prepared archive destination must be a regular file')
    existing = archive.read_bytes() if archive.exists() else None
    if existing == data:
        return {'status': 'already_archived', 'prepared_archive': str(archive),
                'prepared_archive_sha256': expected_context_sha256}
    atomic_write(archive, data)
    if context.read_bytes() != data or archive.read_bytes() != data:
        raise ValueError('Prepared archive readback verification failed')
    return {'status': 'archived', 'prepared_archive': str(archive),
            'prepared_archive_sha256': expected_context_sha256,
            'replaced_existing': existing is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('extract')
    for key in ('database', 'job-key', 'output'):p.add_argument('--' + key, required=True)
    p = sub.add_parser('prepare')
    for key in ('context', 'source', 'patch'):p.add_argument('--' + key, required=True)
    p.add_argument('--report');p.add_argument('--apply', action='store_true')
    p = sub.add_parser('archive-prepared')
    for key in ('context', 'filename', 'outgoing-filename', 'expected-context-sha256'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--report')
    args = parser.parse_args()
    try:
        if getattr(args, 'report', None):
            protected = [args.context]
            if args.command == 'prepare':
                patch = json.loads(Path(args.patch).read_text())
                protected += [args.source, args.patch,
                    Path(args.context).parent / 'history' / archive_name(patch['archive_filename'])]
            else:
                protected += [Path(args.context).parent / 'history' / archive_name(name)
                              for name in (args.filename, args.outgoing_filename)]
            require_distinct_paths(*protected, args.report)
            require_new_file(args.report)
        if args.command == 'extract':
            result = extract(args.database, args.job_key, args.output)
        elif args.command == 'prepare':
            result = prepare(args.context, args.source, args.patch, args.apply)
        else:
            result = archive_prepared(args.context, args.filename, args.outgoing_filename,
                                      args.expected_context_sha256)
        text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        if getattr(args, 'report', None):write_new_file(args.report, text.encode())
        print(text, end='')
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        parser.exit(2, 'Error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
