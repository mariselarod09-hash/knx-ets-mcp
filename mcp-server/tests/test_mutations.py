"""Tests for mutation tools via FastMCP in-memory Client."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_create_group_address --------------------------------------------------

async def test_create_group_address(client: Client) -> None:
    result = await client.call_tool("knx_create_group_address", {
        "name": "Light Kitchen",
        "address": "1/0/3",
        "dpt_main": 1,
        "dpt_sub": 1,
    }, raise_on_error=False)
    data = tool_data(result)
    assert "ref" in data
    assert data["address"] == "1/0/3"

    # Verify it shows up in the list
    list_result = await client.call_tool("knx_list_group_addresses", {}, raise_on_error=False)
    gas = tool_data(list_result)
    new_ga = next((ga for ga in gas if ga["address"] == "1/0/3"), None)
    assert new_ga is not None
    assert new_ga["name"] == "Light Kitchen"
    assert new_ga["dpt"] == "1.001"


async def test_create_group_address_duplicate(client: Client) -> None:
    """Creating a GA with an existing address must fail."""
    result = await client.call_tool("knx_create_group_address", {
        "name": "Duplicate",
        "address": "1/0/1",
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- knx_add_device ------------------------------------------------------------

async def test_add_device(client: Client) -> None:
    """device.addFromCatalog returns {ref, address} and bumps revision."""
    result = await client.call_tool("knx_add_device", {
        "line_ref": "line-1.1",
        "catalog_item_ref": "cat-abb-sa4",
        "address": "1.1.5",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "ref" in data
    assert data["address"] == "1.1.5"

    # Verify the device shows up in the list
    list_result = await client.call_tool("knx_list_devices", {}, raise_on_error=False)
    devices = tool_data(list_result)
    new_dev = next((d for d in devices if d["address"] == "1.1.5"), None)
    assert new_dev is not None
    assert new_dev["ref"] == data["ref"]


async def test_add_device_unknown_catalog_item(client: Client) -> None:
    """device.addFromCatalog with unknown catalog item returns not_found."""
    result = await client.call_tool("knx_add_device", {
        "line_ref": "line-1.1",
        "catalog_item_ref": "DOES-NOT-EXIST",
        "address": "1.1.6",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_add_device_unknown_line(client: Client) -> None:
    """device.addFromCatalog with unknown line returns not_found."""
    result = await client.call_tool("knx_add_device", {
        "line_ref": "line-NOPE",
        "catalog_item_ref": "cat-abb-sa4",
        "address": "1.1.7",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_add_device_duplicate_address(client: Client) -> None:
    """device.addFromCatalog with a taken address returns invalid_params."""
    result = await client.call_tool("knx_add_device", {
        "line_ref": "line-1.1",
        "catalog_item_ref": "cat-abb-sa4",
        "address": "1.1.1",  # already used by seed device
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- knx_link ------------------------------------------------------------------

async def test_link_active_object(client: Client) -> None:
    """Linking an active com-object to a GA should succeed."""
    result = await client.call_tool("knx_link", {
        "com_object_ref": "co-0002",
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["comObjectRef"] == "co-0002"
    assert data["gaRef"] == "ga-0001"
    # co-0002 (1.001) and ga-0001 (1.001) match -> no DPT warning.
    assert "dptWarning" not in data

    # Verify the link appears
    cos_result = await client.call_tool("knx_list_comobjects", {"device_ref": "dev-0001"}, raise_on_error=False)
    cos = tool_data(cos_result)
    co2 = next(co for co in cos if co["ref"] == "co-0002")
    assert "ga-0001" in co2["links"]


async def test_link_dpt_mismatch_warns(client: Client) -> None:
    """Linking DPTs with different main numbers still succeeds but returns dptWarning."""
    result = await client.call_tool("knx_link", {
        "com_object_ref": "co-0004",  # dpt 5.001 (%)
        "ga_ref": "ga-0001",          # dpt 1.001 (switch)
    }, raise_on_error=False)
    assert not result.is_error
    data = tool_data(result)
    assert data["dpt"] == "5.001"
    assert data["gaDpt"] == "1.001"
    assert "dptWarning" in data and "mismatch" in data["dptWarning"].lower()
    # The link still happened despite the warning.
    cos = tool_data(await client.call_tool("knx_list_comobjects", {"device_ref": "dev-0002"}, raise_on_error=False))
    co4 = next(co for co in cos if co["ref"] == "co-0004")
    assert "ga-0001" in co4["links"]


async def test_link_inactive_object(client: Client) -> None:
    """Linking an inactive com-object must fail with inactive_object."""
    result = await client.call_tool("knx_link", {
        "com_object_ref": "co-0003",
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    assert result.is_error
    assert "inactive_object" in error_text(result)


async def test_link_nonexistent_comobject(client: Client) -> None:
    result = await client.call_tool("knx_link", {
        "com_object_ref": "co-9999",
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_link_nonexistent_ga(client: Client) -> None:
    result = await client.call_tool("knx_link", {
        "com_object_ref": "co-0002",
        "ga_ref": "ga-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_unlink ----------------------------------------------------------------

async def test_unlink(client: Client) -> None:
    """Removing an existing link should succeed."""
    result = await client.call_tool("knx_unlink", {
        "com_object_ref": "co-0001",
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True

    cos_result = await client.call_tool("knx_list_comobjects", {"device_ref": "dev-0001"}, raise_on_error=False)
    cos = tool_data(cos_result)
    co1 = next(co for co in cos if co["ref"] == "co-0001")
    assert "ga-0001" not in co1["links"]


async def test_unlink_absent_is_idempotent(client: Client) -> None:
    """Unlinking a valid but unlinked com-object/GA pair is idempotent (ok, no error)."""
    result = await client.call_tool("knx_unlink", {
        "com_object_ref": "co-0002",
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_unlink_unknown_ref(client: Client) -> None:
    """Unlinking with an unknown com-object ref returns not_found."""
    result = await client.call_tool("knx_unlink", {
        "com_object_ref": "co-9999",
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_set_parameter ---------------------------------------------------------

async def test_set_parameter(client: Client) -> None:
    result = await client.call_tool("knx_set_parameter", {
        "device_ref": "dev-0001",
        "parameter_ref": "param-0001",
        "value": "eco",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_set_parameter_wrong_device(client: Client) -> None:
    """Parameter must belong to the specified device."""
    result = await client.call_tool("knx_set_parameter", {
        "device_ref": "dev-0002",
        "parameter_ref": "param-0001",
        "value": "eco",
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- expected_project_revision -------------------------------------------------

async def test_mutation_with_matching_revision(client: Client, transport) -> None:
    """Passing the correct expected_project_revision allows the mutation."""
    current_rev = transport._revision
    result = await client.call_tool("knx_create_group_address", {
        "name": "Revision-guarded GA",
        "address": "5/0/1",
        "expected_project_revision": current_rev,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["address"] == "5/0/1"


async def test_mutation_with_stale_revision(client: Client) -> None:
    """Passing a stale expected_project_revision returns revision_mismatch."""
    result = await client.call_tool("knx_create_group_address", {
        "name": "Stale GA",
        "address": "5/0/2",
        "expected_project_revision": "stale-rev-000",
    }, raise_on_error=False)
    assert result.is_error
    assert "revision_mismatch" in error_text(result)


# -- _MUTATING_METHODS completeness -------------------------------------------

def test_mutating_methods_contain_bus_set_individual_address() -> None:
    """bus.setIndividualAddress must be in _MUTATING_METHODS for idempotency."""
    from knx_ets_mcp.client import _MUTATING_METHODS
    assert "bus.setIndividualAddress" in _MUTATING_METHODS


def test_mutating_methods_contain_device_reset() -> None:
    """device.reset must be in _MUTATING_METHODS for idempotency."""
    from knx_ets_mcp.client import _MUTATING_METHODS
    assert "device.reset" in _MUTATING_METHODS
