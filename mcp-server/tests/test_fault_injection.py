"""Fault-injection tests for the client layer.

Tests transport failures (timeout, EOF, malformed responses, id mismatches)
and verifies that the client handles them correctly with retry and validation.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from knx_ets_mcp.client import KnxBridgeClient, KnxBridgeError
from knx_ets_mcp.transport.base import Transport


class FakeTransport(Transport):
    """Transport that returns pre-configured responses or raises errors."""

    def __init__(self) -> None:
        self._token = "fake-token"
        self._responses: list[dict[str, Any] | Exception] = []
        self._call_count = 0
        self.connect = AsyncMock()

    @property
    def token(self) -> str:
        return self._token

    async def connect(self) -> None:
        pass  # overridden by AsyncMock above

    async def send(self, request: dict[str, Any]) -> dict[str, Any]:
        idx = self._call_count
        self._call_count += 1
        if idx < len(self._responses):
            resp = self._responses[idx]
            if isinstance(resp, Exception):
                raise resp
            return resp
        raise RuntimeError(f"FakeTransport: no response configured for call {idx}")

    async def close(self) -> None:
        pass

    def enqueue(self, *responses: dict[str, Any] | Exception) -> None:
        """Queue responses/errors to be returned by successive send() calls."""
        self._responses.extend(responses)


# -- Malformed response --------------------------------------------------------

async def test_malformed_response_not_dict() -> None:
    """A response that is not a dict must raise KnxBridgeError."""
    t = FakeTransport()
    # The transport returns a list instead of a dict -- simulate by
    # patching send to return something non-dict.  Since our FakeTransport
    # always returns from the queue, we monkey-patch.
    t._responses.append("this is not a dict")  # type: ignore[arg-type]
    client = KnxBridgeClient(t)

    with pytest.raises(KnxBridgeError, match="not a JSON object"):
        await client.project_info()


async def test_response_id_mismatch() -> None:
    """A response with a mismatched id must be rejected."""
    t = FakeTransport()
    t.enqueue({
        "id": "wrong-id-not-matching",
        "ok": True,
        "result": {"projectId": "p1"},
        "projectRevision": "rev-001",
    })
    client = KnxBridgeClient(t)

    with pytest.raises(KnxBridgeError, match="id mismatch"):
        await client.project_info()


async def test_success_response_missing_result() -> None:
    """A success response (ok=True) without 'result' must be rejected."""
    t = FakeTransport()
    # We need to match the request id, so we intercept and build the response.
    original_send = t.send

    async def patched_send(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": request["id"],
            "ok": True,
            # "result" is missing
            "projectRevision": "rev-001",
        }

    t.send = patched_send  # type: ignore[assignment]
    client = KnxBridgeClient(t)

    with pytest.raises(KnxBridgeError, match="missing result"):
        await client.project_info()


async def test_error_response_missing_error() -> None:
    """An error response (ok=False) without 'error' must be rejected."""
    t = FakeTransport()
    original_send = t.send

    async def patched_send(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": request["id"],
            "ok": False,
            # "error" is missing
        }

    t.send = patched_send  # type: ignore[assignment]
    client = KnxBridgeClient(t)

    with pytest.raises(KnxBridgeError, match="missing error"):
        await client.project_info()


# -- Transport failure and retry -----------------------------------------------

async def test_retry_on_transport_failure() -> None:
    """Client retries once on transport failure with reconnect."""
    t = FakeTransport()
    call_count = 0

    async def send_with_failure(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("pipe broken")
        return {
            "id": request["id"],
            "ok": True,
            "result": {"projectId": "proj-001", "name": "Test", "groupAddressStyle": "ThreeLevel", "revision": "rev-001"},
            "projectRevision": "rev-001",
        }

    t.send = send_with_failure  # type: ignore[assignment]
    client = KnxBridgeClient(t)

    result = await client.project_info()
    assert result["projectId"] == "proj-001"
    assert call_count == 2
    # connect() was called for reconnect
    t.connect.assert_called_once()


async def test_retry_exhausted_raises() -> None:
    """If both the original send and the retry fail, KnxBridgeError is raised."""
    t = FakeTransport()

    async def always_fail(request: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionError("pipe permanently broken")

    t.send = always_fail  # type: ignore[assignment]
    client = KnxBridgeClient(t)

    with pytest.raises(KnxBridgeError, match="Transport failed after reconnect"):
        await client.project_info()


async def test_retry_reuses_idempotency_key() -> None:
    """On transport retry, the same idempotency key is reused."""
    t = FakeTransport()
    captured_requests: list[dict[str, Any]] = []

    async def capture_send(request: dict[str, Any]) -> dict[str, Any]:
        captured_requests.append(request)
        if len(captured_requests) == 1:
            raise ConnectionError("transient failure")
        return {
            "id": request["id"],
            "ok": True,
            "result": {"ref": "ga-new", "address": "7/0/1"},
            "projectRevision": "rev-002",
        }

    t.send = capture_send  # type: ignore[assignment]
    client = KnxBridgeClient(t)

    result = await client.create_group_address("Test GA", "7/0/1")
    assert result["ref"] == "ga-new"

    # Both attempts used the same request (same id and idempotency key)
    assert len(captured_requests) == 2
    assert captured_requests[0]["id"] == captured_requests[1]["id"]
    assert captured_requests[0]["idempotencyKey"] == captured_requests[1]["idempotencyKey"]


async def test_reconnect_failure_raises() -> None:
    """If reconnect itself fails, the original error surfaces."""
    t = FakeTransport()

    async def fail_send(request: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionError("pipe broken")

    t.send = fail_send  # type: ignore[assignment]
    t.connect = AsyncMock(side_effect=OSError("cannot reconnect"))
    client = KnxBridgeClient(t)

    with pytest.raises(KnxBridgeError, match="reconnect unsuccessful"):
        await client.project_info()
