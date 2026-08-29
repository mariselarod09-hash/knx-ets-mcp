"""Tests for Phase C: Project management tools."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_project_save (NOT SUPPORTED) -----------------------------------------

async def test_project_save_not_supported(client: Client) -> None:
    result = await client.call_tool("knx_project_save", {}, raise_on_error=False)
    assert result.is_error
    assert "not_supported" in error_text(result)


# -- knx_project_export -------------------------------------------------------

async def test_project_export(client: Client) -> None:
    result = await client.call_tool("knx_project_export", {
        "path": "C:\\Export\\project.knxproj",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


async def test_project_export_with_catalog(client: Client) -> None:
    result = await client.call_tool("knx_project_export", {
        "path": "C:\\Export\\full.knxproj",
        "include_catalog": True,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- knx_project_backup (NOT SUPPORTED) ---------------------------------------

async def test_project_backup_not_supported(client: Client) -> None:
    result = await client.call_tool("knx_project_backup", {
        "path": "C:\\Backup\\project.bak",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_supported" in error_text(result)


# -- knx_project_undo ---------------------------------------------------------

async def test_project_undo(client: Client) -> None:
    result = await client.call_tool("knx_project_undo", {}, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- knx_project_redo ---------------------------------------------------------

async def test_project_redo(client: Client) -> None:
    result = await client.call_tool("knx_project_redo", {}, raise_on_error=False)
    data = tool_data(result)
    assert data["ok"] is True


# -- knx_project_export_semantic (NOT SUPPORTED) -------------------------------

async def test_project_export_semantic_not_supported(client: Client) -> None:
    result = await client.call_tool(
        "knx_project_export_semantic", {}, raise_on_error=False,
    )
    assert result.is_error
    assert "not_supported" in error_text(result)
