import asyncio
import socket

import pytest

from jobagg import browser_proxy
from jobagg.http_safe import SafeHTTPPolicy, ValidatedEndpoint


@pytest.mark.parametrize('answer', ['0.0.0.0', '::', '::ffff:0.0.0.0', '127.0.0.1', '10.0.0.5'])
def test_proxy_rejects_denied_dns_before_dial(monkeypatch, answer):
    attempts = []
    async def forbidden_dial(*args):
        attempts.append(args)
        raise AssertionError('Denied destination reached dialer')
    monkeypatch.setattr(browser_proxy, '_dial', forbidden_dial)
    async def run():
        policy = SafeHTTPPolicy({'jobs.example.test'}, resolver=lambda _: [answer])
        async with browser_proxy.pinned_browser_proxy(policy, 1) as proxy:
            port = int(proxy.rsplit(':', 1)[1])
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(b'CONNECT jobs.example.test:443 HTTP/1.1\r\n\r\n')
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 2) == b''
            writer.close()
            await writer.wait_closed()
    asyncio.run(run())
    assert attempts == []


@pytest.mark.parametrize('payload', [
    b'GET https://jobs.example.test/ HTTP/1.1\r\n\r\n',
    b'CONNECT other.example.test:443 HTTP/1.1\r\n\r\n',
    b'CONNECT jobs.example.test:443/path HTTP/1.1\r\n\r\n',
    b'CONNECT user@jobs.example.test:443 HTTP/1.1\r\n\r\n',
])
def test_proxy_rejects_invalid_or_unreviewed_authority(monkeypatch, payload):
    async def forbidden_dial(*args):
        pytest.fail('Invalid request reached dialer')
    monkeypatch.setattr(browser_proxy, '_dial', forbidden_dial)
    async def run():
        policy = SafeHTTPPolicy({'jobs.example.test'}, resolver=lambda _: ['8.8.8.8'])
        async with browser_proxy.pinned_browser_proxy(policy, 1) as proxy:
            reader, writer = await asyncio.open_connection('127.0.0.1', int(proxy.rsplit(':', 1)[1]))
            writer.write(payload)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 2) == b''
            writer.close()
            await writer.wait_closed()
    asyncio.run(run())


def test_proxy_dial_only_uses_validated_numeric_addresses(monkeypatch):
    attempts = []
    async def run():
        loop = asyncio.get_running_loop()
        async def connect(sock, destination):
            attempts.append(destination)
            raise OSError('Fixture destination unavailable')
        monkeypatch.setattr(loop, 'sock_connect', connect)
        def no_dns(*args):
            pytest.fail('Transport repeated DNS lookup')
        monkeypatch.setattr(socket, 'getaddrinfo', no_dns)
        endpoint = ValidatedEndpoint('jobs.example.test', 443, ('8.8.8.8', '2606:4700:4700::1111'))
        with pytest.raises(OSError, match='unavailable'):
            await browser_proxy._dial(endpoint, 1)
    asyncio.run(run())
    assert attempts == [('8.8.8.8', 443), ('2606:4700:4700::1111', 443)]
