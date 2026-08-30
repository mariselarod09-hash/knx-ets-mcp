"""Tests for batch.apply: many project mutations in one atomic undo marker.

Covers success, atomic rollback on failure, best-effort (atomic=false), and
validation (empty, non-batchable method, unknown method, nested batch).
"""

from __future__ import annotations

from fastmcp import Client

from knx_ets_mcp.client import KnxBridgeClient
from knx_ets_mcp.transport.mock import MockTransport

from tests.conftest import tool_data, error_text


async def _ga_count(bc: KnxBridgeClient) -> int:
    return len(await bc.list_group_addresses())


async def test_batch_success_atomic(bridge_client: KnxBridgeClient) -> None:
    before = await _ga_count(bridge_client)
    res = await bridge_client.batch_apply([
        {"method": "ga.create", "params": {"name": "Batch A", "address": "6/0/1"}},
        {"method": "ga.create", "params": {"name": "Batch B", "address": "6/0/2"}},
    ])
    assert res["applied"] is True
    assert res["atomic"] is True
    assert res["rolledBack"] is False
    assert res["total"] == 2 and res["ok"] == 2 and res["failed"] == 0 and res["skipped"] == 0
    assert [r["status"] for r in res["results"]] == ["ok", "ok"]
    assert await _ga_count(bridge_client) == before + 2


async def test_batch_atomic_rollback_on_failure(bridge_client: KnxBridgeClient) -> None:
    before = await _ga_count(bridge_client)
    res = await bridge_client.batch_apply([
        {"method": "ga.create", "params": {"name": "Rollback A", "address": "6/1/1"}},
        {"method": "ga.rename", "params": {"groupAddressRef": "does-not-exist", "name": "X"}},
        {"method": "ga.create", "params": {"name": "Never", "address": "6/1/9"}},
    ])
    assert res["applied"] is False
    assert res["rolledBack"] is True
    assert res["ok"] == 1 and res["failed"] == 1 and res["skipped"] == 1
    statuses = [r["status"] for r in res["results"]]
    assert statuses == ["ok", "error", "skipped"]
    assert res["results"][1]["error"]["code"] == "not_found"
    # Rollback: the GA created in step 0 must be gone, and step 2 never ran.
    assert await _ga_count(bridge_client) == before
    names = [g["name"] for g in await bridge_client.list_group_addresses()]
    assert "Rollback A" not in names and "Never" not in names


async def test_batch_continue_on_error(bridge_client: KnxBridgeClient) -> None:
    before = await _ga_count(bridge_client)
    res = await bridge_client.batch_apply([
        {"method": "ga.create", "params": {"name": "Best A", "address": "6/2/1"}},
        {"method": "ga.rename", "params": {"groupAddressRef": "nope", "name": "X"}},
        {"method": "ga.create", "params": {"name": "Best B", "address": "6/2/2"}},
    ], atomic=False)
    assert res["applied"] is True
    assert res["rolledBack"] is False
    assert res["ok"] == 2 and res["failed"] == 1 and res["skipped"] == 0
    assert [r["status"] for r in res["results"]] == ["ok", "error", "ok"]
    # Both valid steps persisted despite the failure in the middle.
    assert await _ga_count(bridge_client) == before + 2


async def test_batch_rejects_empty(bridge_client: KnxBridgeClient) -> None:
    try:
        await bridge_client.batch_apply([])
        assert False, "expected error"
    except Exception as exc:  # KnxBridgeError
        assert "operations" in str(exc).lower()


async def test_batch_rejects_non_batchable_method(bridge_client: KnxBridgeClient) -> None:
    try:
        await bridge_client.batch_apply([
            {"method": "device.program", "params": {"deviceRef": "d1"}},
        ])
        assert False, "expected error"
    except Exception as exc:
        assert "not allowed" in str(exc).lower()


async def test_batch_rejects_nested_batch(bridge_client: KnxBridgeClient) -> None:
    try:
        await bridge_client.batch_apply([
            {"method": "batch.apply", "params": {"operations": []}},
        ])
        assert False, "expected error"
    except Exception as exc:
        assert "not allowed" in str(exc).lower()


async def test_batch_rejects_unknown_method(bridge_client: KnxBridgeClient) -> None:
    try:
        await bridge_client.batch_apply([
            {"method": "foo.bar", "params": {}},
        ])
        assert False, "expected error"
    except Exception as exc:
        assert "not allowed" in str(exc).lower()


async def test_batch_via_mcp_tool(client: Client) -> None:
    """End-to-end through the FastMCP tool layer."""
    res = await client.call_tool("knx_batch_apply", {
        "operations": [
            {"method": "ga.create", "params": {"name": "Tool A", "address": "6/3/1"}},
            {"method": "ga.create", "params": {"name": "Tool B", "address": "6/3/2"}},
        ],
    }, raise_on_error=False)
    data = tool_data(res)
    assert data["applied"] is True and data["ok"] == 2


async def test_rename_reports_truncation(bridge_client: KnxBridgeClient) -> None:
    # Create a GA, then rename it to a name longer than the mock's limit.
    ga = await bridge_client.create_group_address("Short", "6/5/1")
    long_name = "X" * 80
    res = await bridge_client.rename_group_address(ga["ref"], long_name)
    assert res.get("truncated") is True
    assert len(res["name"]) < len(long_name)


async def test_rename_no_truncation_flag_when_within_limit(bridge_client: KnxBridgeClient) -> None:
    ga = await bridge_client.create_group_address("Short2", "6/5/2")
    res = await bridge_client.rename_group_address(ga["ref"], "Still short")
    assert "truncated" not in res  # omitted when not truncated


async def test_batch_via_mcp_tool_rollback(client: Client) -> None:
    res = await client.call_tool("knx_batch_apply", {
        "operations": [
            {"method": "ga.create", "params": {"name": "T Roll", "address": "6/4/1"}},
            {"method": "ga.rename", "params": {"groupAddressRef": "missing", "name": "X"}},
        ],
    }, raise_on_error=False)
    data = tool_data(res)
    assert data["applied"] is False and data["rolledBack"] is True
    assert data["results"][0]["status"] == "ok"
    assert data["results"][1]["status"] == "error"


# -- validate_only: read-only pre-flight ---------------------------------------

async def test_batch_validate_only_all_valid(bridge_client: KnxBridgeClient) -> None:
    before = await _ga_count(bridge_client)
    res = await bridge_client.batch_apply([
        {"method": "ga.create", "params": {"name": "Pre A", "address": "7/0/9"}},
        {"method": "link.create", "params": {"comObjectRef": "co-0002", "gaRef": "ga-0001"}},
    ], validate_only=True)
    assert res["validated"] is True
    assert res["total"] == 2 and res["valid"] == 2 and res["invalid"] == 0
    assert all(r["valid"] and r["issues"] == [] for r in res["results"])
    # Nothing was mutated.
    assert await _ga_count(bridge_client) == before


async def test_batch_validate_only_reports_issues(bridge_client: KnxBridgeClient) -> None:
    before = await _ga_count(bridge_client)
    res = await bridge_client.batch_apply([
        {"method": "link.create", "params": {"comObjectRef": "co-9999", "gaRef": "ga-0001"}},
        {"method": "link.create", "params": {"comObjectRef": "co-0003", "gaRef": "ga-0001"}},
        {"method": "ga.create", "params": {"name": "Dup", "address": "1/0/1"}},
        {"method": "link.create", "params": {"comObjectRef": "co-0004", "gaRef": "ga-0001"}},
        {"method": "ga.create", "params": {"name": "NoAddr"}},
    ], validate_only=True)
    assert res["validated"] is True
    assert res["total"] == 5 and res["valid"] == 0 and res["invalid"] == 5
    r = res["results"]
    assert "not found" in " ".join(r[0]["issues"]).lower()          # co-9999 missing
    assert "inactive" in " ".join(r[1]["issues"]).lower()           # co-0003 inactive
    assert "already exists" in " ".join(r[2]["issues"]).lower()     # 1/0/1 collision
    assert "mismatch" in " ".join(r[3]["issues"]).lower()           # 5.001 vs 1.001
    assert "missing required parameter" in " ".join(r[4]["issues"]).lower()  # no address
    # Read-only: no GA created despite the ga.create ops.
    assert await _ga_count(bridge_client) == before
