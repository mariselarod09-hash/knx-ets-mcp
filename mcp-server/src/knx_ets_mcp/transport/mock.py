"""In-process mock transport that simulates the ETS6 AddIn.

Implements the protocol methods against an in-memory project model with
a few fake devices, group addresses, and communication objects.  Used for
tests on macOS where no real ETS6 is available.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from knx_ets_mcp.transport.base import Transport

MOCK_TOKEN = "mock-token-for-testing"

# Canonical todo status values (matches C# ToDoItemInfo)
_VALID_TODO_STATUSES = {"Open", "Accomplished"}

# Methods allowed inside batch.apply -- mirrors the C# BatchableMethods allowlist.
# Fast, undo-reversible project mutations only; no bus/programming/catalog-import
# ops, no nested batch.
_BATCHABLE_METHODS = frozenset({
    "ga.create", "ga.delete", "ga.rename", "ga.setDescription", "ga.setDatapointType",
    "groupRange.create", "groupRange.delete",
    "device.addFromCatalog", "device.delete", "device.rename",
    "device.setAddress", "device.unassign",
    "device.setDescription", "device.setComment",
    "link.create", "link.delete",
    "param.set", "param.setDefault",
    "area.create", "area.delete",
    "line.create", "line.delete",
    "comObject.setFlags", "comObject.setDescription", "comObject.setFunctionText",
    "building.create", "building.delete", "building.rename",
    "building.assignDevice", "building.unassignDevice",
    "buildingFunction.create", "buildingFunction.delete",
    "buildingFunction.linkGroupAddress", "buildingFunction.unlinkGroupAddress",
    "groupRange.moveGroupAddress", "groupRange.moveGroupRange",
    "line.move", "buildingPart.move",
    "device.addAdditionalAddress", "device.removeAdditionalAddress",
    "line.addAdditionalGroupAddress", "line.removeAdditionalGroupAddress",
    "segment.create", "segment.delete",
    "trade.create", "trade.delete",
    "trade.assignDevice", "trade.unassignDevice",
    "busInterface.link", "busInterface.unlink",
    "certificate.add", "certificate.delete",
    "tag.create", "tag.delete",
    "todo.create", "todo.delete",
    "projectHistory.add", "projectHistory.delete",
})

# Mutable state attributes snapshotted for atomic batch rollback.
_MOCK_STATE_ATTRS = (
    "_revision", "_revision_counter", "_project", "_devices", "_group_addresses",
    "_com_objects", "_parameters", "_topology", "_group_ranges", "_building_parts",
    "_building_functions", "_trades", "_trade_counter", "_tags", "_tag_counter",
    "_todos", "_todo_counter", "_certificates", "_additional_device_addresses",
    "_additional_line_gas", "_bus_interface_links", "_project_history",
    "_history_counter", "_segments",
)

_MAX_BATCH_OPERATIONS = 500

# Mock-only simulated name-length limit, so truncation reporting is testable.
# Real ETS enforces per-entity MaxNameLength; the exact value may differ.
_MOCK_MAX_NAME_LEN = 40


def _make_ref(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _normalize_individual_address(raw: str) -> str:
    """Accept '1.1.5', '1, 1, 5', ' 1 , 1 , 5 ' etc. and normalise to '1.1.5'."""
    # Allow both '.' and ',' as separators, strip whitespace
    sep = "," if "," in raw else "."
    parts = [p.strip() for p in raw.split(sep)]
    if len(parts) != 3:
        raise _ProtocolError("invalid_params",
                             f"Invalid address format: '{raw}'")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise _ProtocolError("invalid_params",
                             f"Invalid address format: '{raw}'")
    return f"{nums[0]}.{nums[1]}.{nums[2]}"


class MockTransport(Transport):
    """Fake AddIn that processes protocol requests in-memory."""

    def __init__(self) -> None:
        self._token = MOCK_TOKEN
        self._connected = False

        # -- In-memory project model ------------------------------------------
        self._revision = "rev-001"
        self._revision_counter = 1

        self._project = {
            "projectId": "proj-001",
            "name": "Test KNX Project",
            "groupAddressStyle": "ThreeLevel",
        }

        # Devices keyed by ref
        self._devices: dict[str, dict[str, Any]] = {}
        # Group addresses keyed by ref
        self._group_addresses: dict[str, dict[str, Any]] = {}
        # Com-objects keyed by ref (nested under device but also indexed here)
        self._com_objects: dict[str, dict[str, Any]] = {}
        # Parameters keyed by ref
        self._parameters: dict[str, dict[str, Any]] = {}
        # Topology: list of areas (each with nested lines/segments)
        self._topology: list[dict[str, Any]] = []
        # Catalog manufacturers keyed by manufacturerRef
        self._manufacturers: dict[str, dict[str, Any]] = {}
        # Catalog items keyed by catalogItemRef
        self._catalog_items: dict[str, dict[str, Any]] = {}
        # Online (unified) catalog items keyed by unifiedCatalogItemRef
        self._unified_catalog_items: dict[str, dict[str, Any]] = {}
        # Valid import paths -> list of items they would produce
        self._importable_paths: dict[str, list[dict[str, Any]]] = {}
        # Links: set of (com_object_ref, ga_ref)
        self._links: set[tuple[str, str]] = set()
        # Group ranges keyed by ref
        self._group_ranges: dict[str, dict[str, Any]] = {}
        # Jobs keyed by job_id
        self._jobs: dict[str, dict[str, Any]] = {}
        # Building parts keyed by ref (flat index; tree via parentRef)
        self._building_parts: dict[str, dict[str, Any]] = {}
        # Building functions keyed by ref
        self._building_functions: dict[str, dict[str, Any]] = {}
        # Trades keyed by ref
        self._trades: dict[str, dict[str, Any]] = {}
        self._trade_counter = 0
        # Tags keyed by ref
        self._tags: dict[str, dict[str, Any]] = {}
        self._tag_counter = 0
        # ToDo items keyed by ref
        self._todos: dict[str, dict[str, Any]] = {}
        self._todo_counter = 0
        # KNX Secure certificates keyed by ref
        self._certificates: dict[str, dict[str, Any]] = {}
        # Additional device addresses: deviceRef -> list of int addresses
        self._additional_device_addresses: dict[str, list[int]] = {}
        # Additional line group addresses: lineRef -> list of gaRefs
        self._additional_line_gas: dict[str, list[str]] = {}
        # Bus interface links: deviceRef -> list of gaRefs
        self._bus_interface_links: dict[str, list[str]] = {}
        # Project history entries keyed by ref
        self._project_history: dict[str, dict[str, Any]] = {}
        self._history_counter = 0
        # Segments index: segmentRef -> {segmentRef, name, lineRef, mediumType}
        self._segments: dict[str, dict[str, Any]] = {}

        # Idempotency cache: key -> response result
        self._idempotency_cache: dict[str, dict[str, Any]] = {}

        self._seed_data()

    # -- Transport interface ---------------------------------------------------

    @property
    def token(self) -> str:
        return self._token

    async def connect(self) -> None:
        self._connected = True

    async def send(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._connected:
            raise RuntimeError("MockTransport not connected")

        req_id = request.get("id", "unknown")
        req_token = request.get("token")
        method = request.get("method", "")
        params = request.get("params", {})
        idempotency_key = request.get("idempotencyKey")
        expected_rev = request.get("expectedProjectRevision")

        # Token validation
        if req_token != self._token:
            return self._error(req_id, "invalid_token", "Token mismatch")

        # Idempotency: return cached result BEFORE revision check, so a
        # lost-response retry with the original expectedProjectRevision still
        # replays the cached success (finding 6).
        if idempotency_key and idempotency_key in self._idempotency_cache:
            cached = self._idempotency_cache[idempotency_key]
            return self._ok(req_id, cached)

        # Revision check (after idempotency replay)
        if expected_rev is not None and expected_rev != self._revision:
            return self._error(req_id, "revision_mismatch",
                               f"Expected {expected_rev}, current {self._revision}")

        # Route to handler
        handler = self._handlers().get(method)
        if handler is None:
            return self._error(req_id, "invalid_params",
                               f"Unknown method: {method}")

        try:
            result = handler(params)
        except _ProtocolError as exc:
            return self._error(req_id, exc.code, exc.message)

        # Cache mutating results
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = result

        return self._ok(req_id, result)

    async def close(self) -> None:
        self._connected = False

    # -- Helpers ---------------------------------------------------------------

    def _handlers(self) -> dict[str, Any]:
        return {
            "bridge.info": self._handle_bridge_info,
            "project.info": self._handle_project_info,
            "devices.list": self._handle_devices_list,
            "ga.list": self._handle_ga_list,
            "comobjects.list": self._handle_comobjects_list,
            "topology.list": self._handle_topology_list,
            "catalog.manufacturers": self._handle_catalog_manufacturers,
            "catalog.search": self._handle_catalog_search,
            "catalog.search_online": self._handle_catalog_search_online,
            "catalog.import": self._handle_catalog_import,
            "catalog.internalize": self._handle_catalog_internalize,
            "params.list": self._handle_params_list,
            "ga.create": self._handle_ga_create,
            "link.create": self._handle_link_create,
            "link.delete": self._handle_link_delete,
            "param.set": self._handle_param_set,
            "batch.apply": self._handle_batch_apply,
            "ga.delete": self._handle_ga_delete,
            "ga.rename": self._handle_ga_rename,
            "ga.setDescription": self._handle_ga_set_description,
            "ga.setDatapointType": self._handle_ga_set_dpt,
            "groupRange.create": self._handle_group_range_create,
            "groupRange.delete": self._handle_group_range_delete,
            "device.delete": self._handle_device_delete,
            "device.rename": self._handle_device_rename,
            "device.setAddress": self._handle_device_set_address,
            "device.move": self._handle_device_move,
            "area.create": self._handle_area_create,
            "area.delete": self._handle_area_delete,
            "line.create": self._handle_line_create,
            "line.delete": self._handle_line_delete,
            "comObject.setFlags": self._handle_comobject_set_flags,
            "device.setDescription": self._handle_device_set_description,
            "device.setComment": self._handle_device_set_comment,
            "comObject.setDescription": self._handle_comobject_set_description,
            "comObject.setFunctionText": self._handle_comobject_set_function_text,
            "device.addFromCatalog": self._handle_add_from_catalog,
            "device.program": self._handle_device_program,
            "firmware.update": self._handle_firmware_update,
            "job.status": self._handle_job_status,
            "job.cancel": self._handle_job_cancel,
            # Phase B: Building structure + functions
            "building.list": self._handle_building_list,
            "building.create": self._handle_building_create,
            "building.delete": self._handle_building_delete,
            "building.rename": self._handle_building_rename,
            "building.assignDevice": self._handle_building_assign_device,
            "building.unassignDevice": self._handle_building_unassign_device,
            "buildingFunction.create": self._handle_bf_create,
            "buildingFunction.delete": self._handle_bf_delete,
            "buildingFunction.linkGroupAddress": self._handle_bf_link_ga,
            "buildingFunction.unlinkGroupAddress": self._handle_bf_unlink_ga,
            # Phase C: Project management
            "project.save": self._handle_project_save,
            "project.export": self._handle_project_export,
            "project.backup": self._handle_project_backup,
            "project.undo": self._handle_project_undo,
            "project.redo": self._handle_project_redo,
            "project.exportSemantic": self._handle_project_export_semantic,
            # Phase E: Catalog browsing
            "catalog.browseProducts": self._handle_catalog_browse_products,
            "catalog.productInfo": self._handle_catalog_product_info,
            # Phase D: Bus / Online operations
            "bus.ping": self._handle_bus_ping,
            "bus.scanLine": self._handle_bus_scan_line,
            "bus.reconstructLine": self._handle_bus_reconstruct_line,
            "device.readInfo": self._handle_device_read_info,
            "device.compare": self._handle_device_compare,
            "device.readGroupObjects": self._handle_device_read_group_objects,
            "group.read": self._handle_group_read,
            "group.write": self._handle_group_write,
            "group.monitor": self._handle_group_monitor,
            "device.unload": self._handle_device_unload,
            "bus.setIndividualAddress": self._handle_bus_set_individual_address,
            # Reconciliation
            "device.unassign": self._handle_device_unassign,
            # Phase G: Bus writes
            "device.reset": self._handle_device_reset,
            # Phase G: Moves
            "groupRange.moveGroupAddress": self._handle_move_group_address,
            "groupRange.moveGroupRange": self._handle_move_group_range,
            "line.move": self._handle_line_move,
            "buildingPart.move": self._handle_building_part_move,
            # Phase G: Additional addresses
            "device.addAdditionalAddress": self._handle_device_add_additional_address,
            "device.removeAdditionalAddress": self._handle_device_remove_additional_address,
            "line.addAdditionalGroupAddress": self._handle_line_add_additional_ga,
            "line.removeAdditionalGroupAddress": self._handle_line_remove_additional_ga,
            # Phase G: Segments
            "segment.create": self._handle_segment_create,
            "segment.delete": self._handle_segment_delete,
            # Phase G: Trades
            "trades.list": self._handle_trades_list,
            "trade.create": self._handle_trade_create,
            "trade.delete": self._handle_trade_delete,
            "trade.assignDevice": self._handle_trade_assign_device,
            "trade.unassignDevice": self._handle_trade_unassign_device,
            # Phase G: Bus interface / filter
            "busInterface.link": self._handle_bus_interface_link,
            "busInterface.unlink": self._handle_bus_interface_unlink,
            # Phase G: Parameter default
            "param.setDefault": self._handle_param_set_default,
            # Phase G: Certificates
            "certificates.list": self._handle_certificates_list,
            "certificate.add": self._handle_certificate_add,
            "certificate.delete": self._handle_certificate_delete,
            # Phase G: Navigation
            "project.navigateTo": self._handle_navigate_to,
            # Phase G: Tags
            "tags.list": self._handle_tags_list,
            "tag.create": self._handle_tag_create,
            "tag.delete": self._handle_tag_delete,
            # Phase G: ToDos
            "todos.list": self._handle_todos_list,
            "todo.create": self._handle_todo_create,
            "todo.delete": self._handle_todo_delete,
            # Phase G: Channels/modules
            "device.channels": self._handle_device_channels,
            # Phase G: Project history
            "projectHistory.list": self._handle_project_history_list,
            "projectHistory.add": self._handle_project_history_add,
            "projectHistory.delete": self._handle_project_history_delete,
        }

    def _ok(self, req_id: str, result: Any) -> dict[str, Any]:
        return {
            "id": req_id,
            "ok": True,
            "result": result,
            "projectRevision": self._revision,
        }

    def _error(self, req_id: str, code: str, message: str) -> dict[str, Any]:
        return {
            "id": req_id,
            "ok": False,
            "error": {"code": code, "message": message},
        }

    def _bump_revision(self) -> None:
        self._revision_counter += 1
        self._revision = f"rev-{self._revision_counter:03d}"

    # -- Device from catalog -----------------------------------------------------

    def _handle_add_from_catalog(self, params: dict) -> dict:
        """device.addFromCatalog -- add a device to a line from the catalog."""
        line_ref = params.get("lineRef")
        catalog_item_ref = params.get("catalogItemRef")
        address = params.get("address")
        if not line_ref or not catalog_item_ref or not address:
            raise _ProtocolError("invalid_params",
                                 "lineRef, catalogItemRef, and address are required")

        # Validate line exists
        line_found = False
        for area in self._topology:
            for line in area.get("lines", []):
                if line["lineRef"] == line_ref:
                    line_found = True
                    break
            if line_found:
                break
        if not line_found:
            raise _ProtocolError("not_found", f"Line {line_ref} not found")

        # Validate catalog item exists
        cat_item = self._catalog_items.get(catalog_item_ref)
        if cat_item is None:
            raise _ProtocolError("not_found",
                                 f"CatalogItem {catalog_item_ref} not found")

        # Check for duplicate address
        for dev in self._devices.values():
            if dev["address"] == address:
                raise _ProtocolError("invalid_params",
                                     f"Device address {address} already in use")

        ref = _make_ref("dev")
        device = {
            "ref": ref,
            "address": address,
            "name": cat_item["name"],
            "description": "",
            "comment": "",
            "product": cat_item.get("orderNumber", cat_item["name"]),
            "orderNumber": cat_item.get("orderNumber", ""),
            "line": line_ref,
        }
        self._devices[ref] = device
        self._bump_revision()
        return {"ref": ref, "address": address}

    # -- Programming / jobs ----------------------------------------------------

    def _handle_device_program(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        # Validate options if provided
        options = params.get("options")
        valid_options = {None, "all", "partial", "application", "network", "networkBySerial"}
        if options not in valid_options:
            raise _ProtocolError("invalid_params", f"Unknown option: '{options}'")

        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "state": "done",
            "percent": 100,
            "error": None,
            "result": None,
        }
        return {"jobId": job_id}

    def _handle_job_status(self, params: dict) -> dict:
        job_id = params.get("jobId")
        if not job_id:
            raise _ProtocolError("invalid_params", "jobId is required")
        job = self._jobs.get(job_id)
        if job is None:
            raise _ProtocolError("not_found", f"Job {job_id} not found")
        result: dict[str, Any] = {
            "state": job["state"],
            "percent": job["percent"],
        }
        if job.get("error"):
            result["error"] = job["error"]
        if job.get("result") is not None:
            result["result"] = job["result"]
        return result

    def _handle_job_cancel(self, params: dict) -> dict:
        job_id = params.get("jobId")
        if not job_id:
            raise _ProtocolError("invalid_params", "jobId is required")
        job = self._jobs.get(job_id)
        if job is None:
            raise _ProtocolError("not_found", f"Job {job_id} not found")
        # Idempotent: already terminal -> no-op
        if job["state"] not in ("done", "failed", "canceled"):
            job["state"] = "canceled"
        return {"ok": True}

    def _handle_firmware_update(self, params: dict) -> dict:
        """firmware.update -- gated by AddIn's "Unattended firmware update" preference.

        The mock simulates the default state (preference disabled) and always
        returns approval_required.  There is no per-request approval token.
        """
        raise _ProtocolError(
            "approval_required",
            "The AddIn's 'Unattended firmware update' preference is not "
            "enabled"
        )

    # -- Seed data -------------------------------------------------------------

    def _seed_data(self) -> None:
        # Device 1: Switch Actuator
        dev1_ref = "dev-0001"
        co_1_1 = {
            "ref": "co-0001",
            "deviceRef": dev1_ref,
            "number": 0,
            "name": "Switch Object A",
            "description": "",
            "functionText": "Raffstore links",
            "text": "Schalten Ausgang A",
            "dpt": "1.001",
            "isActive": True,
            "flags": {"read": True, "write": True, "transmit": False, "update": False},
        }
        co_1_2 = {
            "ref": "co-0002",
            "deviceRef": dev1_ref,
            "number": 1,
            "name": "Switch Object B",
            "description": "Status feedback",
            "functionText": "",
            "text": "",
            "dpt": "1.001",
            "isActive": True,
            "flags": {"read": True, "write": True, "transmit": False, "update": False},
        }
        co_1_3 = {
            "ref": "co-0003",
            "deviceRef": dev1_ref,
            "number": 2,
            "name": "Status Object",
            "description": "",
            "functionText": "",
            "text": "",
            "dpt": "1.001",
            "isActive": False,  # Inactive -- linking must fail
            "flags": {"read": True, "write": False, "transmit": True, "update": False},
        }
        self._devices[dev1_ref] = {
            "ref": dev1_ref,
            "address": "1.1.1",
            "name": "Switch Actuator 4x",
            "description": "Couch",
            "comment": "Main actuator for living room",
            "product": "ABB SA/S 4.16.1",
            "orderNumber": "SA/S 4.16.1",
            "line": "line-1.1",
        }
        for co in (co_1_1, co_1_2, co_1_3):
            self._com_objects[co["ref"]] = co

        param_1_1 = {
            "ref": "param-0001",
            "deviceRef": dev1_ref,
            "name": "Operating Mode",
            "value": "normal",
            "defaultValue": "normal",
            "isActive": True,
        }
        param_1_2 = {
            "ref": "param-0003",
            "deviceRef": dev1_ref,
            "name": "Switch-on delay",
            "value": "0",
            "defaultValue": "0",
            "isActive": False,
        }
        self._parameters[param_1_1["ref"]] = param_1_1
        self._parameters[param_1_2["ref"]] = param_1_2

        # Device 2: Dimming Actuator
        dev2_ref = "dev-0002"
        co_2_1 = {
            "ref": "co-0004",
            "deviceRef": dev2_ref,
            "number": 0,
            "name": "Dim Object A",
            "description": "",
            "functionText": "",
            "text": "",
            "dpt": "5.001",
            "isActive": True,
            "flags": {"read": True, "write": True, "transmit": False, "update": False},
        }
        co_2_2 = {
            "ref": "co-0005",
            "deviceRef": dev2_ref,
            "number": 1,
            "name": "Dim Object B",
            "description": "",
            "functionText": "",
            "text": "",
            "dpt": "5.001",
            "isActive": True,
            "flags": {"read": True, "write": True, "transmit": False, "update": False},
        }
        self._devices[dev2_ref] = {
            "ref": dev2_ref,
            "address": "1.1.2",
            "name": "Dimming Actuator 2x",
            "description": "",
            "comment": "",
            "product": "ABB DA/S 2.1",
            "orderNumber": "DA/S 2.1",
            "line": "line-1.1",
        }
        for co in (co_2_1, co_2_2):
            self._com_objects[co["ref"]] = co

        param_2_1 = {
            "ref": "param-0002",
            "deviceRef": dev2_ref,
            "name": "Dimming Speed",
            "value": "3",
            "defaultValue": "5",
            "isActive": True,
        }
        self._parameters[param_2_1["ref"]] = param_2_1

        # Group addresses (addresses as strings like "1/0/3")
        ga1 = {"ref": "ga-0001", "address": "1/0/1", "name": "Light Living Room", "dpt": "1.001", "comment": "Ceiling light"}
        ga2 = {"ref": "ga-0002", "address": "1/0/2", "name": "Dimmer Living Room", "dpt": "5.001", "comment": ""}
        self._group_addresses[ga1["ref"]] = ga1
        self._group_addresses[ga2["ref"]] = ga2

        # One pre-existing link
        self._links.add(("co-0001", "ga-0001"))

        # Topology: 1 area, 2 lines, line-1.1 has a segment
        self._topology = [
            {
                "areaRef": "area-1",
                "address": "1",
                "name": "Building A",
                "description": "Main building area",
                "comment": "",
                "lines": [
                    {
                        "lineRef": "line-1.1",
                        "address": "1.1",
                        "name": "Main Line",
                        "description": "Primary TP line",
                        "comment": "",
                        "segments": [
                            {"segmentRef": "seg-1.1.0", "name": "Segment 0",
                             "description": "", "comment": ""},
                        ],
                    },
                    {
                        "lineRef": "line-1.2",
                        "address": "1.2",
                        "name": "Secondary Line",
                        "description": "",
                        "comment": "",
                        "segments": [],
                    },
                ],
            },
        ]

        # Catalog manufacturers
        self._manufacturers = {
            "mfr-abb": {"manufacturerRef": "mfr-abb", "name": "ABB"},
            "mfr-gira": {"manufacturerRef": "mfr-gira", "name": "Gira"},
        }

        # Catalog items (local product store)
        self._catalog_items = {
            "cat-abb-sa4": {
                "catalogItemRef": "cat-abb-sa4",
                "manufacturer": "ABB",
                "manufacturerRef": "mfr-abb",
                "name": "Switch Actuator 4-fold",
                "orderNumber": "SA/S 4.16.1",
                "description": "4-channel switch actuator 16A",
            },
            "cat-abb-da2": {
                "catalogItemRef": "cat-abb-da2",
                "manufacturer": "ABB",
                "manufacturerRef": "mfr-abb",
                "name": "Dimming Actuator 2-fold",
                "orderNumber": "DA/S 2.1",
                "description": "2-channel universal dimming actuator",
            },
            "cat-gira-ts3": {
                "catalogItemRef": "cat-gira-ts3",
                "manufacturer": "Gira",
                "manufacturerRef": "mfr-gira",
                "name": "Tastsensor 3 Komfort",
                "orderNumber": "2031 00",
                "description": "3-fold push button sensor",
            },
        }

        # Online (unified) catalog -- curated by KNX, NOT in local store.
        # Items here use "uci:" refs and are only usable after internalize.
        self._unified_catalog_items = {
            "uci:abb-spau": {
                "unifiedCatalogItemRef": "uci:abb-spau",
                "manufacturer": "ABB",
                "manufacturerRef": "mfr-abb",
                "name": "Space Unit Sensor",
                "orderNumber": "SPAU/F 1.1",
            },
            "uci:gira-be2": {
                "unifiedCatalogItemRef": "uci:gira-be2",
                "manufacturer": "Gira",
                "manufacturerRef": "mfr-gira",
                "name": "Binary Input 2-fold",
                "orderNumber": "1120 00",
            },
            "uci:abb-rog": {
                "unifiedCatalogItemRef": "uci:abb-rog",
                "manufacturer": "ABB",
                "manufacturerRef": "mfr-abb",
                "name": "Room Controller",
                "orderNumber": "ROG/A 1.1",
            },
        }

        # Importable .knxprod paths -> items they produce
        self._importable_paths = {
            "C:\\Products\\sensor.knxprod": [
                {
                    "catalogItemRef": "cat-imp-sensor",
                    "manufacturer": "ABB",
                    "manufacturerRef": "mfr-abb",
                    "name": "Imported Sensor Module",
                    "orderNumber": "SM/S 1.1",
                },
            ],
        }

        # Group ranges (mirrors the 3-level structure: main -> middle)
        gr1 = {
            "ref": "gr-main-1",
            "name": "Lighting",
            "address": 1,
            "parentRef": None,
            "description": "All lighting GAs",
            "comment": "",
        }
        gr2 = {
            "ref": "gr-mid-1-0",
            "name": "Living Room",
            "address": 0,
            "parentRef": "gr-main-1",
            "description": "",
            "comment": "",
        }
        self._group_ranges[gr1["ref"]] = gr1
        self._group_ranges[gr2["ref"]] = gr2

        # Building structure: Building -> Floor -> Room (with device + function)
        bp_bld = {
            "ref": "bp-bld-1",
            "name": "Main Building",
            "type": "Building",
            "parentRef": None,
            "devices": [],
            "description": "Primary building",
            "comment": "",
        }
        bp_floor = {
            "ref": "bp-floor-1",
            "name": "Ground Floor",
            "type": "Floor",
            "parentRef": "bp-bld-1",
            "devices": [],
            "description": "",
            "comment": "",
        }
        bp_room = {
            "ref": "bp-room-1",
            "name": "Living Room",
            "type": "Room",
            "parentRef": "bp-floor-1",
            "devices": ["dev-0001"],
            "description": "",
            "comment": "Main living area",
        }
        self._building_parts[bp_bld["ref"]] = bp_bld
        self._building_parts[bp_floor["ref"]] = bp_floor
        self._building_parts[bp_room["ref"]] = bp_room

        bf1 = {
            "ref": "bf-0001",
            "name": "Lighting Control",
            "buildingPartRef": "bp-room-1",
            "groupAddresses": ["ga-0001"],
            "description": "Central lighting function",
            "comment": "",
        }
        self._building_functions[bf1["ref"]] = bf1

        # Trades
        self._trade_counter = 1
        self._trades["trade-0001"] = {
            "ref": "trade-0001", "name": "HVAC", "number": 1,
            "devices": ["dev-0001"],
            "description": "Heating, ventilation, air conditioning",
            "comment": "",
        }

        # Tags (ID-based refs: tag:{Id})
        self._tag_counter = 1
        self._tags["tag:t1"] = {
            "ref": "tag:t1", "label": "Important", "color": "#FF0000",
        }

        # ToDo items (ID-based refs: todo:{Id}; canonical status: Open / Accomplished)
        self._todo_counter = 1
        self._todos["todo:t1"] = {
            "ref": "todo:t1", "description": "Check wiring",
            "objectPath": "Building A/Ground Floor", "status": "Open",
        }

        # Certificates (no secrets; deviceRef instead of hasDevice)
        self._certificates["cert-0001"] = {
            "ref": "cert-0001", "serialNumber": "00FA12345678ABCD",
            "deviceRef": "dev-0001",
        }

        # Additional device addresses
        self._additional_device_addresses["dev-0001"] = [10]

        # Project history (ID-based refs: ph:{Id})
        self._history_counter = 1
        self._project_history["ph:h1"] = {
            "ref": "ph:h1", "date": "2025-01-15T10:00:00Z",
            "text": "Project created", "detail": "", "user": "admin",
        }

        # Index the seed segment so segment.delete can find it
        self._segments["seg-1.1.0"] = {
            "segmentRef": "seg-1.1.0", "name": "Segment 0",
            "lineRef": "line-1.1", "mediumType": "TP",
            "description": "", "comment": "",
        }

    # -- Read handlers ---------------------------------------------------------

    def _handle_bridge_info(self, _params: dict) -> dict:
        return {
            "addinVersion": "0.2.0",
            "sdkVersion": "6.3.7959.0",       # runtime-loaded (host ETS) SDK
            "builtAgainstSdk": "6.4.8658.0",  # compile-time SDK (default build target)
            "projectName": self._project["name"],
            "projectId": self._project["projectId"],
            "projectRevision": self._revision,
            "knxIpOnly": True,
        }

    def _handle_project_info(self, _params: dict) -> dict:
        return {
            **self._project,
            "revision": self._revision,
        }

    def _handle_devices_list(self, _params: dict) -> list:
        return [copy.deepcopy(d) for d in self._devices.values()]

    def _handle_ga_list(self, _params: dict) -> list:
        return [copy.deepcopy(ga) for ga in self._group_addresses.values()]

    def _handle_comobjects_list(self, params: dict) -> list:
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        result = []
        for co in self._com_objects.values():
            if co["deviceRef"] != device_ref:
                continue
            links = [ga_ref for (co_ref, ga_ref) in self._links if co_ref == co["ref"]]
            entry = {
                "ref": co["ref"],
                "number": co["number"],
                "name": co["name"],
                "description": co.get("description", ""),
                "functionText": co.get("functionText", ""),
                "text": co.get("text", ""),
                "dpt": co["dpt"],
                "flags": copy.deepcopy(co["flags"]),
                "links": links,
            }
            result.append(entry)
        return result

    def _handle_topology_list(self, _params: dict) -> list:
        return copy.deepcopy(self._topology)

    def _handle_catalog_manufacturers(self, _params: dict) -> list:
        return [copy.deepcopy(m) for m in self._manufacturers.values()]

    def _handle_catalog_search(self, params: dict) -> list:
        query = params.get("query")
        if not query:
            raise _ProtocolError("invalid_params", "query is required")

        manufacturer_ref = params.get("manufacturerRef")
        if manufacturer_ref and manufacturer_ref not in self._manufacturers:
            raise _ProtocolError("not_found",
                                 f"Manufacturer {manufacturer_ref} not found")

        query_lower = query.lower()
        results = []
        for item in self._catalog_items.values():
            if manufacturer_ref and item["manufacturerRef"] != manufacturer_ref:
                continue
            if query_lower not in item["name"].lower() and query_lower not in item["orderNumber"].lower():
                continue
            results.append({
                "catalogItemRef": item["catalogItemRef"],
                "manufacturer": item["manufacturer"],
                "name": item["name"],
                "orderNumber": item["orderNumber"],
                "description": item.get("description", ""),
            })
        return results

    def _handle_catalog_search_online(self, params: dict) -> list:
        query = params.get("query")
        if not query:
            raise _ProtocolError("invalid_params", "query is required")

        manufacturer_ref = params.get("manufacturerRef")
        if manufacturer_ref and manufacturer_ref not in self._manufacturers:
            raise _ProtocolError("not_found",
                                 f"Manufacturer {manufacturer_ref} not found")

        query_lower = query.lower()
        results = []
        for item in self._unified_catalog_items.values():
            if manufacturer_ref and item["manufacturerRef"] != manufacturer_ref:
                continue
            if query_lower not in item["name"].lower() and query_lower not in item["orderNumber"].lower():
                continue
            results.append({
                "unifiedCatalogItemRef": item["unifiedCatalogItemRef"],
                "manufacturer": item["manufacturer"],
                "name": item["name"],
                "orderNumber": item["orderNumber"],
            })
        return results

    def _handle_catalog_import(self, params: dict) -> dict:
        path = params.get("path")
        if not path:
            raise _ProtocolError("invalid_params", "path is required")

        items = self._importable_paths.get(path)
        if items is None:
            raise _ProtocolError("not_found", f"File not found or invalid: {path}")

        imported = []
        for item in items:
            ref = item["catalogItemRef"]
            if ref not in self._catalog_items:
                self._catalog_items[ref] = copy.deepcopy(item)
            imported.append({
                "catalogItemRef": item["catalogItemRef"],
                "manufacturer": item["manufacturer"],
                "name": item["name"],
                "orderNumber": item["orderNumber"],
            })
        return {"imported": imported}

    def _handle_catalog_internalize(self, params: dict) -> dict:
        uci_ref = params.get("unifiedCatalogItemRef")
        if not uci_ref:
            raise _ProtocolError("invalid_params",
                                 "unifiedCatalogItemRef is required")

        unified = self._unified_catalog_items.get(uci_ref)
        if unified is None:
            raise _ProtocolError("not_found",
                                 f"Unified catalog item {uci_ref} not found")

        # Derive a local catalogItemRef from the unified ref.
        local_ref = "ci:" + uci_ref.removeprefix("uci:")
        if local_ref not in self._catalog_items:
            self._catalog_items[local_ref] = {
                "catalogItemRef": local_ref,
                "manufacturer": unified["manufacturer"],
                "manufacturerRef": unified["manufacturerRef"],
                "name": unified["name"],
                "orderNumber": unified["orderNumber"],
            }
        return {"catalogItemRef": local_ref}

    def _handle_params_list(self, params: dict) -> list:
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        results = []
        for p in self._parameters.values():
            if p["deviceRef"] != device_ref:
                continue
            results.append({
                "parameterRef": p["ref"],
                "name": p["name"],
                "value": p["value"],
                "isDefault": p["value"] == p["defaultValue"],
                "isActive": p["isActive"],
            })
        return results

    # -- Mutation handlers -----------------------------------------------------

    def _handle_ga_create(self, params: dict) -> dict:
        name = params.get("name")
        address = params.get("address")
        if not name or not address:
            raise _ProtocolError("invalid_params", "name and address are required")

        # Check for duplicate address
        for ga in self._group_addresses.values():
            if ga["address"] == address:
                raise _ProtocolError("invalid_params",
                                     f"Group address {address} already exists")

        ref = _make_ref("ga")
        dpt = None
        if params.get("dptMain") is not None:
            dpt = str(params["dptMain"])
            if params.get("dptSub") is not None:
                dpt += f".{params['dptSub']:03d}"

        ga = {"ref": ref, "address": address, "name": name, "dpt": dpt}
        self._group_addresses[ref] = ga
        self._bump_revision()
        return {"ref": ref, "address": address}

    def _handle_link_create(self, params: dict) -> dict:
        co_ref = params.get("comObjectRef")
        ga_ref = params.get("gaRef")
        if not co_ref or not ga_ref:
            raise _ProtocolError("invalid_params",
                                 "comObjectRef and gaRef are required")

        co = self._com_objects.get(co_ref)
        if co is None:
            raise _ProtocolError("not_found", f"ComObject {co_ref} not found")

        if ga_ref not in self._group_addresses:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")

        # 6.3.0 constraint: linking inactive object must fail
        if not co.get("isActive", True):
            raise _ProtocolError("inactive_object",
                                 f"ComObject {co_ref} is inactive and cannot be linked")

        self._links.add((co_ref, ga_ref))
        self._bump_revision()
        return {"ok": True}

    def _handle_link_delete(self, params: dict) -> dict:
        co_ref = params.get("comObjectRef")
        ga_ref = params.get("gaRef")
        if not co_ref or not ga_ref:
            raise _ProtocolError("invalid_params",
                                 "comObjectRef and gaRef are required")

        if co_ref not in self._com_objects:
            raise _ProtocolError("not_found", f"ComObject {co_ref} not found")
        if ga_ref not in self._group_addresses:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")

        # Idempotent: discarding an absent link is not an error. Mirrors the AddIn,
        # which calls ComObjectInstanceRef.Unlink unconditionally. not_found is
        # reserved for unknown comObjectRef/gaRef (see docs/protocol.md).
        self._links.discard((co_ref, ga_ref))
        self._bump_revision()
        return {"ok": True}

    def _handle_param_set(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        parameter_ref = params.get("parameterRef")
        value = params.get("value")
        if not device_ref or not parameter_ref or value is None:
            raise _ProtocolError("invalid_params",
                                 "deviceRef, parameterRef, and value are required")

        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        param = self._parameters.get(parameter_ref)
        if param is None:
            raise _ProtocolError("not_found", f"Parameter {parameter_ref} not found")

        if param["deviceRef"] != device_ref:
            raise _ProtocolError("invalid_params",
                                 f"Parameter {parameter_ref} does not belong to device {device_ref}")

        param["value"] = value
        self._bump_revision()
        return {"ok": True}

    def _handle_batch_apply(self, params: dict) -> dict:
        operations = params.get("operations")
        if not isinstance(operations, list) or not operations:
            raise _ProtocolError("invalid_params",
                                 "batch.apply requires a non-empty 'operations' array")
        if len(operations) > _MAX_BATCH_OPERATIONS:
            raise _ProtocolError("invalid_params",
                                 f"batch.apply 'operations' exceeds the maximum of "
                                 f"{_MAX_BATCH_OPERATIONS}")
        atomic = params.get("atomic", True)
        if not isinstance(atomic, bool):
            atomic = True

        handlers = self._handlers()

        # Validate all steps up front (fail fast, nothing executed yet).
        parsed: list[tuple[str, dict]] = []
        for op in operations:
            if not isinstance(op, dict) or not isinstance(op.get("method"), str):
                raise _ProtocolError("invalid_params",
                                     "each batch operation must be an object with a "
                                     "'method' string")
            sub_method = op["method"]
            if sub_method not in _BATCHABLE_METHODS:
                raise _ProtocolError("invalid_params",
                                     f"Method '{sub_method}' is not allowed inside "
                                     f"batch.apply")
            parsed.append((sub_method, op.get("params", {}) or {}))

        snapshot = self._snapshot_state() if atomic else None
        results: list[dict] = []
        ok_count = failed_count = skipped_count = 0
        failed = False

        for i, (sub_method, sub_params) in enumerate(parsed):
            if atomic and failed:
                results.append({"index": i, "method": sub_method, "status": "skipped"})
                skipped_count += 1
                continue
            try:
                sub_result = handlers[sub_method](sub_params)
                results.append({"index": i, "method": sub_method,
                                "status": "ok", "result": sub_result})
                ok_count += 1
            except _ProtocolError as exc:
                results.append({"index": i, "method": sub_method, "status": "error",
                                "error": {"code": exc.code, "message": exc.message}})
                failed_count += 1
                failed = True

        if atomic and failed and snapshot is not None:
            self._restore_state(snapshot)  # roll back all applied steps

        return {
            "applied": (not failed) if atomic else True,
            "atomic": atomic,
            "rolledBack": atomic and failed,
            "total": len(parsed),
            "ok": ok_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "results": results,
        }

    def _apply_name_limit(self, name: str) -> tuple[str, bool]:
        """Simulate ETS truncating a too-long name; returns (stored, truncated)."""
        if len(name) > _MOCK_MAX_NAME_LEN:
            return name[:_MOCK_MAX_NAME_LEN], True
        return name, False

    def _snapshot_state(self) -> dict:
        return {attr: copy.deepcopy(getattr(self, attr)) for attr in _MOCK_STATE_ATTRS}

    def _restore_state(self, snapshot: dict) -> None:
        for attr, value in snapshot.items():
            setattr(self, attr, value)


    # -- Phase A mutation handlers ---------------------------------------------

    def _handle_ga_delete(self, params: dict) -> dict:
        ga_ref = params.get("groupAddressRef")
        if not ga_ref:
            raise _ProtocolError("invalid_params", "groupAddressRef is required")
        if ga_ref not in self._group_addresses:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")

        # Remove links referencing this GA
        self._links = {(co, ga) for co, ga in self._links if ga != ga_ref}
        del self._group_addresses[ga_ref]
        self._bump_revision()
        return {"ok": True}

    def _handle_ga_rename(self, params: dict) -> dict:
        ga_ref = params.get("groupAddressRef")
        name = params.get("name")
        if not ga_ref or not name:
            raise _ProtocolError("invalid_params", "groupAddressRef and name are required")
        ga = self._group_addresses.get(ga_ref)
        if ga is None:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")

        stored, truncated = self._apply_name_limit(name)
        ga["name"] = stored
        self._bump_revision()
        result = copy.deepcopy(ga)
        if truncated:
            result["truncated"] = True
        return result

    def _handle_ga_set_description(self, params: dict) -> dict:
        ga_ref = params.get("groupAddressRef")
        description = params.get("description")
        if not ga_ref or description is None:
            raise _ProtocolError("invalid_params",
                                 "groupAddressRef and description are required")
        ga = self._group_addresses.get(ga_ref)
        if ga is None:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")

        ga["description"] = description
        self._bump_revision()
        return copy.deepcopy(ga)

    def _handle_ga_set_dpt(self, params: dict) -> dict:
        ga_ref = params.get("groupAddressRef")
        dpt_main = params.get("dptMain")
        if not ga_ref or dpt_main is None:
            raise _ProtocolError("invalid_params",
                                 "groupAddressRef and dptMain are required")
        ga = self._group_addresses.get(ga_ref)
        if ga is None:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")

        dpt_sub = params.get("dptSub")
        dpt_str = str(dpt_main)
        if dpt_sub is not None:
            dpt_str += f".{dpt_sub:03d}"
        ga["dpt"] = dpt_str
        self._bump_revision()
        return copy.deepcopy(ga)

    def _handle_group_range_create(self, params: dict) -> dict:
        name = params.get("name")
        address = params.get("address")
        if not name or address is None:
            raise _ProtocolError("invalid_params", "name and address are required")

        parent_ref = params.get("parentGroupRangeRef")
        if parent_ref and parent_ref not in self._group_ranges:
            raise _ProtocolError("not_found",
                                 f"Parent GroupRange {parent_ref} not found")

        ref = _make_ref("gr")
        gr = {"ref": ref, "name": name, "address": address, "parentRef": parent_ref}
        self._group_ranges[ref] = gr
        self._bump_revision()
        return {"ref": ref, "name": name, "address": address}

    def _handle_group_range_delete(self, params: dict) -> dict:
        gr_ref = params.get("groupRangeRef")
        if not gr_ref:
            raise _ProtocolError("invalid_params", "groupRangeRef is required")
        if gr_ref not in self._group_ranges:
            raise _ProtocolError("not_found", f"GroupRange {gr_ref} not found")

        # Remove child ranges
        children = [r for r in self._group_ranges if self._group_ranges[r].get("parentRef") == gr_ref]
        for child in children:
            del self._group_ranges[child]
        del self._group_ranges[gr_ref]
        self._bump_revision()
        return {"ok": True}

    def _handle_device_delete(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        # Remove com objects and their links
        co_refs = [co["ref"] for co in self._com_objects.values() if co["deviceRef"] == device_ref]
        for co_ref in co_refs:
            self._links = {(co, ga) for co, ga in self._links if co != co_ref}
            del self._com_objects[co_ref]
        # Remove parameters
        param_refs = [p["ref"] for p in self._parameters.values() if p["deviceRef"] == device_ref]
        for p_ref in param_refs:
            del self._parameters[p_ref]
        del self._devices[device_ref]
        self._bump_revision()
        return {"ok": True}

    def _handle_device_rename(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        name = params.get("name")
        if not device_ref or not name:
            raise _ProtocolError("invalid_params", "deviceRef and name are required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        stored, truncated = self._apply_name_limit(name)
        device["name"] = stored
        self._bump_revision()
        result = copy.deepcopy(device)
        if truncated:
            result["truncated"] = True
        return result

    def _handle_device_set_address(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        address = params.get("address")
        if not device_ref or not address:
            raise _ProtocolError("invalid_params", "deviceRef and address are required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        device["address"] = address
        self._bump_revision()
        return copy.deepcopy(device)

    def _handle_device_move(self, _params: dict) -> dict:
        raise _ProtocolError(
            "not_supported",
            "Moving an assigned device between lines has no supported SDK API "
            "in ETS 6.3.0. Workaround: delete and re-add the device on the target line."
        )

    def _handle_area_create(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            raise _ProtocolError("invalid_params", "name is required")

        address = params.get("address")
        if address is None:
            # Auto-allocate: find the next unused address
            used = {a["address"] for a in self._topology}
            for i in range(16):
                if str(i) not in used:
                    address = i
                    break
            if address is None:
                raise _ProtocolError("invalid_params", "No free area address available")

        ref = f"area-{address}"
        area = {
            "areaRef": ref,
            "address": str(address),
            "name": name,
            "lines": [],
        }
        self._topology.append(area)
        self._bump_revision()
        return copy.deepcopy(area)

    def _handle_area_delete(self, params: dict) -> dict:
        area_ref = params.get("areaRef")
        if not area_ref:
            raise _ProtocolError("invalid_params", "areaRef is required")

        idx = next((i for i, a in enumerate(self._topology) if a["areaRef"] == area_ref), None)
        if idx is None:
            raise _ProtocolError("not_found", f"Area {area_ref} not found")

        del self._topology[idx]
        self._bump_revision()
        return {"ok": True}

    def _handle_line_create(self, params: dict) -> dict:
        area_ref = params.get("areaRef")
        name = params.get("name")
        if not area_ref or not name:
            raise _ProtocolError("invalid_params", "areaRef and name are required")

        area = next((a for a in self._topology if a["areaRef"] == area_ref), None)
        if area is None:
            raise _ProtocolError("not_found", f"Area {area_ref} not found")

        address = params.get("address")
        if address is None:
            used = {l["address"] for l in area["lines"]}
            for i in range(16):
                candidate = f"{area['address']}.{i}"
                if candidate not in used:
                    address = i
                    break

        line_ref = f"line-{area['address']}.{address}"
        line = {
            "lineRef": line_ref,
            "address": f"{area['address']}.{address}",
            "name": name,
            "segments": [],
        }
        area["lines"].append(line)
        self._bump_revision()
        return copy.deepcopy(line)

    def _handle_line_delete(self, params: dict) -> dict:
        line_ref = params.get("lineRef")
        if not line_ref:
            raise _ProtocolError("invalid_params", "lineRef is required")

        for area in self._topology:
            idx = next((i for i, l in enumerate(area["lines"]) if l["lineRef"] == line_ref), None)
            if idx is not None:
                del area["lines"][idx]
                self._bump_revision()
                return {"ok": True}

        raise _ProtocolError("not_found", f"Line {line_ref} not found")

    def _handle_comobject_set_flags(self, params: dict) -> dict:
        co_ref = params.get("comObjectRef")
        if not co_ref:
            raise _ProtocolError("invalid_params", "comObjectRef is required")
        co = self._com_objects.get(co_ref)
        if co is None:
            raise _ProtocolError("not_found", f"ComObject {co_ref} not found")

        if not co.get("isActive", True):
            raise _ProtocolError("inactive_object",
                                 f"ComObject {co_ref} is inactive")

        flags = co["flags"]
        if params.get("communicationFlag") is not None:
            flags["communication"] = params["communicationFlag"]
        if params.get("readFlag") is not None:
            flags["read"] = params["readFlag"]
        if params.get("writeFlag") is not None:
            flags["write"] = params["writeFlag"]
        if params.get("transmitFlag") is not None:
            flags["transmit"] = params["transmitFlag"]
        if params.get("updateFlag") is not None:
            flags["update"] = params["updateFlag"]
        if params.get("readOnInitFlag") is not None:
            flags["readOnInit"] = params["readOnInitFlag"]

        priority = params.get("priority")
        if priority is not None:
            if priority not in ("Low", "High", "Alert"):
                raise _ProtocolError("invalid_params",
                                     f"Invalid priority: {priority}. Valid: Low, High, Alert.")
            co["priority"] = priority

        self._bump_revision()
        return {
            "ref": co_ref,
            "communicationFlag": flags.get("communication", True),
            "readFlag": flags.get("read", False),
            "writeFlag": flags.get("write", False),
            "transmitFlag": flags.get("transmit", False),
            "updateFlag": flags.get("update", False),
            "readOnInitFlag": flags.get("readOnInit", False),
            "priority": co.get("priority", "Low"),
        }


    # -- Label setter handlers (v0.1.16) -----------------------------------------

    def _handle_device_set_description(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        description = params.get("description")
        if not device_ref or description is None:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and description are required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        device["description"] = description
        self._bump_revision()
        return copy.deepcopy(device)

    def _handle_device_set_comment(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        comment = params.get("comment")
        if not device_ref or comment is None:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and comment are required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        device["comment"] = comment
        self._bump_revision()
        return {"ok": True}

    def _handle_comobject_set_description(self, params: dict) -> dict:
        co_ref = params.get("comObjectRef")
        description = params.get("description")
        if not co_ref or description is None:
            raise _ProtocolError("invalid_params",
                                 "comObjectRef and description are required")
        co = self._com_objects.get(co_ref)
        if co is None:
            raise _ProtocolError("not_found", f"ComObject {co_ref} not found")
        co["description"] = description
        self._bump_revision()
        return {"ref": co_ref, "description": description}

    def _handle_comobject_set_function_text(self, params: dict) -> dict:
        co_ref = params.get("comObjectRef")
        function_text = params.get("functionText")
        if not co_ref or function_text is None:
            raise _ProtocolError("invalid_params",
                                 "comObjectRef and functionText are required")
        co = self._com_objects.get(co_ref)
        if co is None:
            raise _ProtocolError("not_found", f"ComObject {co_ref} not found")
        co["functionText"] = function_text
        self._bump_revision()
        return {"ref": co_ref, "functionText": function_text}

    # -- Phase B: Building structure handlers ------------------------------------

    def _build_bp_tree(self, bp_ref: str) -> dict:
        """Build a tree node for a building part with children, devices, functions."""
        bp = self._building_parts[bp_ref]
        children = [
            self._build_bp_tree(child["ref"])
            for child in self._building_parts.values()
            if child.get("parentRef") == bp_ref
        ]
        functions = []
        for bf in self._building_functions.values():
            if bf.get("buildingPartRef") == bp_ref:
                gas = list(bf.get("groupAddresses", []))
                entry = {
                    "ref": bf["ref"],
                    "name": bf["name"],
                    "groupAddresses": gas,
                }
                if not gas:
                    # Mirror the ETS 6.3.0 stale-empty-list caveat from the gateway.
                    entry["note"] = (
                        "Empty address list. In ETS 6.3.0 this may be genuinely empty OR "
                        "a stale SDK cache after a GA was unlinked/deleted; verify in ETS "
                        "if it matters."
                    )
                functions.append(entry)
        return {
            "ref": bp["ref"],
            "name": bp["name"],
            "type": bp["type"],
            "children": children,
            "devices": list(bp.get("devices", [])),
            "functions": functions,
        }

    def _handle_building_list(self, _params: dict) -> list:
        # Return top-level building parts (those with no parent)
        roots = [
            bp["ref"] for bp in self._building_parts.values()
            if bp.get("parentRef") is None
        ]
        return [self._build_bp_tree(ref) for ref in roots]

    def _handle_building_create(self, params: dict) -> dict:
        name = params.get("name")
        bp_type = params.get("type")
        if not name or not bp_type:
            raise _ProtocolError("invalid_params", "name and type are required")

        valid_types = {
            "Building", "BuildingPart", "Floor", "Room",
            "DistributionBoard", "Corridor", "Stairway",
        }
        if bp_type not in valid_types:
            raise _ProtocolError("invalid_params", f"Invalid type: {bp_type}")

        parent_ref = params.get("parentBuildingPartRef")
        if parent_ref and parent_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {parent_ref} not found")

        ref = _make_ref("bp")
        bp = {
            "ref": ref,
            "name": name,
            "type": bp_type,
            "parentRef": parent_ref,
            "devices": [],
        }
        self._building_parts[ref] = bp
        self._bump_revision()
        return self._build_bp_tree(ref)

    def _handle_building_delete(self, params: dict) -> dict:
        bp_ref = params.get("buildingPartRef")
        if not bp_ref:
            raise _ProtocolError("invalid_params", "buildingPartRef is required")
        if bp_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {bp_ref} not found")

        # Delete children recursively
        children = [r for r, bp in self._building_parts.items() if bp.get("parentRef") == bp_ref]
        for child_ref in children:
            self._handle_building_delete({"buildingPartRef": child_ref})
        # Delete associated building functions
        bf_refs = [r for r, bf in self._building_functions.items() if bf.get("buildingPartRef") == bp_ref]
        for bf_ref in bf_refs:
            del self._building_functions[bf_ref]
        del self._building_parts[bp_ref]
        self._bump_revision()
        return {"ok": True}

    def _handle_building_rename(self, params: dict) -> dict:
        bp_ref = params.get("buildingPartRef")
        name = params.get("name")
        if not bp_ref or not name:
            raise _ProtocolError("invalid_params", "buildingPartRef and name are required")
        if bp_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {bp_ref} not found")

        self._building_parts[bp_ref]["name"] = name
        self._bump_revision()
        return self._build_bp_tree(bp_ref)

    def _handle_building_assign_device(self, params: dict) -> dict:
        bp_ref = params.get("buildingPartRef")
        device_ref = params.get("deviceRef")
        if not bp_ref or not device_ref:
            raise _ProtocolError("invalid_params", "buildingPartRef and deviceRef are required")
        if bp_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {bp_ref} not found")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        devices = self._building_parts[bp_ref].setdefault("devices", [])
        if device_ref not in devices:
            devices.append(device_ref)
        self._bump_revision()
        return {"ok": True}

    def _handle_building_unassign_device(self, params: dict) -> dict:
        bp_ref = params.get("buildingPartRef")
        device_ref = params.get("deviceRef")
        if not bp_ref or not device_ref:
            raise _ProtocolError("invalid_params", "buildingPartRef and deviceRef are required")
        if bp_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {bp_ref} not found")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        devices = self._building_parts[bp_ref].get("devices", [])
        if device_ref in devices:
            devices.remove(device_ref)
        self._bump_revision()
        return {"ok": True}

    # -- Phase B: Building function handlers -----------------------------------

    def _handle_bf_create(self, params: dict) -> dict:
        bp_ref = params.get("buildingPartRef")
        name = params.get("name")
        if not bp_ref or not name:
            raise _ProtocolError("invalid_params", "buildingPartRef and name are required")
        if bp_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {bp_ref} not found")

        ref = _make_ref("bf")
        bf = {
            "ref": ref,
            "name": name,
            "buildingPartRef": bp_ref,
            "groupAddresses": [],
        }
        self._building_functions[ref] = bf
        self._bump_revision()
        return {"ref": ref, "name": name, "groupAddresses": []}

    def _handle_bf_delete(self, params: dict) -> dict:
        bf_ref = params.get("buildingFunctionRef")
        if not bf_ref:
            raise _ProtocolError("invalid_params", "buildingFunctionRef is required")
        if bf_ref not in self._building_functions:
            raise _ProtocolError("not_found", f"BuildingFunction {bf_ref} not found")

        del self._building_functions[bf_ref]
        self._bump_revision()
        return {"ok": True}

    def _handle_bf_link_ga(self, params: dict) -> dict:
        bf_ref = params.get("buildingFunctionRef")
        ga_refs = params.get("groupAddressRefs")
        if not bf_ref or not ga_refs:
            raise _ProtocolError("invalid_params",
                                 "buildingFunctionRef and groupAddressRefs are required")
        bf = self._building_functions.get(bf_ref)
        if bf is None:
            raise _ProtocolError("not_found", f"BuildingFunction {bf_ref} not found")

        for ga_ref in ga_refs:
            if ga_ref not in self._group_addresses:
                raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")
            if ga_ref not in bf["groupAddresses"]:
                bf["groupAddresses"].append(ga_ref)
        self._bump_revision()
        return {"ok": True}

    def _handle_bf_unlink_ga(self, params: dict) -> dict:
        bf_ref = params.get("buildingFunctionRef")
        ga_refs = params.get("groupAddressRefs")
        if not bf_ref or not ga_refs:
            raise _ProtocolError("invalid_params",
                                 "buildingFunctionRef and groupAddressRefs are required")
        bf = self._building_functions.get(bf_ref)
        if bf is None:
            raise _ProtocolError("not_found", f"BuildingFunction {bf_ref} not found")

        for ga_ref in ga_refs:
            if ga_ref not in self._group_addresses:
                raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")
            if ga_ref in bf["groupAddresses"]:
                bf["groupAddresses"].remove(ga_ref)
        self._bump_revision()
        return {"ok": True}

    # -- Phase C: Project management handlers ----------------------------------

    def _handle_project_save(self, _params: dict) -> dict:
        raise _ProtocolError(
            "not_supported",
            "project.save is not supported: ETS auto-saves projects.")

    def _handle_project_export(self, params: dict) -> dict:
        path = params.get("path")
        if not path:
            raise _ProtocolError("invalid_params", "path is required")
        # Mock: always succeed
        return {"ok": True}

    def _handle_project_backup(self, _params: dict) -> dict:
        raise _ProtocolError(
            "not_supported",
            "project.backup is not supported: Root.BackupDatabase has no effect in ETS 6.")

    def _handle_project_undo(self, _params: dict) -> dict:
        self._bump_revision()
        return {"ok": True}

    def _handle_project_redo(self, _params: dict) -> dict:
        self._bump_revision()
        return {"ok": True}

    def _handle_project_export_semantic(self, _params: dict) -> dict:
        raise _ProtocolError(
            "not_supported",
            "project.exportSemantic is not supported in v1.")

    # -- Phase E: Catalog browsing handlers ------------------------------------

    def _handle_catalog_browse_products(self, params: dict) -> list:
        manufacturer_ref = params.get("manufacturerRef")
        if not manufacturer_ref:
            raise _ProtocolError("invalid_params", "manufacturerRef is required")
        if manufacturer_ref not in self._manufacturers:
            raise _ProtocolError("not_found", f"Manufacturer {manufacturer_ref} not found")

        offset = params.get("offset", 0)
        limit = params.get("limit", 100)

        items = [
            {
                "catalogItemRef": item["catalogItemRef"],
                "manufacturer": item["manufacturer"],
                "name": item["name"],
                "orderNumber": item["orderNumber"],
                "description": item.get("description", ""),
            }
            for item in self._catalog_items.values()
            if item.get("manufacturerRef") == manufacturer_ref
        ]
        return items[offset:offset + limit]

    def _handle_catalog_product_info(self, params: dict) -> dict:
        cat_ref = params.get("catalogItemRef")
        if not cat_ref:
            raise _ProtocolError("invalid_params", "catalogItemRef is required")
        item = self._catalog_items.get(cat_ref)
        if item is None:
            raise _ProtocolError("not_found", f"CatalogItem {cat_ref} not found")

        return {
            "catalogItemRef": item["catalogItemRef"],
            "manufacturer": item["manufacturer"],
            "name": item["name"],
            "orderNumber": item["orderNumber"],
            "description": item.get("description", ""),
            "mediumTypes": item.get("mediumTypes", []),
        }


    # -- Phase D: Bus / Online operation handlers --------------------------------

    def _handle_bus_ping(self, params: dict) -> dict:
        address = params.get("address")
        if not address:
            raise _ProtocolError("invalid_params", "address is required")
        # Normalise and validate format
        address = _normalize_individual_address(address)
        # Mock: addresses ending in .0 are "not alive", others are alive
        alive = not address.endswith(".0")
        return {"alive": alive}

    def _handle_bus_scan_line(self, params: dict) -> dict:
        line_ref = params.get("lineRef")
        if not line_ref:
            raise _ProtocolError("invalid_params", "lineRef is required")
        # Validate line exists
        found = False
        for area in self._topology:
            for line in area.get("lines", []):
                if line["lineRef"] == line_ref:
                    found = True
                    break
        if not found:
            raise _ProtocolError("not_found", f"Line {line_ref} not found")

        # Mock: immediately complete the job with known device addresses on the line
        job_id = str(uuid.uuid4())
        addresses = [
            d["address"] for d in self._devices.values()
            if d.get("line") == line_ref
        ]
        self._jobs[job_id] = {
            "state": "done",
            "percent": 100,
            "error": None,
            "result": {"addresses": addresses},
        }
        return {"jobId": job_id}

    def _handle_bus_reconstruct_line(self, params: dict) -> dict:
        line_ref = params.get("lineRef")
        if not line_ref:
            raise _ProtocolError("invalid_params", "lineRef is required")
        # Validate line exists and resolve its address prefix
        line_address = None
        for area in self._topology:
            for line in area.get("lines", []):
                if line["lineRef"] == line_ref:
                    line_address = line["address"]
                    break
            if line_address is not None:
                break
        if line_address is None:
            raise _ProtocolError("not_found", f"Line {line_ref} not found")

        # Build device list from known devices on this line
        devices = []
        for dev in self._devices.values():
            if dev.get("line") == line_ref:
                devices.append({
                    "address": dev["address"],
                    "maskVersion": "0705",
                    "serialNumber": "00FA12345678",
                    "manufacturerId": 2,
                    "error": None,
                })

        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "state": "done",
            "percent": 100,
            "error": None,
            "result": {
                "devices": devices,
                "scannedRange": f"{line_address}.1-{line_address}.255",
            },
        }
        return {"jobId": job_id}

    def _handle_device_read_info(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        # Mock: return a fake mask version
        return {
            "maskVersion": "0x0705",
            "maskVersionId": "MV-0705",
        }

    def _handle_device_compare(self, params: dict) -> dict:
        """device.compare -- limited compare.

        Returns a small sample comparison list.  In production, the AddIn
        reads a subset of device properties from the bus and compares them
        against the project model.  A full byte-level compare is not feasible
        via the SDK's high-level API, so partial=true is always set.
        """
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        # Mock: return a small sample comparison
        return {
            "compared": [
                {
                    "property": "individualAddress",
                    "equal": True,
                    "expected": device["address"],
                    "actual": device["address"],
                },
                {
                    "property": "applicationLoaded",
                    "equal": False,
                    "expected": True,
                    "actual": False,
                },
            ],
            "partial": True,
            "note": "Limited compare via SDK high-level API; "
                    "full byte-level compare not available.",
        }

    def _handle_device_read_group_objects(self, params: dict) -> dict:
        address = params.get("address")
        if not address:
            raise _ProtocolError("invalid_params", "address is required")

        # Normalise address (tolerates commas/whitespace)
        address = _normalize_individual_address(address)

        # Find device by its individual address
        device = None
        for dev in self._devices.values():
            if dev["address"] == address:
                device = dev
                break
        if device is None:
            raise _ProtocolError("not_found", f"No device at address {address}")

        # Build the result payload (shared by sync and async modes)
        result = self._build_group_objects_result(device, address)

        # Dual-mode: async=true returns a jobId
        if params.get("async"):
            job_id = str(uuid.uuid4())
            self._jobs[job_id] = {
                "state": "done",
                "percent": 100,
                "error": None,
                "result": result,
            }
            return {"jobId": job_id}

        # Default (sync): return the result directly
        return result

    def _build_group_objects_result(
        self, device: dict, address: str,
    ) -> dict:
        """Build the DeviceGroupObjectsResult payload for a device."""
        com_objects = []
        for co in self._com_objects.values():
            if co["deviceRef"] != device["ref"]:
                continue
            ga_addresses = []
            for co_ref, ga_ref in self._links:
                if co_ref == co["ref"]:
                    ga = self._group_addresses.get(ga_ref)
                    if ga:
                        ga_addresses.append(ga["address"])
            flags = co.get("flags", {})
            com_objects.append({
                "number": co["number"],
                "groupAddresses": ga_addresses,
                "flags": {
                    "c": flags.get("communication", True),
                    "r": flags.get("read", False),
                    "w": flags.get("write", False),
                    "t": flags.get("transmit", False),
                    "u": flags.get("update", False),
                },
            })

        return {
            "address": address,
            "comObjects": com_objects,
            "partial": True,
            "note": "KNX Secure objects may not be readable without the correct key.",
        }

    def _handle_group_read(self, params: dict) -> dict:
        ga_ref = params.get("gaRef")
        if not ga_ref:
            raise _ProtocolError("invalid_params", "gaRef is required")
        ga = self._group_addresses.get(ga_ref)
        if ga is None:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")
        # Mock: return a simulated value (hex) based on GA name
        # If GA dpt starts with "1." -> boolean "01", else "64" (100 decimal)
        dpt = ga.get("dpt", "")
        if dpt.startswith("1."):
            return {"value": "01"}
        return {"value": "64"}

    def _handle_group_write(self, params: dict) -> dict:
        ga_ref = params.get("gaRef")
        value = params.get("value")
        if not ga_ref or value is None:
            raise _ProtocolError("invalid_params", "gaRef and value are required")
        ga = self._group_addresses.get(ga_ref)
        if ga is None:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")
        # Validate hex
        try:
            bytes.fromhex(value)
        except ValueError:
            raise _ProtocolError("invalid_params", f"Invalid hex value: '{value}'")
        # Mock: always acknowledge
        return {"acknowledged": True}

    def _handle_group_monitor(self, params: dict) -> dict:
        line_ref = params.get("lineRef", "default")
        duration_ms = params.get("durationMs", 10000)
        if duration_ms < 1000 or duration_ms > 300000:
            raise _ProtocolError("invalid_params",
                                 "durationMs must be between 1000 and 300000")

        # Validate line exists (unless "default")
        if line_ref != "default":
            found = False
            for area in self._topology:
                for line in area.get("lines", []):
                    if line["lineRef"] == line_ref:
                        found = True
                        break
            if not found:
                raise _ProtocolError("not_found", f"Line {line_ref} not found")

        # Mock: immediately complete with a few fake telegrams
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "state": "done",
            "percent": 100,
            "error": None,
            "result": {
                "telegrams": [
                    {
                        "service": "GroupValueWrite",
                        "sourceAddress": "1.1.1",
                        "groupAddress": "1/0/1",
                        "value": "01",
                        "timestamp": "2025-01-01T00:00:00.000Z",
                    },
                    {
                        "service": "GroupValueResponse",
                        "sourceAddress": "1.1.2",
                        "groupAddress": "1/0/2",
                        "value": "64",
                        "timestamp": "2025-01-01T00:00:01.000Z",
                    },
                ],
            },
        }
        return {"jobId": job_id}

    def _handle_device_unload(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "state": "done",
            "percent": 100,
            "error": None,
            "result": None,
        }
        return {"jobId": job_id}

    def _handle_bus_set_individual_address(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        current_address = params.get("currentAddress")
        if not device_ref or not current_address:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and currentAddress are required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "state": "done", "percent": 100, "error": None, "result": None,
        }
        return {"jobId": job_id}

    # -- Reconciliation handlers -----------------------------------------------

    def _handle_device_unassign(self, params: dict) -> dict:
        """device.unassign -- detach a device from its line.

        Moves the device to the unassigned-devices collection (line=None).
        The device is NOT deleted.
        """
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        device = self._devices.get(device_ref)
        if device is None:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")

        device["line"] = None
        self._bump_revision()
        return {"ok": True}


    # -- Phase G: Bus write handlers (NOT mutating) ----------------------------

    def _handle_device_reset(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        return {"ok": True}

    # -- Phase G: Move handlers -----------------------------------------------

    def _handle_move_group_address(self, params: dict) -> dict:
        target_ref = params.get("targetGroupRangeRef")
        ga_ref = params.get("groupAddressRef")
        if not target_ref or not ga_ref:
            raise _ProtocolError("invalid_params",
                                 "targetGroupRangeRef and groupAddressRef are required")
        if target_ref not in self._group_ranges:
            raise _ProtocolError("not_found", f"GroupRange {target_ref} not found")
        if ga_ref not in self._group_addresses:
            raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")
        self._bump_revision()
        return {"ok": True}

    def _handle_move_group_range(self, params: dict) -> dict:
        target_ref = params.get("targetGroupRangeRef")
        source_ref = params.get("sourceGroupRangeRef")
        if not target_ref or not source_ref:
            raise _ProtocolError("invalid_params",
                                 "targetGroupRangeRef and sourceGroupRangeRef are required")
        if target_ref not in self._group_ranges:
            raise _ProtocolError("not_found", f"GroupRange {target_ref} not found")
        if source_ref not in self._group_ranges:
            raise _ProtocolError("not_found", f"GroupRange {source_ref} not found")
        self._group_ranges[source_ref]["parentRef"] = target_ref
        self._bump_revision()
        return {"ok": True}

    def _handle_line_move(self, params: dict) -> dict:
        target_area_ref = params.get("targetAreaRef")
        line_ref = params.get("lineRef")
        if not target_area_ref or not line_ref:
            raise _ProtocolError("invalid_params",
                                 "targetAreaRef and lineRef are required")
        target_area = next(
            (a for a in self._topology if a["areaRef"] == target_area_ref), None)
        if target_area is None:
            raise _ProtocolError("not_found", f"Area {target_area_ref} not found")
        # Find and remove the line from its current area
        moved_line = None
        for area in self._topology:
            for i, line in enumerate(area["lines"]):
                if line["lineRef"] == line_ref:
                    moved_line = area["lines"].pop(i)
                    break
            if moved_line:
                break
        if moved_line is None:
            raise _ProtocolError("not_found", f"Line {line_ref} not found")
        target_area["lines"].append(moved_line)
        self._bump_revision()
        return {"ok": True}

    def _handle_building_part_move(self, params: dict) -> dict:
        target_ref = params.get("targetBuildingPartRef")
        source_ref = params.get("sourceBuildingPartRef")
        if not target_ref or not source_ref:
            raise _ProtocolError("invalid_params",
                                 "targetBuildingPartRef and sourceBuildingPartRef are required")
        if target_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {target_ref} not found")
        if source_ref not in self._building_parts:
            raise _ProtocolError("not_found", f"BuildingPart {source_ref} not found")
        self._building_parts[source_ref]["parentRef"] = target_ref
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Additional address handlers ----------------------------------

    def _handle_device_add_additional_address(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        address = params.get("address")
        if not device_ref or address is None:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and address are required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        addrs = self._additional_device_addresses.setdefault(device_ref, [])
        if address in addrs:
            raise _ProtocolError("invalid_params",
                                 f"Additional address {address} already exists")
        addrs.append(address)
        self._bump_revision()
        return {"deviceRef": device_ref, "address": address}

    def _handle_device_remove_additional_address(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        address = params.get("address")
        if not device_ref or address is None:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and address are required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        addrs = self._additional_device_addresses.get(device_ref, [])
        if address not in addrs:
            raise _ProtocolError("not_found",
                                 f"Additional address {address} not found")
        addrs.remove(address)
        self._bump_revision()
        return {"ok": True}

    def _handle_line_add_additional_ga(self, params: dict) -> dict:
        line_ref = params.get("lineRef")
        ga_refs = params.get("groupAddressRefs")
        if not line_ref or not ga_refs:
            raise _ProtocolError("invalid_params",
                                 "lineRef and groupAddressRefs are required")
        # Validate line exists
        found = any(
            line["lineRef"] == line_ref
            for area in self._topology
            for line in area.get("lines", [])
        )
        if not found:
            raise _ProtocolError("not_found", f"Line {line_ref} not found")
        for ga_ref in ga_refs:
            if ga_ref not in self._group_addresses:
                raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")
        addrs = self._additional_line_gas.setdefault(line_ref, [])
        for ga_ref in ga_refs:
            if ga_ref not in addrs:
                addrs.append(ga_ref)
        self._bump_revision()
        return {"ok": True}

    def _handle_line_remove_additional_ga(self, params: dict) -> dict:
        line_ref = params.get("lineRef")
        ga_refs = params.get("groupAddressRefs")
        if not line_ref or not ga_refs:
            raise _ProtocolError("invalid_params",
                                 "lineRef and groupAddressRefs are required")
        found = any(
            line["lineRef"] == line_ref
            for area in self._topology
            for line in area.get("lines", [])
        )
        if not found:
            raise _ProtocolError("not_found", f"Line {line_ref} not found")
        addrs = self._additional_line_gas.get(line_ref, [])
        for ga_ref in ga_refs:
            if ga_ref in addrs:
                addrs.remove(ga_ref)
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Segment handlers ---------------------------------------------

    def _handle_segment_create(self, params: dict) -> dict:
        line_ref = params.get("lineRef")
        name = params.get("name")
        medium_type = params.get("mediumType")
        if not line_ref or not name or not medium_type:
            raise _ProtocolError("invalid_params",
                                 "lineRef, name, and mediumType are required")
        # Find the line
        target_line = None
        for area in self._topology:
            for line in area.get("lines", []):
                if line["lineRef"] == line_ref:
                    target_line = line
                    break
            if target_line:
                break
        if target_line is None:
            raise _ProtocolError("not_found", f"Line {line_ref} not found")
        seg_ref = _make_ref("seg")
        segment = {"segmentRef": seg_ref, "name": name}
        target_line["segments"].append(segment)
        self._segments[seg_ref] = {
            "segmentRef": seg_ref, "name": name,
            "lineRef": line_ref, "mediumType": medium_type,
        }
        self._bump_revision()
        return {"segmentRef": seg_ref, "name": name}

    def _handle_segment_delete(self, params: dict) -> dict:
        seg_ref = params.get("segmentRef")
        if not seg_ref:
            raise _ProtocolError("invalid_params", "segmentRef is required")
        seg = self._segments.get(seg_ref)
        if seg is None:
            raise _ProtocolError("not_found", f"Segment {seg_ref} not found")
        # Remove from the line's segments list
        for area in self._topology:
            for line in area.get("lines", []):
                line["segments"] = [
                    s for s in line["segments"] if s["segmentRef"] != seg_ref
                ]
        del self._segments[seg_ref]
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Trade handlers -----------------------------------------------

    def _handle_trades_list(self, _params: dict) -> list:
        return [copy.deepcopy(t) for t in self._trades.values()]

    def _handle_trade_create(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            raise _ProtocolError("invalid_params", "name is required")
        self._trade_counter += 1
        ref = _make_ref("trade")
        trade = {
            "ref": ref, "name": name,
            "number": self._trade_counter, "devices": [],
        }
        self._trades[ref] = trade
        self._bump_revision()
        return copy.deepcopy(trade)

    def _handle_trade_delete(self, params: dict) -> dict:
        trade_ref = params.get("tradeRef")
        if not trade_ref:
            raise _ProtocolError("invalid_params", "tradeRef is required")
        if trade_ref not in self._trades:
            raise _ProtocolError("not_found", f"Trade {trade_ref} not found")
        del self._trades[trade_ref]
        self._bump_revision()
        return {"ok": True}

    def _handle_trade_assign_device(self, params: dict) -> dict:
        trade_ref = params.get("tradeRef")
        device_ref = params.get("deviceRef")
        if not trade_ref or not device_ref:
            raise _ProtocolError("invalid_params",
                                 "tradeRef and deviceRef are required")
        trade = self._trades.get(trade_ref)
        if trade is None:
            raise _ProtocolError("not_found", f"Trade {trade_ref} not found")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        if device_ref not in trade["devices"]:
            trade["devices"].append(device_ref)
        self._bump_revision()
        return {"ok": True}

    def _handle_trade_unassign_device(self, params: dict) -> dict:
        trade_ref = params.get("tradeRef")
        device_ref = params.get("deviceRef")
        if not trade_ref or not device_ref:
            raise _ProtocolError("invalid_params",
                                 "tradeRef and deviceRef are required")
        trade = self._trades.get(trade_ref)
        if trade is None:
            raise _ProtocolError("not_found", f"Trade {trade_ref} not found")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        if device_ref in trade["devices"]:
            trade["devices"].remove(device_ref)
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Bus interface / filter handlers ------------------------------

    def _handle_bus_interface_link(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        ga_refs = params.get("groupAddressRefs")
        if not device_ref or not ga_refs:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and groupAddressRefs are required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        for ga_ref in ga_refs:
            if ga_ref not in self._group_addresses:
                raise _ProtocolError("not_found", f"GroupAddress {ga_ref} not found")
        links = self._bus_interface_links.setdefault(device_ref, [])
        for ga_ref in ga_refs:
            if ga_ref not in links:
                links.append(ga_ref)
        self._bump_revision()
        return {"ok": True}

    def _handle_bus_interface_unlink(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        ga_refs = params.get("groupAddressRefs")
        if not device_ref or not ga_refs:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and groupAddressRefs are required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        links = self._bus_interface_links.get(device_ref, [])
        for ga_ref in ga_refs:
            if ga_ref in links:
                links.remove(ga_ref)
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Parameter default handler ------------------------------------

    def _handle_param_set_default(self, params: dict) -> dict:
        device_ref = params.get("deviceRef")
        parameter_ref = params.get("parameterRef")
        if not device_ref or not parameter_ref:
            raise _ProtocolError("invalid_params",
                                 "deviceRef and parameterRef are required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        param = self._parameters.get(parameter_ref)
        if param is None:
            raise _ProtocolError("not_found", f"Parameter {parameter_ref} not found")
        if param["deviceRef"] != device_ref:
            raise _ProtocolError("invalid_params",
                                 f"Parameter {parameter_ref} does not belong to "
                                 f"device {device_ref}")
        param["value"] = param["defaultValue"]
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Certificate handlers -----------------------------------------

    def _handle_certificates_list(self, _params: dict) -> list:
        return [copy.deepcopy(c) for c in self._certificates.values()]

    def _handle_certificate_add(self, params: dict) -> dict:
        value = params.get("value")
        if not value:
            raise _ProtocolError("invalid_params", "value is required")
        ref = _make_ref("cert")
        cert = {
            "ref": ref, "serialNumber": value[:16], "deviceRef": None,
        }
        self._certificates[ref] = cert
        self._bump_revision()
        return {"ref": ref, "serialNumber": value[:16], "deviceRef": None}

    def _handle_certificate_delete(self, params: dict) -> dict:
        cert_ref = params.get("certificateRef")
        if not cert_ref:
            raise _ProtocolError("invalid_params", "certificateRef is required")
        if cert_ref not in self._certificates:
            raise _ProtocolError("not_found", f"Certificate {cert_ref} not found")
        del self._certificates[cert_ref]
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Navigation handler -------------------------------------------

    def _handle_navigate_to(self, params: dict) -> dict:
        ref = params.get("ref")
        if not ref:
            raise _ProtocolError("invalid_params", "ref is required")
        # Accept any ref -- in real ETS, this navigates the UI
        return {"ok": True}

    # -- Phase G: Tag handlers -------------------------------------------------

    def _handle_tags_list(self, _params: dict) -> list:
        return [copy.deepcopy(t) for t in self._tags.values()]

    def _handle_tag_create(self, params: dict) -> dict:
        label = params.get("label")
        if not label:
            raise _ProtocolError("invalid_params", "label is required")
        color = params.get("color", "#808080")
        # Ensure color is #RRGGBB (production always returns leading #)
        if not color.startswith("#"):
            color = "#" + color
        self._tag_counter += 1
        ref = f"tag:t{self._tag_counter}"
        tag = {"ref": ref, "label": label, "color": color}
        self._tags[ref] = tag
        self._bump_revision()
        return copy.deepcopy(tag)

    def _handle_tag_delete(self, params: dict) -> dict:
        tag_ref = params.get("tagRef")
        if not tag_ref:
            raise _ProtocolError("invalid_params", "tagRef is required")
        if tag_ref not in self._tags:
            raise _ProtocolError("not_found", f"Tag {tag_ref} not found")
        del self._tags[tag_ref]
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: ToDo handlers ------------------------------------------------

    def _handle_todos_list(self, _params: dict) -> list:
        return [copy.deepcopy(t) for t in self._todos.values()]

    def _handle_todo_create(self, params: dict) -> dict:
        description = params.get("description")
        if not description:
            raise _ProtocolError("invalid_params", "description is required")
        # Canonical status: Open (default) or Accomplished
        status = params.get("status", "Open")
        if status not in _VALID_TODO_STATUSES:
            raise _ProtocolError("invalid_params",
                                 f"Invalid status: '{status}'. "
                                 f"Valid: {', '.join(sorted(_VALID_TODO_STATUSES))}")
        self._todo_counter += 1
        ref = f"todo:t{self._todo_counter}"
        todo = {
            "ref": ref,
            "description": description,
            "objectPath": params.get("objectPath", ""),
            "status": status,
        }
        self._todos[ref] = todo
        self._bump_revision()
        return copy.deepcopy(todo)

    def _handle_todo_delete(self, params: dict) -> dict:
        todo_ref = params.get("todoRef")
        if not todo_ref:
            raise _ProtocolError("invalid_params", "todoRef is required")
        if todo_ref not in self._todos:
            raise _ProtocolError("not_found", f"ToDo {todo_ref} not found")
        del self._todos[todo_ref]
        self._bump_revision()
        return {"ok": True}

    # -- Phase G: Channel / module handler -------------------------------------

    def _handle_device_channels(self, params: dict) -> dict:
        """device.channels -- mirrors C# DeviceChannelsResult / ChannelInstanceInfo."""
        device_ref = params.get("deviceRef")
        if not device_ref:
            raise _ProtocolError("invalid_params", "deviceRef is required")
        if device_ref not in self._devices:
            raise _ProtocolError("not_found", f"Device {device_ref} not found")
        # Build channels from com-objects belonging to this device
        # Mirrors ChannelInstanceInfo fields from C#
        channels = []
        for co in self._com_objects.values():
            if co["deviceRef"] == device_ref:
                channels.append({
                    "name": f"Channel {co['number']}",
                    "text": co.get("text", ""),
                    "description": None,
                    "isActive": co.get("isActive", True),
                    "applicationProgramChannelId": None,
                    "activeComObjectRefs": [co["ref"]] if co.get("isActive", True) else [],
                })
        return {"deviceRef": device_ref, "channels": channels, "modules": []}

    # -- Phase G: Project history handlers -------------------------------------

    def _handle_project_history_list(self, _params: dict) -> list:
        return [copy.deepcopy(h) for h in self._project_history.values()]

    def _handle_project_history_add(self, params: dict) -> dict:
        text = params.get("text")
        if not text:
            raise _ProtocolError("invalid_params", "text is required")
        self._history_counter += 1
        ref = f"ph:h{self._history_counter}"
        entry = {
            "ref": ref,
            "date": "2025-06-01T12:00:00Z",
            "text": text,
            "detail": "",
            "user": "mcp-bridge",
        }
        self._project_history[ref] = entry
        self._bump_revision()
        return copy.deepcopy(entry)

    def _handle_project_history_delete(self, params: dict) -> dict:
        hist_ref = params.get("historyRef")
        if not hist_ref:
            raise _ProtocolError("invalid_params", "historyRef is required")
        if hist_ref not in self._project_history:
            raise _ProtocolError("not_found",
                                 f"History entry {hist_ref} not found")
        del self._project_history[hist_ref]
        self._bump_revision()
        return {"ok": True}


class _ProtocolError(Exception):
    """Internal exception used by mock handlers to signal protocol errors."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
