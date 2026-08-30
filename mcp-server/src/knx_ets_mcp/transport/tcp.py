"""TCP transport for trusted-LAN deployment.

Connects to the ETS6 AddIn over a plain TCP socket (no TLS -- trusted LAN
only).  Sends/receives the same newline-delimited JSON frames as the named
pipe transport, but the token is supplied via configuration (env var
``KNX_BRIDGE_TOKEN``) rather than read from session.json.

All IO is fully async via ``asyncio.open_connection``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from knx_ets_mcp.transport.base import Transport

logger = logging.getLogger(__name__)

# Limits -- mirror pipe.py
_CONNECT_TIMEOUT = 10.0  # seconds
_READ_TIMEOUT = 30.0  # seconds
_MAX_FRAME = 16 * 1024 * 1024  # 16 MB max response frame


class TcpTransport(Transport):
    """Connects to the ETS6 AddIn over a TCP socket."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        *,
        connect_timeout: float = _CONNECT_TIMEOUT,
        read_timeout: float = _READ_TIMEOUT,
    ) -> None:
        # An empty token is allowed and means the AddIn has authentication
        # disabled (its "Token" preference is blank). The token is still sent in
        # each request; the AddIn ignores it when auth is off, and rejects an
        # empty/wrong token with auth_failed when auth is on.
        if not (1 <= port <= 65535):
            raise ValueError(f"Invalid port number: {port}")
        self._host = host
        self._port = port
        self._token = token
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def token(self) -> str:
        return self._token

    # -- Connection ------------------------------------------------------------

    async def connect(self) -> None:
        try:
            # limit=_MAX_FRAME: the StreamReader default line buffer is only
            # 64 KiB, so readuntil("\n") on a large single-line JSON response
            # (e.g. ga.list on a big project) would raise LimitOverrunError.
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port, limit=_MAX_FRAME),
                timeout=self._connect_timeout,
            )
        except TimeoutError:
            raise ConnectionError(
                f"Timed out connecting to ETS6 AddIn at "
                f"{self._host}:{self._port}"
            ) from None
        except OSError as exc:
            raise ConnectionError(
                f"Cannot connect to ETS6 AddIn at "
                f"{self._host}:{self._port}: {exc}"
            ) from exc

        self._reader = reader
        self._writer = writer
        logger.debug("TCP connected to %s:%s", self._host, self._port)

    # -- Send/receive ----------------------------------------------------------

    async def send(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._writer is None or self._reader is None:
            raise ConnectionError("Transport not connected. Call connect() first.")

        line = json.dumps(request, separators=(",", ":")) + "\n"
        self._writer.write(line.encode("utf-8"))
        await self._writer.drain()

        try:
            raw = await asyncio.wait_for(
                self._reader.readuntil(b"\n"),
                timeout=self._read_timeout,
            )
        except TimeoutError:
            raise ConnectionError(
                "Timed out waiting for response from ETS6 AddIn"
            ) from None
        except asyncio.IncompleteReadError:
            raise ConnectionError(
                "Connection closed (EOF) while reading response"
            ) from None
        except asyncio.LimitOverrunError:
            raise ConnectionError(
                f"Response line exceeded the {_MAX_FRAME}-byte buffer limit"
            ) from None

        if len(raw) > _MAX_FRAME:
            raise ConnectionError(
                f"Response exceeded max frame size ({_MAX_FRAME} bytes)"
            )

        return json.loads(raw.decode("utf-8"))

    # -- Close -----------------------------------------------------------------

    async def close(self) -> None:
        """Close the TCP connection and fully reset reader/writer.

        After close(), send() will raise ConnectionError until connect() is
        called again.  Closing discards any partially-read data so a
        de-synced stream cannot poison the next request (Finding #10).
        """
        writer = self._writer
        # Null out immediately so a concurrent send() sees "not connected"
        # rather than writing into a half-closed socket.
        self._writer = None
        self._reader = None
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass  # already closed
