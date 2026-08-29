using System;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// Gates safety-critical operations (e.g. firmware.update) based on the
    /// persistent "Unattended firmware update" preference set in the AddIn
    /// Configuration dialog.
    ///
    /// Thread-safe: the backing field uses volatile.
    /// </summary>
    internal sealed class ApprovalManager
    {
        /// <summary>
        /// When true, firmware.update proceeds without further approval.
        /// When false, firmware.update returns approval_required.
        /// Set via the "Unattended firmware update" checkbox in the Configuration dialog.
        /// Default: false (approval required).
        /// </summary>
        private volatile bool _unattended;
        public bool Unattended
        {
            get => _unattended;
            set => _unattended = value;
        }
    }
}
