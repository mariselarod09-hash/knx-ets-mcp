using System;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// Thrown when a link.create or link.delete targets a ComObjectInstanceRef
    /// whose IsActive flag is false. Maps to error code "inactive_object".
    /// </summary>
    internal sealed class InactiveObjectException : Exception
    {
        public InactiveObjectException(string comObjectRef)
            : base($"Communication object '{comObjectRef}' is not active.")
        {
            ComObjectRef = comObjectRef;
        }

        public string ComObjectRef { get; }
    }
}
