"""Task-local HTTPS tunnel: Chromium retains TLS; only validated IPs are dialed."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import ipaddress
import socket
from urllib.parse import urlsplit

from jobagg.http_safe import SSRFProtectionError


async def _dial(endpoint, timeout):
    loop = asyncio.get_running_loop()
    last_error = None
    for address in endpoint.addresses:
        family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            # Numeric addresses make sock_connect skip getaddrinfo entirely.
            await asyncio.wait_for(loop.sock_connect(sock, (address, endpoint.port)), timeout)
            return await asyncio.open_connection(sock=sock)
        except BaseException as exc:
            sock.close()
            if not isinstance(exc, OSError):
                raise
            last_error = exc
    raise last_error or SSRFProtectionError("No validated connection addresses")


@asynccontextmanager
async def pinned_browser_proxy(policy, timeout):
    """No direct fallback, plaintext forwarding, or TLS interception."""
    tasks = set()

    async def pump(reader, writer):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()

    async def tunnel(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        upstream = None
        relays = []
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
            method, authority, version = header.split(b"\r\n", 1)[0].decode("ascii").split()
            if method != "CONNECT" or version != "HTTP/1.1":
                raise SSRFProtectionError("Only HTTPS CONNECT is allowed")
            # Parse strictly as an authority, never as an arbitrary URL/path.
            parsed = urlsplit("https://" + authority)
            if parsed.path or parsed.query or parsed.fragment or parsed.port is None:
                raise SSRFProtectionError("Invalid CONNECT authority")
            endpoint = await asyncio.to_thread(policy.resolve_url, "https://" + authority)
            upstream_reader, upstream = await asyncio.wait_for(_dial(endpoint, timeout), timeout)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            relays = [asyncio.create_task(pump(reader, upstream)),
                      asyncio.create_task(pump(upstream_reader, writer))]
            await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            # Closing the tunnel makes Chromium fail closed. Never retry direct.
            pass
        finally:
            for relay in relays:
                relay.cancel()
            if relays:
                await asyncio.gather(*relays, return_exceptions=True)
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(tunnel, "127.0.0.1", 0, limit=16384)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()
        active = list(tasks)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
