import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'agents/apex/skills/apex-application-preparation/scripts/prepare_application.py'
spec = importlib.util.spec_from_file_location('application_preparation', SCRIPT)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def setup(tmp_path, newline=b'\n'):
    bodies = {
        'USER_JOB_HISTORY_TEXT': 'Preserve 日本語 and exact spacing.\n```text\n## JOB_DESCRIPTION_TEXT\nnot a section\n```',
        'USER_ADMIN_PROFILE_TEXT': 'unchanged candidate text',
        'JOB_DESCRIPTION_TEXT': 'Outgoing Position Agency 101 City',
        'JOB_REQUIREMENT_TEXT': 'Old requirements',
        'JOB_QUALIFICATION_QUESTIONS': 'Old questions and choices',
        'TERM_EXTRACTOR': 'old keywords', 'JD_KEYWORD_BANK': 'old bank',
        'SKILLS_TAXONOMY': 'original taxonomy',
        'LIMITS': 'TARGET_SYSTEM: OTHER\nCHAR_LIMIT: \nTARGET_LOW: \nTARGET_HIGH: \nWORD_TARGET:',
        'RUN_MODE': 'MODE: SINGLE_AGENT', 'BUDGETS': 'MAX_REVISION_PASSES: 2',
        'CCOG_CLASSIFICATION': 'old confirmed classification',
    }
    before = ('# Context\n\n' + ''.join('## '+k+'\n\n'+v+'\n\n' for k,v in bodies.items())).encode().replace(b'\n',newline)
    context=tmp_path/'application_context.md';context.write_bytes(before)
    source=tmp_path/'source.json';source.write_text(json.dumps({'job':{'job_key':'demo:202','description':'new text'}}))
    patch={'job_key':'demo:202','source_sha256':prep.digest(source.read_bytes()),'context_sha256':prep.digest(before),
           'archive_filename':'OUTGOING POSITION AGENCY 101 CITY.md','sections':{
               'JOB_DESCRIPTION_TEXT':'Incoming Position 202\nFull original duties',
               'JOB_REQUIREMENT_TEXT':'Exact source requirements',
               'JOB_QUALIFICATION_QUESTIONS':'[Not available in the selected database record.]',
               'LIMITS':'TARGET_SYSTEM: UNICEF\nCHAR_LIMIT: UNKNOWN\nTARGET_LOW: UNKNOWN\nTARGET_HIGH: UNKNOWN\nWORD_TARGET: UNKNOWN'}}
    patchfile=tmp_path/'patch.json';patchfile.write_text(json.dumps(patch))
    return context,source,patchfile,patch,before


@pytest.mark.parametrize('newline',[b'\n',b'\r\n'])
def test_archive_replacement_protected_bytes_clear_sections_and_idempotence(tmp_path,newline):
    context,source,pfile,patch,before=setup(tmp_path,newline)
    archive=context.parent/'history'/patch['archive_filename'];archive.parent.mkdir();archive.write_text('stale archive')
    assert prep.prepare(context,source,pfile)['status']=='preview'
    assert archive.read_text()=='stale archive' and context.read_bytes()==before
    result=prep.prepare(context,source,pfile,True)
    assert result['status']=='applied' and archive.read_bytes()==before
    after=context.read_bytes();old=prep.sections(before);new=prep.sections(after)
    for key in set(old)-set(prep.REPLACE+prep.CLEAR):
        a,_,b=old[key];c,_,d=new[key];assert before[a:b]==after[c:d]
    for key in prep.CLEAR:
        _,a,b=new[key];assert not after[a:b].strip()
    for key,body in patch['sections'].items():
        _,a,b=new[key];assert after[a:b].decode().strip().replace('\r\n','\n')==body
    assert prep.prepare(context,source,pfile,True)['status']=='already_applied'
    assert archive.read_bytes()==before


@pytest.mark.parametrize('mutation',['context','source','identity','traversal','duplicate','missing','unexpected','injected_header'])
def test_invalid_inputs_do_not_overwrite_context_or_archive(tmp_path,mutation):
    context,source,pfile,patch,before=setup(tmp_path)
    if mutation=='context':context.write_bytes(before+b'concurrent edit')
    if mutation=='source':source.write_bytes(source.read_bytes()+b' ')
    if mutation=='identity':patch['job_key']='demo:wrong'
    if mutation=='traversal':patch['archive_filename']='../escape.md'
    if mutation=='duplicate':
        context.write_bytes(before+b'## JOB_DESCRIPTION_TEXT\nsecond\n');patch['context_sha256']=prep.digest(context.read_bytes())
    if mutation=='missing':
        context.write_bytes(before.replace(b'## CCOG_CLASSIFICATION',b'# Removed'));patch['context_sha256']=prep.digest(context.read_bytes())
    if mutation=='unexpected':patch['sections']['USER_JOB_HISTORY_TEXT']='overwrite'
    if mutation=='injected_header':patch['sections']['JOB_DESCRIPTION_TEXT']+='\n## EXTRA_SECTION\nno'
    pfile.write_text(json.dumps(patch));original=context.read_bytes()
    with pytest.raises(ValueError):prep.prepare(context,source,pfile,True)
    assert context.read_bytes()==original and not (tmp_path/'history').exists()


def test_changed_context_during_archiving_is_not_overwritten(tmp_path,monkeypatch):
    context,source,pfile,patch,before=setup(tmp_path)
    original=prep.atomic_write
    def race(path,data):
        original(path,data)
        if Path(path).parent.name=='history':context.write_bytes(before+b'concurrent change')
    monkeypatch.setattr(prep,'atomic_write',race)
    with pytest.raises(ValueError,match='during archive'):prep.prepare(context,source,pfile,True)
    assert context.read_bytes()==before+b'concurrent change'
    assert (tmp_path/'history'/patch['archive_filename']).read_bytes()==before


def test_readonly_snapshot_exact_identity_and_attachments(tmp_path):
    dbfile=tmp_path/'jobs.sqlite3'
    with sqlite3.connect(dbfile) as db:
        db.execute('create table jobs(job_key text, title text, description text, raw_json text, status text)')
        db.execute('create table job_attachments(job_key text, extracted_text text)')
        db.execute('insert into jobs values(?,?,?,?,?)',('demo:202','Selected title','Original text','{"screening_questions":["Actual question?"]}','open'))
        db.execute('insert into jobs values(?,?,?,?,?)',('demo:203','Other title','Other text','{}','open'))
        db.execute('insert into job_attachments values(?,?)',('demo:202','Exact attachment text'))
    before=dbfile.read_bytes();output=tmp_path/'capture.json'
    result=prep.extract(dbfile,'demo:202',output);saved=json.loads(output.read_text())
    assert result['source_sha256']==prep.digest(output.read_bytes())
    assert saved['job']['raw_source_fields']['screening_questions']==['Actual question?']
    assert saved['attachments'][0]['extracted_text']=='Exact attachment text'
    assert dbfile.read_bytes()==before
    with pytest.raises(ValueError):prep.extract(dbfile,"demo:202' OR 1=1 --",tmp_path/'bad.json')
    assert not (tmp_path/'bad.json').exists()


def test_symlink_archive_is_not_followed(tmp_path):
    context,source,pfile,patch,before=setup(tmp_path)
    history=tmp_path/'history';history.mkdir();other=tmp_path/'other';other.write_text('keep')
    (history/patch['archive_filename']).symlink_to(other)
    with pytest.raises(ValueError):prep.prepare(context,source,pfile,True)
    assert other.read_text()=='keep' and context.read_bytes()==before


@pytest.mark.parametrize('destination', ['database', 'symlink', 'existing'])
def test_extract_cannot_replace_database_or_existing_capture(tmp_path, destination):
    database = tmp_path / 'jobs.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('create table jobs(job_key text, description text, raw_json text)')
        db.execute('insert into jobs values(?,?,?)', ('demo:202', 'Description', '{}'))
    before = database.read_bytes()
    output = database
    if destination == 'symlink':
        output = tmp_path / 'alias.json'; output.symlink_to(database)
    if destination == 'existing':
        output = tmp_path / 'capture.json'; output.write_bytes(b'previous capture')
    with pytest.raises(ValueError):
        prep.extract(database, 'demo:202', output)
    assert database.read_bytes() == before
    if destination == 'existing': assert output.read_bytes() == b'previous capture'


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('destination', ['context', 'source', 'patch', 'archive', 'existing'])
def test_prepare_report_cannot_overwrite_inputs_or_archive(tmp_path, apply, destination):
    context, source, patchfile, patch, before = setup(tmp_path)
    archive = context.parent / 'history' / patch['archive_filename']
    existing = tmp_path / 'existing.json'; existing.write_bytes(b'keep report')
    report = {'context': context, 'source': source, 'patch': patchfile,
              'archive': archive, 'existing': existing}[destination]
    originals = {p: p.read_bytes() for p in (context, source, patchfile, existing)}
    command = [sys.executable, str(SCRIPT), 'prepare', '--context', str(context),
               '--source', str(source), '--patch', str(patchfile), '--report', str(report)]
    if apply: command.append('--apply')
    result = subprocess.run(command, capture_output=True)
    assert result.returncode == 2
    assert all(p.read_bytes() == data for p, data in originals.items())
    assert not archive.exists()


@pytest.mark.parametrize('destination', ['context', 'outgoing', 'prepared'])
def test_archive_report_cannot_alias_protected_destinations(tmp_path, destination):
    context, source, patchfile, patch, before = setup(tmp_path)
    outgoing = context.parent / 'history' / patch['archive_filename']
    prepared = context.parent / 'history' / 'PREPARED POSITION.md'
    report = {'context': context, 'outgoing': outgoing, 'prepared': prepared}[destination]
    result = subprocess.run([sys.executable, str(SCRIPT), 'archive-prepared',
        '--context', str(context), '--filename', prepared.name,
        '--outgoing-filename', outgoing.name, '--expected-context-sha256', prep.digest(before),
        '--report', str(report)], capture_output=True)
    assert result.returncode == 2
    assert context.read_bytes() == before
    assert not outgoing.exists() and not prepared.exists()
