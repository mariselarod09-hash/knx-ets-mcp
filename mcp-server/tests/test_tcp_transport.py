"""Integration tests for TcpTransport over a real loopback TCP server.

A local asyncio TCP server is started on an ephemeral port.  Incoming
newline-delimited JSON frames are dispatched through MockTransport.send(),
so the test exercises the real KNX bridge protocol over a real socket.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from knx_ets_mcp.client import KnxBridgeClient, KnxBridgeError
from knx_ets_mcp.transport.mock import MockTransport
from knx_ets_mcp.transport.tcp import TcpTransport


# ---------------------------------------------------------------------------
# Loopback server that delegates to MockTransport
# ---------------------------------------------------------------------------

class _LoopbackServer:
    """TCP server that routes each line through a MockTransport instance."""

    def __init__(self) -> None:
        self._mock = MockTransport()
        self._server: asyncio.Server | None = None
        self._client_writers: list[asyncio.StreamWriter] = []
        self.host = "127.0.0.1"
        self.port = 0  # assigned after start

    async def start(self) -> None:
        await self._mock.connect()
        self._server = await asyncio.start_server(
            self._handle_client, self.host, 0,
        )
        # Grab the actual port the OS assigned.
        addr = self._server.sockets[0].getsockname()
        self.port = addr[1]

    async def stop(self) -> None:
        # Forcibly close all connected clients so their reads see EOF.
        for w in self._client_writers:
            w.close()
        self._client_writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._client_writers.append(writer)
        try:
            while True:
                raw = await reader.readuntil(b"\n")
                request: dict[str, Any] = json.loads(raw.decode("utf-8"))
                response = await self._mock.send(request)
                line = json.dumps(response, separators=(",", ":")) + "\n"
                writer.write(line.encode("utf-8"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            if writer in self._client_writers:
                self._client_writers.remove(writer)
            try:
                writer.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def loopback():
    """Start a loopback TCP server, yield it, then tear down."""
    srv = _LoopbackServer()
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def tcp_transport(loopback: _LoopbackServer) -> TcpTransport:
    """A connected TcpTransport aimed at the loopback server."""
    t = TcpTransport(
        host=loopback.host,
        port=loopback.port,
        token=loopback._mock.token,
    )
    await t.connect()
    yield t  # type: ignore[misc]
    await t.close()


@pytest.fixture
async def tcp_client(tcp_transport: TcpTransport) -> KnxBridgeClient:
    """A KnxBridgeClient backed by the TCP transport."""
    return KnxBridgeClient(tcp_transport)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTcpTransportBasics:
    """Low-level transport tests."""

    async def test_connect_and_token(self, tcp_transport: TcpTransport) -> None:
        """Transport connects and exposes the configured token."""
        assert tcp_transport.token == "mock-token-for-testing"

    async def test_constructor_allows_empty_token(self) -> None:
        # Empty token = AddIn authentication disabled; the client must not force
        # a token. The AddIn ignores it when auth is off.
        t = TcpTransport("127.0.0.1", 9999, token="")
        assert t.token == ""

    async def test_constructor_rejects_invalid_port(self) -> None:
        with pytest.raises(ValueError, match="Invalid port"):
            TcpTransport("127.0.0.1", 0, token="tok")
        with pytest.raises(ValueError, match="Invalid port"):
            TcpTransport("127.0.0.1", 70000, token="tok")

    async def test_send_before_connect_raises(self) -> None:
        t = TcpTransport("127.0.0.1", 9999, token="tok")
        with pytest.raises(ConnectionError, match="not connected"):
            await t.send({"id": "1", "token": "tok", "method": "project.info"})

    async def test_connect_refused(self) -> None:
        """Connecting to a port with no listener raises ConnectionError."""
        t = TcpTransport("127.0.0.1", 1, token="tok")
        with pytest.raises(ConnectionError):
            await t.connect()

    async def test_large_response_line_over_64kib(self) -> None:
        """A single response line larger than asyncio's 64 KiB default buffer
        must still be read (regression: LimitOverrunError on big ga.list)."""
        big = "x" * 200_000  # one JSON line well over the 64 KiB stream default

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        ) -> None:
            try:
                await reader.readuntil(b"\n")
                resp = {"id": "1", "ok": True, "result": {"blob": big}}
                writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        t = TcpTransport("127.0.0.1", port, token="tok")
        await t.connect()
        try:
            resp = await t.send(
                {"id": "1", "token": "tok", "method": "project.info", "params": {}}
            )
            assert resp["result"]["blob"] == big
        finally:
            await t.close()
            server.close()
            await server.wait_closed()


class TestTcpProtocol:
    """End-to-end protocol tests through the TCP loopback server."""

    async def test_project_info(self, tcp_client: KnxBridgeClient) -> None:
        """Read tool works end-to-end over TCP."""
        info = await tcp_client.project_info()
        assert info["projectId"] == "proj-001"
        assert info["name"] == "Test KNX Project"
        assert info["groupAddressStyle"] == "ThreeLevel"
        assert "revision" in info

    async def test_list_devices(self, tcp_client: KnxBridgeClient) -> None:
        devices = await tcp_client.list_devices()
        assert len(devices) == 2
        refs = {d["ref"] for d in devices}
        assert "dev-0001" in refs
        assert "dev-0002" in refs

    async def test_list_group_addresses(self, tcp_client: KnxBridgeClient) -> None:
        gas = await tcp_client.list_group_addresses()
        assert len(gas) == 2
        names = {ga["name"] for ga in gas}
        assert "Light Living Room" in names

    async def test_list_comobjects(self, tcp_client: KnxBridgeClient) -> None:
        cos = await tcp_client.list_comobjects("dev-0001")
        assert len(cos) == 3
        co1 = next(co for co in cos if co["ref"] == "co-0001")
        assert "ga-0001" in co1["links"]

    async def test_wrong_token_rejected(
        self, loopback: _LoopbackServer,
    ) -> None:
        """A request with a wrong token gets an invalid_token error."""
        t = TcpTransport(
            host=loopback.host,
            port=loopback.port,
            token="wrong-token",
        )
        await t.connect()
        try:
            client = KnxBridgeClient(t)
            with pytest.raises(KnxBridgeError, match="invalid_token"):
                await client.project_info()
        finally:
            await t.close()

    async def test_mutation_over_tcp(self, tcp_client: KnxBridgeClient) -> None:
        """Mutations (ga.create) work and bump the project revision."""
        info_before = await tcp_client.project_info()
        rev_before = info_before["revision"]

        result = await tcp_client.create_group_address(
            name="Test GA", address="2/0/1",
        )
        assert "ref" in result
        assert result["address"] == "2/0/1"

        info_after = await tcp_client.project_info()
        assert info_after["revision"] != rev_before


class TestTcpReconnect:
    """Test the client-level reconnect-on-failure logic over TCP."""

    async def test_reconnect_after_server_restart(
        self, loopback: _LoopbackServer,
    ) -> None:
        """After the server drops and restarts, the client reconnects once."""
        t = TcpTransport(
            host=loopback.host,
            port=loopback.port,
            token=loopback._mock.token,
            read_timeout=2.0,
            connect_timeout=2.0,
        )
        await t.connect()
        client = KnxBridgeClient(t)

        # Sanity: first call succeeds.
        info = await client.project_info()
        assert info["projectId"] == "proj-001"

        # Restart the server (same port).
        port = loopback.port
        await loopback.stop()
        # Re-create server on the same port.
        loopback._server = await asyncio.start_server(
            loopback._handle_client, loopback.host, port,
        )
        loopback.port = port

        # The next call should trigger one reconnect and succeed.
        info2 = await client.project_info()
        assert info2["projectId"] == "proj-001"

        await t.close()
