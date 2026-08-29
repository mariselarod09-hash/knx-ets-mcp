"""Tests for read-only tools via FastMCP in-memory Client."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_project_info ----------------------------------------------------------

async def test_project_info(client: Client) -> None:
    result = await client.call_tool("knx_project_info", {}, raise_on_error=False)
    data = tool_data(result)
    assert data["projectId"] == "proj-001"
    assert data["name"] == "Test KNX Project"
    assert data["groupAddressStyle"] == "ThreeLevel"
    assert "revision" in data


# -- knx_list_devices ----------------------------------------------------------

async def test_list_devices(client: Client) -> None:
    result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(result)
    assert len(devices) == 2
    refs = {d["ref"] for d in devices}
    assert "dev-0001" in refs
    assert "dev-0002" in refs

    switch = next(d for d in devices if d["ref"] == "dev-0001")
    assert switch["name"] == "Switch Actuator 4x"
    assert switch["address"] == "1.1.1"


# -- knx_list_group_addresses --------------------------------------------------

async def test_list_group_addresses(client: Client) -> None:
    result = await client.call_tool("knx_list_group_addresses", {}, raise_on_error=False)
    gas = tool_data(result)
    assert len(gas) == 2
    names = {ga["name"] for ga in gas}
    assert "Light Living Room" in names
    assert "Dimmer Living Room" in names


# -- knx_list_comobjects -------------------------------------------------------

async def test_list_comobjects(client: Client) -> None:
    result = await client.call_tool("knx_list_comobjects", {"device_ref": "dev-0001"}, raise_on_error=False)
    cos = tool_data(result)
    assert len(cos) == 3
    refs = {co["ref"] for co in cos}
    assert "co-0001" in refs
    assert "co-0002" in refs
    assert "co-0003" in refs

    # co-0001 has a pre-existing link to ga-0001
    co1 = next(co for co in cos if co["ref"] == "co-0001")
    assert "ga-0001" in co1["links"]


async def test_list_comobjects_unknown_device(client: Client) -> None:
    result = await client.call_tool("knx_list_comobjects", {"device_ref": "dev-nonexistent"}, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_topology --------------------------------------------------------------

async def test_topology(client: Client) -> None:
    result = await client.call_tool("knx_topology", {}, raise_on_error=False)
    areas = tool_data(result)
    assert len(areas) == 1
    area = areas[0]
    assert area["areaRef"] == "area-1"
    assert area["name"] == "Building A"

    # Lines
    lines = area["lines"]
    assert len(lines) == 2
    line_refs = {ln["lineRef"] for ln in lines}
    assert "line-1.1" in line_refs
    assert "line-1.2" in line_refs

    # Segments under line-1.1
    main_line = next(ln for ln in lines if ln["lineRef"] == "line-1.1")
    assert len(main_line["segments"]) == 1
    assert main_line["segments"][0]["segmentRef"] == "seg-1.1.0"


# -- knx_catalog_manufacturers ------------------------------------------------

async def test_catalog_manufacturers(client: Client) -> None:
    result = await client.call_tool("knx_catalog_manufacturers", {}, raise_on_error=False)
    mfrs = tool_data(result)
    assert len(mfrs) == 2
    names = {m["name"] for m in mfrs}
    assert "ABB" in names
    assert "Gira" in names


# -- knx_catalog_search --------------------------------------------------------

async def test_catalog_search_by_query(client: Client) -> None:
    """Search by query returns matching items."""
    result = await client.call_tool("knx_catalog_search", {"query": "Actuator"}, raise_on_error=False)
    items = tool_data(result)
    assert len(items) == 2
    names = {i["name"] for i in items}
    assert "Switch Actuator 4-fold" in names
    assert "Dimming Actuator 2-fold" in names


async def test_catalog_search_by_query_narrow(client: Client) -> None:
    """A more specific query returns fewer results."""
    result = await client.call_tool("knx_catalog_search", {"query": "Dimming"}, raise_on_error=False)
    items = tool_data(result)
    assert len(items) == 1
    assert items[0]["catalogItemRef"] == "cat-abb-da2"


async def test_catalog_search_by_manufacturer(client: Client) -> None:
    """Filtering by manufacturerRef restricts results."""
    result = await client.call_tool("knx_catalog_search", {
        "query": "Actuator",
        "manufacturer_ref": "mfr-gira",
    }, raise_on_error=False)
    items = tool_data(result)
    assert len(items) == 0


async def test_catalog_search_by_order_number(client: Client) -> None:
    """Search matches on order number too."""
    result = await client.call_tool("knx_catalog_search", {"query": "2031"}, raise_on_error=False)
    items = tool_data(result)
    assert len(items) == 1
    assert items[0]["name"] == "Tastsensor 3 Komfort"


# -- knx_list_parameters -------------------------------------------------------

async def test_list_parameters(client: Client) -> None:
    result = await client.call_tool("knx_list_parameters", {"device_ref": "dev-0001"}, raise_on_error=False)
    params = tool_data(result)
    assert len(params) == 2
    refs = {p["parameterRef"] for p in params}
    assert "param-0001" in refs
    assert "param-0003" in refs

    # param-0001: value == default -> isDefault True
    p1 = next(p for p in params if p["parameterRef"] == "param-0001")
    assert p1["value"] == "normal"
    assert p1["isDefault"] is True
    assert p1["isActive"] is True

    # param-0003: isActive False
    p3 = next(p for p in params if p["parameterRef"] == "param-0003")
    assert p3["isActive"] is False


async def test_list_parameters_non_default(client: Client) -> None:
    """Dimming Speed has a non-default value."""
    result = await client.call_tool("knx_list_parameters", {"device_ref": "dev-0002"}, raise_on_error=False)
    params = tool_data(result)
    assert len(params) == 1
    p = params[0]
    assert p["parameterRef"] == "param-0002"
    assert p["value"] == "3"
    assert p["isDefault"] is False


async def test_list_parameters_unknown_device(client: Client) -> None:
    result = await client.call_tool("knx_list_parameters", {"device_ref": "dev-nonexistent"}, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)
