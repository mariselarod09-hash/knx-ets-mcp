# Knx.EtsBridge.Addin

ETS6 project-level AddIn that exposes an IPC named-pipe server for the
knx-ets-bridge MCP adapter. Built against the ETS6 6.4 SDK by default; the
resulting DLL runs on ETS 6.3 and 6.4.

## Build on macOS

Prerequisites: .NET SDK 8.0+ (tested with 10.0).

```bash
cd addin/
dotnet build -c Release
```

The build uses `Microsoft.NETFramework.ReferenceAssemblies` for cross-compilation
and resolves the ETS SDK DLLs from the configured `EtsDllPath` (see BUILD.md).
Output: `bin/Release/net48/Knx.EtsBridge.Addin.dll` plus NuGet runtime dependencies
(System.Text.Json etc.) -- all are needed for sideloading.

## Sideload on Windows

1. Build or copy the Release output.
2. Create the sideload directory:
   ```
   C:\ProgramData\KNX\ETS6\Apps\AddIns\M00FF-B0001\
   ```
   (`M00FF-B0001` = the AppId in AddInManifest.xml; replace with a registered ID
   for production.)
3. Copy the **entire contents** of `bin/Release/net48/` into that directory:
   - `Knx.EtsBridge.Addin.dll` (the AddIn itself)
   - `AddInManifest.xml`
   - `System.Text.Json.dll` and all other NuGet dependency DLLs
     (`System.Text.Encodings.Web.dll`, `System.Memory.dll`,
     `System.Buffers.dll`, `System.Runtime.CompilerServices.Unsafe.dll`,
     `Microsoft.Bcl.AsyncInterfaces.dll`, `System.Threading.Tasks.Extensions.dll`,
     `System.Numerics.Vectors.dll`, `System.ValueTuple.dll`)

   Do **not** copy the ETS SDK DLLs (`Knx.Ets.Sdk.dll` etc.) -- ETS provides
   those at runtime.
4. (Re)start ETS6.
5. Open a project. The IPC server starts automatically on Initialize and writes
   the pipe name + token to
   `%LOCALAPPDATA%\knx-ets-bridge\session.json`.
   Open the "KNX-ETS Bridge" app from the toolbar to see the status panel.

### Session file discovery

The AddIn writes a single session file at a well-known path:

```
%LOCALAPPDATA%\knx-ets-bridge\session.json
```

The file contains the pipe name and authentication token needed to connect.
Only one ETS instance at a time owns this file; a new instance overwrites it.
The MCP server reads this single fixed path -- no directory enumeration or
glob matching is needed.

## Architecture

```
EtsBridgeAddIn          Entry class (IEts4AddInV2 + IEts5ClosableAddin)
  |
  +-- VersionGuard      Logs the loaded SDK version on startup
  +-- EtsDispatcher     Marshals all SDK calls to the WPF UI thread
  +-- Ets6ProjectGateway   SDK operations (the ONLY file touching ETS types)
  +-- IpcServer         Named-pipe JSON-RPC server (protocol.md contract)
```

## IPC protocol

- Transport: Windows named pipe, newline-delimited JSON, UTF-8 (no BOM).
- All field names are **camelCase** on the wire (PascalCase DTOs are serialized
  with `JsonNamingPolicy.CamelCase`).
- Refs are opaque strings. See Ets6ProjectGateway.cs header for the encoding.
- Session file and pipe stream use `UTF8Encoding(false)` (no BOM) to avoid
  breaking non-.NET clients.

## Ref encoding

- **Device**: `d{Puid}` (ProjectRelatedObjectWithPuid.Puid)
- **GroupAddress**: `ga:{DomObject.Id}` (stable across address changes)
- **ComObject (device-level)**: `d{puid}:c{Number}`
- **ComObject (module-level)**: `d{puid}:m{modulePuid}:c{Number}` (unique per module)
- **Parameter**: `p:{UniqueId}` (round-trips with params.list)
- **Area**: `a{Address}`
- **Line**: `a{areaAddr}:l{lineAddr}`
- **Segment**: `a{areaAddr}:l{lineAddr}:s{segNumber}`
- **Manufacturer**: `m{KnxManufacturerId}`
- **CatalogItem**: `ci:{Id}`

## Method implementation status

All protocol methods are implemented. The build compiles cleanly against the SDK and the
test suite passes.

### Protocol methods

| Method                     | Category | Notes                                                                          |
| -------------------------- | -------- | ------------------------------------------------------------------------------ |
| `bridge.info`              | READ     | AddIn version, SDK version, project name/ID/revision, knxIpOnly flag           |
| `project.info`             | READ     | ProjectGuid, Name, GroupAddressStyle, revision counter                         |
| `devices.list`             | READ     | Puid ref, address, name, product, orderNumber, line                            |
| `ga.list`                  | READ     | All group addresses; stable Id-based ref, formatted address, name, DPT         |
| `comobjects.list`          | READ     | Active COs (global + module); flags, DPT, linked GA refs; module-aware refs    |
| `params.list`              | READ     | All params (global + module); prefixed parameterRef for round-trip             |
| `device.compare`           | READ     | Deliberately limited compare                                                   |
| `device.channels`          | READ     | ChannelInstances + ModuleInstances per device                                  |
| `topology.list`            | READ     | Areas/lines/segments hierarchy                                                 |
| `building.list`            | READ     | Building structure tree with devices and functions                              |
| `trades.list`              | READ     | Trades with assigned devices                                                   |
| `certificates.list`        | READ     | KNX Secure device certificates                                                 |
| `tags.list`                | READ     | Project tags                                                                   |
| `todos.list`               | READ     | Project to-do items                                                            |
| `projectHistory.list`      | READ     | Audit trail entries                                                            |
| `catalog.*`                | READ     | manufacturers, search, search_online, browseProducts, productInfo              |
| `bus.reconstructLine`      | BUS/READ | Scan + read device identity for recovery                                       |
| `device.readGroupObjects`  | BUS/READ | Read association/GO table from device for recovery                             |
| `ga.create`                | MUTATING | Parses address string (3-level/2-level/raw int); optional DPT                  |
| `link.create/delete`       | MUTATING | IsActive guard; delete is idempotent                                           |
| `param.set/setDefault`     | MUTATING | Value setter / reset to product default                                        |
| `device.addFromCatalog`    | MUTATING | Catalog item lookup + DeviceCollection.Add                                     |
| `device.unassign`          | MUTATING | Detaches device from line to unassigned collection                              |
| `segment.create/delete`    | MUTATING | Segments on a line (mediumType: TP/PL/RF/IP/IoT)                               |
| `groupRange.move*`         | MUTATING | moveGroupAddress, moveGroupRange                                               |
| `line.move`                | MUTATING | Move line to a different area                                                  |
| `buildingPart.move`        | MUTATING | Move building part under a different parent                                    |
| `device.add/removeAdditionalAddress` | MUTATING | Additional individual addresses                                      |
| `line.add/removeAdditionalGroupAddress` | MUTATING | Additional/sending GAs per line                                   |
| `trade.create/delete/assign/unassign` | MUTATING | Trade CRUD + device assignment                                    |
| `busInterface.link/unlink` | MUTATING | Coupler filter table                                                           |
| `certificate.add/delete`   | MUTATING | KNX Secure certificates                                                        |
| `tag.create/delete`        | MUTATING | Project tags                                                                   |
| `todo.create/delete`       | MUTATING | Project to-do items                                                            |
| `projectHistory.add/delete`| MUTATING | Audit trail entries                                                            |
| `project.navigateTo`       | COMMAND  | Selects object in ETS UI                                                       |
| `device.program`           | BUS      | Runs automatically via `Root.StartDownload`                                    |
| `device.unload`            | BUS      | Unload application program                                                     |
| `firmware.update`          | BUS      | Gated: requires "Unattended firmware update" preference                        |
| `bus.setIndividualAddress`  | BUS      | Programs project address onto bus (job-based)                                  |
| `device.reset`             | BUS      | Root.ResetDevice                                                               |
| `job.status/cancel`        | INFRA    | Async job tracking and cancellation                                            |
| `device.move`              | --       | Not supported (no SDK API)                                                     |

Bus operations (device.program, firmware.update, bus.scanLine, etc.) require a bus
interface configured in ETS.

### IPC plumbing

- Token auth, idempotencyKey caching (scoped by projectId, 5-min TTL, bounded)
- expectedProjectRevision check inside UI-thread dispatch (atomic with mutation)
- Error codes from protocol.md with proper classification
- Session file at `%LOCALAPPDATA%\knx-ets-bridge\session.json`
- PipeSecurity restricted to current Windows user SID
- UTF-8 no-BOM encoding throughout
- Max line size (1 MB) to prevent unbounded memory usage
- Graceful Stop() with pipe close and thread join

## Applied Codex review findings

The following findings from the Codex architecture review have been applied:

- **Bus-lease ownership**: bus operations acquire and release a lease to prevent
  concurrent access.
- **Dispatcher-async**: all SDK mutations are dispatched to the WPF UI thread
  via async marshalling (no `.Wait()`/`.Result` on the dispatcher).
- **Project-identity binding**: IPC requests are validated against the currently
  open project (projectId + revision) to prevent cross-project races.
- **Marker Discard rollback**: UndoManager markers are discarded (rolled back)
  on any exception during a mutation, preventing partial state.
- **Merged catalog reads**: catalog lookups merge `Root.Manufacturers`
  (project-local) with `Root.GlobalProductStoreManufacturers` (global store),
  de-duplicated, fixing the case where one source is empty.
- **Idempotency single-flight**: concurrent duplicate requests with the same
  idempotencyKey are coalesced into a single execution.
- **Session file + restricted ACL**: single `session.json` at a well-known path,
  pipe security restricted to the current Windows user SID.

## SDK APIs NOT used (6.4-only)

- **`DetermineLinkSecurityImpacts`**: 6.4-only, intentionally excluded.

## SDK APIs verified (used in this AddIn)

- `DatapointSubtype.Parent`: exists, returns the owning DatapointType (used for DPT formatting).
- `ComObjectInstanceRef.ParentModuleInstance`: exists, returns ModuleInstance or null.
- `ComObjectInstanceRef.Puid`: exists but OBSOLETE, always returns 0 (not used).
- `ModuleInstance.Puid`: exists (from ProjectRelatedObjectWithPuid, used for CO ref uniqueness).
- `ParameterInstanceRef.UniqueId`: exists, used for parameter ref encoding.
