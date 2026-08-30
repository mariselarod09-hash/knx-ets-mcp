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
    # active_only defaults to True, so pass False here to also see the inactive param-0003.
    result = await client.call_tool("knx_list_parameters", {"device_ref": "dev-0001", "active_only": False}, raise_on_error=False)
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
    # Enum semantics: label + allowed choices so the LLM picks a valid value.
    assert p1["text"] == "Operating Mode"
    assert p1["access"] == "ReadWrite"
    assert {o["value"] for o in p1["options"]} == {"normal", "night", "comfort"}
    assert any(o["text"] == "Comfort" for o in p1["options"])
    # Not a numeric parameter -> no min/max.
    assert "min" not in p1 and "max" not in p1

    # param-0003: isActive False (deactivated by a controlling parameter), numeric range.
    p3 = next(p for p in params if p["parameterRef"] == "param-0003")
    assert p3["isActive"] is False
    assert p3["text"] == "Switch-on delay"
    assert p3["unit"] == "s"
    assert p3["min"] == "0" and p3["max"] == "255"
    # Numeric parameter -> no enum options.
    assert "options" not in p3


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


async def test_list_parameters_block_path(client: Client) -> None:
    """Parameters carry their UI block path to disambiguate repeated names."""
    result = await client.call_tool(
        "knx_list_parameters",
        {"device_ref": "dev-0001", "active_only": False},
        raise_on_error=False,
    )
    params = tool_data(result)
    p3 = next(p for p in params if p["parameterRef"] == "param-0003")
    assert "PB9/10" in p3["block"]
    # Targeting workflow: block + name yields exactly one instance.
    hits = [p for p in params
            if "PB9/10" in (p.get("block") or "") and p["name"] == "Switch-on delay"]
    assert len(hits) == 1 and hits[0]["parameterRef"] == "param-0003"


async def test_list_parameters_active_only_default(client: Client) -> None:
    """active_only defaults to True -> the inactive param-0003 is dropped by default."""
    result = await client.call_tool("knx_list_parameters", {"device_ref": "dev-0001"}, raise_on_error=False)
    params = tool_data(result)
    refs = {p["parameterRef"] for p in params}
    assert "param-0001" in refs        # active
    assert "param-0003" not in refs    # inactive -> filtered by default
    assert all(p["isActive"] for p in params)


async def test_list_devices_name_contains_matches_description(client: Client) -> None:
    """Finding a device by its human label in `description` (name is the product name)."""
    result = await client.call_tool("knx_list_devices", {"name_contains": "couch"}, raise_on_error=False)
    devs = tool_data(result)
    assert any(d["ref"] == "dev-0001" for d in devs)          # matched via description "Couch"
    assert all("couch" not in (d.get("name") or "").lower() for d in devs)  # not via name


async def test_list_group_addresses_name_contains_matches_description(client: Client) -> None:
    """Finding a GA by a label in `description` (name is a scheme like 'Light Living Room')."""
    result = await client.call_tool("knx_list_group_addresses", {"name_contains": "mittelgang"}, raise_on_error=False)
    gas = tool_data(result)
    assert any(g["ref"] == "ga-0001" for g in gas)            # matched via description "Mittelgang"


async def test_list_parameters_active_only(client: Client) -> None:
    """active_only drops inactive parameters (param-0003 is inactive)."""
    result = await client.call_tool(
        "knx_list_parameters",
        {"device_ref": "dev-0001", "active_only": True},
        raise_on_error=False,
    )
    params = tool_data(result)
    refs = {p["parameterRef"] for p in params}
    assert "param-0001" in refs      # active
    assert "param-0003" not in refs  # inactive -> filtered out
    assert all(p["isActive"] for p in params)


async def test_list_parameters_name_contains(client: Client) -> None:
    """name_contains filters case-insensitively on name/label.

    param-0003 ("Switch-on delay") is inactive, so active_only=False is needed to reach it.
    """
    result = await client.call_tool(
        "knx_list_parameters",
        {"device_ref": "dev-0001", "name_contains": "delay", "active_only": False},
        raise_on_error=False,
    )
    params = tool_data(result)
    assert len(params) == 1
    assert params[0]["parameterRef"] == "param-0003"  # "Switch-on delay"


async def test_list_comobjects_name_contains(client: Client) -> None:
    """name_contains scopes a large ComObject list to matching entries."""
    full = tool_data(await client.call_tool(
        "knx_list_comobjects", {"device_ref": "dev-0002"}, raise_on_error=False))
    filtered = tool_data(await client.call_tool(
        "knx_list_comobjects",
        {"device_ref": "dev-0002", "name_contains": "dim object b"},
        raise_on_error=False,
    ))
    assert len(filtered) < len(full)
    assert all("dim object b" in (c.get("name") or "").lower() for c in filtered)


async def test_list_comobjects_channel_grouping(client: Client) -> None:
    """ComObjects carry a channel label; dev-0001 objects are channel-independent."""
    cos2 = tool_data(await client.call_tool(
        "knx_list_comobjects", {"device_ref": "dev-0002"}, raise_on_error=False))
    by_ref = {c["ref"]: c for c in cos2}
    assert by_ref["co-0004"]["channel"] == "Channel A - Dimming"
    assert by_ref["co-0005"]["channel"] == "Channel B - Dimming"

    # dev-0001 objects have no channel (channel-independent) -> field omitted.
    cos1 = tool_data(await client.call_tool(
        "knx_list_comobjects", {"device_ref": "dev-0001"}, raise_on_error=False))
    assert all("channel" not in c for c in cos1)


async def test_list_comobjects_block_path(client: Client) -> None:
    """ComObjects carry the authoritative ETS block path (`block`)."""
    cos = tool_data(await client.call_tool(
        "knx_list_comobjects", {"device_ref": "dev-0002"}, raise_on_error=False))
    by_ref = {c["ref"]: c for c in cos}
    assert by_ref["co-0004"]["block"] == "Operation / Display > Dimming > Channel A"


async def test_application_dynamic(client: Client) -> None:
    """application.dynamic returns the dynamic UI tree XML."""
    result = await client.call_tool(
        "knx_application_dynamic", {"device_ref": "dev-0002"}, raise_on_error=False)
    data = tool_data(result)
    assert "xml" in data
    assert "ParameterBlock" in data["xml"] and "ParameterRefRef" in data["xml"]


async def test_application_dynamic_unknown_device(client: Client) -> None:
    result = await client.call_tool(
        "knx_application_dynamic", {"device_ref": "dev-nope"}, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_list_comobjects_filter_by_channel(client: Client) -> None:
    """name_contains matches the channel label, scoping to one function."""
    result = tool_data(await client.call_tool(
        "knx_list_comobjects",
        {"device_ref": "dev-0002", "name_contains": "channel a"},
        raise_on_error=False,
    ))
    assert {c["ref"] for c in result} == {"co-0004"}
