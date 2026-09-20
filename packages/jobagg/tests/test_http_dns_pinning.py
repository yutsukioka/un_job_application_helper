"""Network-free end-to-end urllib tests: DNS must not run again during connect."""

import io
import socket
import ssl

import pytest

from jobagg.http import JobAggHTTPClient
from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError

PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "8.8.8.8"
OK = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"


class Wire:
    def __init__(self, monkeypatch, answers, responses, failures=()):
        self.answers = iter(answers)
        self.responses = iter(responses)
        self.failures = set(failures)
        self.lookups = []
        self.connections = []
        self.sent = []
        self.sockets = []
        wire = self

        def resolve(host, port, *args, **kwargs):
            self.lookups.append((host, port))
            # Any accidental transport lookup receives a denied address.
            addresses = next(self.answers, ["127.0.0.1"])
            return [
                (
                    socket.AF_INET6 if ":" in ip else socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (ip, port or 0),
                )
                for ip in addresses
            ]

        class Socket:
            def __init__(self, family, kind):
                self.family = family
                self.closed = False
                wire.sockets.append(self)

            def settimeout(self, timeout):
                self.timeout = timeout

            def setsockopt(self, *args):
                pass

            def connect(self, address):
                wire.connections.append(address)
                if len(wire.connections) in wire.failures:
                    raise TimeoutError("fixture timeout")

            def sendall(self, data):
                wire.sent.append(data)

            def makefile(self, *args):
                return io.BytesIO(next(wire.responses))

            def close(self):
                self.closed = True

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        monkeypatch.setattr(socket, "socket", Socket)


def client(**kwargs):
    return JobAggHTTPClient(
        safe_policy=SafeHTTPPolicy({"jobs.example.test", "cdn.example.test"}),
        backoff_base_seconds=0,
        jitter_ratio=0,
        **kwargs,
    )


def test_connect_pins_first_public_dns_answer_and_preserves_host(monkeypatch):
    c = client(max_retries=0)
    wire = Wire(monkeypatch, [[PUBLIC]], [OK])
    assert c.get("http://jobs.example.test:8080/jobs").text == "ok"
    assert wire.lookups == [("jobs.example.test", None)]
    assert wire.connections == [(PUBLIC, 8080)]
    assert b"Host: jobs.example.test:8080\r\n" in wire.sent[0]


def test_https_preserves_sni_and_verified_context(monkeypatch):
    context = ssl.create_default_context()
    wrapped = []

    def wrap(sock, *, server_hostname):
        wrapped.append((server_hostname, context.check_hostname, context.verify_mode))
        return sock

    monkeypatch.setattr(context, "wrap_socket", wrap)
    monkeypatch.setattr("jobagg.http.verified_ssl_context", lambda: context)
    c = client(max_retries=0)
    wire = Wire(monkeypatch, [[PUBLIC]], [OK])
    assert c.get("https://jobs.example.test/jobs").text == "ok"
    assert wire.connections == [(PUBLIC, 443)]
    assert wrapped == [("jobs.example.test", True, ssl.CERT_REQUIRED)]
    assert b"Host: jobs.example.test\r\n" in wire.sent[0]
    assert len(wire.lookups) == 1


@pytest.mark.parametrize(
    "target", ["http://jobs.example.test/next", "http://cdn.example.test/next"]
)
def test_redirect_pins_its_own_dns_answer(monkeypatch, target):
    c = client(max_retries=0)
    redirect = f"HTTP/1.1 302 Found\r\nLocation: {target}\r\nContent-Length: 0\r\n\r\n".encode()
    wire = Wire(monkeypatch, [[PUBLIC], [OTHER_PUBLIC]], [redirect, OK])
    assert c.get("http://jobs.example.test/jobs").text == "ok"
    assert wire.connections == [(PUBLIC, 80), (OTHER_PUBLIC, 80)]
    assert len(wire.lookups) == 2


def test_redirect_rebinding_is_rejected_before_connect(monkeypatch):
    c = client(max_retries=0)
    redirect = b"HTTP/1.1 302 Found\r\nLocation: /next\r\nContent-Length: 0\r\n\r\n"
    wire = Wire(monkeypatch, [[PUBLIC], ["127.0.0.1"]], [redirect])
    with pytest.raises(SSRFProtectionError, match="denied network"):
        c.get("http://jobs.example.test/jobs")
    assert wire.connections == [(PUBLIC, 80)]


@pytest.mark.parametrize("failure", ["timeout", "503"])
@pytest.mark.parametrize("next_address", [OTHER_PUBLIC, "127.0.0.1"])
def test_retry_revalidates_and_pins_new_address(monkeypatch, failure, next_address):
    c = client(max_retries=1)
    responses = (
        [OK]
        if failure == "timeout"
        else [b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n", OK]
    )
    wire = Wire(
        monkeypatch,
        [[PUBLIC], [next_address]],
        responses,
        failures=[1] if failure == "timeout" else [],
    )
    if next_address == "127.0.0.1":
        with pytest.raises(SSRFProtectionError, match="denied network"):
            c.get("http://jobs.example.test/jobs")
        assert wire.connections == [(PUBLIC, 80)]
    else:
        assert c.get("http://jobs.example.test/jobs").text == "ok"
        assert wire.connections == [(PUBLIC, 80), (OTHER_PUBLIC, 80)]
    assert len(wire.lookups) == 2


def test_address_fallback_stays_within_one_validated_set(monkeypatch):
    c = client(max_retries=0)
    wire = Wire(monkeypatch, [[PUBLIC, OTHER_PUBLIC]], [OK], failures=[1])
    assert c.get("http://jobs.example.test/jobs").text == "ok"
    assert wire.connections == [(address, 80) for address in sorted([PUBLIC, OTHER_PUBLIC])]
    assert len(wire.lookups) == 1
    assert wire.sockets[0].closed


def test_environment_proxy_cannot_resolve_destination_independently(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9999")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9999")
    c = client(max_retries=0)
    wire = Wire(monkeypatch, [[PUBLIC]], [OK])
    assert c.get("http://jobs.example.test/jobs").text == "ok"
    assert wire.connections == [(PUBLIC, 80)]
    assert wire.lookups == [("jobs.example.test", None)]


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        [PUBLIC, "10.0.0.5"],
        ["::ffff:127.0.0.1"],
        ["::ffff:10.0.0.5"],
        ["::ffff:169.254.169.254"],
        ["::ffff:192.168.1.1"],
    ],
)
def test_invalid_or_denied_resolver_results_never_connect(monkeypatch, addresses):
    c = client(max_retries=0)
    wire = Wire(monkeypatch, [addresses], [])
    with pytest.raises(SSRFProtectionError):
        c.get("http://jobs.example.test/jobs")
    assert wire.connections == []


@pytest.mark.parametrize("address", ["::ffff:93.184.216.34", "2606:4700:4700::1111"])
def test_public_ipv6_answers_remain_usable(monkeypatch, address):
    c = client(max_retries=0)
    wire = Wire(monkeypatch, [[address]], [OK])
    assert c.get("http://jobs.example.test/jobs").text == "ok"
    assert wire.connections == [(address, 80, 0, 0)]
    assert wire.sockets[0].family == socket.AF_INET6


def test_durable_opener_rebuild_keeps_dns_pinning(monkeypatch):
    from jobagg.pipelines.http_checkpoint import OneHopRedirect

    c = client(max_retries=0)
    c._opener = c._build_opener(OneHopRedirect())
    wire = Wire(monkeypatch, [[PUBLIC]], [OK])
    assert c.get("http://jobs.example.test/jobs").text == "ok"
    assert wire.connections == [(PUBLIC, 80)]
    assert len(wire.lookups) == 1


def test_tls_verification_failure_does_not_retry_unverified(monkeypatch):
    from jobagg.http import HTTPError

    context = ssl.create_default_context()

    def reject(sock, *, server_hostname):
        raise ssl.SSLCertVerificationError("fixture hostname mismatch")

    monkeypatch.setattr(context, "wrap_socket", reject)
    monkeypatch.setattr("jobagg.http.verified_ssl_context", lambda: context)
    c = client(max_retries=2)
    wire = Wire(monkeypatch, [[PUBLIC]], [])
    with pytest.raises(HTTPError):
        c.get("https://jobs.example.test/jobs")
    assert wire.connections == [(PUBLIC, 443)]
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
