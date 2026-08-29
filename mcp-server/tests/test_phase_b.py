"""Tests for Phase B: Building structure + building function tools."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_list_building ---------------------------------------------------------

async def test_list_building(client: Client) -> None:
    result = await client.call_tool("knx_list_building", {}, raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data, list)
    assert len(data) == 1  # One root: Main Building

    bld = data[0]
    assert bld["name"] == "Main Building"
    assert bld["type"] == "Building"
    assert len(bld["children"]) == 1

    floor = bld["children"][0]
    assert floor["name"] == "Ground Floor"
    assert floor["type"] == "Floor"
    assert len(floor["children"]) == 1

    room = floor["children"][0]
    assert room["name"] == "Living Room"
    assert room["type"] == "Room"
    assert "dev-0001" in room["devices"]

    # Check building function in room
    assert len(room["functions"]) == 1
    bf = room["functions"][0]
    assert bf["name"] == "Lighting Control"
    assert "ga-0001" in bf["groupAddresses"]


# -- knx_create_building_part -------------------------------------------------

async def test_create_building_part(client: Client) -> None:
    result = await client.call_tool("knx_create_building_part", {
        "name": "Basement",
        "type": "Floor",
        "parent_building_part_ref": "bp-bld-1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "Basement"
    assert data["type"] == "Floor"
    assert "ref" in data

    # Verify it appears in building list
    list_result = await client.call_tool("knx_list_building", {}, raise_on_error=False)
    bld = tool_data(list_result)[0]
    child_names = [c["name"] for c in bld["children"]]
    assert "Basement" in child_names


async def test_create_building_part_top_level(client: Client) -> None:
    result = await client.call_tool("knx_create_building_part", {
        "name": "Annex",
        "type": "Building",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "Annex"
    assert data["type"] == "Building"


async def test_create_building_part_invalid_type(client: Client) -> None:
    result = await client.call_tool("knx_create_building_part", {
        "name": "Bad",
        "type": "Garage",
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


async def test_create_building_part_parent_not_found(client: Client) -> None:
    result = await client.call_tool("knx_create_building_part", {
        "name": "Orphan",
        "type": "Room",
        "parent_building_part_ref": "bp-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_delete_building_part -------------------------------------------------

async def test_delete_building_part(client: Client) -> None:
    # Create then delete
    create = await client.call_tool("knx_create_building_part", {
        "name": "Temp Room",
        "type": "Room",
        "parent_building_part_ref": "bp-floor-1",
    }, raise_on_error=False)
    ref = tool_data(create)["ref"]

    result = await client.call_tool("knx_delete_building_part", {
        "building_part_ref": ref,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_building_part_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_building_part", {
        "building_part_ref": "bp-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_rename_building_part -------------------------------------------------

async def test_rename_building_part(client: Client) -> None:
    result = await client.call_tool("knx_rename_building_part", {
        "building_part_ref": "bp-room-1",
        "name": "Master Bedroom",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "Master Bedroom"
    assert data["ref"] == "bp-room-1"


async def test_rename_building_part_not_found(client: Client) -> None:
    result = await client.call_tool("knx_rename_building_part", {
        "building_part_ref": "bp-9999",
        "name": "X",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_assign_device / knx_unassign_device ----------------------------------

async def test_assign_device(client: Client) -> None:
    result = await client.call_tool("knx_assign_device", {
        "building_part_ref": "bp-room-1",
        "device_ref": "dev-0002",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    # Verify device appears in listing
    list_result = await client.call_tool("knx_list_building", {}, raise_on_error=False)
    room = tool_data(list_result)[0]["children"][0]["children"][0]
    assert "dev-0002" in room["devices"]


async def test_assign_device_not_found_bp(client: Client) -> None:
    result = await client.call_tool("knx_assign_device", {
        "building_part_ref": "bp-9999",
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_assign_device_not_found_device(client: Client) -> None:
    result = await client.call_tool("knx_assign_device", {
        "building_part_ref": "bp-room-1",
        "device_ref": "dev-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_unassign_device(client: Client) -> None:
    result = await client.call_tool("knx_unassign_device", {
        "building_part_ref": "bp-room-1",
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    # Verify device no longer in listing
    list_result = await client.call_tool("knx_list_building", {}, raise_on_error=False)
    room = tool_data(list_result)[0]["children"][0]["children"][0]
    assert "dev-0001" not in room["devices"]


async def test_unassign_device_not_found(client: Client) -> None:
    result = await client.call_tool("knx_unassign_device", {
        "building_part_ref": "bp-9999",
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_create_building_function ----------------------------------------------

async def test_create_building_function(client: Client) -> None:
    result = await client.call_tool("knx_create_building_function", {
        "building_part_ref": "bp-room-1",
        "name": "HVAC Control",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "HVAC Control"
    assert "ref" in data
    assert data["groupAddresses"] == []


async def test_create_building_function_bp_not_found(client: Client) -> None:
    result = await client.call_tool("knx_create_building_function", {
        "building_part_ref": "bp-9999",
        "name": "X",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_delete_building_function ----------------------------------------------

async def test_delete_building_function(client: Client) -> None:
    # Create then delete
    create = await client.call_tool("knx_create_building_function", {
        "building_part_ref": "bp-room-1",
        "name": "Temp Function",
    }, raise_on_error=False)
    ref = tool_data(create)["ref"]

    result = await client.call_tool("knx_delete_building_function", {
        "building_function_ref": ref,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_building_function_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_building_function", {
        "building_function_ref": "bf-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_link_building_function_ga / knx_unlink_building_function_ga -----------

async def test_link_building_function_ga(client: Client) -> None:
    result = await client.call_tool("knx_link_building_function_ga", {
        "building_function_ref": "bf-0001",
        "group_address_refs": ["ga-0002"],
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    # Verify in building list
    list_result = await client.call_tool("knx_list_building", {}, raise_on_error=False)
    room = tool_data(list_result)[0]["children"][0]["children"][0]
    bf = [f for f in room["functions"] if f["ref"] == "bf-0001"][0]
    assert "ga-0002" in bf["groupAddresses"]


async def test_link_building_function_ga_not_found_bf(client: Client) -> None:
    result = await client.call_tool("knx_link_building_function_ga", {
        "building_function_ref": "bf-9999",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_link_building_function_ga_not_found_ga(client: Client) -> None:
    result = await client.call_tool("knx_link_building_function_ga", {
        "building_function_ref": "bf-0001",
        "group_address_refs": ["ga-9999"],
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_unlink_building_function_ga(client: Client) -> None:
    result = await client.call_tool("knx_unlink_building_function_ga", {
        "building_function_ref": "bf-0001",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    # Verify GA removed
    list_result = await client.call_tool("knx_list_building", {}, raise_on_error=False)
    room = tool_data(list_result)[0]["children"][0]["children"][0]
    bf = [f for f in room["functions"] if f["ref"] == "bf-0001"][0]
    assert "ga-0001" not in bf["groupAddresses"]


async def test_unlink_building_function_ga_not_found(client: Client) -> None:
    result = await client.call_tool("knx_unlink_building_function_ga", {
        "building_function_ref": "bf-9999",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)
