using System.Collections.Generic;

namespace Knx.EtsBridge.Addin
{
    // ---------------------------------------------------------------
    // DTOs returned by IEtsProjectGateway methods.
    // These are plain objects with no SDK dependencies, safe to
    // serialize on any thread.
    //
    // Property names are PascalCase; the IpcServer serializes them as
    // camelCase via CamelCasePropertyNamesContractResolver (#4).
    // ---------------------------------------------------------------

    internal sealed class ProjectInfo
    {
        public string ProjectId { get; set; } = "";
        public string Name { get; set; } = "";
        public string GroupAddressStyle { get; set; } = "";
        public string Revision { get; set; } = "";
    }

    internal sealed class DeviceInfo
    {
        public string Ref { get; set; } = "";
        public string Address { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public string Product { get; set; } = "";
        public string OrderNumber { get; set; } = "";
        public string Line { get; set; } = "";
        /// <summary>
        /// True when a create/rename stored a NAME shorter than requested, i.e. ETS
        /// truncated it (name length limit). Detected by reading the value back.
        /// Omitted from JSON when false.
        /// </summary>
        [Newtonsoft.Json.JsonProperty("truncated",
            DefaultValueHandling = Newtonsoft.Json.DefaultValueHandling.Ignore)]
        public bool Truncated { get; set; }
    }

    internal sealed class GroupAddressInfo
    {
        public string Ref { get; set; } = "";
        public string Address { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public string? Dpt { get; set; }
        /// <summary>
        /// True when a create/rename stored a NAME shorter than requested, i.e. ETS
        /// truncated it (name length limit). Detected by reading the value back.
        /// Omitted from JSON when false.
        /// </summary>
        [Newtonsoft.Json.JsonProperty("truncated",
            DefaultValueHandling = Newtonsoft.Json.DefaultValueHandling.Ignore)]
        public bool Truncated { get; set; }
    }

    internal sealed class GroupRangeInfo
    {
        public string Ref { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public uint Address { get; set; }
    }

    internal sealed class ComObjectFlagsResult
    {
        public string Ref { get; set; } = "";
        public bool CommunicationFlag { get; set; }
        public bool ReadFlag { get; set; }
        public bool WriteFlag { get; set; }
        public bool TransmitFlag { get; set; }
        public bool UpdateFlag { get; set; }
        public bool ReadOnInitFlag { get; set; }
        public string Priority { get; set; } = "";
    }

    internal sealed class ComObjectInfo
    {
        public string Ref { get; set; } = "";
        public uint Number { get; set; }
        public string Name { get; set; } = "";
        /// <summary>User-editable description (get/set, SDK: ComObjectInstanceRef.Description).</summary>
        public string? Description { get; set; }
        /// <summary>User-editable function text (get/set, SDK: ComObjectInstanceRef.FunctionText).</summary>
        public string? FunctionText { get; set; }
        /// <summary>User-editable text (get/set, SDK: ComObjectInstanceRef.Text).</summary>
        public string? Text { get; set; }
        public string? Dpt { get; set; }
        public string Flags { get; set; } = "";
        public List<string> Links { get; set; } = new List<string>();
    }

    internal sealed class SegmentInfo
    {
        public string SegmentRef { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
    }

    internal sealed class TopologyLineInfo
    {
        public string LineRef { get; set; } = "";
        public string Address { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public List<SegmentInfo> Segments { get; set; } = new List<SegmentInfo>();
    }

    internal sealed class TopologyAreaInfo
    {
        public string AreaRef { get; set; } = "";
        public string Address { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public List<TopologyLineInfo> Lines { get; set; } = new List<TopologyLineInfo>();
    }

    internal sealed class ManufacturerInfo
    {
        public string ManufacturerRef { get; set; } = "";
        public string Name { get; set; } = "";
    }

    internal sealed class CatalogItemInfo
    {
        public string CatalogItemRef { get; set; } = "";
        public string Manufacturer { get; set; } = "";
        public string Name { get; set; } = "";
        public string OrderNumber { get; set; } = "";
        /// <summary>From CatalogItem.VisibleDescription (get-only). Null for global product store items.</summary>
        public string? Description { get; set; }
    }

    internal sealed class ParameterInfo
    {
        public string ParameterRef { get; set; } = "";
        public string Name { get; set; } = "";
        public string Value { get; set; } = "";
        public bool IsDefault { get; set; }
        public bool IsActive { get; set; }
    }

    internal sealed class UnifiedCatalogItemInfo
    {
        public string UnifiedCatalogItemRef { get; set; } = "";
        public string Manufacturer { get; set; } = "";
        public string Name { get; set; } = "";
        public string OrderNumber { get; set; } = "";
    }

    internal sealed class CatalogImportResult
    {
        public List<CatalogItemInfo> Imported { get; set; } = new List<CatalogItemInfo>();
    }

    internal sealed class InternalizeResult
    {
        public string CatalogItemRef { get; set; } = "";
    }

    internal sealed class AddDeviceResult
    {
        public string Ref { get; set; } = "";
        public string Address { get; set; } = "";
    }

    internal sealed class JobStatusInfo
    {
        public string State { get; set; } = "";
        public int? Percent { get; set; }
        public string? Error { get; set; }
        /// <summary>Job-specific result payload (scan addresses, monitor telegrams, etc.). Null while running.</summary>
        public object? Result { get; set; }
    }

    // -- Phase B: Building structure DTOs --

    internal sealed class BuildingPartInfo
    {
        public string Ref { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public string Type { get; set; } = "";
        public List<BuildingPartInfo> Children { get; set; } = new List<BuildingPartInfo>();
        public List<string> Devices { get; set; } = new List<string>();
        public List<BuildingFunctionInfo> Functions { get; set; } = new List<BuildingFunctionInfo>();
    }

    internal sealed class BuildingFunctionInfo
    {
        public string Ref { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public List<string> GroupAddresses { get; set; } = new List<string>();
        /// <summary>
        /// Non-null caveat when GroupAddresses is empty. Known ETS 6.3.0 SDK behaviour:
        /// after a GA is unlinked/deleted from a function (via this app OR in ETS), the
        /// SDK caches the object tree and returns this function's address list as empty
        /// until the owning app is restarted. An empty list therefore cannot be trusted.
        /// </summary>
        public string? Note { get; set; }
    }

    // -- Phase E: Catalog detail DTO --

    internal sealed class CatalogProductDetailInfo
    {
        public string CatalogItemRef { get; set; } = "";
        public string Manufacturer { get; set; } = "";
        public string Name { get; set; } = "";
        public string OrderNumber { get; set; } = "";
        public string Description { get; set; } = "";
        public List<string> MediumTypes { get; set; } = new List<string>();
    }

    // -- Phase D: Bus/Online operations DTOs --

    internal sealed class BusPingResult
    {
        public bool Alive { get; set; }
    }

    internal sealed class DeviceReadInfoResult
    {
        public string MaskVersion { get; set; } = "";
        public string MaskVersionId { get; set; } = "";
    }

    internal sealed class GroupReadResult
    {
        /// <summary>Hex-encoded group value bytes, or null if no response within timeout (2.3 s).</summary>
        public string? Value { get; set; }
    }

    internal sealed class GroupWriteResult
    {
        /// <summary>True if the bus acknowledged the write positively.</summary>
        public bool Acknowledged { get; set; }
    }

    internal sealed class GroupMonitorTelegram
    {
        public string Service { get; set; } = "";
        public string SourceAddress { get; set; } = "";
        public string GroupAddress { get; set; } = "";
        public string? Value { get; set; }
        public string Timestamp { get; set; } = "";
    }

    internal sealed class ScanLineJobResult
    {
        public List<string> Addresses { get; set; } = new List<string>();
    }

    // -- Phase F: Trade DTO --

    internal sealed class TradeInfo
    {
        public string Ref { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>May be plain text or RTF text (returned as-is from ETS).</summary>
        public string? Comment { get; set; }
        public string? Number { get; set; }
        public List<string> Devices { get; set; } = new List<string>();
    }

    // -- Phase F: Additional address DTOs --

    internal sealed class AdditionalAddressResult
    {
        public string DeviceRef { get; set; } = "";
        public ushort Address { get; set; }
    }

    // -- bridge.info DTO --

    internal sealed class BridgeInfo
    {
        public string AddinVersion { get; set; } = "";
        /// <summary>Knx.Ets.Sdk version loaded at RUNTIME (the host ETS's SDK).</summary>
        public string SdkVersion { get; set; } = "";
        /// <summary>Knx.Ets.Sdk version this AddIn was COMPILED against (may differ from
        /// SdkVersion when a 6.4-built AddIn runs on ETS 6.3, bound via AssemblyResolve).</summary>
        public string BuiltAgainstSdk { get; set; } = "";
        public string ProjectName { get; set; } = "";
        public string ProjectId { get; set; } = "";
        public string ProjectRevision { get; set; } = "";
        public bool KnxIpOnly { get; set; } = true;
    }

    // -- bus.reconstructLine DTOs --

    internal sealed class ReconstructLineDeviceInfo
    {
        public string Address { get; set; } = "";
        public string? MaskVersion { get; set; }
        public string? SerialNumber { get; set; }
        public uint? ManufacturerId { get; set; }
        public string? Error { get; set; }
    }

    internal sealed class ReconstructLineJobResult
    {
        public List<ReconstructLineDeviceInfo> Devices { get; set; } = new List<ReconstructLineDeviceInfo>();
        public string ScannedRange { get; set; } = "";
    }

    // -- device.readGroupObjects DTOs --

    /// <summary>
    /// Structured KNX communication-object flags read from the physical device.
    /// Each flag is nullable: null means the flag could not be read (mask-dependent).
    /// </summary>
    internal sealed class BusComObjectFlags
    {
        public bool? C { get; set; }
        public bool? R { get; set; }
        public bool? W { get; set; }
        public bool? T { get; set; }
        public bool? U { get; set; }
    }

    internal sealed class BusComObjectEntry
    {
        public uint Number { get; set; }
        public List<string> GroupAddresses { get; set; } = new List<string>();
        /// <summary>
        /// Structured flags (nullable per-field). Null when flags could not be read at all.
        /// </summary>
        public BusComObjectFlags? Flags { get; set; }
    }

    internal sealed class DeviceGroupObjectsResult
    {
        public string Address { get; set; } = "";
        public List<BusComObjectEntry> ComObjects { get; set; } = new List<BusComObjectEntry>();
        public bool Partial { get; set; }
        public string Note { get; set; } = "";
    }

    // -- NEW METHOD: device.compare DTOs --

    internal sealed class DeviceComparePropertyResult
    {
        public string Property { get; set; } = "";
        public bool Equal { get; set; }
        public string? Expected { get; set; }
        public string? Actual { get; set; }
    }

    internal sealed class DeviceCompareResult
    {
        public List<DeviceComparePropertyResult> Compared { get; set; } = new List<DeviceComparePropertyResult>();
        public bool Partial { get; set; }
        public string Note { get; set; } = "";
    }

    internal sealed class GroupMonitorJobResult
    {
        /// <summary>
        /// Captured telegrams during the monitor window.
        /// NOTE (6.3.0): GroupMessageReceived does NOT fire for KNX Secure telegrams.
        /// </summary>
        public List<GroupMonitorTelegram> Telegrams { get; set; } = new List<GroupMonitorTelegram>();
    }

    // -- Group 1: KNX Secure Certificates DTO --

    /// <summary>
    /// #16 (SECURITY): FDSK and Password removed -- secret material must not
    /// leave the ETS process boundary over IPC. Only the non-sensitive certificate
    /// identity fields are exposed.
    /// </summary>
    internal sealed class CertificateInfo
    {
        public string Ref { get; set; } = "";
        public string? SerialNumber { get; set; }
        public string? DeviceRef { get; set; }
    }

    // -- Group 2: UI Navigation (no new DTO; returns OkResult) --

    // -- Group 3: Organization DTOs --

    internal sealed class TagInfo
    {
        public string Ref { get; set; } = "";
        public string Label { get; set; } = "";
        public string Color { get; set; } = "";
    }

    internal sealed class ToDoItemInfo
    {
        public string Ref { get; set; } = "";
        public string Description { get; set; } = "";
        public string ObjectPath { get; set; } = "";
        public string Status { get; set; } = "";
    }

    // -- Group 5: Channels / Modules DTOs --

    internal sealed class ModuleArgumentInfo
    {
        public string RefId { get; set; } = "";
        public string Name { get; set; } = "";
        public string? Value { get; set; }
        public string Type { get; set; } = "";
    }

    internal sealed class ChannelInstanceInfo
    {
        public string Name { get; set; } = "";
        public string? Description { get; set; }
        /// <summary>Display text of the group object tree element (get-only, SDK: ChannelInstance.Text).</summary>
        public string? Text { get; set; }
        public bool IsActive { get; set; }
        public string? ApplicationProgramChannelId { get; set; }
        public List<string> ActiveComObjectRefs { get; set; } = new List<string>();
    }

    internal sealed class ModuleInstanceInfo
    {
        public string Ref { get; set; } = "";
        public string Name { get; set; } = "";
        public List<ChannelInstanceInfo> Channels { get; set; } = new List<ChannelInstanceInfo>();
        public List<ModuleArgumentInfo> Arguments { get; set; } = new List<ModuleArgumentInfo>();
    }

    internal sealed class DeviceChannelsResult
    {
        public string DeviceRef { get; set; } = "";
        public List<ChannelInstanceInfo> Channels { get; set; } = new List<ChannelInstanceInfo>();
        public List<ModuleInstanceInfo> Modules { get; set; } = new List<ModuleInstanceInfo>();
    }

    // -- Group 6: ProjectHistory DTO --

    internal sealed class ProjectHistoryInfo
    {
        public string Ref { get; set; } = "";
        public string Date { get; set; } = "";
        public string Text { get; set; } = "";
        public string? Detail { get; set; }
        public string? User { get; set; }
    }

    // ---------------------------------------------------------------
    // The gateway interface -- the ONLY seam touching ETS SDK types.
    // All methods are called via EtsDispatcher on the UI thread.
    // Implementations MUST wrap mutations in UndoManager markers.
    // ---------------------------------------------------------------

    internal interface IEtsProjectGateway
    {
        /// <summary>Current project revision (opaque, monotonically increasing).</summary>
        string ProjectRevision { get; }

        /// <summary>Stable project identifier (ProjectGuid).</summary>
        string ProjectId { get; }

        // -- Batch orchestration: opaque UndoManager marker handle --
        // Used by batch.apply to wrap many sub-operations in ONE undo marker so the
        // whole batch is atomic (a later step's failure rolls back the earlier ones).
        // Sub-operations open their own child markers, which nest under this one.
        // MUST be called on the UI thread (batch.apply runs the whole sequence inside
        // a single dispatcher invocation, so callers are already on the UI thread).

        /// <summary>Open an outer undo marker and return an opaque handle.</summary>
        object BeginMarker(string name);

        /// <summary>Commit the marker (and bump the project revision), then dispose it.</summary>
        void CommitMarker(object markerHandle);

        /// <summary>Discard the marker -- rolls back all operations under it -- then dispose it.</summary>
        void DiscardMarker(object markerHandle);

        ProjectInfo GetProjectInfo();
        List<DeviceInfo> ListDevices();
        List<GroupAddressInfo> ListGroupAddresses();
        List<ComObjectInfo> ListComObjects(string deviceRef);
        List<TopologyAreaInfo> ListTopology();
        List<ManufacturerInfo> ListManufacturers();
        List<CatalogItemInfo> SearchCatalog(string query, string? manufacturerRef);
        List<ParameterInfo> ListParameters(string deviceRef);

        /// <summary>
        /// Parse address string per project groupAddressStyle (3-level "main/middle/sub",
        /// 2-level "main/sub", or raw integer). (#3)
        /// </summary>
        GroupAddressInfo CreateGroupAddress(string name, string address, uint? dptMain, uint? dptSub);
        void CreateLink(string comObjectRef, string gaRef);
        void DeleteLink(string comObjectRef, string gaRef);
        void SetParameter(string deviceRef, string parameterRef, string value);

        /// <summary>
        /// Add a device from the catalog to a topology line.
        /// Wraps DeviceCollection.Add(Line, CatalogItem, ...) under UndoManager.
        /// </summary>
        AddDeviceResult AddDeviceFromCatalog(string lineRef, string catalogItemRef, string address);

        /// <summary>
        /// Starts an asynchronous device download.
        /// Returns a jobId for tracking via GetJobStatus/CancelJob.
        /// Throws BusUnavailableException if no KNXnet/IP connection is available.
        /// </summary>
        /// <param name="options">Download type: "all" (default), "partial", "application",
        /// "network", "networkBySerial". Maps to LoadDeviceOptions enum.</param>
        string StartDeviceProgram(string deviceRef, string? options);

        /// <summary>
        /// Query the status of a running or completed job.
        /// </summary>
        JobStatusInfo GetJobStatus(string jobId);

        /// <summary>
        /// Cancel a running job. Idempotent: canceling an already-finished job is a no-op.
        /// </summary>
        void CancelJob(string jobId);

        /// <summary>
        /// Starts an asynchronous firmware update for the given device.
        /// Returns a jobId. IoT-only in 6.3.0; throws NotSupportedException for non-IoT devices.
        /// </summary>
        string UpdateFirmware(string deviceRef, string firmware);

        /// <summary>
        /// Search the unified (online + local) catalog by query string.
        /// Returns matching UnifiedCatalogItems with "uci:{Id}" refs.
        /// </summary>
        List<UnifiedCatalogItemInfo> SearchOnlineCatalog(string query, string? manufacturerRef);

        /// <summary>
        /// Import a .knxprod file into the local product store.
        /// Wraps Root.ImportProductData(path). No UndoManager (product store, not project).
        /// </summary>
        CatalogImportResult ImportProductData(string path);

        /// <summary>
        /// Internalize a UnifiedCatalogItem into the project's local catalog.
        /// Wraps Root.InternalizeProductData(UnifiedCatalogItem). No UndoManager.
        /// </summary>
        InternalizeResult InternalizeProductData(string unifiedCatalogItemRef);

        // -- Phase A: Group Address structural editing --
        void DeleteGroupAddress(string gaRef);
        GroupAddressInfo RenameGroupAddress(string gaRef, string name);
        GroupAddressInfo SetGroupAddressDescription(string gaRef, string description);
        GroupAddressInfo SetGroupAddressDpt(string gaRef, uint dptMain, uint? dptSub);

        // -- Phase A: Group Range --
        GroupRangeInfo CreateGroupRange(string name, ushort address, string? parentGroupRangeRef);
        void DeleteGroupRange(string groupRangeRef);

        // -- Phase A: Device structural editing --
        void DeleteDevice(string deviceRef);
        DeviceInfo RenameDevice(string deviceRef, string name);
        DeviceInfo SetDeviceAddress(string deviceRef, string address);
        void UnassignDeviceFromLine(string deviceRef);

        // -- Phase A: Topology --
        TopologyAreaInfo CreateArea(string name, ushort? address);
        void DeleteArea(string areaRef);
        TopologyLineInfo CreateLine(string areaRef, string name, ushort? address);
        void DeleteLine(string lineRef);

        // -- Phase A: Device label fields --
        /// <summary>Set Device.Description (get/set, SDK XML line 12683).</summary>
        DeviceInfo SetDeviceDescription(string deviceRef, string description);
        /// <summary>Set Device.Comment (get/set, SDK XML line 12632). May be plain text or RTF.</summary>
        DeviceInfo SetDeviceComment(string deviceRef, string comment);

        // -- Phase A: ComObject label fields --
        /// <summary>Set ComObjectInstanceRef.Description (get/set, SDK XML line 11788).</summary>
        ComObjectInfo SetComObjectDescription(string comObjectRef, string description);
        /// <summary>Set ComObjectInstanceRef.FunctionText (get/set, SDK XML line 11802).</summary>
        ComObjectInfo SetComObjectFunctionText(string comObjectRef, string functionText);

        // -- Phase A: ComObject flags --
        ComObjectFlagsResult SetComObjectFlags(string comObjectRef,
            bool? communicationFlag, bool? readFlag, bool? writeFlag,
            bool? transmitFlag, bool? updateFlag, bool? readOnInitFlag,
            string? priority);

        // -- Phase B: Building structure --
        List<BuildingPartInfo> ListBuilding();
        BuildingPartInfo CreateBuildingPart(string name, string type, string? parentBuildingPartRef, string? spaceUsage);
        void DeleteBuildingPart(string buildingPartRef);
        BuildingPartInfo RenameBuildingPart(string buildingPartRef, string name);
        void AssignDevice(string buildingPartRef, string deviceRef);
        void UnassignDevice(string buildingPartRef, string deviceRef);

        // -- Phase B: Building functions --
        BuildingFunctionInfo CreateBuildingFunction(string buildingPartRef, string name);
        void DeleteBuildingFunction(string buildingFunctionRef);
        void LinkBuildingFunctionGA(string buildingFunctionRef, List<string> gaRefs);
        void UnlinkBuildingFunctionGA(string buildingFunctionRef, List<string> gaRefs);

        // -- Phase C: Project management --
        void ProjectSave();
        bool ProjectExport(string path, bool includeCatalog);
        void ProjectBackup(string path);
        void ProjectUndo();
        void ProjectRedo();

        // -- Phase E: Catalog browsing --
        List<CatalogItemInfo> BrowseProducts(string manufacturerRef, int offset, int limit);
        CatalogProductDetailInfo ProductInfo(string catalogItemRef);

        // -- Bus/Online operations --
        // All bus ops serialize through an internal lock; concurrent calls get bus_unavailable.
        // Require a bus interface (KNXnet/IP or USB) selected in ETS.

        /// <summary>Ping a KNX individual address. Direct return.</summary>
        BusPingResult BusPing(string address);

        /// <summary>Scan a line for responding devices. Long-running -> job model.</summary>
        string StartScanLine(string lineRef);

        /// <summary>Read device descriptor from physical device. Direct return.</summary>
        DeviceReadInfoResult DeviceReadInfo(string deviceRef);

        /// <summary>
        /// Limited device compare: reads device descriptor from the physical device and
        /// compares basic identity (mask version) against project data. READ-only, bus op.
        /// Returns partial=true; not a full ETS device compare.
        /// </summary>
        DeviceCompareResult DeviceCompare(string deviceRef);

        /// <summary>Read a group value from the bus. Direct return (2.3 s timeout).</summary>
        GroupReadResult GroupRead(string gaRef);

        /// <summary>Write a group value to the bus. Direct return.</summary>
        GroupWriteResult GroupWrite(string gaRef, string valueHex, bool less7Bits);

        /// <summary>Monitor group telegrams for a bounded duration. Long-running -> job model.
        /// NOTE (6.3.0): GroupMessageReceived does NOT fire for KNX Secure telegrams.</summary>
        string StartGroupMonitor(string lineRef, int durationMs);

        /// <summary>Unload a device (application only or full including address).
        /// Long-running -> job model. Progress via OnOnlineOperationsEvent.</summary>
        string StartDeviceUnload(string deviceRef, bool fullUnload);

        // -- bridge.info (READ, no bus) --

        /// <summary>
        /// Health/version endpoint. Returns bridge info without requiring a bus connection.
        /// Reads assembly versions, project identity, and the KNXnet/IP-only policy flag.
        /// </summary>
        BridgeInfo GetBridgeInfo();

        // -- bus.reconstructLine (READ, JOB) --

        /// <summary>
        /// Scan a line for present devices and read per-device identity (mask version,
        /// serial number, manufacturer ID) via DeviceManagement. Returns a jobId;
        /// result available via job.status. For LLM-driven KNX project recovery.
        /// </summary>
        string StartReconstructLine(string lineRef);

        // -- device.readGroupObjects (READ) --

        /// <summary>
        /// Synchronous read of a physical device's group-object/association tables.
        /// Returns the DeviceGroupObjectsResult directly. Blocks the dispatcher for
        /// the duration of bus reads (acceptable for most devices; use
        /// StartReadDeviceGroupObjects for slow/unreachable devices).
        /// Best-effort: returns partial=true when the mask/realisation type does not
        /// support generic readout. READ-only (no UndoManager, no revision bump).
        /// </summary>
        DeviceGroupObjectsResult ReadDeviceGroupObjects(string address);

        /// <summary>
        /// Start an asynchronous read of a physical device's group-object association
        /// tables via DeviceManagement property reads. Returns a jobId; result available
        /// via job.status. Best-effort: returns partial=true when the mask/realisation
        /// type does not support generic readout. READ-only (no UndoManager, no revision
        /// bump). Uses LocateObject to find the correct interface-object indices for the
        /// association table, address table, and group object table -- does not assume
        /// fixed object indices.
        /// </summary>
        string StartReadDeviceGroupObjects(string address);

        // -- bus.setIndividualAddress (WRITE, JOB) --

        /// <summary>
        /// Program a device's individual address by overwriting its current bus address,
        /// without pressing the programming button. The NEW address is the device's
        /// project-configured individual address. The currentAddress is the address the
        /// physical device currently responds to on the bus.
        /// Long-running -> job model. Progress via OnOnlineOperationsEvent.
        /// Bus write, NOT a project mutation (no UndoManager).
        /// </summary>
        /// <param name="deviceRef">The project device ref ("d{puid}").</param>
        /// <param name="currentAddress">The device's current bus address ("area.line.device")
        /// that will be overwritten with the project address.</param>
        /// <returns>A jobId for tracking via GetJobStatus/CancelJob.</returns>
        string StartSetIndividualAddress(string deviceRef, string currentAddress);

        // -- device.reset (WRITE, direct return) --

        /// <summary>
        /// Perform a bus reset on a physical device (restart command).
        /// Synchronous, direct return. Bus write, NOT a project mutation.
        /// </summary>
        /// <param name="deviceRef">The project device ref ("d{puid}").</param>
        void ResetDevice(string deviceRef);

        // ---------------------------------------------------------------
        // Phase F: Move / re-parent
        // ---------------------------------------------------------------

        /// <summary>Move a GroupAddress into a different GroupRange.</summary>
        void MoveGroupAddress(string targetGroupRangeRef, string groupAddressRef);

        /// <summary>Move a child GroupRange into a different parent GroupRange.</summary>
        void MoveGroupRange(string targetGroupRangeRef, string sourceGroupRangeRef);

        /// <summary>Move a Line into a different Area.</summary>
        void MoveLine(string targetAreaRef, string lineRef);

        /// <summary>Move a BuildingPart into a different parent BuildingPart.</summary>
        void MoveBuildingPart(string targetBuildingPartRef, string sourceBuildingPartRef);

        // ---------------------------------------------------------------
        // Phase F: Additional addresses
        // ---------------------------------------------------------------

        /// <summary>Add an additional individual address to a device (multi-address couplers).</summary>
        AdditionalAddressResult AddAdditionalAddress(string deviceRef, ushort address);

        /// <summary>Remove (park) an additional individual address from a device.</summary>
        void RemoveAdditionalAddress(string deviceRef, ushort address);

        /// <summary>Add sending group addresses to a line (filter table / additional GA list).</summary>
        void AddLineAdditionalGroupAddresses(string lineRef, List<string> gaRefs);

        /// <summary>Remove sending group addresses from a line.</summary>
        void RemoveLineAdditionalGroupAddresses(string lineRef, List<string> gaRefs);

        // ---------------------------------------------------------------
        // Phase F: Segments
        // ---------------------------------------------------------------

        /// <summary>Create a segment on a line.</summary>
        SegmentInfo CreateSegment(string lineRef, string name, string mediumTypeName);

        /// <summary>Delete a segment from its parent line.</summary>
        void DeleteSegment(string segmentRef);

        // ---------------------------------------------------------------
        // Phase F: Trades
        // ---------------------------------------------------------------

        /// <summary>List all trades in the installation.</summary>
        List<TradeInfo> ListTrades();

        /// <summary>Create a new trade.</summary>
        TradeInfo CreateTrade(string name);

        /// <summary>Delete a trade.</summary>
        void DeleteTrade(string tradeRef);

        /// <summary>Assign a device to a trade (link).</summary>
        void TradeAssignDevice(string tradeRef, string deviceRef);

        /// <summary>Unassign a device from a trade (unlink).</summary>
        void TradeUnassignDevice(string tradeRef, string deviceRef);

        // ---------------------------------------------------------------
        // Phase F: Bus interface (filter table)
        // ---------------------------------------------------------------

        /// <summary>Link group addresses to a device's bus interface (filter table).</summary>
        void BusInterfaceLink(string deviceRef, List<string> gaRefs);

        /// <summary>Unlink group addresses from a device's bus interface.</summary>
        void BusInterfaceUnlink(string deviceRef, List<string> gaRefs);

        // ---------------------------------------------------------------
        // Phase F: Parameter reset to default
        // ---------------------------------------------------------------

        /// <summary>Reset a parameter to its default value (sets Value = null).</summary>
        void ResetParameterToDefault(string deviceRef, string parameterRef);

        // ---------------------------------------------------------------
        // Group 1: KNX Secure Certificates
        // ---------------------------------------------------------------

        /// <summary>List all device certificates in the project.</summary>
        List<CertificateInfo> ListCertificates();

        /// <summary>Add a device certificate from a raw certificate string (e.g. QR scan).</summary>
        CertificateInfo AddCertificate(string rawKey);

        /// <summary>Delete a certificate by ref.</summary>
        void DeleteCertificate(string certificateRef);

        // ---------------------------------------------------------------
        // Group 2: UI Navigation
        // ---------------------------------------------------------------

        /// <summary>
        /// Navigate the ETS UI to the given object ref.
        /// Dispatches to the appropriate Root.NavigateTo* method based on the ref prefix.
        /// Not a project mutation, no UndoManager marker.
        /// </summary>
        void NavigateTo(string objectRef);

        // ---------------------------------------------------------------
        // Group 3: Organization (Tags, ToDoItems)
        // ---------------------------------------------------------------

        /// <summary>List all project tags.</summary>
        List<TagInfo> ListTags();

        /// <summary>Create a new project tag. Remarks: TagCollection.Add is NOT undoable.</summary>
        TagInfo CreateTag(string label, string colorHex);

        /// <summary>Delete a project tag by ref.</summary>
        void DeleteTag(string tagRef);

        /// <summary>List all project to-do items.</summary>
        List<ToDoItemInfo> ListToDoItems();

        /// <summary>Create a new to-do item.</summary>
        ToDoItemInfo CreateToDoItem(string description, string objectPath, string status);

        /// <summary>Delete a to-do item by ref.</summary>
        void DeleteToDoItem(string toDoItemRef);

        // ---------------------------------------------------------------
        // Group 5: Channels / Modules (READ)
        // ---------------------------------------------------------------

        /// <summary>
        /// List device channels (ChannelInstance), modules (ModuleInstance with
        /// ModuleArguments), and their active COM objects.
        /// </summary>
        DeviceChannelsResult ListDeviceChannels(string deviceRef);

        // ---------------------------------------------------------------
        // Group 6: Project History (Niche -- useful for LLM audit trail)
        // ---------------------------------------------------------------

        /// <summary>List project history entries.</summary>
        List<ProjectHistoryInfo> ListProjectHistory();

        /// <summary>Add a project history entry (LLM audit trail).</summary>
        ProjectHistoryInfo AddProjectHistory(string text);

        /// <summary>Delete a project history entry by ref.</summary>
        void DeleteProjectHistory(string historyRef);
    }
}
