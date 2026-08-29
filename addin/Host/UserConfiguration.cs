using System;
using System.IO;
using System.Xml;
using System.Xml.Serialization;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// User-specific configuration persisted by ETS via the UserConfiguration
    /// property and the IEditAddInConfiguration / ShowConfigurationDialog pattern.
    /// Uses XML serialization (same approach as the official EtsApp demo).
    /// </summary>
    public class UserConfiguration
    {
        private static readonly XmlSerializer Serializer = new XmlSerializer(typeof(UserConfiguration));

        /// <summary>
        /// When true, firmware.update skips the per-device approval token check
        /// (standing operator opt-in).  Default: false (token required).
        /// </summary>
        public bool UnattendedFirmwareUpdate { get; set; }

        /// <summary>
        /// When true, the IPC server also listens on a TCP port in addition to the
        /// named pipe.  Default: false (pipe only).
        /// Changes take effect after project reopen (no live re-bind in v1).
        /// </summary>
        public bool TcpEnabled { get; set; }

        /// <summary>
        /// TCP port the optional TCP endpoint binds to.  Default: 8730.
        /// </summary>
        public int TcpPort { get; set; } = 8730;

        /// <summary>
        /// When true, the TCP endpoint binds to all interfaces (IPAddress.Any)
        /// instead of loopback only.  Default: false (loopback = 127.0.0.1).
        /// WARNING: No TLS -- token and data travel in cleartext.  Use only on
        /// a trusted LAN.
        /// </summary>
        public bool TcpAllowLan { get; set; }

        /// <summary>
        /// Optional fixed auth token for the endpoint. When empty, a random token is
        /// generated per session (shown in the panel). Set a fixed value for a stable
        /// token that survives restarts -- convenient for the remote MCP config.
        /// </summary>
        public string Token { get; set; } = "";

        /// <summary>
        /// Deserialize a <see cref="UserConfiguration"/> from the XML document
        /// that ETS passes into Initialize / ShowConfigurationDialog.
        /// Returns null when <paramref name="document"/> is null or malformed.
        /// </summary>
        public static UserConfiguration? LoadFromDocument(XmlDocument? document)
        {
            if (document == null)
                return null;

            try
            {
                var stream = new MemoryStream();
                document.Save(stream);
                stream.Seek(0, SeekOrigin.Begin);

                using (var reader = XmlReader.Create(stream))
                {
                    return Serializer.Deserialize(reader) as UserConfiguration;
                }
            }
            catch (XmlException)
            {
                return null;
            }
            catch (InvalidOperationException)
            {
                return null;
            }
        }

        /// <summary>
        /// Serialize this instance to an <see cref="XmlDocument"/> that ETS
        /// reads from the UserConfiguration property to persist.
        /// </summary>
        public XmlDocument GetDocument()
        {
            var stream = new MemoryStream();
            Serializer.Serialize(stream, this);
            stream.Seek(0, SeekOrigin.Begin);

            using (var reader = XmlReader.Create(stream))
            {
                var doc = new XmlDocument();
                doc.Load(reader);
                return doc;
            }
        }
    }
}
