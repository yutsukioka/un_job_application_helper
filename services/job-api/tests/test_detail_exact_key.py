import asyncio
from pathlib import Path

import httpx
from urllib.parse import quote

from fastapi.testclient import TestClient
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from job_api.app import create_app
from job_api.config import ApiSettings
import pytest


@pytest.fixture
def route_client(tmp_path):
    database = JobDatabase(tmp_path / 'jobs.sqlite3')
    database.initialize()
    source = OrganizationSource(id='osce_custom_html', name='OSCE', ats_family='static_html',
                                base_url='https://vacancies.osce.org', enabled=True)
    records = [('consultant-%E2%80%93-roster', 'Literal encoded source slug'),
               ('consultant-–-roster', 'Distinct Unicode source slug'),
               ('unicode-–-only', 'Unicode-only vacancy')]
    for external, title in records:
        database.upsert_job(build_job(source, external_id=external, title=title,
            description='Complete public responsibilities and qualifications for ' + title,
            apply_url='https://vacancies.osce.org/' + external,
            raw={'attachments': [{'text': 'Complete required terms of reference.'}]}))
    settings = ApiSettings(repo_root=Path(tmp_path), db_path=database.path,
                          saved_searches_path=tmp_path / 'saved.json', tracker_path=tmp_path / 'tracker.json')
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize('route', ['/api/job-detail', '/api/jobs/by-key'])
def test_exact_percent_encoded_key_wins_over_another_real_unicode_key(route_client, route):
    key = 'osce_custom_html:consultant-%E2%80%93-roster'
    response = route_client.get(route, params={'job_key': key})
    assert response.status_code == 200
    payload = response.json()
    assert payload['job_key'] == key
    assert payload['title'] == 'Literal encoded source slug'
    assert payload['raw']['attachments'][0]['text'] == 'Complete required terms of reference.'


@pytest.mark.parametrize('route', ['/api/jobs/', '/api/jobs/path/'])
def test_path_route_preserves_literal_encoded_identity(route_client, route):
    key = 'osce_custom_html:consultant-%E2%80%93-roster'
    # The installed deprecated Starlette/httpx TestClient adapter decodes
    # request.url.path twice. ASGITransport supplies the ASGI path contract
    # correctly (one URL decode) without opening a network socket.
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=route_client.app),
                                     base_url='http://testserver') as client:
            return await client.get(route + quote(key, safe=''))
    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()['job_key'] == key
    assert response.json()['title'] == 'Literal encoded source slug'


def test_normal_unicode_key_is_unchanged(route_client):
    key = 'osce_custom_html:consultant-–-roster'
    response = route_client.get('/api/job-detail', params={'job_key': key})
    assert response.status_code == 200
    assert response.json()['job_key'] == key
    assert response.json()['title'] == 'Distinct Unicode source slug'


def test_encoded_fallback_resolves_stored_unicode_identity(route_client):
    response = route_client.get('/api/job-detail', params={'job_key': 'osce_custom_html:unicode-%E2%80%93-only'})
    assert response.status_code == 200
    assert response.json()['job_key'] == 'osce_custom_html:unicode-–-only'
    assert response.json()['title'] == 'Unicode-only vacancy'
    assert 'classification' in response.json() and 'locations' in response.json()


def test_unknown_encoded_key_still_returns_404(route_client):
    response = route_client.get('/api/job-detail', params={'job_key': 'osce_custom_html:absent-%E2%80%93'})
    assert response.status_code == 404
