"""Tests for reconciliation methods: device.unassign and device.compare."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_device_unassign (device.unassign) ------------------------------------

async def test_device_unassign(client: Client) -> None:
    """Unassigning a device detaches it from its line (ok + revision bump)."""
    result = await client.call_tool("knx_device_unassign", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    # Verify the device still exists but has no line
    list_result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(list_result)
    dev = next((d for d in devices if d["ref"] == "dev-0001"), None)
    assert dev is not None
    assert dev["line"] is None


async def test_device_unassign_not_found(client: Client) -> None:
    """Unassigning a non-existent device returns not_found."""
    result = await client.call_tool("knx_device_unassign", {
        "device_ref": "dev-NOPE",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_device_unassign_idempotent(client: Client) -> None:
    """Unassigning an already-unassigned device succeeds (idempotent)."""
    # First unassign
    await client.call_tool("knx_device_unassign", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    # Second unassign should still succeed
    result = await client.call_tool("knx_device_unassign", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- knx_device_compare (device.compare) --------------------------------------

async def test_device_compare(client: Client) -> None:
    """Comparing a device returns {compared, partial, note}."""
    result = await client.call_tool("knx_device_compare", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data["compared"], list)
    assert len(data["compared"]) > 0
    assert data["partial"] is True
    assert isinstance(data["note"], str)

    # Check shape of each comparison entry
    for entry in data["compared"]:
        assert "property" in entry
        assert "equal" in entry
        assert "expected" in entry
        assert "actual" in entry


async def test_device_compare_not_found(client: Client) -> None:
    """Comparing a non-existent device returns not_found."""
    result = await client.call_tool("knx_device_compare", {
        "device_ref": "dev-NOPE",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_device_compare_is_read(client: Client, transport) -> None:
    """device.compare is a read -- it must NOT bump the revision."""
    rev_before = transport._revision
    await client.call_tool("knx_device_compare", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    assert transport._revision == rev_before
