"""Tests for Phase A mutation tools via FastMCP in-memory Client."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_delete_group_address --------------------------------------------------

async def test_delete_group_address(client: Client) -> None:
    result = await client.call_tool("knx_delete_group_address", {
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    # Verify it's gone
    list_result = await client.call_tool("knx_list_group_addresses", {}, raise_on_error=False)
    gas = tool_data(list_result)
    assert all(ga["ref"] != "ga-0001" for ga in gas)


async def test_delete_group_address_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_group_address", {
        "ga_ref": "ga-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_rename_group_address --------------------------------------------------

async def test_rename_group_address(client: Client) -> None:
    result = await client.call_tool("knx_rename_group_address", {
        "ga_ref": "ga-0001",
        "name": "Renamed GA",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "Renamed GA"
    assert data["ref"] == "ga-0001"


async def test_rename_group_address_not_found(client: Client) -> None:
    result = await client.call_tool("knx_rename_group_address", {
        "ga_ref": "ga-9999",
        "name": "X",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_set_group_address_description -----------------------------------------

async def test_set_group_address_description(client: Client) -> None:
    result = await client.call_tool("knx_set_group_address_description", {
        "ga_ref": "ga-0001",
        "description": "Controls the living room light",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["description"] == "Controls the living room light"


# -- knx_set_group_address_dpt ------------------------------------------------

async def test_set_group_address_dpt(client: Client) -> None:
    result = await client.call_tool("knx_set_group_address_dpt", {
        "ga_ref": "ga-0001",
        "dpt_main": 5,
        "dpt_sub": 1,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["dpt"] == "5.001"


async def test_set_group_address_dpt_not_found(client: Client) -> None:
    result = await client.call_tool("knx_set_group_address_dpt", {
        "ga_ref": "ga-9999",
        "dpt_main": 1,
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_create_group_range ---------------------------------------------------

async def test_create_group_range(client: Client) -> None:
    result = await client.call_tool("knx_create_group_range", {
        "name": "HVAC",
        "address": 3,
    }, raise_on_error=False)
    data = tool_data(result)
    assert "ref" in data
    assert data["name"] == "HVAC"
    assert data["address"] == 3


async def test_create_group_range_nested(client: Client) -> None:
    # Create parent first
    parent = await client.call_tool("knx_create_group_range", {
        "name": "Parent Group",
        "address": 5,
    }, raise_on_error=False)
    parent_data = tool_data(parent)

    result = await client.call_tool("knx_create_group_range", {
        "name": "Child Group",
        "address": 2,
        "parent_group_range_ref": parent_data["ref"],
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "Child Group"
    assert data["address"] == 2


# -- knx_delete_group_range ---------------------------------------------------

async def test_delete_group_range(client: Client) -> None:
    result = await client.call_tool("knx_delete_group_range", {
        "group_range_ref": "gr-main-1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_group_range_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_group_range", {
        "group_range_ref": "gr-nonexistent",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_delete_device --------------------------------------------------------

async def test_delete_device(client: Client) -> None:
    result = await client.call_tool("knx_delete_device", {
        "device_ref": "dev-0002",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    # Verify it's gone
    list_result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devs = tool_data(list_result)
    assert all(d["ref"] != "dev-0002" for d in devs)


async def test_delete_device_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_device", {
        "device_ref": "dev-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_rename_device --------------------------------------------------------

async def test_rename_device(client: Client) -> None:
    result = await client.call_tool("knx_rename_device", {
        "device_ref": "dev-0001",
        "name": "Renamed Device",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "Renamed Device"
    assert data["ref"] == "dev-0001"


# -- knx_set_device_address ----------------------------------------------------

async def test_set_device_address(client: Client) -> None:
    result = await client.call_tool("knx_set_device_address", {
        "device_ref": "dev-0001",
        "address": "1.1.5",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["address"] == "1.1.5"


# -- knx_move_device -----------------------------------------------------------

async def test_move_device_not_supported(client: Client) -> None:
    result = await client.call_tool("knx_move_device", {
        "device_ref": "dev-0001",
        "line_ref": "line-1.2",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_supported" in error_text(result)


# -- knx_create_area -----------------------------------------------------------

async def test_create_area(client: Client) -> None:
    result = await client.call_tool("knx_create_area", {
        "name": "Building B",
        "address": 2,
    }, raise_on_error=False)
    data = tool_data(result)
    assert "areaRef" in data
    assert data["name"] == "Building B"

    # Verify in topology
    topo = await client.call_tool("knx_topology", {}, raise_on_error=False)
    areas = tool_data(topo)
    assert any(a["name"] == "Building B" for a in areas)


# -- knx_delete_area -----------------------------------------------------------

async def test_delete_area(client: Client) -> None:
    # Create then delete
    create_result = await client.call_tool("knx_create_area", {
        "name": "Temp Area",
        "address": 3,
    }, raise_on_error=False)
    area_ref = tool_data(create_result)["areaRef"]

    result = await client.call_tool("knx_delete_area", {
        "area_ref": area_ref,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_area_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_area", {
        "area_ref": "area-999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_create_line -----------------------------------------------------------

async def test_create_line(client: Client) -> None:
    result = await client.call_tool("knx_create_line", {
        "area_ref": "area-1",
        "name": "Third Line",
        "address": 3,
    }, raise_on_error=False)
    data = tool_data(result)
    assert "lineRef" in data
    assert data["name"] == "Third Line"


async def test_create_line_area_not_found(client: Client) -> None:
    result = await client.call_tool("knx_create_line", {
        "area_ref": "area-999",
        "name": "Orphan",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_delete_line -----------------------------------------------------------

async def test_delete_line(client: Client) -> None:
    result = await client.call_tool("knx_delete_line", {
        "line_ref": "line-1.2",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_line_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_line", {
        "line_ref": "line-99.99",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_set_comobject_flags ---------------------------------------------------

async def test_set_comobject_flags(client: Client) -> None:
    result = await client.call_tool("knx_set_comobject_flags", {
        "com_object_ref": "co-0001",
        "write_flag": False,
        "transmit_flag": True,
        "priority": "High",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ref"] == "co-0001"
    assert data["writeFlag"] is False
    assert data["transmitFlag"] is True
    assert data["priority"] == "High"
    # Unchanged flags should retain their original values
    assert data["readFlag"] is True


async def test_set_comobject_flags_inactive(client: Client) -> None:
    result = await client.call_tool("knx_set_comobject_flags", {
        "com_object_ref": "co-0003",
        "read_flag": True,
    }, raise_on_error=False)
    assert result.is_error
    assert "inactive_object" in error_text(result)


async def test_set_comobject_flags_not_found(client: Client) -> None:
    result = await client.call_tool("knx_set_comobject_flags", {
        "com_object_ref": "co-9999",
        "read_flag": True,
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_set_comobject_flags_invalid_priority(client: Client) -> None:
    result = await client.call_tool("knx_set_comobject_flags", {
        "com_object_ref": "co-0001",
        "priority": "Invalid",
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)
