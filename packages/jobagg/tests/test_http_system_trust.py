"""Verified OS trust roots must survive the durable redirect-opener rebuild."""
import ssl
import sys
from types import SimpleNamespace
import urllib.request

import pytest

from jobagg.http import JobAggHTTPClient, verified_ssl_context
from jobagg.pipelines.http_checkpoint import DurableCapture, OneHopRedirect
from jobagg.robots import load_policy


def test_installed_truststore_uses_verified_client_context(monkeypatch):
    expected = ssl.create_default_context()
    calls = []
    def factory(protocol):
        calls.append(protocol)
        return expected
    monkeypatch.setitem(sys.modules, 'truststore', SimpleNamespace(SSLContext=factory))
    assert verified_ssl_context() is expected
    assert calls == [ssl.PROTOCOL_TLS_CLIENT]
    assert expected.verify_mode == ssl.CERT_REQUIRED and expected.check_hostname


def test_absent_truststore_fallback_still_verifies(monkeypatch):
    monkeypatch.setitem(sys.modules, 'truststore', None)
    context = verified_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_installed_context_failure_does_not_downgrade(monkeypatch):
    def factory(_):
        raise RuntimeError('OS trust initialization failed')
    monkeypatch.setitem(sys.modules, 'truststore', SimpleNamespace(SSLContext=factory))
    with pytest.raises(RuntimeError, match='OS trust'):
        verified_ssl_context()


def test_nonverifying_provider_context_rejected(monkeypatch):
    context = ssl.create_default_context()
    context.check_hostname = False
    monkeypatch.setitem(sys.modules, 'truststore', SimpleNamespace(SSLContext=lambda _: context))
    with pytest.raises(ValueError, match='hostname verification'):
        verified_ssl_context()


def test_durable_capture_preserves_exact_context_cookie_jar_and_redirect_guard(tmp_path):
    client = JobAggHTTPClient()
    context = client._ssl_context
    policy = tmp_path / 'policy.yaml'
    policy.write_text('default:\n  honor_robots_txt: true\n  min_delay_seconds: 2\n')
    first_https = next(h for h in client._opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
    assert first_https._context is context
    DurableCapture(client, load_policy(policy), tmp_path / 'captures', {}, lock_root=tmp_path / 'locks')
    handlers = client._opener.handlers
    https = next(h for h in handlers if isinstance(h, urllib.request.HTTPSHandler))
    cookies = next(h for h in handlers if isinstance(h, urllib.request.HTTPCookieProcessor))
    assert https._context is context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert cookies.cookiejar is client._cookie_jar
    assert len([h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]) == 1
    assert any(isinstance(h, OneHopRedirect) for h in handlers)


def test_changed_client_context_is_rejected_on_opener_rebuild():
    client = JobAggHTTPClient()
    client._ssl_context.check_hostname = False
    with pytest.raises(ValueError, match='hostname verification'):
        client._build_opener(OneHopRedirect())
