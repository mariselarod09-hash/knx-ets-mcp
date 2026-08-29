"""Tests for the programming / firmware workflow via FastMCP in-memory Client.

device.program now works in the mock -- returns a jobId.
job.status and job.cancel are now functional.

firmware.update is gated by the AddIn's persistent "Unattended firmware
update" preference.  The mock simulates the default (preference off) and
always returns ``approval_required``.  There is no per-request approval
token.
"""

from __future__ import annotations

from fastmcp import Client

from tests.conftest import tool_data, error_text


# -- device.program (automatic, no approval token) ----------------------------

async def test_program_device(client: Client) -> None:
    """device.program returns a jobId for the download operation."""
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_program_device_with_options(client: Client) -> None:
    """device.program accepts options as a string."""
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-0001",
        "options": "partial",
    }, raise_on_error=False)
    data = tool_data(result)
    assert "jobId" in data


async def test_program_device_not_found(client: Client) -> None:
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-9999",
    }, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


# -- firmware.update (preference-gated, no per-request token) -----------------

async def test_firmware_update_approval_required(client: Client) -> None:
    """firmware.update returns approval_required when the AddIn preference is off.

    The mock simulates the default state ("Unattended firmware update" = off).
    No approval_token parameter exists; the gate is solely the AddIn preference.
    """
    result = await client.call_tool("knx_update_firmware", {
        "device_ref": "dev-0001",
        "firmware": "fw-v2.0",
    }, raise_on_error=False)
    assert result.is_error
    assert "approval_required" in error_text(result)


# -- job.status / job.cancel (functional) --------------------------------------

async def test_job_status_not_found(client: Client) -> None:
    """job.status returns not_found for unknown job IDs."""
    result = await client.call_tool("knx_job_status", {"job_id": "any-job-id"}, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_job_cancel_not_found(client: Client) -> None:
    """job.cancel returns not_found for unknown job IDs."""
    result = await client.call_tool("knx_job_cancel", {"job_id": "any-job-id"}, raise_on_error=False)
    assert result.is_error
    assert "not_found" in error_text(result)


async def test_job_lifecycle(client: Client) -> None:
    """Full lifecycle: program -> status -> cancel."""
    # Start a program job
    result = await client.call_tool("knx_program_device", {
        "device_ref": "dev-0001",
    }, raise_on_error=False)
    data = tool_data(result)
    job_id = data["jobId"]

    # Check status
    status_result = await client.call_tool("knx_job_status", {
        "job_id": job_id,
    }, raise_on_error=False)
    status = tool_data(status_result)
    assert status["state"] == "done"

    # Cancel (idempotent on terminal state)
    cancel_result = await client.call_tool("knx_job_cancel", {
        "job_id": job_id,
    }, raise_on_error=False)
    cancel = tool_data(cancel_result)
    assert cancel["ok"] is True
