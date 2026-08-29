using System;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// Thrown when a bus operation cannot be performed because no KNXnet/IP
    /// connection is available. Maps to error code "bus_unavailable".
    /// In ETS 6.3.0, USB-based DeviceManagement is broken; only KNXnet/IP works.
    /// </summary>
    internal sealed class BusUnavailableException : Exception
    {
        public BusUnavailableException(string message) : base(message) { }
    }
}
