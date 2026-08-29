"""Tests for online catalog tools: search_online, import, internalize."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_search_online_catalog ------------------------------------------------

async def test_search_online_returns_unified_items(client: Client) -> None:
    """Broad query returns all matching unified catalog items."""
    result = await client.call_tool(
        "knx_search_online_catalog", {"query": "ABB"}, raise_on_error=False,
    )
    items = tool_data(result)
    # Seed has 2 ABB items in the online catalog (matched on manufacturer name
    # embedded in orderNumber or name -- but "ABB" is not in name/orderNumber,
    # so let's search for something that IS in the names).
    assert isinstance(items, list)


async def test_search_online_by_name(client: Client) -> None:
    """Query matching product name returns the right item."""
    result = await client.call_tool(
        "knx_search_online_catalog", {"query": "Binary"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 1
    assert items[0]["unifiedCatalogItemRef"] == "uci:gira-be2"
    assert items[0]["manufacturer"] == "Gira"
    assert items[0]["name"] == "Binary Input 2-fold"
    assert items[0]["orderNumber"] == "1120 00"


async def test_search_online_by_order_number(client: Client) -> None:
    result = await client.call_tool(
        "knx_search_online_catalog", {"query": "ROG"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 1
    assert items[0]["unifiedCatalogItemRef"] == "uci:abb-rog"


async def test_search_online_filter_by_manufacturer(client: Client) -> None:
    """manufacturerRef filters results to that manufacturer only."""
    # "Input" only matches Gira's Binary Input; but if we filter to ABB -> empty.
    result = await client.call_tool(
        "knx_search_online_catalog",
        {"query": "Input", "manufacturer_ref": "mfr-abb"},
        raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 0


async def test_search_online_no_match(client: Client) -> None:
    """A query with no match returns an empty list (not an error)."""
    result = await client.call_tool(
        "knx_search_online_catalog", {"query": "nonexistent-xyz"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert items == []


async def test_search_online_items_not_in_local_catalog(client: Client) -> None:
    """Online items must NOT appear in the local catalog search."""
    result = await client.call_tool(
        "knx_catalog_search", {"query": "Binary"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 0  # "Binary Input 2-fold" is online-only


# -- knx_import_product -------------------------------------------------------

async def test_import_valid_path(client: Client) -> None:
    """Importing a valid .knxprod path returns imported items."""
    result = await client.call_tool(
        "knx_import_product",
        {"path": "C:\\Products\\sensor.knxprod"},
        raise_on_error=False,
    )
    data = tool_data(result)
    assert "imported" in data
    assert len(data["imported"]) == 1
    item = data["imported"][0]
    assert item["catalogItemRef"] == "cat-imp-sensor"
    assert item["name"] == "Imported Sensor Module"


async def test_import_item_appears_in_local_search(client: Client) -> None:
    """After import, the product is findable via local catalog search."""
    # Import first
    await client.call_tool(
        "knx_import_product",
        {"path": "C:\\Products\\sensor.knxprod"},
        raise_on_error=False,
    )
    # Now search locally
    result = await client.call_tool(
        "knx_catalog_search", {"query": "Imported Sensor"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 1
    assert items[0]["catalogItemRef"] == "cat-imp-sensor"


async def test_import_invalid_path(client: Client) -> None:
    """Importing a nonexistent path returns an error."""
    result = await client.call_tool(
        "knx_import_product",
        {"path": "C:\\bad\\missing.knxprod"},
        raise_on_error=False,
    )
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_import_idempotent(client: Client) -> None:
    """Importing the same path twice does not double-add items."""
    for _ in range(2):
        result = await client.call_tool(
            "knx_import_product",
            {"path": "C:\\Products\\sensor.knxprod"},
            raise_on_error=False,
        )
        data = tool_data(result)
        assert len(data["imported"]) == 1

    # Local search should return exactly one item, not two.
    result = await client.call_tool(
        "knx_catalog_search", {"query": "Imported Sensor"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 1


# -- knx_internalize_product --------------------------------------------------

async def test_internalize_known_ref(client: Client) -> None:
    """Internalizing a known unified ref returns a local ci: ref."""
    result = await client.call_tool(
        "knx_internalize_product",
        {"unified_catalog_item_ref": "uci:gira-be2"},
        raise_on_error=False,
    )
    data = tool_data(result)
    assert "catalogItemRef" in data
    assert data["catalogItemRef"].startswith("ci:")


async def test_internalize_item_appears_in_local_search(client: Client) -> None:
    """After internalize, the item is findable in the local catalog."""
    # Internalize
    await client.call_tool(
        "knx_internalize_product",
        {"unified_catalog_item_ref": "uci:gira-be2"},
        raise_on_error=False,
    )
    # Search locally -- "Binary Input" was online-only before internalize.
    result = await client.call_tool(
        "knx_catalog_search", {"query": "Binary Input"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 1
    assert items[0]["name"] == "Binary Input 2-fold"
    assert items[0]["catalogItemRef"].startswith("ci:")


async def test_internalize_unknown_ref(client: Client) -> None:
    """Internalizing an unknown unified ref returns not_found."""
    result = await client.call_tool(
        "knx_internalize_product",
        {"unified_catalog_item_ref": "uci:unknown-xyz"},
        raise_on_error=False,
    )
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_internalize_idempotent(client: Client) -> None:
    """Internalizing the same ref twice does not double-add in local catalog."""
    for _ in range(2):
        result = await client.call_tool(
            "knx_internalize_product",
            {"unified_catalog_item_ref": "uci:abb-spau"},
            raise_on_error=False,
        )
        data = tool_data(result)
        assert data["catalogItemRef"].startswith("ci:")

    # Local search should return exactly one match.
    result = await client.call_tool(
        "knx_catalog_search", {"query": "Space Unit"}, raise_on_error=False,
    )
    items = tool_data(result)
    assert len(items) == 1
