"""Tests for v0.1.16 AddIn contract: label fields + 4 setter tools."""

from __future__ import annotations

from fastmcp import Client

from knx_ets_mcp.client import _MUTATING_METHODS, KnxBridgeClient
from knx_ets_mcp.transport.mock import MockTransport
from tests.conftest import tool_data, error_text


# ---------------------------------------------------------------------------
# _MUTATING_METHODS must include the 4 new setter methods
# ---------------------------------------------------------------------------

def test_mutating_methods_include_label_setters() -> None:
    assert "device.setDescription" in _MUTATING_METHODS
    assert "device.setComment" in _MUTATING_METHODS
    assert "comObject.setDescription" in _MUTATING_METHODS
    assert "comObject.setFunctionText" in _MUTATING_METHODS


# ---------------------------------------------------------------------------
# Read-side: new label fields present in mock data
# ---------------------------------------------------------------------------

async def test_device_has_description_and_comment(client: Client) -> None:
    """devices.list returns description and comment fields."""
    result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(result)
    dev1 = next(d for d in devices if d["ref"] == "dev-0001")
    assert "description" in dev1
    assert "comment" in dev1
    # Seeded value
    assert dev1["description"] == "Couch"
    assert dev1["comment"] == "Main actuator for living room"


async def test_device_description_couch_discoverable(client: Client) -> None:
    """The seeded device with description='Couch' is discoverable."""
    result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(result)
    couch_devs = [d for d in devices if d.get("description") == "Couch"]
    assert len(couch_devs) == 1
    assert couch_devs[0]["ref"] == "dev-0001"


async def test_group_address_has_comment(client: Client) -> None:
    """ga.list returns comment field (description already existed)."""
    result = await client.call_tool("knx_list_group_addresses", {}, raise_on_error=False)
    gas = tool_data(result)
    ga1 = next(g for g in gas if g["ref"] == "ga-0001")
    assert "comment" in ga1
    assert ga1["comment"] == "Ceiling light"


async def test_comobject_has_label_fields(client: Client) -> None:
    """comobjects.list returns description, functionText, text."""
    result = await client.call_tool("knx_list_comobjects", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    cos = tool_data(result)
    co1 = next(c for c in cos if c["ref"] == "co-0001")
    assert "description" in co1
    assert "functionText" in co1
    assert "text" in co1
    # Seeded value
    assert co1["functionText"] == "Raffstore links"
    assert co1["text"] == "Schalten Ausgang A"


async def test_comobject_functiontext_raffstore_discoverable(client: Client) -> None:
    """The seeded com object with functionText='Raffstore links' is discoverable."""
    result = await client.call_tool("knx_list_comobjects", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    cos = tool_data(result)
    raffstore_cos = [c for c in cos if c.get("functionText") == "Raffstore links"]
    assert len(raffstore_cos) == 1
    assert raffstore_cos[0]["ref"] == "co-0001"


async def test_topology_has_description_comment(client: Client) -> None:
    """topology.list returns description/comment on areas, lines, segments."""
    result = await client.call_tool("knx_topology", {}, raise_on_error=False)
    areas = tool_data(result)
    area = areas[0]
    assert "description" in area
    assert "comment" in area
    line = area["lines"][0]
    assert "description" in line
    assert "comment" in line
    seg = line["segments"][0]
    assert "description" in seg
    assert "comment" in seg


async def test_catalog_search_has_description(client: Client) -> None:
    """catalog.search results include description."""
    result = await client.call_tool("knx_catalog_search", {
        "query": "Actuator",
    }, raise_on_error=False)
    items = tool_data(result)
    assert len(items) >= 1
    assert "description" in items[0]
    assert items[0]["description"] != ""


async def test_channels_have_text(client: Client) -> None:
    """device.channels returns text field in channel entries."""
    result = await client.call_tool("knx_device_channels", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    channels = data["channels"]
    assert len(channels) >= 1
    assert "text" in channels[0]


# ---------------------------------------------------------------------------
# Setter tools: set -> read roundtrip
# ---------------------------------------------------------------------------

async def test_set_device_description_roundtrip(client: Client) -> None:
    """Set device description and verify via list."""
    set_result = await client.call_tool("knx_set_device_description", {
        "device_ref": "dev-0001",
        "description": "Wohnzimmer Sideboard",
    }, raise_on_error=False)
    data = tool_data(set_result)
    assert data["description"] == "Wohnzimmer Sideboard"

    # Read back
    list_result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(list_result)
    dev = next(d for d in devices if d["ref"] == "dev-0001")
    assert dev["description"] == "Wohnzimmer Sideboard"


async def test_set_device_comment_roundtrip(client: Client) -> None:
    """Set device comment and verify via list."""
    set_result = await client.call_tool("knx_set_device_comment", {
        "device_ref": "dev-0002",
        "comment": "Needs rewiring",
    }, raise_on_error=False)
    data = tool_data(set_result)
    assert data["ok"] is True

    # Read back
    list_result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(list_result)
    dev = next(d for d in devices if d["ref"] == "dev-0002")
    assert dev["comment"] == "Needs rewiring"


async def test_set_comobject_description_roundtrip(client: Client) -> None:
    """Set comobject description and verify via list."""
    set_result = await client.call_tool("knx_set_comobject_description", {
        "com_object_ref": "co-0001",
        "description": "Jalousie Kanal A",
    }, raise_on_error=False)
    data = tool_data(set_result)
    assert data["ref"] == "co-0001"
    assert data["description"] == "Jalousie Kanal A"

    # Read back
    list_result = await client.call_tool("knx_list_comobjects", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    cos = tool_data(list_result)
    co = next(c for c in cos if c["ref"] == "co-0001")
    assert co["description"] == "Jalousie Kanal A"


async def test_set_comobject_function_text_roundtrip(client: Client) -> None:
    """Set comobject functionText and verify via list."""
    set_result = await client.call_tool("knx_set_comobject_function_text", {
        "com_object_ref": "co-0001",
        "function_text": "Markise rechts",
    }, raise_on_error=False)
    data = tool_data(set_result)
    assert data["ref"] == "co-0001"
    assert data["functionText"] == "Markise rechts"

    # Read back
    list_result = await client.call_tool("knx_list_comobjects", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    cos = tool_data(list_result)
    co = next(c for c in cos if c["ref"] == "co-0001")
    assert co["functionText"] == "Markise rechts"


# ---------------------------------------------------------------------------
# Setter tools: not_found errors
# ---------------------------------------------------------------------------

async def test_set_device_description_not_found(client: Client) -> None:
    result = await client.call_tool("knx_set_device_description", {
        "device_ref": "dev-9999",
        "description": "x",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_set_device_comment_not_found(client: Client) -> None:
    result = await client.call_tool("knx_set_device_comment", {
        "device_ref": "dev-9999",
        "comment": "x",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_set_comobject_description_not_found(client: Client) -> None:
    result = await client.call_tool("knx_set_comobject_description", {
        "com_object_ref": "co-9999",
        "description": "x",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_set_comobject_function_text_not_found(client: Client) -> None:
    result = await client.call_tool("knx_set_comobject_function_text", {
        "com_object_ref": "co-9999",
        "function_text": "x",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# ---------------------------------------------------------------------------
# Bridge-client-level roundtrip (no MCP layer)
# ---------------------------------------------------------------------------

async def test_bridge_client_set_device_description(
    bridge_client: KnxBridgeClient,
) -> None:
    result = await bridge_client.set_device_description("dev-0001", "Esstisch")
    assert result["description"] == "Esstisch"


async def test_bridge_client_set_comobject_function_text(
    bridge_client: KnxBridgeClient,
) -> None:
    result = await bridge_client.set_comobject_function_text("co-0001", "Rollo Ost")
    assert result["functionText"] == "Rollo Ost"
