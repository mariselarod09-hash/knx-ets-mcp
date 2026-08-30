"""Tests for new read methods: bridge.info, bus.reconstructLine, device.readGroupObjects."""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- knx_bridge_info (bridge.info) --------------------------------------------

async def test_bridge_info(client: Client) -> None:
    """bridge.info returns AddIn/SDK version, project metadata, and knxIpOnly."""
    result = await client.call_tool("knx_bridge_info", {}, raise_on_error=False)
    data = tool_data(result)
    assert data["addinVersion"] == "0.3.0"
    assert data["sdkVersion"] == "6.3.7959.0"
    assert data["builtAgainstSdk"] == "6.4.8658.0"
    assert isinstance(data["projectName"], str)
    assert isinstance(data["projectId"], str)
    assert isinstance(data["projectRevision"], str)
    assert data["knxIpOnly"] is True


async def test_bridge_info_has_all_keys(client: Client) -> None:
    """bridge.info response contains exactly the expected keys."""
    result = await client.call_tool("knx_bridge_info", {}, raise_on_error=False)
    data = tool_data(result)
    expected_keys = {
        "addinVersion", "sdkVersion", "builtAgainstSdk", "projectName",
        "projectId", "projectRevision", "knxIpOnly",
    }
    assert set(data.keys()) == expected_keys


async def test_bridge_info_is_read(client: Client, transport) -> None:
    """bridge.info is a read -- it must NOT bump the revision."""
    rev_before = transport._revision
    await client.call_tool("knx_bridge_info", {}, raise_on_error=False)
    assert transport._revision == rev_before


# -- knx_reconstruct_line (bus.reconstructLine) --------------------------------

async def test_reconstruct_line(client: Client) -> None:
    """bus.reconstructLine returns a jobId; job.status completes with devices."""
    result = await client.call_tool("knx_reconstruct_line", {
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

    job_result = status["result"]
    assert isinstance(job_result["devices"], list)
    assert len(job_result["devices"]) > 0
    assert isinstance(job_result["scannedRange"], str)

    # Check device shape
    dev = job_result["devices"][0]
    assert "address" in dev
    assert "maskVersion" in dev
    assert "serialNumber" in dev
    assert "manufacturerId" in dev
    assert "error" in dev  # present, may be None


async def test_reconstruct_line_not_found(client: Client) -> None:
    """bus.reconstructLine with unknown lineRef returns not_found."""
    result = await client.call_tool("knx_reconstruct_line", {
        "line_ref": "line-9.9",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_reconstruct_line_is_read(client: Client, transport) -> None:
    """bus.reconstructLine is a read -- it must NOT bump the revision."""
    rev_before = transport._revision
    await client.call_tool("knx_reconstruct_line", {
        "line_ref": "line-1.1",
    }, raise_on_error=False)
    assert transport._revision == rev_before


# -- knx_read_group_objects (device.readGroupObjects) --------------------------

async def test_read_group_objects(client: Client) -> None:
    """device.readGroupObjects returns comObjects with flags and partial=true."""
    result = await client.call_tool("knx_read_group_objects", {
        "address": "1.1.1",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["address"] == "1.1.1"
    assert isinstance(data["comObjects"], list)
    assert len(data["comObjects"]) > 0
    assert data["partial"] is True
    assert isinstance(data["note"], str)

    # Check comObject shape
    co = data["comObjects"][0]
    assert "number" in co
    assert isinstance(co["groupAddresses"], list)
    assert "flags" in co
    flags = co["flags"]
    for key in ("c", "r", "w", "t", "u"):
        assert key in flags
        assert isinstance(flags[key], bool)


async def test_read_group_objects_has_linked_gas(client: Client) -> None:
    """device.readGroupObjects includes linked group addresses."""
    result = await client.call_tool("knx_read_group_objects", {
        "address": "1.1.1",
    }, raise_on_error=False)
    data = tool_data(result)
    # Device dev-0001 at 1.1.1 has co-0001 linked to ga-0001 (address "1/0/1")
    co_with_links = [
        co for co in data["comObjects"]
        if len(co["groupAddresses"]) > 0
    ]
    assert len(co_with_links) > 0
    assert "1/0/1" in co_with_links[0]["groupAddresses"]


async def test_read_group_objects_not_found(client: Client) -> None:
    """device.readGroupObjects with unknown address returns not_found."""
    result = await client.call_tool("knx_read_group_objects", {
        "address": "9.9.9",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_read_group_objects_is_read(client: Client, transport) -> None:
    """device.readGroupObjects is a read -- it must NOT bump the revision."""
    rev_before = transport._revision
    await client.call_tool("knx_read_group_objects", {
        "address": "1.1.1",
    }, raise_on_error=False)
    assert transport._revision == rev_before


# -- knx_read_group_objects dual-mode (async/job) -----------------------------

async def test_read_group_objects_async_returns_job_id(client: Client) -> None:
    """device.readGroupObjects with use_job=true returns {jobId}."""
    result = await client.call_tool("knx_read_group_objects", {
        "address": "1.1.1",
        "use_job": True,
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data
    # Must NOT have comObjects at top level (it is a job reference)
    assert "comObjects" not in data


async def test_read_group_objects_async_job_result(client: Client) -> None:
    """Polling the job from use_job=true yields the same shape as sync mode."""
    result = await client.call_tool("knx_read_group_objects", {
        "address": "1.1.1",
        "use_job": True,
    }, raise_on_error=False)
    job_id = tool_data(result)["jobId"]

    status_result = await client.call_tool("knx_job_status", {
        "job_id": job_id,
    }, raise_on_error=False)
    status = tool_data(status_result)
    assert status["state"] == "done"
    job_result = status["result"]
    assert job_result["address"] == "1.1.1"
    assert isinstance(job_result["comObjects"], list)
    assert job_result["partial"] is True
    assert isinstance(job_result["note"], str)


async def test_read_group_objects_sync_default(client: Client) -> None:
    """device.readGroupObjects without use_job returns result directly (default)."""
    result = await client.call_tool("knx_read_group_objects", {
        "address": "1.1.1",
    }, raise_on_error=False)
    data = tool_data(result)
    # Sync mode: result has the payload directly, not a jobId
    assert "comObjects" in data
    assert "jobId" not in data


# -- address normalisation for readGroupObjects --------------------------------

async def test_read_group_objects_comma_address(client: Client) -> None:
    """device.readGroupObjects tolerates comma-separated address."""
    result = await client.call_tool("knx_read_group_objects", {
        "address": " 1, 1, 1 ",
    }, raise_on_error=False)
    data = tool_data(result)
    assert data["address"] == "1.1.1"
    assert isinstance(data["comObjects"], list)
