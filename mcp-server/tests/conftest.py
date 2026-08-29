"""Shared fixtures for KNX MCP tests.

Provides both a FastMCP in-memory Client (for end-to-end MCP-protocol tests)
and raw transport/bridge_client fixtures (for transport-isolation tests).
"""

from __future__ import annotations

from typing import Any

import pytest

from fastmcp import Client

from knx_ets_mcp.client import KnxBridgeClient
from knx_ets_mcp.server import mcp as mcp_server, _set_client, _reset_client
from knx_ets_mcp.transport.mock import MockTransport


@pytest.fixture
async def transport() -> MockTransport:
    """A connected MockTransport with seed data."""
    t = MockTransport()
    await t.connect()
    return t


@pytest.fixture
async def bridge_client(transport: MockTransport) -> KnxBridgeClient:
    """A KnxBridgeClient wired to the mock transport (for direct-access tests)."""
    return KnxBridgeClient(transport)


@pytest.fixture
async def client(transport: MockTransport):
    """FastMCP in-memory Client exercising the full MCP tool layer.

    Injects a KnxBridgeClient backed by the mock transport into the
    server singleton, then yields a connected FastMCP Client.
    """
    _set_client(KnxBridgeClient(transport))
    try:
        async with Client(mcp_server) as c:
            yield c
    finally:
        _reset_client()


def tool_data(result) -> Any:
    """Extract structured data from a successful tool call result."""
    assert not result.is_error, f"Expected success but got error: {result.content}"
    return result.data


def error_text(result) -> str:
    """Extract error message from a failed tool call (is_error=True)."""
    assert result.is_error, f"Expected error but got success: {result.data}"
    return result.content[0].text
