"""Client layer between MCP tools and the transport.

Responsibilities:
- Generate unique request IDs (UUID4).
- Generate a fresh UUID4 idempotency key per mutating operation, reused only
  when _request() itself retries after a transport failure (per protocol.md).
- Thread the session token into every request.
- Validate response envelopes (shape, id match).
- Surface protocol error codes as typed KnxBridgeError exceptions.
- One controlled reconnect+retry on transport failure.
- Serialize all request/response exchanges with an asyncio.Lock so concurrent
  MCP tool calls cannot interleave on the single-connection transport
  (Finding #10).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from knx_ets_mcp.transport.base import Transport


class KnxBridgeError(Exception):
    """Error returned by the AddIn via the IPC protocol."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# Methods that mutate the project and therefore require an idempotency key.
_MUTATING_METHODS = frozenset({
    "ga.create", "ga.delete", "ga.rename", "ga.setDescription", "ga.setDatapointType",
    "groupRange.create", "groupRange.delete",
    "device.addFromCatalog", "device.delete", "device.rename",
    "device.setAddress", "device.move",
    "link.create", "link.delete",
    "param.set", "device.program", "firmware.update",
    "area.create", "area.delete",
    "line.create", "line.delete",
    "comObject.setFlags",
    "device.setDescription", "device.setComment",
    "comObject.setDescription", "comObject.setFunctionText",
    "catalog.import", "catalog.internalize",
    # Phase B: Building structure + functions
    "building.create", "building.delete", "building.rename",
    "building.assignDevice", "building.unassignDevice",
    "buildingFunction.create", "buildingFunction.delete",
    "buildingFunction.linkGroupAddress", "buildingFunction.unlinkGroupAddress",
    # Phase C: Project management
    "project.undo", "project.redo",
    # Phase D: Bus operations (mutating only)
    "group.write", "device.unload",
    "bus.setIndividualAddress", "device.reset",
    # Reconciliation
    "device.unassign",
    # Phase G: Moves
    "groupRange.moveGroupAddress", "groupRange.moveGroupRange",
    "line.move", "buildingPart.move",
    # Phase G: Additional addresses
    "device.addAdditionalAddress", "device.removeAdditionalAddress",
    "line.addAdditionalGroupAddress", "line.removeAdditionalGroupAddress",
    # Phase G: Segments
    "segment.create", "segment.delete",
    # Phase G: Trades
    "trade.create", "trade.delete", "trade.assignDevice", "trade.unassignDevice",
    # Phase G: Bus interface / filter
    "busInterface.link", "busInterface.unlink",
    # Phase G: Parameter default
    "param.setDefault",
    # Phase G: Certificates
    "certificate.add", "certificate.delete",
    # Phase G: Tags
    "tag.create", "tag.delete",
    # Phase G: ToDos
    "todo.create", "todo.delete",
    # Phase G: Project history
    "projectHistory.add", "projectHistory.delete",
    # Batch: many project mutations in one atomic undo marker
    "batch.apply",
})


class KnxBridgeClient:
    """High-level client for the KNX ETS bridge protocol.

    Wraps a Transport and exposes one async method per protocol method.

    All request/response exchanges are serialized by an internal asyncio.Lock
    so that concurrent MCP tool invocations cannot interleave reads and writes
    on the single-connection transport (Finding #10).
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        # Serializes the entire send+receive exchange so concurrent tool calls
        # cannot race on the shared transport connection (Finding #10).
        self._lock = asyncio.Lock()

    @property
    def token(self) -> str:
        return self._transport.token

    # -- Generic request plumbing ----------------------------------------------

    async def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        expected_revision: str | None = None,
    ) -> Any:
        params = params or {}
        request_id = str(uuid.uuid4())

        req: dict[str, Any] = {
            "id": request_id,
            "token": self.token,
            "method": method,
            "params": params,
        }

        # Fresh UUID4 per operation; reused across retries of THIS call only.
        if method in _MUTATING_METHODS:
            req["idempotencyKey"] = str(uuid.uuid4())

        if expected_revision is not None:
            req["expectedProjectRevision"] = expected_revision

        # The lock ensures the entire send+receive (including one retry) is
        # atomic.  Without it, concurrent calls can interleave on the same
        # TCP stream / pipe handle and consume each other's responses.
        async with self._lock:
            response = await self._send_with_retry(req)

        self._validate_response(response, request_id)

        if not response.get("ok"):
            error = response.get("error", {})
            if not isinstance(error, dict):
                raise KnxBridgeError("internal", "Malformed error in response")
            raise KnxBridgeError(
                code=error.get("code", "internal"),
                message=error.get("message", "Unknown error from AddIn"),
            )

        return response.get("result")

    async def _send_with_retry(self, req: dict[str, Any]) -> dict[str, Any]:
        """Send request; on transport failure, close, reconnect, and retry once.

        The same request dict (including idempotencyKey) is reused so the
        AddIn deduplicates a lost-response scenario correctly.

        On any transport error the connection is closed BEFORE reconnecting so
        a potentially de-synced stream (e.g. partial read after timeout) is
        discarded rather than reused (Finding #10).
        """
        try:
            return await self._transport.send(req)
        except (ConnectionError, TimeoutError, OSError):
            # Close the potentially de-synced connection first (Finding #10).
            try:
                await self._transport.close()
            except Exception:
                pass  # best-effort; we're about to reconnect anyway
            # One reconnect attempt
            try:
                await self._transport.connect()
            except Exception as reconnect_exc:
                raise KnxBridgeError(
                    "internal", "Transport failed and reconnect unsuccessful"
                ) from reconnect_exc
            try:
                return await self._transport.send(req)
            except Exception as retry_exc:
                raise KnxBridgeError(
                    "internal", "Transport failed after reconnect"
                ) from retry_exc

    @staticmethod
    def _validate_response(response: Any, expected_id: str) -> None:
        """Validate the response envelope shape and id match."""
        if not isinstance(response, dict):
            raise KnxBridgeError("internal", "Invalid response: not a JSON object")
        if response.get("id") != expected_id:
            raise KnxBridgeError(
                "internal",
                "Response id mismatch (stale or misrouted response)",
            )
        # Must have either ok+result or ok==false+error
        is_ok = response.get("ok")
        if is_ok and "result" not in response:
            raise KnxBridgeError("internal", "Success response missing result field")
        if not is_ok and "error" not in response:
            raise KnxBridgeError("internal", "Error response missing error field")

    # -- Read methods ----------------------------------------------------------

    async def bridge_info(self) -> dict[str, Any]:
        """bridge.info -> {addinVersion, sdkVersion, projectName, projectId, projectRevision, knxIpOnly}"""
        return await self._request("bridge.info")

    async def project_info(self) -> dict[str, Any]:
        """project.info -> {projectId, name, groupAddressStyle, revision}"""
        return await self._request("project.info")

    async def list_devices(self) -> list[dict[str, Any]]:
        """devices.list -> [{ref, address, name, product, line}]"""
        return await self._request("devices.list")

    async def list_group_addresses(self) -> list[dict[str, Any]]:
        """ga.list -> [{ref, address, name, dpt}]"""
        return await self._request("ga.list")

    async def list_comobjects(self, device_ref: str) -> list[dict[str, Any]]:
        """comobjects.list -> [{ref, number, name, description, functionText, text, dpt,
        flags, links, channel?}]. channel is the function-grouping label when the object
        belongs to a device channel."""
        return await self._request("comobjects.list", {"deviceRef": device_ref})

    async def list_topology(self) -> list[dict[str, Any]]:
        """topology.list -> [{areaRef, address, name, lines}]"""
        return await self._request("topology.list")

    async def list_catalog_manufacturers(self) -> list[dict[str, Any]]:
        """catalog.manufacturers -> [{manufacturerRef, name}]"""
        return await self._request("catalog.manufacturers")

    async def search_catalog(
        self,
        query: str,
        manufacturer_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """catalog.search -> [{catalogItemRef, manufacturer, name, orderNumber}]"""
        params: dict[str, Any] = {"query": query}
        if manufacturer_ref is not None:
            params["manufacturerRef"] = manufacturer_ref
        return await self._request("catalog.search", params)

    async def search_catalog_online(
        self,
        query: str,
        manufacturer_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """catalog.search_online -> [{unifiedCatalogItemRef, manufacturer, name, orderNumber}]"""
        params: dict[str, Any] = {"query": query}
        if manufacturer_ref is not None:
            params["manufacturerRef"] = manufacturer_ref
        return await self._request("catalog.search_online", params)

    async def list_parameters(self, device_ref: str) -> list[dict[str, Any]]:
        """params.list -> [{parameterRef, name, value, isDefault, isActive,
        text?, unit?, access?, options?[{value,text}], min?, max?}].

        isActive is post-visibility: false = deactivated by a controlling parameter
        (set has no effect). options/min/max give the valid value range."""
        return await self._request("params.list", {"deviceRef": device_ref})

    # -- Mutation methods ------------------------------------------------------

    async def create_group_address(
        self,
        name: str,
        address: str,
        dpt_main: int | None = None,
        dpt_sub: int | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """ga.create -> {ref, address}"""
        params: dict[str, Any] = {"name": name, "address": address}
        if dpt_main is not None:
            params["dptMain"] = dpt_main
        if dpt_sub is not None:
            params["dptSub"] = dpt_sub
        return await self._request("ga.create", params, expected_revision)

    async def add_device(
        self,
        line_ref: str,
        catalog_item_ref: str,
        address: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.addFromCatalog -> {ref, address}"""
        return await self._request("device.addFromCatalog", {
            "lineRef": line_ref,
            "catalogItemRef": catalog_item_ref,
            "address": address,
        }, expected_revision)

    async def link(
        self,
        com_object_ref: str,
        ga_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """link.create -> {comObjectRef, gaRef, dpt?, gaDpt?, dptWarning?}.

        dptWarning is a soft advisory when the com-object and group-address DPTs have
        different main numbers (the link still proceeds)."""
        return await self._request("link.create", {
            "comObjectRef": com_object_ref,
            "gaRef": ga_ref,
        }, expected_revision)

    async def unlink(
        self,
        com_object_ref: str,
        ga_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """link.delete -> {ok}"""
        return await self._request("link.delete", {
            "comObjectRef": com_object_ref,
            "gaRef": ga_ref,
        }, expected_revision)

    async def set_parameter(
        self,
        device_ref: str,
        parameter_ref: str,
        value: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """param.set -> {ok}"""
        return await self._request("param.set", {
            "deviceRef": device_ref,
            "parameterRef": parameter_ref,
            "value": value,
        }, expected_revision)

    # -- Catalog mutation methods (modify local product store, not project) ----

    async def import_product(
        self,
        path: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """catalog.import -> {imported: [{catalogItemRef, manufacturer, name, orderNumber}]}"""
        return await self._request("catalog.import", {
            "path": path,
        }, expected_revision)

    async def internalize_product(
        self,
        unified_catalog_item_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """catalog.internalize -> {catalogItemRef}"""
        return await self._request("catalog.internalize", {
            "unifiedCatalogItemRef": unified_catalog_item_ref,
        }, expected_revision)

    # -- Phase A: Group Address editing ----------------------------------------

    async def delete_group_address(
        self,
        ga_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """ga.delete -> {ok}"""
        return await self._request("ga.delete", {
            "groupAddressRef": ga_ref,
        }, expected_revision)

    async def rename_group_address(
        self,
        ga_ref: str,
        name: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """ga.rename -> {ref, address, name, dpt}"""
        return await self._request("ga.rename", {
            "groupAddressRef": ga_ref,
            "name": name,
        }, expected_revision)

    async def set_group_address_description(
        self,
        ga_ref: str,
        description: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """ga.setDescription -> {ref, address, name, description, dpt}"""
        return await self._request("ga.setDescription", {
            "groupAddressRef": ga_ref,
            "description": description,
        }, expected_revision)

    async def set_group_address_dpt(
        self,
        ga_ref: str,
        dpt_main: int,
        dpt_sub: int | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """ga.setDatapointType -> {ref, address, name, dpt}"""
        params: dict[str, Any] = {
            "groupAddressRef": ga_ref,
            "dptMain": dpt_main,
        }
        if dpt_sub is not None:
            params["dptSub"] = dpt_sub
        return await self._request("ga.setDatapointType", params, expected_revision)

    # -- Phase A: Group Range --------------------------------------------------

    async def create_group_range(
        self,
        name: str,
        address: int,
        parent_group_range_ref: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """groupRange.create -> {ref, name, address}"""
        params: dict[str, Any] = {"name": name, "address": address}
        if parent_group_range_ref is not None:
            params["parentGroupRangeRef"] = parent_group_range_ref
        return await self._request("groupRange.create", params, expected_revision)

    async def delete_group_range(
        self,
        group_range_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """groupRange.delete -> {ok}"""
        return await self._request("groupRange.delete", {
            "groupRangeRef": group_range_ref,
        }, expected_revision)

    # -- Phase A: Device editing -----------------------------------------------

    async def delete_device(
        self,
        device_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.delete -> {ok}"""
        return await self._request("device.delete", {
            "deviceRef": device_ref,
        }, expected_revision)

    async def rename_device(
        self,
        device_ref: str,
        name: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.rename -> {ref, address, name, product, line}"""
        return await self._request("device.rename", {
            "deviceRef": device_ref,
            "name": name,
        }, expected_revision)

    async def set_device_address(
        self,
        device_ref: str,
        address: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.setAddress -> {ref, address, name, product, line}"""
        return await self._request("device.setAddress", {
            "deviceRef": device_ref,
            "address": address,
        }, expected_revision)

    async def move_device(
        self,
        device_ref: str,
        line_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.move -> always returns not_supported error"""
        return await self._request("device.move", {
            "deviceRef": device_ref,
            "lineRef": line_ref,
        }, expected_revision)

    # -- Phase A: Topology -----------------------------------------------------

    async def create_area(
        self,
        name: str,
        address: int | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """area.create -> {areaRef, address, name, lines}"""
        params: dict[str, Any] = {"name": name}
        if address is not None:
            params["address"] = address
        return await self._request("area.create", params, expected_revision)

    async def delete_area(
        self,
        area_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """area.delete -> {ok}"""
        return await self._request("area.delete", {
            "areaRef": area_ref,
        }, expected_revision)

    async def create_line(
        self,
        area_ref: str,
        name: str,
        address: int | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """line.create -> {lineRef, address, name, segments}"""
        params: dict[str, Any] = {"areaRef": area_ref, "name": name}
        if address is not None:
            params["address"] = address
        return await self._request("line.create", params, expected_revision)

    async def delete_line(
        self,
        line_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """line.delete -> {ok}"""
        return await self._request("line.delete", {
            "lineRef": line_ref,
        }, expected_revision)

    # -- Phase A: ComObject flags ----------------------------------------------

    async def set_comobject_flags(
        self,
        com_object_ref: str,
        communication_flag: bool | None = None,
        read_flag: bool | None = None,
        write_flag: bool | None = None,
        transmit_flag: bool | None = None,
        update_flag: bool | None = None,
        read_on_init_flag: bool | None = None,
        priority: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """comObject.setFlags -> {ref, communicationFlag, readFlag, ...}"""
        params: dict[str, Any] = {"comObjectRef": com_object_ref}
        if communication_flag is not None:
            params["communicationFlag"] = communication_flag
        if read_flag is not None:
            params["readFlag"] = read_flag
        if write_flag is not None:
            params["writeFlag"] = write_flag
        if transmit_flag is not None:
            params["transmitFlag"] = transmit_flag
        if update_flag is not None:
            params["updateFlag"] = update_flag
        if read_on_init_flag is not None:
            params["readOnInitFlag"] = read_on_init_flag
        if priority is not None:
            params["priority"] = priority
        return await self._request("comObject.setFlags", params, expected_revision)

    # -- Label setters (v0.1.16) ------------------------------------------------

    async def set_device_description(
        self,
        device_ref: str,
        description: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.setDescription -> updated device dict"""
        return await self._request("device.setDescription", {
            "deviceRef": device_ref,
            "description": description,
        }, expected_revision)

    async def set_device_comment(
        self,
        device_ref: str,
        comment: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.setComment -> {ok}"""
        return await self._request("device.setComment", {
            "deviceRef": device_ref,
            "comment": comment,
        }, expected_revision)

    async def set_comobject_description(
        self,
        com_object_ref: str,
        description: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """comObject.setDescription -> {ref, description}"""
        return await self._request("comObject.setDescription", {
            "comObjectRef": com_object_ref,
            "description": description,
        }, expected_revision)

    async def set_comobject_function_text(
        self,
        com_object_ref: str,
        function_text: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """comObject.setFunctionText -> {ref, functionText}"""
        return await self._request("comObject.setFunctionText", {
            "comObjectRef": com_object_ref,
            "functionText": function_text,
        }, expected_revision)

    # -- Programming (job-based) -----------------------------------------------

    async def program_device(
        self,
        device_ref: str,
        options: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.program -> {jobId}

        Downloads application/parameters/GA tables to a physical device.
        This is reversible and automatic -- no approval token required.
        Options: "partial" (default -- writes only the changes, no individual
        address / no programming-button press), "all" (full incl. individual
        address), "application", "network", "networkBySerial".
        """
        params: dict[str, Any] = {
            "deviceRef": device_ref,
        }
        if options is not None:
            params["options"] = options
        return await self._request("device.program", params, expected_revision)

    async def update_firmware(
        self,
        device_ref: str,
        firmware: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """firmware.update -> {jobId}

        Flashes firmware onto a physical device.  Potentially irreversible.
        Gated by the AddIn's "Unattended firmware update" preference; if not
        enabled the AddIn returns approval_required.
        """
        params: dict[str, Any] = {
            "deviceRef": device_ref,
            "firmware": firmware,
        }
        return await self._request("firmware.update", params, expected_revision)

    async def job_status(self, job_id: str) -> dict[str, Any]:
        """job.status -> {state, percent, error?}"""
        return await self._request("job.status", {"jobId": job_id})

    async def job_cancel(self, job_id: str) -> dict[str, Any]:
        """job.cancel -> {ok}"""
        return await self._request("job.cancel", {"jobId": job_id})

    # -- Phase B: Building structure -------------------------------------------

    async def list_building(self) -> list[dict[str, Any]]:
        """building.list -> [{ref, name, type, children, devices, functions}]"""
        return await self._request("building.list")

    async def create_building_part(
        self,
        name: str,
        type: str,
        parent_building_part_ref: str | None = None,
        space_usage: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """building.create -> {ref, name, type, children, devices, functions}"""
        params: dict[str, Any] = {"name": name, "type": type}
        if parent_building_part_ref is not None:
            params["parentBuildingPartRef"] = parent_building_part_ref
        if space_usage is not None:
            params["spaceUsage"] = space_usage
        return await self._request("building.create", params, expected_revision)

    async def delete_building_part(
        self,
        building_part_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """building.delete -> {ok}"""
        return await self._request("building.delete", {
            "buildingPartRef": building_part_ref,
        }, expected_revision)

    async def rename_building_part(
        self,
        building_part_ref: str,
        name: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """building.rename -> {ref, name, type, ...}"""
        return await self._request("building.rename", {
            "buildingPartRef": building_part_ref,
            "name": name,
        }, expected_revision)

    async def assign_device(
        self,
        building_part_ref: str,
        device_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """building.assignDevice -> {ok}"""
        return await self._request("building.assignDevice", {
            "buildingPartRef": building_part_ref,
            "deviceRef": device_ref,
        }, expected_revision)

    async def unassign_device(
        self,
        building_part_ref: str,
        device_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """building.unassignDevice -> {ok}"""
        return await self._request("building.unassignDevice", {
            "buildingPartRef": building_part_ref,
            "deviceRef": device_ref,
        }, expected_revision)

    # -- Phase B: Building functions -------------------------------------------

    async def create_building_function(
        self,
        building_part_ref: str,
        name: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """buildingFunction.create -> {ref, name, groupAddresses}"""
        return await self._request("buildingFunction.create", {
            "buildingPartRef": building_part_ref,
            "name": name,
        }, expected_revision)

    async def delete_building_function(
        self,
        building_function_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """buildingFunction.delete -> {ok}"""
        return await self._request("buildingFunction.delete", {
            "buildingFunctionRef": building_function_ref,
        }, expected_revision)

    async def link_building_function_ga(
        self,
        building_function_ref: str,
        group_address_refs: list[str],
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """buildingFunction.linkGroupAddress -> {ok}"""
        return await self._request("buildingFunction.linkGroupAddress", {
            "buildingFunctionRef": building_function_ref,
            "groupAddressRefs": group_address_refs,
        }, expected_revision)

    async def unlink_building_function_ga(
        self,
        building_function_ref: str,
        group_address_refs: list[str],
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """buildingFunction.unlinkGroupAddress -> {ok}"""
        return await self._request("buildingFunction.unlinkGroupAddress", {
            "buildingFunctionRef": building_function_ref,
            "groupAddressRefs": group_address_refs,
        }, expected_revision)

    # -- Phase C: Project management -------------------------------------------

    async def project_save(self) -> dict[str, Any]:
        """project.save -> always not_supported"""
        return await self._request("project.save")

    async def project_export(
        self,
        path: str,
        include_catalog: bool = False,
    ) -> dict[str, Any]:
        """project.export -> {ok: bool}"""
        return await self._request("project.export", {
            "path": path,
            "includeCatalog": include_catalog,
        })

    async def project_backup(self, path: str) -> dict[str, Any]:
        """project.backup -> always not_supported"""
        return await self._request("project.backup", {"path": path})

    async def project_undo(
        self,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """project.undo -> {ok}"""
        return await self._request("project.undo", {}, expected_revision)

    async def project_redo(
        self,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """project.redo -> {ok}"""
        return await self._request("project.redo", {}, expected_revision)

    async def project_export_semantic(self) -> dict[str, Any]:
        """project.exportSemantic -> always not_supported"""
        return await self._request("project.exportSemantic")

    # -- Phase E: Catalog browsing ---------------------------------------------

    async def browse_products(
        self,
        manufacturer_ref: str,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """catalog.browseProducts -> [{catalogItemRef, manufacturer, name, orderNumber}]"""
        return await self._request("catalog.browseProducts", {
            "manufacturerRef": manufacturer_ref,
            "offset": offset,
            "limit": limit,
        })

    async def product_info(self, catalog_item_ref: str) -> dict[str, Any]:
        """catalog.productInfo -> {catalogItemRef, manufacturer, name, orderNumber, description, mediumTypes}"""
        return await self._request("catalog.productInfo", {
            "catalogItemRef": catalog_item_ref,
        })

    # -- Phase D: Bus / Online operations --------------------------------------

    async def bus_ping(self, address: str) -> dict[str, Any]:
        """bus.ping -> {alive: bool}"""
        return await self._request("bus.ping", {"address": address})

    async def bus_scan_line(self, line_ref: str) -> dict[str, Any]:
        """bus.scanLine -> {jobId}"""
        return await self._request("bus.scanLine", {"lineRef": line_ref})

    async def device_read_info(self, device_ref: str) -> dict[str, Any]:
        """device.readInfo -> {maskVersion, maskVersionId}"""
        return await self._request("device.readInfo", {"deviceRef": device_ref})

    async def device_compare(self, device_ref: str) -> dict[str, Any]:
        """device.compare -> {compared, partial, note}

        Limited compare: reads a subset of device properties from the bus
        and compares them against the project model.  Returns partial=true
        because a full byte-level compare is not feasible via the SDK's
        high-level API.
        """
        return await self._request("device.compare", {"deviceRef": device_ref})

    async def reconstruct_line(self, line_ref: str) -> dict[str, Any]:
        """bus.reconstructLine -> {jobId}

        Scan a line for physical devices and compare/reconcile against the
        project model.  Long-running: poll with job_status().
        """
        return await self._request("bus.reconstructLine", {"lineRef": line_ref})

    async def read_group_objects(
        self,
        address: str,
        use_job: bool = False,
    ) -> dict[str, Any]:
        """device.readGroupObjects -> sync: {address, comObjects, partial, note} or async: {jobId}."""
        params: dict[str, Any] = {"address": address}
        if use_job:
            params["async"] = True
        return await self._request("device.readGroupObjects", params)

    async def group_read(self, ga_ref: str) -> dict[str, Any]:
        """group.read -> {value: hex_string | null}"""
        return await self._request("group.read", {"gaRef": ga_ref})

    async def group_write(
        self,
        ga_ref: str,
        value: str,
        less_7_bits: bool = False,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """group.write -> {acknowledged: bool}"""
        params: dict[str, Any] = {"gaRef": ga_ref, "value": value}
        if less_7_bits:
            params["less7Bits"] = True
        return await self._request("group.write", params, expected_revision)

    async def group_monitor(
        self,
        line_ref: str = "default",
        duration_ms: int = 10000,
    ) -> dict[str, Any]:
        """group.monitor -> {jobId}"""
        return await self._request("group.monitor", {
            "lineRef": line_ref,
            "durationMs": duration_ms,
        })

    async def device_unload(
        self,
        device_ref: str,
        full_unload: bool = False,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.unload -> {jobId}"""
        params: dict[str, Any] = {"deviceRef": device_ref}
        if full_unload:
            params["fullUnload"] = True
        return await self._request("device.unload", params, expected_revision)

    async def bus_set_individual_address(
        self,
        device_ref: str,
        current_address: str,
    ) -> dict[str, Any]:
        """bus.setIndividualAddress -> {jobId}"""
        return await self._request("bus.setIndividualAddress", {
            "deviceRef": device_ref,
            "currentAddress": current_address,
        })

    # -- Reconciliation methods ------------------------------------------------

    async def device_unassign(
        self,
        device_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.unassign -> {ok}

        Detach a device from its line and move it to the unassigned-devices
        collection.  The device is NOT deleted -- it can be re-assigned later.
        MUTATING: requires idempotency key.
        """
        return await self._request("device.unassign", {
            "deviceRef": device_ref,
        }, expected_revision)

    # -- Phase G: Bus writes (NOT mutating) ------------------------------------

    async def device_reset(self, device_ref: str) -> dict[str, Any]:
        """device.reset -> {ok}"""
        return await self._request("device.reset", {"deviceRef": device_ref})

    # -- Phase G: Moves --------------------------------------------------------

    async def move_group_address(
        self,
        target_group_range_ref: str,
        group_address_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """groupRange.moveGroupAddress -> {ok}"""
        return await self._request("groupRange.moveGroupAddress", {
            "targetGroupRangeRef": target_group_range_ref,
            "groupAddressRef": group_address_ref,
        }, expected_revision)

    async def move_group_range(
        self,
        target_group_range_ref: str,
        source_group_range_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """groupRange.moveGroupRange -> {ok}"""
        return await self._request("groupRange.moveGroupRange", {
            "targetGroupRangeRef": target_group_range_ref,
            "sourceGroupRangeRef": source_group_range_ref,
        }, expected_revision)

    async def move_line(
        self,
        target_area_ref: str,
        line_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """line.move -> {ok}"""
        return await self._request("line.move", {
            "targetAreaRef": target_area_ref,
            "lineRef": line_ref,
        }, expected_revision)

    async def move_building_part(
        self,
        target_building_part_ref: str,
        source_building_part_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """buildingPart.move -> {ok}"""
        return await self._request("buildingPart.move", {
            "targetBuildingPartRef": target_building_part_ref,
            "sourceBuildingPartRef": source_building_part_ref,
        }, expected_revision)

    # -- Phase G: Additional addresses -----------------------------------------

    async def add_device_additional_address(
        self,
        device_ref: str,
        address: int,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.addAdditionalAddress -> {deviceRef, address}"""
        return await self._request("device.addAdditionalAddress", {
            "deviceRef": device_ref,
            "address": address,
        }, expected_revision)

    async def remove_device_additional_address(
        self,
        device_ref: str,
        address: int,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """device.removeAdditionalAddress -> {ok}"""
        return await self._request("device.removeAdditionalAddress", {
            "deviceRef": device_ref,
            "address": address,
        }, expected_revision)

    async def add_line_additional_ga(
        self,
        line_ref: str,
        group_address_refs: list[str],
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """line.addAdditionalGroupAddress -> {ok}"""
        return await self._request("line.addAdditionalGroupAddress", {
            "lineRef": line_ref,
            "groupAddressRefs": group_address_refs,
        }, expected_revision)

    async def remove_line_additional_ga(
        self,
        line_ref: str,
        group_address_refs: list[str],
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """line.removeAdditionalGroupAddress -> {ok}"""
        return await self._request("line.removeAdditionalGroupAddress", {
            "lineRef": line_ref,
            "groupAddressRefs": group_address_refs,
        }, expected_revision)

    # -- Phase G: Segments -----------------------------------------------------

    async def create_segment(
        self,
        line_ref: str,
        name: str,
        medium_type: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """segment.create -> {segmentRef, name}"""
        return await self._request("segment.create", {
            "lineRef": line_ref,
            "name": name,
            "mediumType": medium_type,
        }, expected_revision)

    async def delete_segment(
        self,
        segment_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """segment.delete -> {ok}"""
        return await self._request("segment.delete", {
            "segmentRef": segment_ref,
        }, expected_revision)

    # -- Phase G: Trades -------------------------------------------------------

    async def list_trades(self) -> list[dict[str, Any]]:
        """trades.list -> [{ref, name, number, devices}]"""
        return await self._request("trades.list")

    async def create_trade(
        self,
        name: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """trade.create -> {ref, name, number, devices}"""
        return await self._request("trade.create", {"name": name}, expected_revision)

    async def delete_trade(
        self,
        trade_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """trade.delete -> {ok}"""
        return await self._request("trade.delete", {
            "tradeRef": trade_ref,
        }, expected_revision)

    async def trade_assign_device(
        self,
        trade_ref: str,
        device_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """trade.assignDevice -> {ok}"""
        return await self._request("trade.assignDevice", {
            "tradeRef": trade_ref,
            "deviceRef": device_ref,
        }, expected_revision)

    async def trade_unassign_device(
        self,
        trade_ref: str,
        device_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """trade.unassignDevice -> {ok}"""
        return await self._request("trade.unassignDevice", {
            "tradeRef": trade_ref,
            "deviceRef": device_ref,
        }, expected_revision)

    # -- Phase G: Bus interface / filter ---------------------------------------

    async def bus_interface_link(
        self,
        device_ref: str,
        group_address_refs: list[str],
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """busInterface.link -> {ok}"""
        return await self._request("busInterface.link", {
            "deviceRef": device_ref,
            "groupAddressRefs": group_address_refs,
        }, expected_revision)

    async def bus_interface_unlink(
        self,
        device_ref: str,
        group_address_refs: list[str],
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """busInterface.unlink -> {ok}"""
        return await self._request("busInterface.unlink", {
            "deviceRef": device_ref,
            "groupAddressRefs": group_address_refs,
        }, expected_revision)

    # -- Phase G: Parameter default --------------------------------------------

    async def set_parameter_default(
        self,
        device_ref: str,
        parameter_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """param.setDefault -> {ok}"""
        return await self._request("param.setDefault", {
            "deviceRef": device_ref,
            "parameterRef": parameter_ref,
        }, expected_revision)

    # -- Phase G: KNX Secure certificates --------------------------------------

    async def list_certificates(self) -> list[dict[str, Any]]:
        """certificates.list -> [{ref, serialNumber, deviceRef}]"""
        return await self._request("certificates.list")

    async def add_certificate(
        self,
        value: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """certificate.add -> {ref}"""
        return await self._request("certificate.add", {
            "value": value,
        }, expected_revision)

    async def delete_certificate(
        self,
        certificate_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """certificate.delete -> {ok}"""
        return await self._request("certificate.delete", {
            "certificateRef": certificate_ref,
        }, expected_revision)

    # -- Phase G: Navigation ---------------------------------------------------

    async def navigate_to(self, ref: str) -> dict[str, Any]:
        """project.navigateTo -> {ok}"""
        return await self._request("project.navigateTo", {"ref": ref})

    # -- Phase G: Tags ---------------------------------------------------------

    async def list_tags(self) -> list[dict[str, Any]]:
        """tags.list -> [{ref, label, color}]"""
        return await self._request("tags.list")

    async def create_tag(
        self,
        label: str,
        color: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """tag.create -> {ref, label, color}"""
        params: dict[str, Any] = {"label": label}
        if color is not None:
            params["color"] = color
        return await self._request("tag.create", params, expected_revision)

    async def delete_tag(
        self,
        tag_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """tag.delete -> {ok}"""
        return await self._request("tag.delete", {
            "tagRef": tag_ref,
        }, expected_revision)

    # -- Phase G: ToDos --------------------------------------------------------

    async def list_todos(self) -> list[dict[str, Any]]:
        """todos.list -> [{ref, description, objectPath, status}]"""
        return await self._request("todos.list")

    async def create_todo(
        self,
        description: str,
        object_path: str | None = None,
        status: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """todo.create -> {ref, description, objectPath, status}"""
        params: dict[str, Any] = {"description": description}
        if object_path is not None:
            params["objectPath"] = object_path
        if status is not None:
            params["status"] = status
        return await self._request("todo.create", params, expected_revision)

    async def delete_todo(
        self,
        todo_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """todo.delete -> {ok}"""
        return await self._request("todo.delete", {
            "todoRef": todo_ref,
        }, expected_revision)

    # -- Phase G: Channels / modules -------------------------------------------

    async def device_channels(self, device_ref: str) -> dict[str, Any]:
        """device.channels -> {channels, modules}"""
        return await self._request("device.channels", {
            "deviceRef": device_ref,
        })

    async def application_dynamic(self, device_ref: str) -> dict[str, Any]:
        """application.dynamic -> {xml}. The app-program dynamic UI tree
        (ParameterBlock/Channel/ParameterRefRef), authoritative source for mapping a
        parameter to its UI block/channel."""
        return await self._request("application.dynamic", {
            "deviceRef": device_ref,
        })

    # -- Phase G: Project history ----------------------------------------------

    async def list_project_history(self) -> list[dict[str, Any]]:
        """projectHistory.list -> [{ref, date, text, detail, user}]"""
        return await self._request("projectHistory.list")

    async def add_project_history(
        self,
        text: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """projectHistory.add -> {ref, date, text, detail, user}"""
        return await self._request("projectHistory.add", {
            "text": text,
        }, expected_revision)

    async def delete_project_history(
        self,
        history_ref: str,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """projectHistory.delete -> {ok}"""
        return await self._request("projectHistory.delete", {
            "historyRef": history_ref,
        }, expected_revision)

    # -- Batch: many project mutations in one atomic undo marker ---------------

    async def batch_apply(
        self,
        operations: list[dict[str, Any]],
        atomic: bool = True,
        validate_only: bool = False,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """batch.apply -> {applied, atomic, rolledBack, total, ok, failed, skipped, results[]}.

        validate_only=True -> read-only pre-flight: {validated, atomic, total, valid,
        invalid, results:[{index, method, valid, issues[]}]}, nothing mutated."""
        return await self._request("batch.apply", {
            "operations": operations,
            "atomic": atomic,
            "validateOnly": validate_only,
        }, expected_revision)
