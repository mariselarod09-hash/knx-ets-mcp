"""Named-pipe transport for production use on Windows.

Reads the pipe name and token from %LOCALAPPDATA%\\knx-ets-bridge\\session.json,
connects to the named pipe, and sends/receives newline-delimited JSON.

All blocking file/pipe IO is offloaded to threads so the asyncio event loop
is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from knx_ets_mcp.transport.base import Transport

# Limits
_CONNECT_TIMEOUT = 10.0  # seconds
_READ_TIMEOUT = 30.0  # seconds
_MAX_FRAME = 16 * 1024 * 1024  # 16 MB max response frame


class NamedPipeTransport(Transport):
    """Connects to the ETS6 AddIn over a Windows named pipe.

    Limitation (Finding #10): when ``send()`` times out, the background
    ``asyncio.to_thread`` worker may still be blocked on a pipe read.  There
    is no safe way to cancel a synchronous blocking read from another thread.
    After a timeout the transport is marked *unusable* and ``close()`` drops
    the handle so the stale worker will eventually see EOF or an OS error and
    exit.  The caller (``KnxBridgeClient._send_with_retry``) must close and
    reconnect the transport before retrying.
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise NotImplementedError(
                "NamedPipeTransport is only supported on Windows. "
                "Set KNX_BRIDGE_TRANSPORT=mock for development on other platforms."
            )
        self._token: str = ""
        self._pipe_name: str = ""
        self._pipe_handle: Any = None
        # Set to True after a timeout so we never reuse a de-synced handle
        # (a stale to_thread worker may still be reading from it).
        self._unusable: bool = False

    @property
    def token(self) -> str:
        return self._token

    # -- Connection ------------------------------------------------------------

    async def connect(self) -> None:
        # A fresh connect resets the unusable flag (Finding #10).
        self._unusable = False
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._connect_sync),
                timeout=_CONNECT_TIMEOUT,
            )
        except TimeoutError:
            raise ConnectionError(
                "Timed out connecting to ETS6 AddIn pipe"
            ) from None

    def _connect_sync(self) -> None:
        session_path = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "knx-ets-bridge",
            "session.json",
        )
        # Tolerate UTF-8 BOM from the net48 AddIn (utf-8-sig strips it).
        with open(session_path, encoding="utf-8-sig") as f:
            session = json.load(f)

        self._pipe_name = session["pipeName"]
        self._token = session["token"]

        pipe_path = rf"\\.\pipe\{self._pipe_name}"
        self._pipe_handle = open(pipe_path, "r+b", buffering=0)  # noqa: SIM115

    # -- Send/receive ----------------------------------------------------------

    async def send(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._pipe_handle is None:
            raise ConnectionError("Transport not connected. Call connect() first.")
        if self._unusable:
            raise ConnectionError(
                "Transport is unusable after a previous timeout. "
                "Close and reconnect before retrying."
            )

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._send_sync, request),
                timeout=_READ_TIMEOUT,
            )
        except TimeoutError:
            # The to_thread worker may still be blocked reading from the
            # handle.  Mark the transport unusable so the caller closes and
            # reconnects rather than sending another request on this
            # potentially de-synced handle (Finding #10).
            self._unusable = True
            raise ConnectionError(
                "Timed out waiting for response from ETS6 AddIn"
            ) from None

    def _send_sync(self, request: dict[str, Any]) -> dict[str, Any]:
        line = json.dumps(request, separators=(",", ":")) + "\n"
        self._pipe_handle.write(line.encode("utf-8"))
        self._pipe_handle.flush()

        # Line-buffered read into a bytearray (no quadratic concat).
        buf = bytearray()
        while len(buf) < _MAX_FRAME:
            chunk = self._pipe_handle.read(1)
            if not chunk:
                raise ConnectionError("Pipe closed (EOF) while reading response")
            if chunk == b"\n":
                break
            buf.extend(chunk)
        else:
            raise ConnectionError(
                f"Response exceeded max frame size ({_MAX_FRAME} bytes)"
            )

        # Tolerate UTF-8 BOM from the AddIn (utf-8-sig strips it).
        return json.loads(buf.decode("utf-8-sig"))

    # -- Close -----------------------------------------------------------------

    async def close(self) -> None:
        # Closing the handle will cause any stale to_thread worker that is
        # still blocked on a read to receive EOF or an OS error (Finding #10).
        if self._pipe_handle is not None:
            try:
                self._pipe_handle.close()
            except OSError:
                pass  # already closed
            self._pipe_handle = None
        self._unusable = False
