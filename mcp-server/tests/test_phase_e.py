"""Tests for Phase E: Catalog browsing tools."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_browse_products ------------------------------------------------------

async def test_browse_products(client: Client) -> None:
    result = await client.call_tool("knx_browse_products", {
        "manufacturer_ref": "mfr-abb",
    }, raise_on_error=False)
    data = tool_data(result)
    assert isinstance(data, list)
    assert len(data) == 2  # ABB has 2 catalog items
    names = [item["name"] for item in data]
    assert "Switch Actuator 4-fold" in names
    assert "Dimming Actuator 2-fold" in names


async def test_browse_products_pagination(client: Client) -> None:
    result = await client.call_tool("knx_browse_products", {
        "manufacturer_ref": "mfr-abb",
        "offset": 0,
        "limit": 1,
    }, raise_on_error=False)
    data = tool_data(result)
    assert len(data) == 1


async def test_browse_products_offset_past_end(client: Client) -> None:
    result = await client.call_tool("knx_browse_products", {
        "manufacturer_ref": "mfr-abb",
        "offset": 100,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data == []


async def test_browse_products_gira(client: Client) -> None:
    result = await client.call_tool("knx_browse_products", {
        "manufacturer_ref": "mfr-gira",
    }, raise_on_error=False)
    data = tool_data(result)
    assert len(data) == 1
    assert data[0]["name"] == "Tastsensor 3 Komfort"


async def test_browse_products_manufacturer_not_found(client: Client) -> None:
    result = await client.call_tool("knx_browse_products", {
        "manufacturer_ref": "mfr-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_product_info ---------------------------------------------------------

async def test_product_info(client: Client) -> None:
    result = await client.call_tool("knx_product_info", {
        "catalog_item_ref": "cat-abb-sa4",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["catalogItemRef"] == "cat-abb-sa4"
    assert data["manufacturer"] == "ABB"
    assert data["name"] == "Switch Actuator 4-fold"
    assert data["orderNumber"] == "SA/S 4.16.1"
    assert "description" in data
    assert "mediumTypes" in data


async def test_product_info_not_found(client: Client) -> None:
    result = await client.call_tool("knx_product_info", {
        "catalog_item_ref": "cat-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)
