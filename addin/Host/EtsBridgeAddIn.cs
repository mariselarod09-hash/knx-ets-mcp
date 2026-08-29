using System;
using System.AddIn;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using System.Xml;
using Knx.Ets.Sdk;
using Knx.Ets.Sdk.AddIns.AddInViews;
using Knx.Ets.Sdk.Project;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// ETS6 project-level AddIn entry point.
    /// Implements IEts4AddInV2 + IEts5ClosableAddin + IEditAddInConfiguration
    /// (same pattern as the official EtsApp demo).
    ///
    /// Lifecycle:
    ///   1. ETS instantiates this class and calls Initialize(context).
    ///   2. ETS calls GetAddInUI() to get the WPF panel.
    ///   3. On project close / ETS exit: Dispose().
    ///
    /// IPC lifetime is tied to Initialize/Dispose (project open/close), NOT to the
    /// panel open/close cycle (#16).
    ///
    /// OnProjectOpened / OnProjectClosing are called on SEPARATE short-lived instances
    /// (per ETS AddIn contract); they are no-ops here because the long-lived instance
    /// manages the IPC server lifecycle through Initialize/Dispose.
    /// </summary>
    [AddIn("KNX-ETS MCP Bridge", Version = "0.2.0", Publisher = "knx-ets-bridge")]
#if ETS5
    // ETS5 SDK 5.7 has no IEditAddInConfiguration; config dialog is the 2-arg
    // ShowConfigurationDialog on IEts4AddIn instead.
    public sealed class EtsBridgeAddIn : IEts4AddInV2, IEts5ClosableAddin, IDisposable
#else
    public sealed class EtsBridgeAddIn : IEts4AddInV2, IEts5ClosableAddin, IEditAddInConfiguration, IDisposable
#endif
    {
        // Process-wide bridge state. ETS uses SEPARATE AddIn instances for the panel,
        // the configuration dialog, and project-lifecycle events -- so this state MUST
        // be shared (static), or config changes never reach the running IPC server.
        //
        // #1: The gateway is bound to a specific project identity (ProjectGuid).
        // When Initialize is called with a DIFFERENT project, the bridge is atomically
        // rebuilt for the new project.
        private static IInitializationContext? _ctx;
        private static EtsDispatcher? _dispatcher;
        private static Ets6ProjectGateway? _gateway;
        private static ApprovalManager? _approvalManager;
        private static IpcServer? _ipcServer;
        private static UserConfiguration? _userConfiguration;
        private static bool _versionOk;
        private static bool _sessionWriteFailed; // #17: fail-closed

        // #1: Track the project identity the gateway was built for.
        // ETS6: Project.ProjectGuid (Guid, SDK XML line 16836)
        // ETS5: Project.ProjectId (ushort) -- no ProjectGuid in ETS5 SDK.
        // Unified as a string comparison in both builds.
        private static string _activeProjectId = "";

        // The CLR probes the HOST process directory (ETS.exe) + GAC for dependencies,
        // NOT our AddIn folder. So bundled deps next to our DLL (Newtonsoft.Json.dll) are
        // otherwise "could not load file or assembly". Resolve them from our own folder.
        // Registered in the static ctor so it is active before any bundled type is touched.
        static EtsBridgeAddIn()
        {
            AppDomain.CurrentDomain.AssemblyResolve += ResolveFromAddInFolder;
        }

        private static Assembly? ResolveFromAddInFolder(object? sender, ResolveEventArgs args)
        {
            try
            {
                var simpleName = new AssemblyName(args.Name).Name;
                if (string.IsNullOrEmpty(simpleName)) return null;
                // Skip resource-satellite probes to avoid needless recursion.
                if (simpleName.EndsWith(".resources", StringComparison.OrdinalIgnoreCase)) return null;

                // The ETS SDK assemblies are provided by the host process. If this AddIn
                // was built against a different SDK version than the running ETS provides
                // (e.g. a 6.4-built AddIn on ETS 6.3), the strong-name version bind fails.
                // Return the host's already-loaded assembly of the same simple name -- we
                // avoid version-specific SDK APIs, so the host's version satisfies our calls.
                if (simpleName.StartsWith("Knx.Ets.", StringComparison.OrdinalIgnoreCase))
                {
                    foreach (var loaded in AppDomain.CurrentDomain.GetAssemblies())
                    {
                        if (string.Equals(loaded.GetName().Name, simpleName, StringComparison.OrdinalIgnoreCase))
                        {
                            System.Diagnostics.Trace.TraceInformation(
                                $"[EtsBridge] AssemblyResolve: bound '{simpleName}' to host-loaded {loaded.GetName().Version}");
                            return loaded;
                        }
                    }
                    return null; // host hasn't loaded it (unexpected) -- let default binder fail
                }

                var self = typeof(EtsBridgeAddIn).Assembly;

                // 1) Prefer an EMBEDDED copy. Bulletproof: independent of the load context
                //    or where ETS placed our DLL (LoadFile / shadow-copy), because it reads
                //    from our own already-loaded manifest, not the filesystem.
                var wanted = simpleName + ".dll";
                foreach (var res in self.GetManifestResourceNames())
                {
                    if (res.Equals(wanted, StringComparison.OrdinalIgnoreCase)
                        || res.EndsWith("." + wanted, StringComparison.OrdinalIgnoreCase))
                    {
                        using var rs = self.GetManifestResourceStream(res);
                        if (rs == null) break;
                        var buf = new byte[rs.Length];
                        int off = 0, n;
                        while ((n = rs.Read(buf, off, buf.Length - off)) > 0) off += n;
                        System.Diagnostics.Trace.TraceInformation(
                            $"[EtsBridge] AssemblyResolve: loaded '{simpleName}' from embedded resource '{res}'");
                        return Assembly.Load(buf);
                    }
                }

                // 2) Fallback: next to OUR DLL (CodeBase survives shadow-copy where Location
                //    would point at a temp folder).
                var dir = AddInFolder();
                if (string.IsNullOrEmpty(dir)) return null;
                var candidate = Path.Combine(dir!, simpleName + ".dll");
                if (!File.Exists(candidate))
                {
                    System.Diagnostics.Trace.TraceWarning(
                        $"[EtsBridge] AssemblyResolve: '{simpleName}' not embedded and not found in {dir}");
                    return null;
                }
                System.Diagnostics.Trace.TraceInformation(
                    $"[EtsBridge] AssemblyResolve: loaded '{simpleName}' from {dir}");
                return Assembly.LoadFrom(candidate);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Trace.TraceWarning($"[EtsBridge] AssemblyResolve failed: {ex.Message}");
                return null; // let the default binder fail normally
            }
        }

        /// <summary>Directory of our own AddIn DLL, resilient to ETS shadow-copy.</summary>
        private static string? AddInFolder()
        {
            var asm = typeof(EtsBridgeAddIn).Assembly;
            try
            {
#pragma warning disable SYSLIB0012 // CodeBase is obsolete but is the shadow-copy-safe path on net48
                var codeBase = asm.CodeBase;
#pragma warning restore SYSLIB0012
                if (!string.IsNullOrEmpty(codeBase))
                {
                    var localPath = new Uri(codeBase).LocalPath;
                    var dir = Path.GetDirectoryName(localPath);
                    if (!string.IsNullOrEmpty(dir)) return dir;
                }
            }
            catch { /* fall through to Location */ }
            return Path.GetDirectoryName(asm.Location);
        }

        // ---------------------------------------------------------------
        // IEts4AddIn implementation
        // ---------------------------------------------------------------

        /// <summary>
        /// App ID -- development placeholder. Replace with a KNX Association-registered
        /// ID before distribution. Must match AddInManifest.xml.
        /// </summary>
        public string AppId => "M0FFF-A0001";

        /// <summary>No persistent machine configuration in v0.</summary>
        public XmlDocument? Configuration => null;

        /// <summary>
        /// User configuration persisted by ETS. Returns the current
        /// <see cref="Addin.UserConfiguration"/> serialized as XML, or a
        /// default instance if none was loaded yet.
        /// </summary>
        public XmlDocument? UserConfiguration
        {
            get
            {
                var config = _userConfiguration ?? new UserConfiguration();
                return config.GetDocument();
            }
        }

        public void Initialize(IInitializationContext initializationContext)
        {
            // #1: The bridge state is process-wide (static). If the bridge is already
            // running for THIS project, reuse it. If the project has CHANGED (different
            // ProjectGuid), atomically rebuild the gateway for the new project.
#if ETS5
            var newProjectId = initializationContext.Project.ProjectId.ToString();
#else
            var newProjectId = initializationContext.Project.ProjectGuid.ToString();
#endif

            if (_ipcServer != null)
            {
                if (newProjectId == _activeProjectId)
                {
                    // Same project -- just refresh the context reference.
                    _ctx = initializationContext;
                    return;
                }

                // #1: Different project detected. Shut down the old bridge and rebuild.
                System.Diagnostics.Trace.TraceInformation(
                    $"[EtsBridge] Project changed ({_activeProjectId} -> {newProjectId}); rebuilding bridge.");
                Shutdown();
            }

            _ctx = initializationContext;
            _activeProjectId = newProjectId;

            // Load persisted user configuration (e.g. Unattended firmware update flag).
            _userConfiguration = Addin.UserConfiguration.LoadFromDocument(
                initializationContext.UserConfiguration);

            // --- Version guard: refuse to run on anything other than 6.3.x ---
            var versionError = VersionGuard.Check();
            if (versionError != null)
            {
                System.Diagnostics.Trace.TraceWarning(
                    $"[EtsBridge] Version guard failed: {versionError}");
                _versionOk = false;
                return;
            }
            _versionOk = true;

            // #20: Capture the ETS UI dispatcher robustly.
            // Dispatcher.CurrentDispatcher creates a NEW dispatcher if none exists on
            // the calling thread, which may not be the pumped ETS UI dispatcher.
            // Prefer Application.Current.Dispatcher which returns the WPF UI thread
            // dispatcher, falling back to an ETS-provided UI element's dispatcher.
            // If ETS uses multiple UI threads, the correct dispatcher must be the one
            // owning the project window.
            Dispatcher uiDispatcher;
            if (Application.Current != null)
            {
                uiDispatcher = Application.Current.Dispatcher;
            }
            else
            {
                // Fallback: assume ETS calls Initialize on its UI thread.
                uiDispatcher = Dispatcher.CurrentDispatcher;
                System.Diagnostics.Trace.TraceWarning(
                    "[EtsBridge] Application.Current is null; using Dispatcher.CurrentDispatcher. "
                  + "Verify this is the pumped ETS UI dispatcher.");
            }

            _dispatcher = new EtsDispatcher(uiDispatcher);

            // Create the gateway (the only seam that touches SDK types).
            _gateway = new Ets6ProjectGateway(_ctx);

            // Approval manager: gates firmware.update on the Unattended preference.
            _approvalManager = new ApprovalManager();

            // Apply persisted Unattended setting so it takes effect at startup.
            if (_userConfiguration != null)
            {
                _approvalManager.Unattended = _userConfiguration.UnattendedFirmwareUpdate;
                System.Diagnostics.Trace.TraceInformation(
                    $"[EtsBridge] Loaded persisted Unattended={_userConfiguration.UnattendedFirmwareUpdate}.");
            }

            // Start the IPC server (pipe + optional TCP) from the persisted config.
            RestartIpcServer();
            if (_sessionWriteFailed)
            {
                System.Diagnostics.Trace.TraceWarning(
                    "[EtsBridge] Session file write failed; bridge is inactive.");
            }

            // Log bridge.info at startup so the panel's Activity Log shows versions +
            // project immediately, without needing a client call. We are on the UI thread.
            try
            {
                var info = _gateway?.GetBridgeInfo();
                if (info != null)
                {
                    _ipcServer?.Log(
                        $"bridge start: addin v{info.AddinVersion}, built against SDK "
                      + $"{info.BuiltAgainstSdk}, running on SDK {info.SdkVersion}, "
                      + $"project '{info.ProjectName}'", "start");
                }
            }
            catch { /* logging must never break startup */ }
        }

        public FrameworkElement GetAddInUI()
        {
            // Minimal UI: a status label. The AddIn is headless by design;
            // the real interaction happens through the IPC pipe.
            var panel = new StackPanel { Margin = new Thickness(12) };

            if (!_versionOk)
            {
                panel.Children.Add(new TextBlock
                {
                    Text = "KNX-ETS MCP Bridge: INACTIVE (unsupported SDK version).",
                    Foreground = System.Windows.Media.Brushes.Red,
                    FontWeight = FontWeights.Bold
                });
                return panel;
            }

            // #17: Report session write failure in the status panel.
            if (_sessionWriteFailed)
            {
                panel.Children.Add(new TextBlock
                {
                    Text = "KNX-ETS MCP Bridge: INACTIVE (session file write failed).",
                    Foreground = System.Windows.Media.Brushes.Orange,
                    FontWeight = FontWeights.Bold
                });
                return panel;
            }

            // --- Status section ---
            var statusText = new TextBlock
            {
                FontWeight = FontWeights.Bold,
                FontSize = 14,
                Margin = new Thickness(0, 0, 0, 2)
            };
            panel.Children.Add(statusText);

            panel.Children.Add(new TextBlock
            {
                Text = _ipcServer != null ? $"Pipe: {_ipcServer.PipeName}" : "Pipe: (not running)",
                Foreground = System.Windows.Media.Brushes.Gray,
                Margin = new Thickness(0, 0, 0, 4)
            });

            // --- TCP endpoint info (shown only when TCP is enabled) ---
            if (_ipcServer != null && _ipcServer.TcpEnabled)
            {
                var hostHint = _ipcServer.TcpAllowLan ? "<this-machine-IP>" : "127.0.0.1";
                panel.Children.Add(new TextBlock
                {
                    Text = $"TCP: {hostHint}:{_ipcServer.TcpPort}"
                         + (_ipcServer.TcpAllowLan ? "  (LAN - all interfaces)" : "  (loopback only)"),
                    Foreground = System.Windows.Media.Brushes.Gray,
                    Margin = new Thickness(0, 0, 0, 2)
                });

                // Show the token so the user can copy it into the remote MCP config.
                // The remote machine cannot read session.json, so this is the only way.
                var tokenPanel = new StackPanel
                {
                    Orientation = Orientation.Horizontal,
                    Margin = new Thickness(0, 0, 0, 8)
                };
                tokenPanel.Children.Add(new TextBlock
                {
                    Text = "Token: ",
                    Foreground = System.Windows.Media.Brushes.Gray,
                    VerticalAlignment = VerticalAlignment.Center
                });
                var tokenBox = new TextBox
                {
                    Text = _ipcServer.Token,
                    IsReadOnly = true,
                    IsReadOnlyCaretVisible = true,
                    Width = 260,
                    FontFamily = new System.Windows.Media.FontFamily("Consolas, Courier New, monospace"),
                    FontSize = 12,
                    VerticalAlignment = VerticalAlignment.Center,
                    ToolTip = "Copy this token into the remote MCP config (KNX_BRIDGE_TOKEN)."
                };
                tokenPanel.Children.Add(tokenBox);
                panel.Children.Add(tokenPanel);
            }
            else
            {
                panel.Children.Add(new Separator { Margin = new Thickness(0, 0, 0, 8), Visibility = Visibility.Hidden });
            }

            // --- Auth status warning (only relevant when the TCP endpoint is exposed) ---
            // With TCP off, only the local named pipe is used (restricted to the current
            // Windows user SID), so a missing token is not a network-exposure risk.
            TextBlock? authWarning = null;
            if (_ipcServer != null && _ipcServer.TcpEnabled && !_ipcServer.AuthRequired)
            {
                authWarning = new TextBlock
                {
                    Text = "Auth: DISABLED - no token (anyone on the network can control ETS)",
                    Foreground = System.Windows.Media.Brushes.Red,
                    FontWeight = FontWeights.Bold,
                    Margin = new Thickness(0, 0, 0, 4)
                };
                panel.Children.Add(authWarning);
            }

            // --- Unattended mode: read-only indicator (setting lives in Configuration dialog) ---
            var approvalMgr = _approvalManager;
            var unattendedText = new TextBlock
            {
                Margin = new Thickness(0, 8, 0, 0),
                FontStyle = FontStyles.Italic,
                ToolTip = "Change this setting in the app Configuration dialog (ETS settings)."
            };
            panel.Children.Add(unattendedText);
#if ETS5
            // firmware.update is not supported on ETS5, and there is no Configuration
            // dialog to change it in -- hide this line on ETS5.
            unattendedText.Visibility = Visibility.Collapsed;
#endif

            var tcpStatusText = new TextBlock
            {
                Margin = new Thickness(0, 2, 0, 0),
                FontStyle = FontStyles.Italic
            };
            panel.Children.Add(tcpStatusText);

#if !ETS5
            // On ETS6 the TCP endpoint / token are configured in the app's settings
            // (ETS Apps/Extensions preferences), not in this panel. Point the user there.
            panel.Children.Add(new TextBlock
            {
                Text = "To enable the TCP endpoint (for a remote MCP server) and set the token, "
                     + "open this app's settings in ETS: Apps / Extensions preferences.",
                FontStyle = FontStyles.Italic,
                Foreground = System.Windows.Media.Brushes.Gray,
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 2, 0, 0)
            });
#endif

            Action updateTcpStatus = () =>
            {
                var s = _ipcServer;
                if (s != null && s.IsTcpListening)
                {
                    tcpStatusText.Text = $"TCP: listening on {(s.TcpAllowLan ? "0.0.0.0" : "127.0.0.1")}:{s.TcpPort}";
                    tcpStatusText.Foreground = System.Windows.Media.Brushes.DarkGreen;
                }
                else if (s != null && s.TcpEnabled)
                {
                    tcpStatusText.Text = "TCP: enabled but NOT listening (bind failed - see log)";
                    tcpStatusText.Foreground = System.Windows.Media.Brushes.OrangeRed;
                }
                else
                {
                    tcpStatusText.Text = "TCP endpoint: off";
                    tcpStatusText.Foreground = System.Windows.Media.Brushes.Gray;
                }
            };

#if ETS5
            // --- ETS5 configuration (ETS5 has no IEditAddInConfiguration dialog, so the
            //     TCP endpoint / token are configured directly in this panel). ---
            {
                var cfg0 = _userConfiguration ?? new UserConfiguration();
                panel.Children.Add(new TextBlock
                {
                    Text = "Configuration (ETS5)",
                    FontWeight = FontWeights.SemiBold,
                    Margin = new Thickness(0, 12, 0, 4)
                });

                var tcpCheck = new CheckBox
                {
                    Content = "TCP endpoint enabled",
                    IsChecked = cfg0.TcpEnabled,
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Margin = new Thickness(0, 0, 0, 2)
                };
                panel.Children.Add(tcpCheck);

                var lanCheck = new CheckBox
                {
                    Content = "Allow LAN (bind 0.0.0.0) - required for a remote MCP server",
                    IsChecked = cfg0.TcpAllowLan,
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Margin = new Thickness(0, 0, 0, 2)
                };
                panel.Children.Add(lanCheck);

                var portRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 2) };
                portRow.Children.Add(new TextBlock { Text = "Port: ", VerticalAlignment = VerticalAlignment.Center });
                var portBox = new TextBox { Text = cfg0.TcpPort.ToString(), Width = 80, VerticalAlignment = VerticalAlignment.Center };
                portRow.Children.Add(portBox);
                panel.Children.Add(portRow);

                var tokRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 4) };
                tokRow.Children.Add(new TextBlock { Text = "Token: ", VerticalAlignment = VerticalAlignment.Center });
                var tokBox = new TextBox
                {
                    Text = cfg0.Token,
                    Width = 260,
                    VerticalAlignment = VerticalAlignment.Center,
                    FontFamily = new System.Windows.Media.FontFamily("Consolas, Courier New, monospace"),
                    ToolTip = "Empty = no authentication (trusted LAN only)."
                };
                tokRow.Children.Add(tokBox);
                panel.Children.Add(tokRow);

                var applyBtn = new Button
                {
                    Content = "Apply & restart bridge",
                    Padding = new Thickness(8, 2, 8, 2),
                    HorizontalAlignment = HorizontalAlignment.Left
                };
                applyBtn.Click += (_, __) =>
                {
                    var cfg = _userConfiguration ?? new UserConfiguration();
                    cfg.TcpEnabled = tcpCheck.IsChecked == true;
                    cfg.TcpAllowLan = lanCheck.IsChecked == true;
                    if (int.TryParse((portBox.Text ?? "").Trim(), out var p) && p > 0 && p < 65536)
                        cfg.TcpPort = p;
                    cfg.Token = (tokBox.Text ?? "").Trim();
                    _userConfiguration = cfg;
                    RestartIpcServer();
                };
                panel.Children.Add(applyBtn);

                panel.Children.Add(new TextBlock
                {
                    Text = "Note: on ETS5 these settings apply to the running bridge; re-apply after an ETS restart.",
                    FontStyle = FontStyles.Italic,
                    Foreground = System.Windows.Media.Brushes.Gray,
                    TextWrapping = TextWrapping.Wrap,
                    Margin = new Thickness(0, 4, 0, 0)
                });
            }
#endif

            // --- Activity log (below the key info) ---
            panel.Children.Add(new TextBlock
            {
                Text = "Activity Log",
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(0, 12, 0, 4)
            });

            var logTextBox = new TextBox
            {
                Height = 220,
                FontFamily = new System.Windows.Media.FontFamily("Consolas, Courier New, monospace"),
                FontSize = 11,
                BorderThickness = new Thickness(1),
                BorderBrush = System.Windows.Media.Brushes.LightGray,
                IsReadOnly = true,
                IsReadOnlyCaretVisible = true,
                AcceptsReturn = true,
                TextWrapping = TextWrapping.NoWrap,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto
            };
            panel.Children.Add(logTextBox);

            // --- Live refresh via DispatcherTimer ---
            // Read the _ipcServer field each tick so the panel tracks the current
            // server instance even after a config-driven restart (not a stale one).
            int lastLogVersion = -1;

            var refreshTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
            refreshTimer.Tick += (_, __) =>
            {
                // Connection status
                bool connected = _ipcServer?.ClientConnected == true;
                statusText.Text = connected
                    ? "MCP Client: Connected"
                    : "MCP Client: Not connected";
                statusText.Foreground = connected
                    ? System.Windows.Media.Brushes.DarkGreen
                    : System.Windows.Media.Brushes.Gray;

                // Activity log (rebuild only when version changes)
                int currentVersion = _ipcServer?.LogVersion ?? 0;
                if (currentVersion != lastLogVersion)
                {
                    lastLogVersion = currentVersion;
                    var snapshot = _ipcServer?.GetLogSnapshot();
                    if (snapshot != null)
                    {
                        var sb = new System.Text.StringBuilder(snapshot.Count * 60);
                        foreach (var entry in snapshot)
                        {
                            sb.AppendLine($"{entry.Timestamp:HH:mm:ss}  {entry.Text,-25} {entry.Outcome}");
                        }
                        logTextBox.Text = sb.ToString();
                    }
                    else
                    {
                        logTextBox.Text = "";
                    }
                    // Auto-scroll to end so latest entries are visible.
                    logTextBox.CaretIndex = logTextBox.Text.Length;
                    logTextBox.ScrollToEnd();
                }

                // Unattended indicator (may change via Configuration dialog at any time)
                var state = approvalMgr?.Unattended == true ? "ON" : "OFF";
                unattendedText.Text = $"Unattended firmware update: {state}";
                updateTcpStatus();
            };

            // Set initial state before the first timer tick fires.
            {
                bool connected = _ipcServer?.ClientConnected == true;
                statusText.Text = connected
                    ? "MCP Client: Connected"
                    : "MCP Client: Not connected";
                statusText.Foreground = connected
                    ? System.Windows.Media.Brushes.DarkGreen
                    : System.Windows.Media.Brushes.Gray;
                var state = approvalMgr?.Unattended == true ? "ON" : "OFF";
                unattendedText.Text = $"Unattended firmware update: {state}";
                updateTcpStatus();
            }

            // If ETS reuses or reparents the FrameworkElement, the timer
            // stop may need to move into OnPanelClosing() or a separate cleanup path.
            panel.Unloaded += (_, __) =>
            {
                refreshTimer.Stop();
            };

            refreshTimer.Start();

            // Wrap in a ScrollViewer so the panel scrolls properly when the ETS
            // panel area is small.
            return new ScrollViewer
            {
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
                Content = panel
            };
        }

        public void Ets4SelectionChanged(IEnumerable<DomObject> selectedObjects)
        {
            // No-op: the bridge does not react to ETS selection changes.
        }

        public void ToolbarItemClick(string itemIdentifier)
        {
            // No toolbar buttons defined in v0.
        }

#if ETS5
        // ETS5: this 2-arg overload IS the config entry point (no IEditAddInConfiguration).
        // ETS5 has no IDialogService here; run with persisted UserConfiguration
        // / defaults (token may be empty = no-auth on trusted LAN).
        public bool ShowConfigurationDialog(XmlDocument configuration, XmlDocument userConfiguration)
        {
            return false;
        }
#else
        [Obsolete("Use IEditAddInConfiguration instead")]
        public bool ShowConfigurationDialog(XmlDocument configuration, XmlDocument userConfiguration)
        {
            // Deprecated overload; the IEditAddInConfiguration overload below is used instead.
            return false;
        }

        // ---------------------------------------------------------------
        // IEditAddInConfiguration
        // ---------------------------------------------------------------

        /// <summary>
        /// Shows the AddIn configuration dialog (called from ETS settings).
        /// Signature: IEditAddInConfiguration.ShowConfigurationDialog(XmlDocument, XmlDocument, IDialogService).
        /// Returns true if the user confirmed changes (ETS then reads the
        /// Configuration / UserConfiguration properties to persist).
        /// </summary>
        public bool ShowConfigurationDialog(
            XmlDocument configuration,
            XmlDocument userConfiguration,
            IDialogService dialogService)
        {
            var userConfig = Addin.UserConfiguration.LoadFromDocument(userConfiguration)
                             ?? new UserConfiguration();

            var currentToken = string.IsNullOrWhiteSpace(userConfig.Token)
                ? (_ipcServer?.Token ?? "")
                : userConfig.Token;
            var dialog = new ConfigurationDialog(
                userConfig.UnattendedFirmwareUpdate,
                userConfig.TcpEnabled,
                userConfig.TcpPort,
                userConfig.TcpAllowLan,
                currentToken);
            var result = dialogService.ShowModal(dialog, ModalScope.Application);
            _ipcServer?.Log($"Config dialog closed: result={(result.HasValue ? result.Value.ToString() : "null")}", "---");

            if (result == true)
            {
                _ipcServer?.Log("Config OK -> saving + restarting IPC", "---");
                userConfig.UnattendedFirmwareUpdate = dialog.UnattendedFirmwareUpdate;
                userConfig.TcpEnabled = dialog.TcpEnabled;
                userConfig.TcpPort = dialog.TcpPort;
                userConfig.TcpAllowLan = dialog.TcpAllowLan;
                userConfig.Token = dialog.Token;
                _userConfiguration = userConfig;

                // Apply immediately to the running approval manager.
                if (_approvalManager != null)
                {
                    _approvalManager.Unattended = userConfig.UnattendedFirmwareUpdate;
                }

                // Restart the IPC server so TCP settings take effect immediately.
                RestartIpcServer();
                System.Diagnostics.Trace.TraceInformation(
                    $"[EtsBridge] Configuration saved + IPC restarted: Unattended={userConfig.UnattendedFirmwareUpdate}, "
                  + $"TcpEnabled={userConfig.TcpEnabled}, TcpPort={userConfig.TcpPort}, "
                  + $"TcpAllowLan={userConfig.TcpAllowLan}.");
                return true;
            }

            return false;
        }
#endif

        public FrameworkElement? GetSidebarControl(IEnumerable<object> selectedObjects)
        {
            return null; // No sidebar customization.
        }

        // ---------------------------------------------------------------
        // IEts4AddInV2
        // ---------------------------------------------------------------

        public FrameworkElement? GetSidebarProperties(IEnumerable<object> selectedObjects)
        {
            return null; // No sidebar customization.
        }

        // ---------------------------------------------------------------
        // IEts5ClosableAddin
        // ---------------------------------------------------------------

        public bool OnPanelClosing()
        {
            // #16: Panel close should NOT kill the bridge. IPC lifetime is tied
            // to project open/close (Initialize/Dispose), not to the UI panel.
            // The user can re-open the panel without losing the IPC connection.
            return true; // Allow close without stopping the bridge.
        }

        // ---------------------------------------------------------------
        // Project lifecycle events (short-lived instances)
        // These are called on SEPARATE temporary instances, NOT this one.
        // ---------------------------------------------------------------

        public void OnProjectOpened(Project openedProject)
        {
            // No-op on the temporary event instance.
            // The long-lived instance starts the IPC server in Initialize().
        }

        public void OnProjectClosing(Project closingProject, bool changesSinceProjectOpened)
        {
            // #1: Only shut down if the closing project IS the one we are serving.
            // ETS may close a different project (e.g. second project in the same session);
            // tearing down the bridge for a foreign project would kill the active session.
#if ETS5
            var closingId = closingProject.ProjectId.ToString();
#else
            var closingId = closingProject.ProjectGuid.ToString();
#endif
            if (closingId != _activeProjectId)
            {
                System.Diagnostics.Trace.TraceInformation(
                    $"[EtsBridge] Ignoring close for project {closingId} "
                  + $"(active: {_activeProjectId}).");
                return;
            }

            System.Diagnostics.Trace.TraceInformation("[EtsBridge] Active project closing; stopping bridge.");
            Shutdown();
        }

        // ---------------------------------------------------------------
        // Dispose
        // ---------------------------------------------------------------

        public void Dispose()
        {
            // Do NOT shut down here. ETS disposes transient instances (e.g. the
            // configuration-dialog instance) while the shared bridge must keep running
            // for the open project. The bridge is stopped in OnProjectClosing.
        }

        private void RestartIpcServer()
        {
            if (!_versionOk || _gateway == null || _dispatcher == null || _approvalManager == null)
                return;

            // Fully stop the previous server (pipe + TCP) before rebinding, so the
            // TCP port and pipe are free for the new instance.
            if (_ipcServer != null)
            {
                _ipcServer.Stop();
                _ipcServer = null;
            }

            bool tcpEnabled = _userConfiguration?.TcpEnabled ?? false;
            int tcpPort = _userConfiguration?.TcpPort ?? 8730;
            bool tcpAllowLan = _userConfiguration?.TcpAllowLan ?? false;
            // Pass token as-is: empty string means NO authentication.
            string? token = _userConfiguration?.Token;
            _ipcServer = new IpcServer(_gateway, _dispatcher, _approvalManager,
                                       tcpEnabled, tcpPort, tcpAllowLan, token);
            _sessionWriteFailed = !_ipcServer.Start();
            System.Diagnostics.Trace.TraceInformation(
                $"[EtsBridge] IPC server (re)started. Pipe='{_ipcServer.PipeName}', "
              + $"TcpEnabled={tcpEnabled}, TcpPort={tcpPort}, TcpAllowLan={tcpAllowLan}.");
            _ipcServer.Log(
                $"Config applied: Unattended={_userConfiguration?.UnattendedFirmwareUpdate}, "
              + $"TcpEnabled={tcpEnabled}, TcpPort={tcpPort}, TcpAllowLan={tcpAllowLan}", "---");
        }

        private void Shutdown()
        {
            if (_ipcServer != null)
            {
                System.Diagnostics.Trace.TraceInformation("[EtsBridge] Stopping IPC server.");
                _ipcServer.Stop();
                _ipcServer = null;
            }
            // #1/#3: Dispose the gateway (unsubscribes event handlers, disposes CTS objects).
            _gateway?.Dispose();
            _gateway = null;
            _approvalManager = null;
            _dispatcher = null;
            _ctx = null;
            _activeProjectId = "";
        }
    }
}
