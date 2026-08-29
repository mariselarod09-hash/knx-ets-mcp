using System;
using System.Reflection;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// Detects the loaded Knx.Ets.Sdk assembly version at startup and logs it.
    /// Deliberately does NOT block on version: the AddIn runs on whatever ETS/SDK
    /// version it is loaded into (ETS5 5.x, ETS6 6.3/6.4, ...). Compatibility is the
    /// user's to verify -- the build is compiled against a specific SDK for compile-time
    /// safety, but at runtime we never reject a version. (User decision: don't guard.)
    /// </summary>
    internal static class VersionGuard
    {
        /// <summary>
        /// Always returns null (never blocks). Logs the detected SDK version for info.
        /// The return type is kept nullable so callers stay unchanged.
        /// </summary>
        public static string? Check()
        {
            try
            {
                var version = typeof(Knx.Ets.Sdk.Root).Assembly.GetName().Version;
                System.Diagnostics.Trace.TraceInformation(
                    $"[EtsBridge] Loaded Knx.Ets.Sdk version {version} (not version-gated).");
            }
            catch (Exception ex)
            {
                // Informational only -- do not block startup on a reflection failure.
                System.Diagnostics.Trace.TraceWarning(
                    $"[EtsBridge] Could not read Knx.Ets.Sdk version: {ex.Message}");
            }

            return null; // never reject
        }
    }
}
