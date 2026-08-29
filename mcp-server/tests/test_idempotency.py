"""Tests for idempotency-key handling via FastMCP in-memory Client.

Key behavioral change: idempotency keys are now fresh UUID4s per operation
(not sha256 of method+params).  Two separate tool calls with identical params
get DIFFERENT keys and therefore execute independently.  This makes sequences
like link -> unlink -> link work correctly.
"""

from __future__ import annotations

from fastmcp import Client

from knx_ets_mcp.client import KnxBridgeClient
from knx_ets_mcp.server import mcp as mcp_server, _set_client, _reset_client
from knx_ets_mcp.transport.mock import MockTransport

from tests.conftest import tool_data, error_text


async def test_different_params_different_result(client: Client) -> None:
    """Different params produce distinct resources (sanity check)."""
    r1 = await client.call_tool("knx_create_group_address", {
        "name": "GA One",
        "address": "3/0/1",
    }, raise_on_error=False)
    r2 = await client.call_tool("knx_create_group_address", {
        "name": "GA Two",
        "address": "3/0/2",
    }, raise_on_error=False)
    d1 = tool_data(r1)
    d2 = tool_data(r2)
    assert d1["ref"] != d2["ref"]
    assert d1["address"] != d2["address"]


async def test_idempotency_cache_is_per_transport() -> None:
    """Two transports (= two AddIn instances) have independent caches."""
    t1 = MockTransport()
    await t1.connect()
    t2 = MockTransport()
    await t2.connect()

    # Test with transport 1
    _set_client(KnxBridgeClient(t1))
    try:
        async with Client(mcp_server) as c1:
            r1 = await c1.call_tool("knx_create_group_address", {
                "name": "Shared GA",
                "address": "4/0/1",
            }, raise_on_error=False)
    finally:
        _reset_client()

    # Test with transport 2
    _set_client(KnxBridgeClient(t2))
    try:
        async with Client(mcp_server) as c2:
            r2 = await c2.call_tool("knx_create_group_address", {
                "name": "Shared GA",
                "address": "4/0/1",
            }, raise_on_error=False)
    finally:
        _reset_client()

    d1 = tool_data(r1)
    d2 = tool_data(r2)

    # Different transports -> different refs
    assert d1["ref"] != d2["ref"]


async def test_link_unlink_link_executes_both_links(client: Client) -> None:
    """State-transition test: link -> unlink -> link must execute both links.

    With fresh-UUID4 idempotency keys, the second link gets a different key
    than the first and is therefore NOT deduplicated.  This was broken when
    keys were sha256(method+params).
    """
    args_link = {"com_object_ref": "co-0002", "ga_ref": "ga-0002"}
    args_unlink = {"com_object_ref": "co-0002", "ga_ref": "ga-0002"}

    # 1. Link
    r1 = await client.call_tool("knx_link", args_link, raise_on_error=False)
    assert not r1.is_error
    assert tool_data(r1)["ok"] is True

    # Verify link exists
    cos = tool_data(await client.call_tool("knx_list_comobjects", {"device_ref": "dev-0001"}, raise_on_error=False))
    co2 = next(co for co in cos if co["ref"] == "co-0002")
    assert "ga-0002" in co2["links"]

    # 2. Unlink
    r2 = await client.call_tool("knx_unlink", args_unlink, raise_on_error=False)
    assert not r2.is_error
    assert tool_data(r2)["ok"] is True

    # Verify link is gone
    cos = tool_data(await client.call_tool("knx_list_comobjects", {"device_ref": "dev-0001"}, raise_on_error=False))
    co2 = next(co for co in cos if co["ref"] == "co-0002")
    assert "ga-0002" not in co2["links"]

    # 3. Re-link (must succeed, not be deduplicated)
    r3 = await client.call_tool("knx_link", args_link, raise_on_error=False)
    assert not r3.is_error
    assert tool_data(r3)["ok"] is True

    # Verify link is back
    cos = tool_data(await client.call_tool("knx_list_comobjects", {"device_ref": "dev-0001"}, raise_on_error=False))
    co2 = next(co for co in cos if co["ref"] == "co-0002")
    assert "ga-0002" in co2["links"]


async def test_identical_ga_creates_are_distinct(client: Client) -> None:
    """Two identical ga.create calls now get different keys and both execute.

    The second call fails because the address already exists (duplicate
    check in the mock), proving that it was NOT deduplicated by idempotency.
    """
    r1 = await client.call_tool("knx_create_group_address", {
        "name": "Same GA",
        "address": "2/0/1",
    }, raise_on_error=False)
    d1 = tool_data(r1)
    assert "ref" in d1

    # Second identical call: hits duplicate-address check (not idempotency cache)
    r2 = await client.call_tool("knx_create_group_address", {
        "name": "Same GA",
        "address": "2/0/1",
    }, raise_on_error=False)
    assert r2.is_error
    assert "invalid_params" in error_text(r2)
    assert "already exists" in error_text(r2)


async def test_idempotency_replay_before_revision_check(transport: MockTransport) -> None:
    """Idempotency cache replay must happen BEFORE the revision check.

    Scenario: a mutation succeeds but the response is lost.  The client
    retries with the same idempotencyKey AND the original (now stale)
    expectedProjectRevision.  The AddIn must return the cached success,
    not a revision_mismatch error.
    """
    import uuid

    req_id = str(uuid.uuid4())
    idem_key = str(uuid.uuid4())

    # First call: succeeds, bumps revision from rev-001 to rev-002.
    req1 = {
        "id": req_id,
        "token": transport.token,
        "method": "ga.create",
        "params": {"name": "Replay GA", "address": "6/0/1"},
        "idempotencyKey": idem_key,
        "expectedProjectRevision": "rev-001",
    }
    resp1 = await transport.send(req1)
    assert resp1["ok"] is True
    assert resp1["projectRevision"] == "rev-002"

    # Retry with a new request id but same idempotencyKey and old revision.
    req2 = {
        "id": str(uuid.uuid4()),
        "token": transport.token,
        "method": "ga.create",
        "params": {"name": "Replay GA", "address": "6/0/1"},
        "idempotencyKey": idem_key,
        "expectedProjectRevision": "rev-001",  # stale!
    }
    resp2 = await transport.send(req2)
    # Must be a cache hit (success), NOT revision_mismatch.
    assert resp2["ok"] is True
    assert resp2["result"] == resp1["result"]
