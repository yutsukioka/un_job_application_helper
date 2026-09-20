import io
from email.message import Message
import urllib.request
import urllib.response

import pytest

from jobagg.http import JobAggHTTPClient
from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError


class OfflineTransport(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def https_open(self, request):
        self.requests.append(request)
        location = self.routes[request.full_url]
        headers = Message()
        headers['Content-Type'] = 'text/plain; charset=utf-8'
        if location:
            headers['Location'] = location
        response = urllib.response.addinfourl(io.BytesIO(b'ok'), headers, request.full_url, 302 if location else 200)
        response.msg = 'Found' if location else 'OK'
        return response


def make_client(routes, hosts, max_redirects=5):
    client = JobAggHTTPClient(safe_policy=SafeHTTPPolicy(
        allowed_hosts=set(hosts), resolver=lambda host: ['8.8.8.8'], max_redirects=max_redirects))
    transport = OfflineTransport(routes)
    client._opener.add_handler(transport)
    return client, transport


def test_rejects_intermediate_unallowed_redirect_before_request():
    client, transport = make_client({'https://source.test/a': 'https://unallowed.test/b'}, ['source.test'])
    with pytest.raises(SSRFProtectionError, match='allowlist'):
        client.get('https://source.test/a')
    assert [r.full_url for r in transport.requests] == ['https://source.test/a']


def test_allowed_cross_origin_redirect_drops_cookie_and_custom_credentials():
    client, transport = make_client({'https://source.test/a': 'https://cdn.test/b', 'https://cdn.test/b': None}, ['source.test', 'cdn.test'])
    assert client.get('https://source.test/a', headers={
        'Cookie': 'session=private', 'Authorization': 'Bearer private', 'X-Custom-Token': 'private', 'Accept-Language': 'en'
    }).text == 'ok'
    forwarded = {k.lower(): v for k, v in transport.requests[1].header_items()}
    assert not {'cookie', 'authorization', 'x-custom-token'} & forwarded.keys()
    assert forwarded['accept-language'] == 'en'


def test_same_origin_public_form_redirect_preserves_explicit_cookie():
    client, transport = make_client({'https://source.test/a': '/b', 'https://source.test/b': None}, ['source.test'])
    client.get('https://source.test/a', headers={'Cookie': 'session=private'})
    assert transport.requests[1].get_header('Cookie') == 'session=private'


def test_redirect_limit_is_enforced_before_next_request():
    client, transport = make_client({'https://source.test/a': '/b', 'https://source.test/b': '/c'}, ['source.test'], max_redirects=1)
    with pytest.raises(SSRFProtectionError, match='Too many'):
        client.get('https://source.test/a')
    assert len(transport.requests) == 2


def test_tls_downgrade_is_rejected_before_following():
    client, transport = make_client({'https://source.test/a': 'http://source.test/b'}, ['source.test'])
    with pytest.raises(SSRFProtectionError, match='downgrade'):
        client.get('https://source.test/a')
    assert len(transport.requests) == 1
