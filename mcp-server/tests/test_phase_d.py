"""Tests for Phase D: Bus / Online operations tools."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_bus_ping --------------------------------------------------------------

async def test_bus_ping_alive(client: Client) -> None:
    result = await client.call_tool("knx_bus_ping", {
        "address": "1.1.1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["alive"] is True


async def test_bus_ping_not_alive(client: Client) -> None:
    # Mock: addresses ending in .0 are "not alive"
    result = await client.call_tool("knx_bus_ping", {
        "address": "1.1.0",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["alive"] is False


async def test_bus_ping_invalid_address(client: Client) -> None:
    result = await client.call_tool("knx_bus_ping", {
        "address": "bad",
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- knx_bus_scan_line ---------------------------------------------------------

async def test_bus_scan_line(client: Client) -> None:
    result = await client.call_tool("knx_bus_scan_line", {
        "line_ref": "line-1.1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data
    job_id = data["jobId"]

    # Poll job status
    status_result = await client.call_tool("knx_job_status", {
        "job_id": job_id,
    }, raise_on_error=False)
    status = tool_data(status_result)
    assert status["state"] == "done"
    assert "result" in status
    addresses = status["result"]["addresses"]
    assert isinstance(addresses, list)
    assert "1.1.1" in addresses
    assert "1.1.2" in addresses


async def test_bus_scan_line_not_found(client: Client) -> None:
    result = await client.call_tool("knx_bus_scan_line", {
        "line_ref": "line-9.9",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_device_read_info -----------------------------------------------------

async def test_device_read_info(client: Client) -> None:
    result = await client.call_tool("knx_device_read_info", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "maskVersion" in data
    assert "maskVersionId" in data
    assert data["maskVersion"] == "0x0705"


async def test_device_read_info_not_found(client: Client) -> None:
    result = await client.call_tool("knx_device_read_info", {
        "device_ref": "dev-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_device_compare -------------------------------------------------------

async def test_device_compare(client: Client) -> None:
    """device.compare returns a partial comparison result."""
    result = await client.call_tool("knx_device_compare", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["partial"] is True
    assert isinstance(data["compared"], list)
    assert len(data["compared"]) > 0


# -- knx_group_read -----------------------------------------------------------

async def test_group_read_boolean(client: Client) -> None:
    result = await client.call_tool("knx_group_read", {
        "ga_ref": "ga-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["value"] == "01"  # DPT 1.001 -> boolean value


async def test_group_read_percentage(client: Client) -> None:
    result = await client.call_tool("knx_group_read", {
        "ga_ref": "ga-0002",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["value"] == "64"  # DPT 5.001 -> percentage value


async def test_group_read_not_found(client: Client) -> None:
    result = await client.call_tool("knx_group_read", {
        "ga_ref": "ga-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_group_write ----------------------------------------------------------

async def test_group_write(client: Client) -> None:
    result = await client.call_tool("knx_group_write", {
        "ga_ref": "ga-0001",
        "value": "01",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["acknowledged"] is True


async def test_group_write_less_7_bits(client: Client) -> None:
    result = await client.call_tool("knx_group_write", {
        "ga_ref": "ga-0001",
        "value": "01",
        "less_7_bits": True,
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["acknowledged"] is True


async def test_group_write_invalid_hex(client: Client) -> None:
    result = await client.call_tool("knx_group_write", {
        "ga_ref": "ga-0001",
        "value": "ZZ",
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


async def test_group_write_not_found(client: Client) -> None:
    result = await client.call_tool("knx_group_write", {
        "ga_ref": "ga-9999",
        "value": "01",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_group_monitor --------------------------------------------------------

async def test_group_monitor(client: Client) -> None:
    result = await client.call_tool("knx_group_monitor", {
        "line_ref": "default",
        "duration_ms": 5000,
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data
    job_id = data["jobId"]

    # Poll job status
    status_result = await client.call_tool("knx_job_status", {
        "job_id": job_id,
    }, raise_on_error=False)
    status = tool_data(status_result)
    assert status["state"] == "done"
    telegrams = status["result"]["telegrams"]
    assert isinstance(telegrams, list)
    assert len(telegrams) == 2
    t0 = telegrams[0]
    assert "service" in t0
    assert "sourceAddress" in t0
    assert "groupAddress" in t0
    assert "value" in t0
    assert "timestamp" in t0


async def test_group_monitor_line_not_found(client: Client) -> None:
    result = await client.call_tool("knx_group_monitor", {
        "line_ref": "line-9.9",
        "duration_ms": 5000,
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_group_monitor_invalid_duration(client: Client) -> None:
    result = await client.call_tool("knx_group_monitor", {
        "duration_ms": 100,  # Too short
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- knx_device_unload --------------------------------------------------------

async def test_device_unload(client: Client) -> None:
    result = await client.call_tool("knx_device_unload", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_device_unload_full(client: Client) -> None:
    result = await client.call_tool("knx_device_unload", {
        "device_ref": "dev-0001",
        "full_unload": True,
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_device_unload_not_found(client: Client) -> None:
    result = await client.call_tool("knx_device_unload", {
        "device_ref": "dev-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- knx_program_device (extended with options) --------------------------------

async def test_program_device_default(client: Client) -> None:
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_program_device_partial(client: Client) -> None:
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-0001",
        "options": "partial",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_program_device_application(client: Client) -> None:
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-0001",
        "options": "application",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_program_device_invalid_option(client: Client) -> None:
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-0001",
        "options": "bogus",
    }, raise_on_error=False)
    assert result.is_error
    assert "invalid_params" in error_text(result)


# -- knx_bus_set_individual_address (v0.1.13: job-based) -----------------------

async def test_bus_set_individual_address(client: Client) -> None:
    result = await client.call_tool("knx_bus_set_individual_address", {
        "device_ref": "dev-0001",
        "current_address": "1.1.1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_bus_set_individual_address_not_found(client: Client) -> None:
    result = await client.call_tool("knx_bus_set_individual_address", {
        "device_ref": "dev-9999",
        "current_address": "1.1.1",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)
