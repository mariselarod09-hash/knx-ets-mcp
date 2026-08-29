"""Tests for Phase G (v0.1.13) methods: moves, additional addresses,
segments, trades, bus interface, param default, certificates, navigation,
tags, todos, channels, project history, device reset."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- device.reset (bus write) ------------------------------------------------

async def test_device_reset(client: Client) -> None:
    result = await client.call_tool("knx_device_reset", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_device_reset_not_found(client: Client) -> None:
    result = await client.call_tool("knx_device_reset", {
        "device_ref": "dev-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- groupRange.moveGroupAddress [M] -----------------------------------------

async def test_move_group_address(client: Client) -> None:
    result = await client.call_tool("knx_move_group_address", {
        "target_group_range_ref": "gr-main-1",
        "group_address_ref": "ga-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_move_group_address_ga_not_found(client: Client) -> None:
    result = await client.call_tool("knx_move_group_address", {
        "target_group_range_ref": "gr-main-1",
        "group_address_ref": "ga-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- groupRange.moveGroupRange [M] -------------------------------------------

async def test_move_group_range(client: Client) -> None:
    result = await client.call_tool("knx_move_group_range", {
        "target_group_range_ref": "gr-main-1",
        "source_group_range_ref": "gr-mid-1-0",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_move_group_range_not_found(client: Client) -> None:
    result = await client.call_tool("knx_move_group_range", {
        "target_group_range_ref": "gr-main-1",
        "source_group_range_ref": "gr-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- line.move [M] -----------------------------------------------------------

async def test_move_line(client: Client) -> None:
    # Create a second area first
    create_result = await client.call_tool("knx_create_area", {
        "name": "Area B",
    }, raise_on_error=False)
    area_b = tool_data(create_result)

    result = await client.call_tool("knx_move_line", {
        "target_area_ref": area_b["areaRef"],
        "line_ref": "line-1.2",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_move_line_not_found(client: Client) -> None:
    result = await client.call_tool("knx_move_line", {
        "target_area_ref": "area-1",
        "line_ref": "line-9.9",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- buildingPart.move [M] ---------------------------------------------------

async def test_move_building_part(client: Client) -> None:
    result = await client.call_tool("knx_move_building_part", {
        "target_building_part_ref": "bp-bld-1",
        "source_building_part_ref": "bp-room-1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_move_building_part_not_found(client: Client) -> None:
    result = await client.call_tool("knx_move_building_part", {
        "target_building_part_ref": "bp-bld-1",
        "source_building_part_ref": "bp-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- device.addAdditionalAddress [M] -----------------------------------------

async def test_add_device_additional_address(client: Client) -> None:
    result = await client.call_tool("knx_add_device_additional_address", {
        "device_ref": "dev-0001",
        "address": 20,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["deviceRef"] == "dev-0001"
    assert data["address"] == 20


async def test_add_device_additional_address_duplicate(client: Client) -> None:
    # Address 10 already exists in seed data
    result = await client.call_tool("knx_add_device_additional_address", {
        "device_ref": "dev-0001",
        "address": 10,
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- device.removeAdditionalAddress [M] --------------------------------------

async def test_remove_device_additional_address(client: Client) -> None:
    # Address 10 exists in seed data
    result = await client.call_tool("knx_remove_device_additional_address", {
        "device_ref": "dev-0001",
        "address": 10,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_remove_device_additional_address_not_found(client: Client) -> None:
    result = await client.call_tool("knx_remove_device_additional_address", {
        "device_ref": "dev-0001",
        "address": 99,
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- line.addAdditionalGroupAddress [M] --------------------------------------

async def test_add_line_additional_ga(client: Client) -> None:
    result = await client.call_tool("knx_add_line_additional_ga", {
        "line_ref": "line-1.1",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_add_line_additional_ga_line_not_found(client: Client) -> None:
    result = await client.call_tool("knx_add_line_additional_ga", {
        "line_ref": "line-9.9",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- line.removeAdditionalGroupAddress [M] -----------------------------------

async def test_remove_line_additional_ga(client: Client) -> None:
    # First add, then remove
    await client.call_tool("knx_add_line_additional_ga", {
        "line_ref": "line-1.1",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    result = await client.call_tool("knx_remove_line_additional_ga", {
        "line_ref": "line-1.1",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- segment.create [M] ------------------------------------------------------

async def test_create_segment(client: Client) -> None:
    result = await client.call_tool("knx_create_segment", {
        "line_ref": "line-1.1",
        "name": "New Segment",
        "medium_type": "TP",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "segmentRef" in data
    assert data["name"] == "New Segment"


async def test_create_segment_line_not_found(client: Client) -> None:
    result = await client.call_tool("knx_create_segment", {
        "line_ref": "line-9.9",
        "name": "Seg",
        "medium_type": "TP",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- segment.delete [M] ------------------------------------------------------

async def test_delete_segment(client: Client) -> None:
    # Seed has seg-1.1.0
    result = await client.call_tool("knx_delete_segment", {
        "segment_ref": "seg-1.1.0",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_segment_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_segment", {
        "segment_ref": "seg-9.9.9",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- trades.list [R] ---------------------------------------------------------

async def test_list_trades(client: Client) -> None:
    result = await client.call_tool("knx_list_trades", {}, raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    t = data[0]
    assert "ref" in t
    assert "name" in t
    assert "number" in t
    assert "devices" in t


# -- trade.create [M] --------------------------------------------------------

async def test_create_trade(client: Client) -> None:
    result = await client.call_tool("knx_create_trade", {
        "name": "Electrical",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["name"] == "Electrical"
    assert "ref" in data
    assert "number" in data


# -- trade.delete [M] --------------------------------------------------------

async def test_delete_trade(client: Client) -> None:
    result = await client.call_tool("knx_delete_trade", {
        "trade_ref": "trade-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_trade_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_trade", {
        "trade_ref": "trade-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- trade.assignDevice [M] --------------------------------------------------

async def test_trade_assign_device(client: Client) -> None:
    result = await client.call_tool("knx_trade_assign_device", {
        "trade_ref": "trade-0001",
        "device_ref": "dev-0002",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_trade_assign_device_trade_not_found(client: Client) -> None:
    result = await client.call_tool("knx_trade_assign_device", {
        "trade_ref": "trade-9999",
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- trade.unassignDevice [M] ------------------------------------------------

async def test_trade_unassign_device(client: Client) -> None:
    # dev-0001 is assigned in seed data
    result = await client.call_tool("knx_trade_unassign_device", {
        "trade_ref": "trade-0001",
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- busInterface.link [M] ---------------------------------------------------

async def test_bus_interface_link(client: Client) -> None:
    result = await client.call_tool("knx_bus_interface_link", {
        "device_ref": "dev-0001",
        "group_address_refs": ["ga-0001", "ga-0002"],
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_bus_interface_link_device_not_found(client: Client) -> None:
    result = await client.call_tool("knx_bus_interface_link", {
        "device_ref": "dev-9999",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- busInterface.unlink [M] -------------------------------------------------

async def test_bus_interface_unlink(client: Client) -> None:
    # First link, then unlink
    await client.call_tool("knx_bus_interface_link", {
        "device_ref": "dev-0001",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    result = await client.call_tool("knx_bus_interface_unlink", {
        "device_ref": "dev-0001",
        "group_address_refs": ["ga-0001"],
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- param.setDefault [M] ----------------------------------------------------

async def test_set_parameter_default(client: Client) -> None:
    # param-0002 has value "3", default "5"
    result = await client.call_tool("knx_set_parameter_default", {
        "device_ref": "dev-0002",
        "parameter_ref": "param-0002",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_set_parameter_default_wrong_device(client: Client) -> None:
    result = await client.call_tool("knx_set_parameter_default", {
        "device_ref": "dev-0001",
        "parameter_ref": "param-0002",  # belongs to dev-0002
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- certificates.list [R] ---------------------------------------------------

async def test_list_certificates(client: Client) -> None:
    result = await client.call_tool("knx_list_certificates", {},
                                    raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    c = data[0]
    assert "ref" in c
    assert "serialNumber" in c
    assert "deviceRef" in c
    # hasDevice must NOT be present (no secrets in cert DTO)
    assert "hasDevice" not in c


# -- certificate.add [M] -----------------------------------------------------

async def test_add_certificate(client: Client) -> None:
    result = await client.call_tool("knx_add_certificate", {
        "value": "ABCDE12345FGHIJ67890KLMNO",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "ref" in data


# -- certificate.delete [M] --------------------------------------------------

async def test_delete_certificate(client: Client) -> None:
    result = await client.call_tool("knx_delete_certificate", {
        "certificate_ref": "cert-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_certificate_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_certificate", {
        "certificate_ref": "cert-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- project.navigateTo [R/command] ------------------------------------------

async def test_navigate_to(client: Client) -> None:
    result = await client.call_tool("knx_navigate_to", {
        "ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- tags.list [R] -----------------------------------------------------------

async def test_list_tags(client: Client) -> None:
    result = await client.call_tool("knx_list_tags", {}, raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    t = data[0]
    assert "ref" in t
    assert "label" in t
    assert "color" in t


# -- tag.create [M] ----------------------------------------------------------

async def test_create_tag(client: Client) -> None:
    result = await client.call_tool("knx_create_tag", {
        "label": "Reviewed",
        "color": "#00FF00",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["label"] == "Reviewed"
    assert data["color"] == "#00FF00"
    assert "ref" in data


async def test_create_tag_default_color(client: Client) -> None:
    result = await client.call_tool("knx_create_tag", {
        "label": "Draft",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["label"] == "Draft"
    assert "color" in data


# -- tag.delete [M] ----------------------------------------------------------

async def test_delete_tag(client: Client) -> None:
    result = await client.call_tool("knx_delete_tag", {
        "tag_ref": "tag:t1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_tag_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_tag", {
        "tag_ref": "tag-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- todos.list [R] ----------------------------------------------------------

async def test_list_todos(client: Client) -> None:
    result = await client.call_tool("knx_list_todos", {}, raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    t = data[0]
    assert "ref" in t
    assert "description" in t
    assert "status" in t


# -- todo.create [M] ---------------------------------------------------------

async def test_create_todo(client: Client) -> None:
    result = await client.call_tool("knx_create_todo", {
        "description": "Verify addresses",
        "object_path": "Area 1/Line 1",
        "status": "Open",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["description"] == "Verify addresses"
    assert "ref" in data
    assert data["status"] == "Open"


async def test_create_todo_minimal(client: Client) -> None:
    result = await client.call_tool("knx_create_todo", {
        "description": "Quick check",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["description"] == "Quick check"
    assert data["status"] == "Open"


# -- todo.delete [M] ---------------------------------------------------------

async def test_delete_todo(client: Client) -> None:
    result = await client.call_tool("knx_delete_todo", {
        "todo_ref": "todo:t1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_todo_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_todo", {
        "todo_ref": "todo-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- device.channels [R] -----------------------------------------------------

async def test_device_channels(client: Client) -> None:
    result = await client.call_tool("knx_device_channels", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "channels" in data
    assert "modules" in data
    assert isinstance(data["channels"], list)
    assert len(data["channels"]) > 0


async def test_device_channels_not_found(client: Client) -> None:
    result = await client.call_tool("knx_device_channels", {
        "device_ref": "dev-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- projectHistory.list [R] -------------------------------------------------

async def test_list_project_history(client: Client) -> None:
    result = await client.call_tool("knx_list_project_history", {},
                                    raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    h = data[0]
    assert "ref" in h
    assert "date" in h
    assert "text" in h
    assert "user" in h


# -- projectHistory.add [M] --------------------------------------------------

async def test_add_project_history(client: Client) -> None:
    result = await client.call_tool("knx_add_project_history", {
        "text": "Added dimmer via MCP",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["text"] == "Added dimmer via MCP"
    assert "ref" in data
    assert "date" in data


# -- projectHistory.delete [M] -----------------------------------------------

async def test_delete_project_history(client: Client) -> None:
    result = await client.call_tool("knx_delete_project_history", {
        "history_ref": "ph:h1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_delete_project_history_not_found(client: Client) -> None:
    result = await client.call_tool("knx_delete_project_history", {
        "history_ref": "hist-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# ===========================================================================
# Mock-parity assertions (Codex findings)
# ===========================================================================

# -- Device orderNumber shape --------------------------------------------------

async def test_device_has_order_number(client: Client) -> None:
    """Every mock device must include orderNumber (C# DeviceInfo)."""
    result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(result)
    for d in devices:
        assert "orderNumber" in d, f"Device {d['ref']} missing orderNumber"
        assert isinstance(d["orderNumber"], str)


# -- Tag color is #RRGGBB -----------------------------------------------------

async def test_tag_color_is_hash_prefixed(client: Client) -> None:
    """Tag color must be #RRGGBB (production returns leading '#')."""
    result = await client.call_tool("knx_list_tags", {}, raise_on_error=False)
    tags = tool_data(result)
    for t in tags:
        assert t["color"].startswith("#"), f"Tag color must start with #: {t['color']}"
        assert len(t["color"]) == 7, f"Tag color must be #RRGGBB: {t['color']}"


# -- Todo status canonical casing ---------------------------------------------

async def test_todo_status_canonical_casing(client: Client) -> None:
    """Todo status must be Open or Accomplished (not lowercase)."""
    result = await client.call_tool("knx_list_todos", {}, raise_on_error=False)
    todos = tool_data(result)
    valid = {"Open", "Accomplished"}
    for t in todos:
        assert t["status"] in valid, f"Invalid status '{t['status']}'"


async def test_create_todo_invalid_status(client: Client) -> None:
    """Creating a todo with invalid status returns error."""
    result = await client.call_tool("knx_create_todo", {
        "description": "Bad status",
        "status": "open",  # lowercase is now invalid
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- ID-based ref roundtrips ---------------------------------------------------

async def test_tag_create_delete_roundtrip(client: Client) -> None:
    """Create a tag, then delete it using the returned ref."""
    create_result = await client.call_tool("knx_create_tag", {
        "label": "Roundtrip",
        "color": "#AABB00",
    }, raise_on_error=False)
    tag = tool_data(create_result)
    ref = tag["ref"]
    assert ref.startswith("tag:")

    delete_result = await client.call_tool("knx_delete_tag", {
        "tag_ref": ref,
    }, raise_on_error=False)
    data = tool_data(delete_result)
    assert data["ok"] is True


async def test_todo_create_delete_roundtrip(client: Client) -> None:
    """Create a todo, then delete it using the returned ref."""
    create_result = await client.call_tool("knx_create_todo", {
        "description": "Roundtrip todo",
    }, raise_on_error=False)
    todo = tool_data(create_result)
    ref = todo["ref"]
    assert ref.startswith("todo:")

    delete_result = await client.call_tool("knx_delete_todo", {
        "todo_ref": ref,
    }, raise_on_error=False)
    data = tool_data(delete_result)
    assert data["ok"] is True


async def test_project_history_create_delete_roundtrip(client: Client) -> None:
    """Create a history entry, then delete it using the returned ref."""
    create_result = await client.call_tool("knx_add_project_history", {
        "text": "Roundtrip entry",
    }, raise_on_error=False)
    entry = tool_data(create_result)
    ref = entry["ref"]
    assert ref.startswith("ph:")

    delete_result = await client.call_tool("knx_delete_project_history", {
        "history_ref": ref,
    }, raise_on_error=False)
    data = tool_data(delete_result)
    assert data["ok"] is True


# -- Address normalisation (bus.ping) ------------------------------------------

async def test_bus_ping_comma_address(client: Client) -> None:
    """bus.ping tolerates comma-separated address with whitespace."""
    result = await client.call_tool("knx_bus_ping", {
        "address": " 1, 1, 5 ",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["alive"] is True


async def test_bus_ping_comma_address_not_alive(client: Client) -> None:
    """bus.ping with comma address ending in 0 -> not alive."""
    result = await client.call_tool("knx_bus_ping", {
        "address": "1,1,0",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["alive"] is False


# -- Channels match C# DeviceChannelsResult ------------------------------------

async def test_device_channels_has_device_ref(client: Client) -> None:
    """device.channels result includes deviceRef (C# DeviceChannelsResult)."""
    result = await client.call_tool("knx_device_channels", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["deviceRef"] == "dev-0001"


async def test_device_channels_shape_mirrors_csharp(client: Client) -> None:
    """Channel entries match C# ChannelInstanceInfo fields."""
    result = await client.call_tool("knx_device_channels", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "channels" in data
    assert "modules" in data
    assert isinstance(data["modules"], list)
    for ch in data["channels"]:
        assert "name" in ch
        assert "isActive" in ch
        assert "activeComObjectRefs" in ch
        assert isinstance(ch["activeComObjectRefs"], list)
        # description and applicationProgramChannelId are nullable
        assert "description" in ch
        assert "applicationProgramChannelId" in ch


# -- Certificate add returns full DTO (no secrets) ----------------------------

async def test_certificate_add_returns_dto(client: Client) -> None:
    """certificate.add returns {ref, serialNumber, deviceRef}."""
    result = await client.call_tool("knx_add_certificate", {
        "value": "ABCDE12345FGHIJ67890KLMNO",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "ref" in data
    assert "serialNumber" in data
    assert "deviceRef" in data
    assert data["deviceRef"] is None  # newly added cert has no device
    assert "hasDevice" not in data
    assert "fdsk" not in data
    assert "password" not in data
