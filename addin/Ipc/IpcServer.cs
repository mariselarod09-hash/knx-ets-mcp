using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using System.Threading;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// Named-pipe JSON-RPC server implementing the IPC contract from protocol.md.
    /// - One connection at a time (single MCP server client).
    /// - Newline-delimited JSON (UTF-8, no BOM -- #2).
    /// - Token-based auth from session.json.
    /// - idempotencyKey caching with (projectId, key) scope and TTL (#5).
    /// - expectedProjectRevision check inside the UI-thread dispatch (#19).
    ///
    /// Pipe name: "knx-ets-bridge-{random}" restricted to the current Windows user SID.
    /// Session info written to %LOCALAPPDATA%\knx-ets-bridge\session.json (#6).
    /// </summary>
    internal sealed class IpcServer : IDisposable
    {
        private readonly IEtsProjectGateway _gateway;
        private readonly EtsDispatcher _dispatcher;
        private readonly ApprovalManager _approvalManager;
        private readonly string _pipeName;
        private readonly string _token;
        private Thread? _listenThread;
        private volatile bool _stopping;

        // --- TCP settings (read at Start(); changing them takes effect after project reopen) ---
        private readonly bool _tcpEnabled;
        private readonly int _tcpPort;
        private readonly bool _tcpAllowLan;
        private TcpListener? _tcpListener;
        private Thread? _tcpListenThread;
        private volatile bool _tcpListening;
        private readonly List<TcpClient> _activeTcpClients = new List<TcpClient>();
        private readonly object _tcpClientsLock = new object();

        // #14: Track current pipe so Stop() can close it to unblock WaitForConnection/ReadLine.
        private NamedPipeServerStream? _currentPipe;
        private readonly object _pipeLock = new object();

        // --- Observable state for the AddIn status/log panel ---
        private volatile bool _clientConnected;
        private readonly object _logLock = new object();
        private readonly List<LogEntry> _log = new List<LogEntry>();
        private int _logVersion;
        private const int MaxLogEntries = 200;

        // #5: Idempotency cache scoped by (projectId, idempotencyKey) with TTL.
        // Stores serialized result JSON (not full envelope), so the envelope can be
        // rebuilt with the current request id and projectRevision on cache hit.
        // Residual semantics: process-local, does not survive ETS restarts.
        // Prevents double-application within a 5-min retry window; replay after
        // TTL expiry re-executes the mutation.
        private readonly ConcurrentDictionary<string, IdempotencyCacheEntry> _idempotencyCache
            = new ConcurrentDictionary<string, IdempotencyCacheEntry>();
        // #12: Per-key single-flight gates to prevent duplicate mutation execution.
        // A mutating request with an idempotencyKey acquires the per-key semaphore,
        // re-checks the cache inside, executes+stores while holding it, then releases.
        // Concurrent duplicates wait on the semaphore and get the cached result.
        private readonly ConcurrentDictionary<string, SemaphoreSlim> _idempotencyGates
            = new ConcurrentDictionary<string, SemaphoreSlim>(StringComparer.Ordinal);
        private const int MaxCacheEntries = 1000;
        private static readonly TimeSpan CacheTtl = TimeSpan.FromMinutes(5);

        // #21: Max message/line size (1 MB) to prevent unbounded memory from newline-less streams.
        private const int MaxLineBytes = 1024 * 1024;

        // #11: Mutating methods require idempotencyKey.
        private static readonly HashSet<string> MutatingMethods = new HashSet<string>(StringComparer.Ordinal)
        {
            "ga.create", "ga.delete", "ga.rename", "ga.setDescription", "ga.setDatapointType",
            "groupRange.create", "groupRange.delete",
            "device.addFromCatalog", "device.delete", "device.rename",
            "device.setAddress", "device.move", "device.unassign",
            "link.create", "link.delete",
            "param.set", "device.program", "firmware.update",
            "area.create", "area.delete",
            "line.create", "line.delete",
            "comObject.setFlags", "comObject.setDescription", "comObject.setFunctionText",
            "device.setDescription", "device.setComment",
            "catalog.import", "catalog.internalize",
            // Phase B: Building structure + functions
            "building.create", "building.delete", "building.rename",
            "building.assignDevice", "building.unassignDevice",
            "buildingFunction.create", "buildingFunction.delete",
            "buildingFunction.linkGroupAddress", "buildingFunction.unlinkGroupAddress",
            // Phase C: Project management
            "project.undo", "project.redo",
            // Phase D: Bus operations (mutating only)
            "group.write", "device.unload",
            "bus.setIndividualAddress", "device.reset",
            // Phase F: Move / re-parent
            "groupRange.moveGroupAddress", "groupRange.moveGroupRange",
            "line.move", "buildingPart.move",
            // Phase F: Additional addresses
            "device.addAdditionalAddress", "device.removeAdditionalAddress",
            "line.addAdditionalGroupAddress", "line.removeAdditionalGroupAddress",
            // Phase F: Segments
            "segment.create", "segment.delete",
            // Phase F: Trades
            "trade.create", "trade.delete",
            "trade.assignDevice", "trade.unassignDevice",
            // Phase F: Bus interface
            "busInterface.link", "busInterface.unlink",
            // Phase F: Parameter
            "param.setDefault",
            // Group 1: KNX Secure Certificates
            "certificate.add", "certificate.delete",
            // Group 3: Organization
            "tag.create", "tag.delete",
            "todo.create", "todo.delete",
            // Group 6: Project History
            "projectHistory.add", "projectHistory.delete",
            // Batch: many project mutations in one atomic undo marker
            "batch.apply"
        };

        // Methods allowed as steps inside batch.apply. Deliberately an ALLOWLIST (not a
        // denylist): only fast, undo-reversible PROJECT mutations that can share one undo
        // marker. Excludes bus/job/long-running ops (device.program, firmware.update,
        // group.write, device.unload, bus.setIndividualAddress, device.reset -- not
        // atomic, not undo-reversible, serialized separately), product-store ops
        // (catalog.import/internalize -- no undo marker), project.undo/redo, device.move
        // (not_supported), and batch.apply itself (no nesting).
        private static readonly HashSet<string> BatchableMethods = new HashSet<string>(StringComparer.Ordinal)
        {
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
            "projectHistory.add", "projectHistory.delete"
        };

        // Upper bound on batch size to keep one dispatcher round bounded.
        private const int MaxBatchOperations = 500;

        // #4: Shared JSON settings -- camelCase for both serialization and deserialization.
        internal static readonly JsonSerializerSettings JsonSettings = new JsonSerializerSettings
        {
            ContractResolver = new CamelCasePropertyNamesContractResolver()
        };

        // #2: No-BOM UTF-8 encoding for pipe I/O and session.json.
        private static readonly UTF8Encoding Utf8NoBom = new UTF8Encoding(false);

        public IpcServer(IEtsProjectGateway gateway, EtsDispatcher dispatcher,
                         ApprovalManager approvalManager,
                         bool tcpEnabled = false, int tcpPort = 8730, bool tcpAllowLan = false,
                         string? token = null)
        {
            _gateway = gateway ?? throw new ArgumentNullException(nameof(gateway));
            _dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
            _approvalManager = approvalManager ?? throw new ArgumentNullException(nameof(approvalManager));
            _pipeName = "knx-ets-bridge-" + Guid.NewGuid().ToString("N").Substring(0, 12);
            // Empty/whitespace token = NO authentication (anyone on the network can control ETS).
            // Non-empty token = require matching token in every request.
            _token = token ?? "";
            _tcpEnabled = tcpEnabled;
            _tcpPort = (tcpPort >= 1 && tcpPort <= 65535) ? tcpPort : 8730;
            _tcpAllowLan = tcpAllowLan;
        }

        public string PipeName => _pipeName;
        public string Token => _token;
        public bool TcpEnabled => _tcpEnabled;
        public int TcpPort => _tcpPort;
        public bool TcpAllowLan => _tcpAllowLan;

        /// <summary>True when a non-empty token is configured; false = auth disabled.</summary>
        public bool AuthRequired => !string.IsNullOrEmpty(_token);

        /// <summary>True while the TCP listener is actually bound and accepting.</summary>
        public bool IsTcpListening => _tcpListening;

        /// <summary>Append a diagnostic line to the panel activity log (lifecycle events).</summary>
        public void Log(string text, string outcome) => AppendLog(text, outcome);

        /// <summary>True while a client is connected to the named pipe.</summary>
        public bool ClientConnected => _clientConnected;

        /// <summary>Monotonically increasing version; bumped on every log/state change.</summary>
        public int LogVersion { get { lock (_logLock) { return _logVersion; } } }

        /// <summary>
        /// Raised (on a background thread) whenever ClientConnected or the log changes.
        /// Subscribers must marshal to the UI thread if needed.
        /// </summary>
        public event Action? StateChanged;

        /// <summary>Returns a snapshot of the rolling log. Thread-safe.</summary>
        public List<LogEntry> GetLogSnapshot()
        {
            lock (_logLock)
            {
                return new List<LogEntry>(_log);
            }
        }

        private void AppendLog(string text, string outcome)
        {
            lock (_logLock)
            {
                _log.Add(new LogEntry(DateTime.Now, text, outcome));
                if (_log.Count > MaxLogEntries)
                    _log.RemoveAt(0);
                _logVersion++;
            }
            try { StateChanged?.Invoke(); }
            catch { /* UI handler fault must not crash the IPC thread. */ }
        }

        /// <summary>
        /// #17: Start the listener BEFORE publishing session.json so the pipe is
        /// accepting connections by the time a client discovers the session file.
        /// Returns true if session.json was written successfully; false means
        /// the bridge should be treated as inactive.
        /// </summary>
        public bool Start()
        {
            _stopping = false;
            _listenThread = new Thread(ListenLoop)
            {
                Name = "IpcServer-Listen",
                IsBackground = true
            };
            _listenThread.Start();
            AppendLog($"Bridge started (pipe {_pipeName})", "---");

            // Start optional TCP listener.
            if (_tcpEnabled)
            {
                try
                {
                    var bindAddress = _tcpAllowLan ? IPAddress.Any : IPAddress.Loopback;
                    _tcpListener = new TcpListener(bindAddress, _tcpPort);
                    _tcpListener.Start();

                    _tcpListenThread = new Thread(TcpListenLoop)
                    {
                        Name = "IpcServer-TcpListen",
                        IsBackground = true
                    };
                    _tcpListenThread.Start();
                    _tcpListening = true;
                    AppendLog($"TCP listening on {bindAddress}:{_tcpPort}", "---");

                    System.Diagnostics.Trace.TraceInformation(
                        $"[IpcServer] TCP listener started on {bindAddress}:{_tcpPort}.");
                }
                catch (Exception ex)
                {
                    _tcpListening = false;
                    AppendLog($"TCP bind FAILED (port {_tcpPort}): {ex.Message}", "error");
                    System.Diagnostics.Trace.TraceWarning(
                        $"[IpcServer] Failed to start TCP listener on port {_tcpPort}: {ex.Message}");
                    // TCP failure is non-fatal; the pipe endpoint still works.
                    _tcpListener = null;
                }
            }
            else
            {
                AppendLog("TCP endpoint disabled (pipe only)", "---");
            }

            // Listener is running (pipe created almost immediately); now publish session file.
            return WriteSessionFile();
        }

        /// <summary>
        /// #14: Stop must close the pipe stream, signal the thread, and join it
        /// so WaitForConnection/ReadLine actually unblocks.
        /// #18: Delete only THIS instance's session file.
        /// </summary>
        public void Stop()
        {
            _stopping = true;

            // Close current pipe to unblock WaitForConnection or ReadLine.
            lock (_pipeLock)
            {
                try { _currentPipe?.Dispose(); }
                catch (ObjectDisposedException) { }
                _currentPipe = null;
            }

            // Stop TCP listener and close all active TCP clients.
            _tcpListening = false;
            try { _tcpListener?.Stop(); }
            catch { /* best-effort */ }

            lock (_tcpClientsLock)
            {
                foreach (var client in _activeTcpClients)
                {
                    try { client.Close(); }
                    catch { /* best-effort */ }
                }
                _activeTcpClients.Clear();
            }

            // Join the listen thread with a timeout so Stop() is bounded.
            if (_listenThread != null && _listenThread.IsAlive)
            {
                _listenThread.Join(TimeSpan.FromSeconds(5));
                _listenThread = null;
            }

            if (_tcpListenThread != null && _tcpListenThread.IsAlive)
            {
                _tcpListenThread.Join(TimeSpan.FromSeconds(5));
                _tcpListenThread = null;
            }

            DeleteSessionFile();
        }

        public void Dispose()
        {
            Stop();
        }

        // ---------------------------------------------------------------
        // Session file (#2, #17, #18)
        // ---------------------------------------------------------------

        private string SessionDir
        {
            get
            {
                var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.Combine(localAppData, "knx-ets-bridge");
            }
        }

        /// <summary>
        /// #6: Standardized on a single discovery file "session.json" (single-instance bridge).
        /// The file CONTENT contains the actual pipeName + token fields.
        /// </summary>
        private string SessionFilePath => Path.Combine(SessionDir, "session.json");

        /// <summary>
        /// #19: Write session.json with restricted ACL (current user + SYSTEM + Administrators).
        /// Atomic write via temp file + move when practical.
        /// Guarded with try/catch so cross-building on macOS still compiles and runs.
        /// </summary>
        private bool WriteSessionFile()
        {
            try
            {
                var dir = SessionDir;
                Directory.CreateDirectory(dir);

                // #19: Restrict directory ACL to current user, SYSTEM, and Administrators.
                try
                {
                    var dirInfo = new System.IO.DirectoryInfo(dir);
                    var dirSecurity = dirInfo.GetAccessControl();
                    dirSecurity.SetAccessRuleProtection(true, false); // remove inherited access
                    var currentUser = WindowsIdentity.GetCurrent().User;
                    if (currentUser != null)
                    {
                        dirSecurity.AddAccessRule(new FileSystemAccessRule(
                            currentUser,
                            FileSystemRights.FullControl,
                            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                            PropagationFlags.None,
                            AccessControlType.Allow));
                    }
                    dirSecurity.AddAccessRule(new FileSystemAccessRule(
                        new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                        FileSystemRights.FullControl,
                        InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                        PropagationFlags.None,
                        AccessControlType.Allow));
                    dirSecurity.AddAccessRule(new FileSystemAccessRule(
                        new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null),
                        FileSystemRights.FullControl,
                        InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                        PropagationFlags.None,
                        AccessControlType.Allow));
                    dirInfo.SetAccessControl(dirSecurity);
                }
                catch (Exception ex)
                {
                    // Non-Windows (macOS cross-build) or permission denied: best-effort.
                    System.Diagnostics.Trace.TraceWarning(
                        $"[IpcServer] Could not set directory ACL: {ex.Message}");
                }

                var json = JsonConvert.SerializeObject(new
                {
                    pipeName = _pipeName,
                    token = AuthRequired ? _token : (string?)null,
                    authRequired = AuthRequired,
                    pid = System.Diagnostics.Process.GetCurrentProcess().Id,
                    startedAt = DateTime.UtcNow.ToString("o")
                }, JsonSettings);

                // #19: Atomic write via temp file + move.
                var finalPath = SessionFilePath;
                var tmpPath = finalPath + ".tmp";
                File.WriteAllText(tmpPath, json, Utf8NoBom);

                // #19: Restrict file ACL before moving into place.
                try
                {
                    var fileInfo = new System.IO.FileInfo(tmpPath);
                    var fileSecurity = fileInfo.GetAccessControl();
                    fileSecurity.SetAccessRuleProtection(true, false);
                    var currentUser = WindowsIdentity.GetCurrent().User;
                    if (currentUser != null)
                    {
                        fileSecurity.AddAccessRule(new FileSystemAccessRule(
                            currentUser,
                            FileSystemRights.FullControl,
                            AccessControlType.Allow));
                    }
                    fileSecurity.AddAccessRule(new FileSystemAccessRule(
                        new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                        FileSystemRights.ReadAndExecute,
                        AccessControlType.Allow));
                    fileSecurity.AddAccessRule(new FileSystemAccessRule(
                        new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null),
                        FileSystemRights.FullControl,
                        AccessControlType.Allow));
                    fileInfo.SetAccessControl(fileSecurity);
                }
                catch (Exception ex)
                {
                    // Non-Windows (macOS cross-build) or permission denied: best-effort.
                    System.Diagnostics.Trace.TraceWarning(
                        $"[IpcServer] Could not set file ACL: {ex.Message}");
                }

                // Atomic move (same volume).
                if (File.Exists(finalPath))
                    File.Delete(finalPath);
                File.Move(tmpPath, finalPath);

                return true;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Trace.TraceWarning(
                    $"[IpcServer] Failed to write session file: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// #18: Only delete THIS instance's session file.
        /// </summary>
        private void DeleteSessionFile()
        {
            try
            {
                var path = SessionFilePath;
                if (File.Exists(path))
                    File.Delete(path);
                var tmpPath = path + ".tmp";
                if (File.Exists(tmpPath))
                    File.Delete(tmpPath);
            }
            catch
            {
                // Best-effort cleanup.
            }
        }

        // ---------------------------------------------------------------
        // Pipe listener (#14)
        // ---------------------------------------------------------------

        private void ListenLoop()
        {
            while (!_stopping)
            {
                NamedPipeServerStream? pipe = null;
                try
                {
                    pipe = CreatePipe();
                    lock (_pipeLock) { _currentPipe = pipe; }

                    pipe.WaitForConnection(); // blocks until client connects or pipe is closed
                    if (_stopping) break;

                    HandleConnection(pipe);
                }
                catch (ObjectDisposedException) when (_stopping)
                {
                    break;
                }
                catch (IOException) when (_stopping)
                {
                    break;
                }
                catch (Exception ex)
                {
                    if (_stopping) break;
                    System.Diagnostics.Trace.TraceError(
                        $"[IpcServer] Listener error: {ex}");
                    // Brief pause before retry to avoid spin.
                    Thread.Sleep(500);
                }
                finally
                {
                    lock (_pipeLock) { _currentPipe = null; }
                    pipe?.Dispose();
                }
            }
        }

        private NamedPipeServerStream CreatePipe()
        {
            // Restrict pipe access to the current Windows user.
            var security = new PipeSecurity();
            security.AddAccessRule(new PipeAccessRule(
                WindowsIdentity.GetCurrent().User!,
                PipeAccessRights.FullControl,
                AccessControlType.Allow));

            return new NamedPipeServerStream(
                _pipeName,
                PipeDirection.InOut,
                1, // max one concurrent connection
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous,
                inBufferSize: 4096,
                outBufferSize: 4096,
                pipeSecurity: security);
        }

        // ---------------------------------------------------------------
        // TCP listener (opt-in, same JSON-line protocol as pipe)
        // ---------------------------------------------------------------

        private void TcpListenLoop()
        {
            while (!_stopping)
            {
                TcpClient? client = null;
                try
                {
                    client = _tcpListener!.AcceptTcpClient();
                    if (_stopping)
                    {
                        client.Close();
                        break;
                    }

                    // Set read/write timeouts to prevent hung connections (same
                    // defensive posture as the pipe's bounded ReadLineWithLimit).
                    client.ReceiveTimeout = 300_000; // 5 min
                    client.SendTimeout = 30_000;     // 30 sec

                    lock (_tcpClientsLock) { _activeTcpClients.Add(client); }

                    var captured = client;
                    var thread = new Thread(() => HandleTcpConnection(captured))
                    {
                        Name = "IpcServer-TcpClient",
                        IsBackground = true
                    };
                    thread.Start();

                    // Don't dispose client here; the handler thread owns it.
                    client = null;
                }
                catch (SocketException) when (_stopping)
                {
                    break;
                }
                catch (ObjectDisposedException) when (_stopping)
                {
                    break;
                }
                catch (InvalidOperationException) when (_stopping)
                {
                    break;
                }
                catch (Exception ex)
                {
                    if (_stopping) break;
                    System.Diagnostics.Trace.TraceError(
                        $"[IpcServer] TCP accept error: {ex}");
                    Thread.Sleep(500);
                }
                finally
                {
                    // Only close if we didn't hand off to the handler thread.
                    client?.Close();
                }
            }
        }

        private void HandleTcpConnection(TcpClient client)
        {
            var endpoint = client.Client.RemoteEndPoint?.ToString() ?? "unknown";
            _clientConnected = true;
            AppendLog($"TCP client connected ({endpoint})", "---");
            try
            {
                using (client)
                using (var stream = client.GetStream())
                {
                    HandleStreamConnection(stream, () => client.Connected);
                }
            }
            catch (Exception ex)
            {
                if (!_stopping)
                {
                    System.Diagnostics.Trace.TraceError(
                        $"[IpcServer] TCP client error ({endpoint}): {ex.Message}");
                }
            }
            finally
            {
                lock (_tcpClientsLock) { _activeTcpClients.Remove(client); }
                _clientConnected = false;
                AppendLog($"TCP client disconnected ({endpoint})", "---");
            }
        }

        // ---------------------------------------------------------------
        // Connection handler (#2, #21) -- shared by pipe and TCP
        // ---------------------------------------------------------------

        private void HandleConnection(NamedPipeServerStream pipe)
        {
            _clientConnected = true;
            AppendLog("Pipe client connected", "---");
            try
            {
                HandleStreamConnection(pipe, () => pipe.IsConnected);
            }
            finally
            {
                _clientConnected = false;
                AppendLog("Pipe client disconnected", "---");
            }
        }

        /// <summary>
        /// Shared connection handler for both pipe and TCP transports.
        /// Reads newline-delimited JSON from the stream, processes each request
        /// through the same token check + ProcessRequestCore path, and writes
        /// the response back.  The <paramref name="isConnected"/> callback lets
        /// the pipe path check <c>pipe.IsConnected</c> while TCP uses a simple
        /// always-true (socket disconnect surfaces as IOException/read-null).
        /// </summary>
        private void HandleStreamConnection(Stream stream, Func<bool> isConnected)
        {
            // #2: Use no-BOM UTF-8 for reader/writer.
            using var reader = new StreamReader(stream, Utf8NoBom, false, 4096, leaveOpen: true);
            using var writer = new StreamWriter(stream, Utf8NoBom, 4096, leaveOpen: true)
            {
                AutoFlush = true
            };

            while (!_stopping && isConnected())
            {
                string? line;
                try
                {
                    // #21: Read with a max line size limit.
                    line = ReadLineWithLimit(reader, MaxLineBytes);
                }
                catch (LineTooLongException)
                {
                    try
                    {
                        var errJson = ErrorResponse(null, ErrorCodes.InvalidParams,
                            $"Request exceeds maximum size of {MaxLineBytes} bytes.");
                        writer.WriteLine(errJson);
                    }
                    catch (IOException) { }
                    break; // disconnect oversized sender
                }
                catch (IOException)
                {
                    break;
                }

                if (line == null) break; // client disconnected
                if (string.IsNullOrWhiteSpace(line)) continue;

                var response = ProcessRequest(line);
                try
                {
                    writer.WriteLine(response);
                }
                catch (IOException)
                {
                    break;
                }
            }
        }

        /// <summary>
        /// #21: Read a single newline-delimited line with a byte budget.
        /// Prevents a same-user process from exhausting memory with a newline-less stream.
        /// Returns null on end-of-stream.
        /// </summary>
        private static string? ReadLineWithLimit(StreamReader reader, int maxBytes)
        {
            var sb = new StringBuilder(256);
            int byteBudget = maxBytes;

            while (true)
            {
                int ch = reader.Read();
                if (ch == -1)
                    return sb.Length == 0 ? null : sb.ToString();
                if (ch == '\n')
                    return sb.ToString();
                if (ch == '\r')
                {
                    if (reader.Peek() == '\n')
                        reader.Read();
                    return sb.ToString();
                }

                // Approximate UTF-8 byte cost of this char.
                byteBudget -= (ch > 0x7F ? (ch > 0x7FF ? (ch > 0xFFFF ? 4 : 3) : 2) : 1);
                if (byteBudget < 0)
                    throw new LineTooLongException();

                sb.Append((char)ch);
            }
        }

        // ---------------------------------------------------------------
        // Request processing (#4, #5, #11, #12, #15, #19)
        // ---------------------------------------------------------------

        /// <summary>Wraps ProcessRequestCore with method/outcome logging, including params snippet.</summary>
        private string ProcessRequest(string requestJson)
        {
            string? method = null;
            string paramsSnippet = "";
            try
            {
                var peek = JObject.Parse(requestJson);
                var mTok = peek["method"];
                if (mTok != null && mTok.Type == JTokenType.String)
                    method = (string?)mTok;
                // Extract params for logging (compact JSON, truncated).
                // Deliberately does NOT log "token" -- only the "params" object.
                var pTok = peek["params"];
                if (pTok != null && pTok.Type == JTokenType.Object)
                {
                    var raw = pTok.ToString(Formatting.None);
                    if (raw.Length > 200)
                        raw = raw.Substring(0, 200) + "...";
                    paramsSnippet = " " + raw;
                }
            }
            catch { /* best-effort method/params extraction for logging */ }

            var response = ProcessRequestCore(requestJson);

            // Extract outcome from our own response format.
            string outcome = "ok";
            try
            {
                var respObj = JObject.Parse(response);
                var okTok = respObj["ok"];
                if (okTok != null && okTok.Type == JTokenType.Boolean && !(bool)okTok)
                {
                    outcome = "error";
                    var errTok = respObj["error"] as JObject;
                    if (errTok != null)
                    {
                        var codeTok = errTok["code"];
                        if (codeTok != null && codeTok.Type == JTokenType.String)
                            outcome = (string?)codeTok ?? "error";
                    }
                }
            }
            catch { outcome = "error"; }

            AppendLog($"{method ?? "(malformed)"}{paramsSnippet}", outcome);
            return response;
        }

        private string ProcessRequestCore(string requestJson)
        {
            string? id = null;
            try
            {
                var root = JObject.Parse(requestJson);

                // #12: Missing required envelope fields -> invalid_params.
                JToken? idTok;
                if (!root.TryGetValue("id", out idTok))
                    return ErrorResponse(null, ErrorCodes.InvalidParams, "Missing 'id' field.");
                id = (string?)idTok;

                JToken? methodTok;
                if (!root.TryGetValue("method", out methodTok))
                    return ErrorResponse(id, ErrorCodes.InvalidParams, "Missing 'method' field.");
                var method = (string?)methodTok ?? "";

                // Token validation: only when auth is enabled (non-empty _token).
                if (AuthRequired)
                {
                    JToken? tokenTok;
                    if (!root.TryGetValue("token", out tokenTok))
                        return ErrorResponse(id, ErrorCodes.InvalidParams, "Missing 'token' field.");
                    var token = (string?)tokenTok;
                    if (token != _token)
                        return ErrorResponse(id, "auth_failed", "Invalid session token.");
                }
                // When auth is disabled, accept requests with or without a token field.

                // #19: Capture expectedProjectRevision; the actual check is
                // deferred to inside the UI-thread dispatch (GuardMutation).
                string? expectedRevision = null;
                {
                    JToken? revTok;
                    if (root.TryGetValue("expectedProjectRevision", out revTok)
                        && revTok.Type == JTokenType.String)
                    {
                        expectedRevision = (string?)revTok;
                    }
                }

                // Idempotency key handling
                string? idempotencyKey = null;
                {
                    JToken? ikTok;
                    if (root.TryGetValue("idempotencyKey", out ikTok)
                        && ikTok.Type == JTokenType.String)
                    {
                        idempotencyKey = (string?)ikTok;
                    }
                }

                if (MutatingMethods.Contains(method))
                {
                    if (string.IsNullOrEmpty(idempotencyKey))
                        return ErrorResponse(id, ErrorCodes.InvalidParams,
                            "idempotencyKey is required for mutating methods.");

                    // #12: Per-key single-flight gate. Acquire BEFORE cache check
                    // so concurrent duplicates serialize and get the cached result.
                    var cacheKey = $"{_gateway.ProjectId}:{idempotencyKey}";
                    var gate = _idempotencyGates.GetOrAdd(cacheKey, _ => new SemaphoreSlim(1, 1));
                    gate.Wait();
                    try
                    {
                        // #12: Re-check cache INSIDE the gate.
                        if (_idempotencyCache.TryGetValue(cacheKey, out var cached))
                        {
                            if (DateTime.UtcNow - cached.CreatedAt < CacheTtl)
                                return BuildSuccessResponse(id, cached.ResultJson);
                            _idempotencyCache.TryRemove(cacheKey, out _);
                        }

                        // firmware.update approval gate.
                        if (method == "firmware.update")
                        {
                            if (!_approvalManager.Unattended)
                            {
                                return ErrorResponse(id, ErrorCodes.ApprovalRequired,
                                    "firmware.update requires the 'Unattended firmware update' "
                                  + "preference to be enabled in the AddIn Configuration dialog.");
                            }
                        }

                        JToken? parms = root["params"];

                        var result = DispatchMethod(method, parms, expectedRevision);
                        var resultJson = JsonConvert.SerializeObject(result, JsonSettings);
                        var response = BuildSuccessResponse(id, resultJson);

                        // Store in cache while still holding the gate.
                        if (_idempotencyCache.Count >= MaxCacheEntries)
                            EvictStaleCacheEntries();
                        _idempotencyCache.TryAdd(cacheKey, new IdempotencyCacheEntry
                        {
                            ResultJson = resultJson,
                            CreatedAt = DateTime.UtcNow
                        });

                        return response;
                    }
                    finally
                    {
                        gate.Release();
                        // #12: Clean up gate to prevent unbounded growth.
                        // Safe removal: if another thread just GetOrAdd'd the same key,
                        // it will have gotten this same SemaphoreSlim instance (or a new one
                        // if we already removed it -- that's fine, it's a fresh gate).
                        if (gate.CurrentCount == 1) // no one else waiting
                            _idempotencyGates.TryRemove(cacheKey, out _);
                    }
                }

                // --- Non-mutating request path (no idempotencyKey required) ---

                // firmware.update approval is handled inside the mutating path above.
                // (firmware.update is always in MutatingMethods, so this is dead code defense)

                JToken? parmsNonMut = root["params"];

                var resultNonMut = DispatchMethod(method, parmsNonMut, expectedRevision);
                var resultJsonNonMut = JsonConvert.SerializeObject(resultNonMut, JsonSettings);
                return BuildSuccessResponse(id, resultJsonNonMut);
            }
            catch (JsonException ex)
            {
                // #12: Malformed JSON -> invalid_params.
                // Newtonsoft.Json.JsonException (and JsonReaderException : JsonException)
                // still caught by this same type name with using Newtonsoft.Json.
                return ErrorResponse(id, ErrorCodes.InvalidParams, $"Invalid JSON: {ex.Message}");
            }
            catch (KeyNotFoundException ex)
            {
                // #12: Unknown refs -> not_found.
                return ErrorResponse(id, ErrorCodes.NotFound, ex.Message);
            }
            catch (InactiveObjectException ex)
            {
                return ErrorResponse(id, ErrorCodes.InactiveObject, ex.Message);
            }
            catch (BusUnavailableException ex)
            {
                return ErrorResponse(id, ErrorCodes.BusUnavailable, ex.Message);
            }
            catch (NotSupportedOperationException ex)
            {
                return ErrorResponse(id, ErrorCodes.NotSupported, ex.Message);
            }
            catch (ArgumentException ex)
            {
                // #12: Missing/wrong-typed properties, unknown methods -> invalid_params.
                return ErrorResponse(id, ErrorCodes.InvalidParams, ex.Message);
            }
            catch (NotImplementedException ex)
            {
                // #12: Stubs are "not yet available", not an internal error.
                return ErrorResponse(id, ErrorCodes.InvalidParams, ex.Message);
            }
            catch (FormatException ex)
            {
                // #16: Parsing failures (bad hex, bad address format) -> invalid_params.
                return ErrorResponse(id, ErrorCodes.InvalidParams, ex.Message);
            }
            catch (InvalidOperationException ex) when (ex.Message.Contains("stopping"))
            {
                // #15: Shutdown detected inside dispatcher.
                return ErrorResponse(id, ErrorCodes.Busy, "Bridge is shutting down.");
            }
            catch (RevisionMismatchException ex)
            {
                // #19: Revision check failed inside the dispatcher.
                return ErrorResponse(id, ErrorCodes.RevisionMismatch, ex.Message);
            }
            catch (Exception ex)
            {
                // #12: Reserve internal for genuine unexpected errors.
                System.Diagnostics.Trace.TraceError(
                    $"[IpcServer] Unexpected error processing request: {ex}");
                return ErrorResponse(id, ErrorCodes.Internal, ex.Message);
            }
        }

        /// <summary>
        /// #19: Validates expectedProjectRevision INSIDE the UI-thread dispatch
        /// (same atomic scope as the mutation). Also checks _stopping (#15).
        ///
        /// The process-local revision counter cannot detect external ETS
        /// edits, undo/redo, or save-reopen. A future version needs an ETS-provided
        /// revision/dirty token for full race detection.
        /// </summary>
        private void GuardMutation(string? expectedRevision)
        {
            // #15: Re-check stopping flag inside the dispatched operation.
            if (_stopping)
                throw new InvalidOperationException("Bridge is stopping.");

            // #19: Revision comparison is now inside the UI-thread operation,
            // so it is atomic with the subsequent mutation.
            if (!string.IsNullOrEmpty(expectedRevision) && expectedRevision != _gateway.ProjectRevision)
            {
                throw new RevisionMismatchException(
                    $"Expected revision {expectedRevision}, current is {_gateway.ProjectRevision}.");
            }
        }

        private object DispatchMethod(string method, JToken? parms, string? expectedRevision)
        {
            switch (method)
            {
                // --- Reads (no mutation guard needed) ---
                case "project.info":
                    return _dispatcher.RunOnUiThread(() => _gateway.GetProjectInfo());

                case "devices.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListDevices());

                case "ga.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListGroupAddresses());

                case "comobjects.list":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() => _gateway.ListComObjects(deviceRef));
                }

                case "topology.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListTopology());

                case "catalog.manufacturers":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListManufacturers());

                case "catalog.search":
                {
                    var query = GetRequiredString(parms, "query");
                    string? manufacturerRef = GetOptionalString(parms, "manufacturerRef");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.SearchCatalog(query, manufacturerRef));
                }

                case "catalog.search_online":
                {
                    var query = GetRequiredString(parms, "query");
                    string? manufacturerRef = GetOptionalString(parms, "manufacturerRef");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.SearchOnlineCatalog(query, manufacturerRef));
                }

                case "catalog.import":
                {
                    // MUTATING (product store, no UndoManager). idempotencyKey required.
                    var path = GetRequiredString(parms, "path");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.ImportProductData(path);
                    });
                }

                case "catalog.internalize":
                {
                    // MUTATING (product store, no UndoManager). idempotencyKey required.
                    var uciRef = GetRequiredString(parms, "unifiedCatalogItemRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.InternalizeProductData(uciRef);
                    });
                }

                case "params.list":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() => _gateway.ListParameters(deviceRef));
                }

                // --- Mutations (guard inside the UI-thread lambda: #15, #19) ---
                case "ga.create":
                {
                    var name = GetRequiredString(parms, "name");
                    // #3: Accept address as a string (e.g. "1/0/3"), not a numeric UInt16.
                    var address = GetRequiredString(parms, "address");
                    uint? dptMain = GetOptionalUint(parms, "dptMain");
                    uint? dptSub = GetOptionalUint(parms, "dptSub");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return _gateway.CreateGroupAddress(name, address, dptMain, dptSub);
                    });
                }

                case "link.create":
                {
                    var coRef = GetRequiredString(parms, "comObjectRef");
                    var gaRef = GetRequiredString(parms, "gaRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateLink(coRef, gaRef);
                    });
                }

                case "link.delete":
                {
                    var coRef = GetRequiredString(parms, "comObjectRef");
                    var gaRef = GetRequiredString(parms, "gaRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteLink(coRef, gaRef);
                        return (object)new OkResult();
                    });
                }

                case "param.set":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var parameterRef = GetRequiredString(parms, "parameterRef");
                    var value = GetRequiredString(parms, "value");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.SetParameter(deviceRef, parameterRef, value);
                        return (object)new OkResult();
                    });
                }

                case "device.program":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var options = GetOptionalString(parms, "options");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        var jobId = _gateway.StartDeviceProgram(deviceRef, options);
                        return (object)new { jobId };
                    });
                }

                case "device.addFromCatalog":
                {
                    var lineRef = GetRequiredString(parms, "lineRef");
                    var catalogItemRef = GetRequiredString(parms, "catalogItemRef");
                    var address = GetRequiredString(parms, "address");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.AddDeviceFromCatalog(lineRef, catalogItemRef, address);
                    });
                }

                case "firmware.update":
                {
                    // Safety-gated stub. Unattended check already passed
                    // in ProcessRequestCore above.
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var firmware = GetRequiredString(parms, "firmware");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        var jobId = _gateway.UpdateFirmware(deviceRef, firmware);
                        return (object)new { jobId };
                    });
                }

                // job.status and job.cancel: reads (no idempotencyKey per protocol.md)
                case "job.status":
                {
                    var jobId = GetRequiredString(parms, "jobId");
                    return _dispatcher.RunOnUiThread(() =>
                        (object)_gateway.GetJobStatus(jobId));
                }

                case "job.cancel":
                {
                    var jobId = GetRequiredString(parms, "jobId");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        _gateway.CancelJob(jobId);
                        return (object)new OkResult();
                    });
                }

                // --- Phase A: Group Address editing ---
                case "ga.delete":
                {
                    var gaRef = GetRequiredString(parms, "groupAddressRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteGroupAddress(gaRef);
                        return (object)new OkResult();
                    });
                }

                case "ga.rename":
                {
                    var gaRef = GetRequiredString(parms, "groupAddressRef");
                    var name = GetRequiredString(parms, "name");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.RenameGroupAddress(gaRef, name);
                    });
                }

                case "ga.setDescription":
                {
                    var gaRef = GetRequiredString(parms, "groupAddressRef");
                    var description = GetRequiredString(parms, "description");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetGroupAddressDescription(gaRef, description);
                    });
                }

                case "ga.setDatapointType":
                {
                    var gaRef = GetRequiredString(parms, "groupAddressRef");
                    var dptMain = GetOptionalUint(parms, "dptMain");
                    if (!dptMain.HasValue)
                        throw new ArgumentException("dptMain is required for ga.setDatapointType.");
                    uint? dptSub = GetOptionalUint(parms, "dptSub");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetGroupAddressDpt(gaRef, dptMain.Value, dptSub);
                    });
                }

                // --- Phase A: Group Range ---
                case "groupRange.create":
                {
                    var name = GetRequiredString(parms, "name");
                    var addrVal = GetOptionalUint(parms, "address");
                    if (!addrVal.HasValue)
                        throw new ArgumentException("address is required for groupRange.create.");
                    string? parentRef = GetOptionalString(parms, "parentGroupRangeRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateGroupRange(name, (ushort)addrVal.Value, parentRef);
                    });
                }

                case "groupRange.delete":
                {
                    var grRef = GetRequiredString(parms, "groupRangeRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteGroupRange(grRef);
                        return (object)new OkResult();
                    });
                }

                // --- Phase A: Device editing ---
                case "device.delete":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteDevice(deviceRef);
                        return (object)new OkResult();
                    });
                }

                case "device.rename":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var name = GetRequiredString(parms, "name");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.RenameDevice(deviceRef, name);
                    });
                }

                case "device.setAddress":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var address = GetRequiredString(parms, "address");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetDeviceAddress(deviceRef, address);
                    });
                }

                case "device.move":
                {
                    // SDK 6.3.0 does not expose Device.Move. Always return not_supported.
                    throw new NotSupportedOperationException(
                        "Moving an assigned device between lines has no supported SDK API "
                      + "in ETS 6.3.0. Workaround: delete and re-add the device on the target line.");
                }

                case "device.unassign":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.UnassignDeviceFromLine(deviceRef);
                        return (object)new OkResult();
                    });
                }

                // --- Phase A: Topology ---
                case "area.create":
                {
                    var name = GetRequiredString(parms, "name");
                    var addrVal = GetOptionalUint(parms, "address");
                    ushort? address = addrVal.HasValue ? (ushort)addrVal.Value : (ushort?)null;
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateArea(name, address);
                    });
                }

                case "area.delete":
                {
                    var areaRef = GetRequiredString(parms, "areaRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteArea(areaRef);
                        return (object)new OkResult();
                    });
                }

                case "line.create":
                {
                    var areaRef = GetRequiredString(parms, "areaRef");
                    var name = GetRequiredString(parms, "name");
                    var addrVal = GetOptionalUint(parms, "address");
                    ushort? address = addrVal.HasValue ? (ushort)addrVal.Value : (ushort?)null;
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateLine(areaRef, name, address);
                    });
                }

                case "line.delete":
                {
                    var lineRef = GetRequiredString(parms, "lineRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteLine(lineRef);
                        return (object)new OkResult();
                    });
                }

                // --- Phase A: ComObject flags ---
                case "comObject.setFlags":
                {
                    var coRef = GetRequiredString(parms, "comObjectRef");
                    bool? communicationFlag = GetOptionalBool(parms, "communicationFlag");
                    bool? readFlag = GetOptionalBool(parms, "readFlag");
                    bool? writeFlag = GetOptionalBool(parms, "writeFlag");
                    bool? transmitFlag = GetOptionalBool(parms, "transmitFlag");
                    bool? updateFlag = GetOptionalBool(parms, "updateFlag");
                    bool? readOnInitFlag = GetOptionalBool(parms, "readOnInitFlag");
                    string? priority = GetOptionalString(parms, "priority");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetComObjectFlags(coRef,
                            communicationFlag, readFlag, writeFlag,
                            transmitFlag, updateFlag, readOnInitFlag,
                            priority);
                    });
                }

                // --- Phase A: Label setters ---
                case "device.setDescription":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var description = GetRequiredString(parms, "description");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetDeviceDescription(deviceRef, description);
                    });
                }

                case "device.setComment":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var comment = GetRequiredString(parms, "comment");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetDeviceComment(deviceRef, comment);
                    });
                }

                case "comObject.setDescription":
                {
                    var coRef = GetRequiredString(parms, "comObjectRef");
                    var description = GetRequiredString(parms, "description");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetComObjectDescription(coRef, description);
                    });
                }

                case "comObject.setFunctionText":
                {
                    var coRef = GetRequiredString(parms, "comObjectRef");
                    var functionText = GetRequiredString(parms, "functionText");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.SetComObjectFunctionText(coRef, functionText);
                    });
                }

                // --- Phase B: Building structure ---
                case "building.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListBuilding());

                case "building.create":
                {
                    var name = GetRequiredString(parms, "name");
                    var type = GetRequiredString(parms, "type");
                    string? parentRef = GetOptionalString(parms, "parentBuildingPartRef");
                    string? spaceUsage = GetOptionalString(parms, "spaceUsage");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateBuildingPart(name, type, parentRef, spaceUsage);
                    });
                }

                case "building.delete":
                {
                    var bpRef = GetRequiredString(parms, "buildingPartRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteBuildingPart(bpRef);
                        return (object)new OkResult();
                    });
                }

                case "building.rename":
                {
                    var bpRef = GetRequiredString(parms, "buildingPartRef");
                    var name = GetRequiredString(parms, "name");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.RenameBuildingPart(bpRef, name);
                    });
                }

                case "building.assignDevice":
                {
                    var bpRef = GetRequiredString(parms, "buildingPartRef");
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.AssignDevice(bpRef, deviceRef);
                        return (object)new OkResult();
                    });
                }

                case "building.unassignDevice":
                {
                    var bpRef = GetRequiredString(parms, "buildingPartRef");
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.UnassignDevice(bpRef, deviceRef);
                        return (object)new OkResult();
                    });
                }

                // --- Phase B: Building functions ---
                case "buildingFunction.create":
                {
                    var bpRef = GetRequiredString(parms, "buildingPartRef");
                    var name = GetRequiredString(parms, "name");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateBuildingFunction(bpRef, name);
                    });
                }

                case "buildingFunction.delete":
                {
                    var bfRef = GetRequiredString(parms, "buildingFunctionRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteBuildingFunction(bfRef);
                        return (object)new OkResult();
                    });
                }

                case "buildingFunction.linkGroupAddress":
                {
                    var bfRef = GetRequiredString(parms, "buildingFunctionRef");
                    var gaRefs = GetRequiredStringArray(parms, "groupAddressRefs");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.LinkBuildingFunctionGA(bfRef, gaRefs);
                        return (object)new OkResult();
                    });
                }

                case "buildingFunction.unlinkGroupAddress":
                {
                    var bfRef = GetRequiredString(parms, "buildingFunctionRef");
                    var gaRefs = GetRequiredStringArray(parms, "groupAddressRefs");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.UnlinkBuildingFunctionGA(bfRef, gaRefs);
                        return (object)new OkResult();
                    });
                }

                // --- Phase C: Project management ---
                case "project.save":
                {
                    // Root.Save does NOT exist in ETS SDK. Always not_supported.
                    throw new NotSupportedOperationException(
                        "project.save is not supported: ETS auto-saves projects.");
                }

                case "project.export":
                {
                    var path = GetRequiredString(parms, "path");
                    bool? includeCatalog = GetOptionalBool(parms, "includeCatalog");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        var exported = _gateway.ProjectExport(path, includeCatalog ?? false);
                        return (object)new { ok = exported };
                    });
                }

                case "project.backup":
                {
                    // Root.BackupDatabase is a no-op stub. Always not_supported.
                    throw new NotSupportedOperationException(
                        "project.backup is not supported: Root.BackupDatabase has no effect in ETS 6.");
                }

                case "project.undo":
                {
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.ProjectUndo();
                        return (object)new OkResult();
                    });
                }

                case "project.redo":
                {
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.ProjectRedo();
                        return (object)new OkResult();
                    });
                }

                case "project.exportSemantic":
                {
                    // Root.ExportSemanticDataAsync is async; blocking on dispatcher = deadlock risk.
                    // Not wired in v1 (async deadlock risk on the UI dispatcher).
                    throw new NotSupportedOperationException(
                        "project.exportSemantic is not supported in v1: async semantic export "
                      + "not wired (deadlock risk on UI thread).");
                }

                // --- Phase E: Catalog browsing ---
                case "catalog.browseProducts":
                {
                    var manufacturerRef = GetRequiredString(parms, "manufacturerRef");
                    var offset = GetOptionalUint(parms, "offset") ?? 0;
                    var limit = GetOptionalUint(parms, "limit") ?? 100;
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.BrowseProducts(manufacturerRef, (int)offset, (int)limit));
                }

                case "catalog.productInfo":
                {
                    var catalogItemRef = GetRequiredString(parms, "catalogItemRef");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.ProductInfo(catalogItemRef));
                }

                // --- Phase D: Bus / Online operations ---

                case "bus.ping":
                {
                    var address = GetRequiredString(parms, "address");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.BusPing(address));
                }

                case "bus.scanLine":
                {
                    var lineRef = GetRequiredString(parms, "lineRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        var jobId = _gateway.StartScanLine(lineRef);
                        return (object)new { jobId };
                    });
                }

                case "device.readInfo":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.DeviceReadInfo(deviceRef));
                }

                case "device.compare":
                {
                    // NEW METHOD: limited device compare (READ, bus).
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.DeviceCompare(deviceRef));
                }

                case "group.read":
                {
                    var gaRef = GetRequiredString(parms, "gaRef");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.GroupRead(gaRef));
                }

                case "group.write":
                {
                    var gaRef = GetRequiredString(parms, "gaRef");
                    var value = GetRequiredString(parms, "value");
                    var less7Bits = GetOptionalBool(parms, "less7Bits") ?? false;
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.GroupWrite(gaRef, value, less7Bits);
                    });
                }

                case "group.monitor":
                {
                    var lineRef = GetOptionalString(parms, "lineRef") ?? "default";
                    var durationMs = (int)(GetOptionalUint(parms, "durationMs") ?? 10000);
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        var jobId = _gateway.StartGroupMonitor(lineRef, durationMs);
                        return (object)new { jobId };
                    });
                }

                case "device.unload":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var fullUnload = GetOptionalBool(parms, "fullUnload") ?? false;
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        var jobId = _gateway.StartDeviceUnload(deviceRef, fullUnload);
                        return (object)new { jobId };
                    });
                }

                case "bus.setIndividualAddress":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var currentAddress = GetRequiredString(parms, "currentAddress");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        var jobId = _gateway.StartSetIndividualAddress(deviceRef, currentAddress);
                        return (object)new { jobId };
                    });
                }

                case "device.reset":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.ResetDevice(deviceRef);
                        return (object)new OkResult();
                    });
                }

                // --- bridge.info (READ, no bus, no mutation guard) ---

                case "bridge.info":
                {
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.GetBridgeInfo());
                }

                // --- bus.reconstructLine (READ, JOB) ---

                case "bus.reconstructLine":
                {
                    var lineRef = GetRequiredString(parms, "lineRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        var jobId = _gateway.StartReconstructLine(lineRef);
                        return (object)new { jobId };
                    });
                }

                // --- device.readGroupObjects (READ) ---
                // Sync by default (one call, one result). Opt into async/job mode
                // with { "async": true } for slow/unreachable devices.

                case "device.readGroupObjects":
                {
                    var address = GetRequiredString(parms, "address");
                    var asyncMode = GetOptionalBool(parms, "async") ?? false;
                    if (asyncMode)
                    {
                        return _dispatcher.RunOnUiThread(() =>
                        {
                            var jobId = _gateway.StartReadDeviceGroupObjects(address);
                            return (object)new { jobId };
                        });
                    }
                    else
                    {
                        return _dispatcher.RunOnUiThread(() =>
                            (object)_gateway.ReadDeviceGroupObjects(address));
                    }
                }

                // ---------------------------------------------------------------
                // Phase F: Move / re-parent
                // ---------------------------------------------------------------

                case "groupRange.moveGroupAddress":
                {
                    var targetRef = GetRequiredString(parms, "targetGroupRangeRef");
                    var gaRef = GetRequiredString(parms, "groupAddressRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.MoveGroupAddress(targetRef, gaRef);
                        return (object)new OkResult();
                    });
                }

                case "groupRange.moveGroupRange":
                {
                    var targetRef = GetRequiredString(parms, "targetGroupRangeRef");
                    var sourceRef = GetRequiredString(parms, "sourceGroupRangeRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.MoveGroupRange(targetRef, sourceRef);
                        return (object)new OkResult();
                    });
                }

                case "line.move":
                {
                    var targetAreaRef = GetRequiredString(parms, "targetAreaRef");
                    var lineRef = GetRequiredString(parms, "lineRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.MoveLine(targetAreaRef, lineRef);
                        return (object)new OkResult();
                    });
                }

                case "buildingPart.move":
                {
                    var targetRef = GetRequiredString(parms, "targetBuildingPartRef");
                    var sourceRef = GetRequiredString(parms, "sourceBuildingPartRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.MoveBuildingPart(targetRef, sourceRef);
                        return (object)new OkResult();
                    });
                }

                // ---------------------------------------------------------------
                // Phase F: Additional addresses
                // ---------------------------------------------------------------

                case "device.addAdditionalAddress":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var addrVal = GetOptionalUint(parms, "address");
                    if (!addrVal.HasValue)
                        throw new ArgumentException("address is required for device.addAdditionalAddress.");
                    // #11: Validate uint fits in ushort and reject 0xFFFF (internal "parked" sentinel).
                    if (addrVal.Value > 0xFFFE)
                        throw new ArgumentException(
                            $"address {addrVal.Value} is out of range for an additional device address (0-65534). "
                          + "0xFFFF is reserved as 'parked/unused' sentinel.");
                    var addrUShort = (ushort)addrVal.Value;
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.AddAdditionalAddress(deviceRef, addrUShort);
                    });
                }

                case "device.removeAdditionalAddress":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var addrVal = GetOptionalUint(parms, "address");
                    if (!addrVal.HasValue)
                        throw new ArgumentException("address is required for device.removeAdditionalAddress.");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.RemoveAdditionalAddress(deviceRef, (ushort)addrVal.Value);
                        return (object)new OkResult();
                    });
                }

                case "line.addAdditionalGroupAddress":
                {
                    var lineRef = GetRequiredString(parms, "lineRef");
                    var gaRefs = GetRequiredStringArray(parms, "groupAddressRefs");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.AddLineAdditionalGroupAddresses(lineRef, gaRefs);
                        return (object)new OkResult();
                    });
                }

                case "line.removeAdditionalGroupAddress":
                {
                    var lineRef = GetRequiredString(parms, "lineRef");
                    var gaRefs = GetRequiredStringArray(parms, "groupAddressRefs");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.RemoveLineAdditionalGroupAddresses(lineRef, gaRefs);
                        return (object)new OkResult();
                    });
                }

                // ---------------------------------------------------------------
                // Phase F: Segments
                // ---------------------------------------------------------------

                case "segment.create":
                {
                    var lineRef = GetRequiredString(parms, "lineRef");
                    var name = GetRequiredString(parms, "name");
                    var mediumType = GetRequiredString(parms, "mediumType");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateSegment(lineRef, name, mediumType);
                    });
                }

                case "segment.delete":
                {
                    var segmentRef = GetRequiredString(parms, "segmentRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteSegment(segmentRef);
                        return (object)new OkResult();
                    });
                }

                // ---------------------------------------------------------------
                // Phase F: Trades
                // ---------------------------------------------------------------

                case "trades.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListTrades());

                case "trade.create":
                {
                    var name = GetRequiredString(parms, "name");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateTrade(name);
                    });
                }

                case "trade.delete":
                {
                    var tradeRef = GetRequiredString(parms, "tradeRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteTrade(tradeRef);
                        return (object)new OkResult();
                    });
                }

                case "trade.assignDevice":
                {
                    var tradeRef = GetRequiredString(parms, "tradeRef");
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.TradeAssignDevice(tradeRef, deviceRef);
                        return (object)new OkResult();
                    });
                }

                case "trade.unassignDevice":
                {
                    var tradeRef = GetRequiredString(parms, "tradeRef");
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.TradeUnassignDevice(tradeRef, deviceRef);
                        return (object)new OkResult();
                    });
                }

                // ---------------------------------------------------------------
                // Phase F: Bus interface (filter table)
                // ---------------------------------------------------------------

                case "busInterface.link":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var gaRefs = GetRequiredStringArray(parms, "groupAddressRefs");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.BusInterfaceLink(deviceRef, gaRefs);
                        return (object)new OkResult();
                    });
                }

                case "busInterface.unlink":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var gaRefs = GetRequiredStringArray(parms, "groupAddressRefs");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.BusInterfaceUnlink(deviceRef, gaRefs);
                        return (object)new OkResult();
                    });
                }

                // ---------------------------------------------------------------
                // Phase F: Parameter reset to default
                // ---------------------------------------------------------------

                case "param.setDefault":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    var parameterRef = GetRequiredString(parms, "parameterRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.ResetParameterToDefault(deviceRef, parameterRef);
                        return (object)new OkResult();
                    });
                }

                // --- param.setActive: NOT SUPPORTED ---
                // ParameterInstanceRef.IsActive is GET-only (computed from
                // visibility calculation, SDK XML line 16441). Cannot be set.
                case "param.setActive":
                {
                    throw new NotSupportedOperationException(
                        "param.setActive is not supported: ParameterInstanceRef.IsActive is a "
                      + "read-only computed property (visibility calculation result). "
                      + "To change parameter visibility, set the controlling parameter that "
                      + "affects the visibility condition via param.set.");
                }

                // ===============================================================
                // Group 1: KNX Secure Certificates
                // ===============================================================

                case "certificates.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListCertificates());

                case "certificate.add":
                {
                    var rawKey = GetRequiredString(parms, "value");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.AddCertificate(rawKey);
                    });
                }

                case "certificate.delete":
                {
                    var certRef = GetRequiredString(parms, "certificateRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteCertificate(certRef);
                        return (object)new OkResult();
                    });
                }

                // ===============================================================
                // Group 2: UI Navigation
                // ===============================================================

                case "project.navigateTo":
                {
                    var objectRef = GetRequiredString(parms, "ref");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        // Not a project mutation; no GuardMutation, no idempotencyKey.
                        _gateway.NavigateTo(objectRef);
                        return (object)new OkResult();
                    });
                }

                // ===============================================================
                // Group 3: Organization (Tags, ToDoItems)
                // ===============================================================

                case "tags.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListTags());

                case "tag.create":
                {
                    var label = GetRequiredString(parms, "label");
                    var colorHex = GetOptionalString(parms, "color") ?? "808080";
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateTag(label, colorHex);
                    });
                }

                case "tag.delete":
                {
                    var tagRef = GetRequiredString(parms, "tagRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteTag(tagRef);
                        return (object)new OkResult();
                    });
                }

                case "todos.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListToDoItems());

                case "todo.create":
                {
                    var description = GetRequiredString(parms, "description");
                    var objectPath = GetOptionalString(parms, "objectPath") ?? "";
                    var status = GetOptionalString(parms, "status") ?? "Open";
                    // #18: Validate status to accepted values only (case-insensitive).
                    if (!string.Equals(status, "Open", StringComparison.OrdinalIgnoreCase) &&
                        !string.Equals(status, "Accomplished", StringComparison.OrdinalIgnoreCase))
                    {
                        throw new ArgumentException(
                            $"Invalid todo status: '{status}'. Valid values: Open, Accomplished.");
                    }
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.CreateToDoItem(description, objectPath, status);
                    });
                }

                case "todo.delete":
                {
                    var todoRef = GetRequiredString(parms, "todoRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteToDoItem(todoRef);
                        return (object)new OkResult();
                    });
                }

                // ===============================================================
                // Group 4: Semantic Export (not_supported)
                // ===============================================================

                // (project.exportSemantic already handled above)

                // ===============================================================
                // Group 5: Channels / Modules (READ)
                // ===============================================================

                case "device.channels":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(
                        () => _gateway.ListDeviceChannels(deviceRef));
                }

                case "application.dynamic":
                {
                    var deviceRef = GetRequiredString(parms, "deviceRef");
                    return _dispatcher.RunOnUiThread(
                        () => (object)new { xml = _gateway.GetApplicationDynamic(deviceRef) });
                }

                // ===============================================================
                // Group 6: Project History
                // ===============================================================

                case "projectHistory.list":
                    return _dispatcher.RunOnUiThread(() => _gateway.ListProjectHistory());

                case "projectHistory.add":
                {
                    var text = GetRequiredString(parms, "text");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        return (object)_gateway.AddProjectHistory(text);
                    });
                }

                case "projectHistory.delete":
                {
                    var phRef = GetRequiredString(parms, "historyRef");
                    return _dispatcher.RunOnUiThread(() =>
                    {
                        GuardMutation(expectedRevision);
                        _gateway.DeleteProjectHistory(phRef);
                        return (object)new OkResult();
                    });
                }

                // --- Niche types: NOT SUPPORTED ---
                // Folder: Not a project organizational folder; represents a parameter block
                //   display item in the device group object tree. No project-level CRUD.
                // ProjectTrace: Read-only system-generated entries; no Add/Delete in SDK.
                // UserFile: Undocumented internal plumbing; no useful CRUD for LLM.
                // AddinData: Per-AddIn binary storage; internal plumbing, not exposed.
                // DeviceBinaryData: Per-device binary storage for DCAs; internal plumbing.
                // DeviceTemplates: Root.DeviceTemplates; niche read -- not wired in v1.
                // KnxMasterData: Partially used (DatapointTypes, MediumTypes, SpaceUsages).
                //   Already accessible via existing endpoints (catalog.productInfo, segment.create).

                case "batch.apply":
                    return DispatchBatch(parms, expectedRevision);

                default:
                    // #12: Unknown method -> invalid_params, not internal.
                    throw new ArgumentException($"Unknown method: {method}");
            }
        }

        // ---------------------------------------------------------------
        // batch.apply: run many project mutations in ONE undo marker.
        // ---------------------------------------------------------------
        // Request params:
        //   operations: [ { method, params }, ... ]   (required, non-empty, <= MaxBatchOperations)
        //   atomic:     bool (default true)
        //     true  -> one outer undo marker; first failure rolls back the whole batch,
        //              remaining steps are reported as "skipped", applied=false.
        //     false -> best-effort; each step commits independently; failures do not stop
        //              later steps; applied=true (partial), rolledBack=false.
        // Response: { applied, atomic, rolledBack, total, ok, failed, skipped, results[] }
        //   results[i] = { index, method, status: "ok"|"error"|"skipped",
        //                  result?, error?: { code, message } }
        // Hardening (Thomas' changelog lessons): never report success for a step that did
        // not run (skipped/failed are explicit); per-step errors are surfaced, never swallowed.
        private object DispatchBatch(JToken? parms, string? expectedRevision)
        {
            // -- Parse + validate up front (fail fast, before executing anything) --
            var parmsObj = parms as JObject;
            JToken? opsToken;
            if (parmsObj == null
                || !parmsObj.TryGetValue("operations", out opsToken)
                || !(opsToken is JArray opsArr))
            {
                throw new ArgumentException("batch.apply requires an 'operations' array.");
            }

            int count = opsArr.Count;
            if (count == 0)
                throw new ArgumentException("batch.apply 'operations' must not be empty.");
            if (count > MaxBatchOperations)
                throw new ArgumentException(
                    $"batch.apply 'operations' exceeds the maximum of {MaxBatchOperations}.");

            bool atomic = true;
            {
                JToken? atomicTok;
                if (parmsObj.TryGetValue("atomic", out atomicTok)
                    && atomicTok.Type == JTokenType.Boolean)
                {
                    atomic = (bool)atomicTok;
                }
            }

            var ops = new List<(string Method, JToken? Params)>(count);
            foreach (var opTok in opsArr)
            {
                var opObj = opTok as JObject;
                JToken? mTok;
                if (opObj == null
                    || !opObj.TryGetValue("method", out mTok)
                    || mTok.Type != JTokenType.String)
                {
                    throw new ArgumentException(
                        "Each batch operation must be an object with a 'method' string.");
                }
                string subMethod = (string?)mTok ?? "";
                if (!BatchableMethods.Contains(subMethod))
                {
                    throw new ArgumentException(
                        $"Method '{subMethod}' is not allowed inside batch.apply. Batchable methods "
                      + "are fast, undo-reversible project mutations only (no bus/programming/"
                      + "catalog-import ops, no nested batch).");
                }
                JToken? subParams = opObj["params"];
                ops.Add((subMethod, subParams));
            }

            // validateOnly: read-only pre-flight. Report per-op issues without opening a
            // marker or mutating anything. Lets the caller catch bad refs / inactive
            // com-objects / DPT mismatches / GA collisions BEFORE applying the batch.
            bool validateOnly = false;
            {
                JToken? vTok;
                if (parmsObj.TryGetValue("validateOnly", out vTok)
                    && vTok.Type == JTokenType.Boolean)
                {
                    validateOnly = (bool)vTok;
                }
            }
            if (validateOnly)
                return _dispatcher.RunOnUiThread(() => ValidateBatchOps(ops, atomic));

            // -- Execute inside a single UI-thread round so the outer marker spans all
            //    sub-operations (their child markers nest under it). --
            return _dispatcher.RunOnUiThread(() =>
            {
                // Revision guard once, at the start (sub-ops are passed null).
                GuardMutation(expectedRevision);

                var results = new List<object>(ops.Count);
                int okCount = 0, failedCount = 0, skippedCount = 0;
                bool failed = false;

                object? marker = atomic ? _gateway.BeginMarker($"Bridge: Batch ({ops.Count} ops)") : null;
                try
                {
                    for (int i = 0; i < ops.Count; i++)
                    {
                        if (atomic && failed)
                        {
                            results.Add(new { index = i, method = ops[i].Method, status = "skipped" });
                            skippedCount++;
                            continue;
                        }

                        try
                        {
                            // Sub-op runs inline on this same UI thread; its RunInMarker
                            // opens a child marker under the outer batch marker.
                            var subResult = DispatchMethod(ops[i].Method, ops[i].Params, null);
                            results.Add(new
                            {
                                index = i,
                                method = ops[i].Method,
                                status = "ok",
                                result = subResult
                            });
                            okCount++;
                        }
                        catch (Exception ex)
                        {
                            var (code, message) = ClassifyException(ex);
                            results.Add(new
                            {
                                index = i,
                                method = ops[i].Method,
                                status = "error",
                                error = new { code, message }
                            });
                            failedCount++;
                            failed = true;
                            // atomic: stop executing; remaining marked skipped next iterations.
                            // non-atomic: continue with the next operation.
                        }
                    }

                    if (atomic)
                    {
                        if (failed) _gateway.DiscardMarker(marker!);
                        else _gateway.CommitMarker(marker!);
                        marker = null; // Commit/Discard already disposed it.
                    }
                }
                finally
                {
                    // Safety net: if an unexpected error escaped the loop before we
                    // committed/discarded, roll back rather than leak an open marker.
                    if (atomic && marker != null)
                    {
                        try { _gateway.DiscardMarker(marker); } catch { }
                    }
                }

                return new
                {
                    applied = atomic ? !failed : true,
                    atomic,
                    rolledBack = atomic && failed,
                    total = ops.Count,
                    ok = okCount,
                    failed = failedCount,
                    skipped = skippedCount,
                    results
                };
            });
        }

        // Required params per batchable method, used by validateOnly's structural check.
        // Methods not listed have no required-param check (still allowlisted structurally).
        private static readonly Dictionary<string, string[]> BatchRequiredParams =
            new Dictionary<string, string[]>
            {
                ["link.create"] = new[] { "comObjectRef", "gaRef" },
                ["link.delete"] = new[] { "comObjectRef", "gaRef" },
                ["ga.create"] = new[] { "name", "address" },
                ["ga.delete"] = new[] { "gaRef" },
                ["ga.rename"] = new[] { "gaRef", "name" },
                ["ga.setDescription"] = new[] { "gaRef" },
                ["ga.setDatapointType"] = new[] { "gaRef" },
                ["param.set"] = new[] { "deviceRef", "parameterRef", "value" },
                ["param.setDefault"] = new[] { "deviceRef", "parameterRef" },
                ["comObject.setFlags"] = new[] { "comObjectRef" },
                ["comObject.setDescription"] = new[] { "comObjectRef" },
                ["comObject.setFunctionText"] = new[] { "comObjectRef" },
                ["device.rename"] = new[] { "deviceRef", "name" },
                ["device.setAddress"] = new[] { "deviceRef", "address" },
            };

        // Read-only pre-flight for batch.apply?validateOnly=true. Runs on the UI thread so
        // the gateway reads (ref resolution, DPT compare, GA collision) are safe.
        private object ValidateBatchOps(List<(string Method, JToken? Params)> ops, bool atomic)
        {
            var results = new List<object>(ops.Count);
            int validCount = 0, invalidCount = 0;
            for (int i = 0; i < ops.Count; i++)
            {
                var issues = ValidateOneOp(ops[i].Method, ops[i].Params as JObject);
                bool ok = issues.Count == 0;
                results.Add(new { index = i, method = ops[i].Method, valid = ok, issues });
                if (ok) validCount++; else invalidCount++;
            }
            return new
            {
                validated = true,
                atomic,
                total = ops.Count,
                valid = validCount,
                invalid = invalidCount,
                results
            };
        }

        private List<string> ValidateOneOp(string method, JObject? p)
        {
            var issues = new List<string>();

            // Structural: required params present and non-empty.
            if (BatchRequiredParams.TryGetValue(method, out var required))
            {
                foreach (var key in required)
                {
                    var tok = p?[key];
                    if (tok == null || tok.Type == JTokenType.Null
                        || (tok.Type == JTokenType.String && string.IsNullOrEmpty((string?)tok)))
                    {
                        issues.Add($"missing required parameter '{key}'");
                    }
                }
            }

            // Semantic (read-only) checks for the highest-value methods.
            try
            {
                if (method == "link.create")
                {
                    var coRef = (string?)p?["comObjectRef"];
                    var gaRef = (string?)p?["gaRef"];
                    if (!string.IsNullOrEmpty(coRef) && !string.IsNullOrEmpty(gaRef))
                        issues.AddRange(_gateway.ValidateLink(coRef!, gaRef!));
                }
                else if (method == "ga.create")
                {
                    var address = (string?)p?["address"];
                    if (!string.IsNullOrEmpty(address) && _gateway.GroupAddressAddressInUse(address!))
                        issues.Add($"group address {address} already exists");
                }
            }
            catch { /* validation is best-effort; a check failure must not crash validate */ }

            return issues;
        }

        // Map an exception thrown by a sub-operation to the same (code, message) pairs the
        // top-level request handler uses, so batch step errors are classified consistently.
        private static (string Code, string Message) ClassifyException(Exception ex)
        {
            switch (ex)
            {
                case KeyNotFoundException:
                    return (ErrorCodes.NotFound, ex.Message);
                case InactiveObjectException:
                    return (ErrorCodes.InactiveObject, ex.Message);
                case BusUnavailableException:
                    return (ErrorCodes.BusUnavailable, ex.Message);
                case NotSupportedOperationException:
                    return (ErrorCodes.NotSupported, ex.Message);
                case ArgumentException:
                    return (ErrorCodes.InvalidParams, ex.Message);
                default:
                    return (ErrorCodes.Internal, ex.Message);
            }
        }

        // ---------------------------------------------------------------
        // Parameter extraction helpers
        // #12: Throws ArgumentException for missing/wrong-typed params
        //      (maps to invalid_params error code in the catch block).
        // ---------------------------------------------------------------

        private static string GetRequiredString(JToken? parms, string name)
        {
            var obj = parms as JObject;
            if (obj == null)
                throw new ArgumentException("Missing 'params' object.");
            JToken? el;
            if (!obj.TryGetValue(name, out el))
                throw new ArgumentException($"Missing required parameter '{name}'.");
            if (el.Type != JTokenType.String)
                throw new ArgumentException($"Parameter '{name}' must be a string.");
            return (string)el!;
        }

        private static string? GetOptionalString(JToken? parms, string name)
        {
            var obj = parms as JObject;
            if (obj == null) return null;
            JToken? el;
            if (!obj.TryGetValue(name, out el)) return null;
            if (el.Type == JTokenType.Null) return null;
            // #16: Present-but-wrong-typed -> ArgumentException, not silent null.
            if (el.Type != JTokenType.String)
                throw new ArgumentException($"Parameter '{name}' must be a string, got {el.Type}.");
            return (string?)el;
        }

        private static uint? GetOptionalUint(JToken? parms, string name)
        {
            var obj = parms as JObject;
            if (obj == null) return null;
            JToken? el;
            if (!obj.TryGetValue(name, out el)) return null;
            if (el.Type == JTokenType.Null) return null;
            // #16: Present-but-wrong-typed -> ArgumentException, not silent null.
            if (el.Type != JTokenType.Integer && el.Type != JTokenType.Float)
                throw new ArgumentException($"Parameter '{name}' must be a number, got {el.Type}.");
            return el.Value<uint>();
        }

        private static bool? GetOptionalBool(JToken? parms, string name)
        {
            var obj = parms as JObject;
            if (obj == null) return null;
            JToken? el;
            if (!obj.TryGetValue(name, out el)) return null;
            if (el.Type == JTokenType.Null) return null;
            if (el.Type == JTokenType.Boolean) return (bool)el;
            // #16: Present-but-wrong-typed -> ArgumentException, not silent null.
            throw new ArgumentException($"Parameter '{name}' must be a boolean, got {el.Type}.");
        }

        private static List<string> GetRequiredStringArray(JToken? parms, string name)
        {
            var obj = parms as JObject;
            if (obj == null)
                throw new ArgumentException("Missing 'params' object.");
            JToken? el;
            if (!obj.TryGetValue(name, out el))
                throw new ArgumentException($"Missing required parameter '{name}'.");
            if (!(el is JArray arr))
                throw new ArgumentException($"Parameter '{name}' must be an array.");

            var result = new List<string>();
            foreach (var item in arr)
            {
                if (item.Type != JTokenType.String)
                    throw new ArgumentException($"Array items in '{name}' must be strings.");
                result.Add((string)item!);
            }
            return result;
        }

        // ---------------------------------------------------------------
        // Response builders (#4: camelCase via JsonSettings)
        // ---------------------------------------------------------------

        /// <summary>Small DTO for ok:true results (link.create/delete, param.set).</summary>
        private sealed class OkResult
        {
            public bool Ok { get; } = true;
        }

        /// <summary>
        /// Build a success response envelope wrapping a pre-serialized result JSON string.
        /// Used for both fresh responses and idempotency cache hits (#5).
        /// </summary>
        private string BuildSuccessResponse(string? id, string resultJson)
        {
            // Manually assemble the envelope to embed the pre-serialized result
            // without double-serializing it.
            var sb = new StringBuilder(resultJson.Length + 128);
            sb.Append("{\"id\":");
            sb.Append(JsonConvert.SerializeObject(id));
            sb.Append(",\"ok\":true,\"result\":");
            sb.Append(resultJson);
            sb.Append(",\"projectRevision\":");
            sb.Append(JsonConvert.SerializeObject(_gateway.ProjectRevision));
            sb.Append('}');
            return sb.ToString();
        }

        private static string ErrorResponse(string? id, string code, string message)
        {
            return JsonConvert.SerializeObject(new
            {
                id,
                ok = false,
                error = new { code, message }
            }, JsonSettings);
        }

        // ---------------------------------------------------------------
        // Idempotency cache internals (#5)
        // ---------------------------------------------------------------

        private struct IdempotencyCacheEntry
        {
            public string ResultJson;
            public DateTime CreatedAt;
        }

        /// <summary>
        /// Evict stale (TTL-expired) entries, then oldest if still at capacity.
        /// </summary>
        private void EvictStaleCacheEntries()
        {
            var now = DateTime.UtcNow;
            var staleKeys = _idempotencyCache
                .Where(kvp => now - kvp.Value.CreatedAt >= CacheTtl)
                .Select(kvp => kvp.Key)
                .ToList();
            foreach (var key in staleKeys)
                _idempotencyCache.TryRemove(key, out _);

            // If still at capacity after TTL eviction, drop oldest entries.
            if (_idempotencyCache.Count >= MaxCacheEntries)
            {
                var oldest = _idempotencyCache
                    .OrderBy(kvp => kvp.Value.CreatedAt)
                    .Take(_idempotencyCache.Count - MaxCacheEntries + 100)
                    .Select(kvp => kvp.Key)
                    .ToList();
                foreach (var key in oldest)
                    _idempotencyCache.TryRemove(key, out _);
            }
        }

        // ---------------------------------------------------------------
        // Log entry for the observable activity log
        // ---------------------------------------------------------------

        internal readonly struct LogEntry
        {
            public readonly DateTime Timestamp;
            public readonly string Text;
            public readonly string Outcome;

            public LogEntry(DateTime timestamp, string text, string outcome)
            {
                Timestamp = timestamp;
                Text = text;
                Outcome = outcome;
            }
        }
    }

    // ---------------------------------------------------------------
    // Error code constants (from protocol.md)
    // ---------------------------------------------------------------

    internal static class ErrorCodes
    {
        public const string RevisionMismatch = "revision_mismatch";
        public const string NotFound = "not_found";
        public const string InvalidParams = "invalid_params";
        public const string InactiveObject = "inactive_object";
        public const string SecureConstraint = "secure_constraint";
        public const string BusUnavailable = "bus_unavailable";
        public const string Busy = "busy";
        public const string ApprovalRequired = "approval_required";
        public const string NotSupported = "not_supported";
        public const string Internal = "internal";
    }

    // ---------------------------------------------------------------
    // Custom exceptions for structured error handling
    // ---------------------------------------------------------------

    /// <summary>
    /// #19: Thrown inside the dispatcher when expectedProjectRevision mismatches.
    /// </summary>
    internal sealed class RevisionMismatchException : Exception
    {
        public RevisionMismatchException(string message) : base(message) { }
    }

    /// <summary>
    /// #21: Thrown when a pipe message line exceeds the configured max byte limit.
    /// </summary>
    internal sealed class LineTooLongException : Exception
    {
        public LineTooLongException() : base("Request line exceeds maximum allowed size.") { }
    }

    /// <summary>
    /// Thrown when an operation is not supported by the current SDK version.
    /// </summary>
    internal sealed class NotSupportedOperationException : Exception
    {
        public NotSupportedOperationException(string message) : base(message) { }
    }
}
