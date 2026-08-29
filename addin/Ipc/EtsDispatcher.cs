using System;
using System.Threading;
using System.Windows.Threading;

namespace Knx.EtsBridge.Addin
{
    /// <summary>
    /// Marshals all ETS SDK access onto the WPF Dispatcher (UI) thread.
    /// The AddIn captures the Dispatcher during Initialize() and all gateway
    /// calls go through this class -- both reads and writes.
    ///
    /// Design: single bounded queue (Dispatcher already is one). Short synchronous
    /// critical sections only. Never call .Wait()/.Result on the Dispatcher thread
    /// itself (deadlock). Background callers invoke RunOnUiThread which blocks
    /// the caller until the delegate completes on the UI thread.
    /// </summary>
    internal sealed class EtsDispatcher
    {
        private readonly Dispatcher _dispatcher;

        public EtsDispatcher(Dispatcher dispatcher)
        {
            _dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
        }

        /// <summary>
        /// Execute <paramref name="action"/> on the UI thread and return its result.
        /// Blocks the calling (background) thread until completion.
        /// MUST NOT be called from the UI thread itself.
        /// </summary>
        public T RunOnUiThread<T>(Func<T> action)
        {
            if (_dispatcher.CheckAccess())
            {
                // Already on UI thread -- run inline. This path should not normally
                // happen from IPC handlers, but is safe for initialization code.
                return action();
            }

            // Dispatcher.Invoke blocks the caller until the delegate finishes on
            // the UI thread. DispatcherPriority.Normal is fine; we do not want to
            // pre-empt user interactions (Send priority).
            return _dispatcher.Invoke(action, DispatcherPriority.Normal);
        }

        /// <summary>
        /// Execute <paramref name="action"/> on the UI thread (no return value).
        /// Blocks the calling (background) thread until completion.
        /// </summary>
        public void RunOnUiThread(Action action)
        {
            if (_dispatcher.CheckAccess())
            {
                action();
                return;
            }

            _dispatcher.Invoke(action, DispatcherPriority.Normal);
        }

        /// <summary>
        /// Returns true if the calling thread is the UI thread.
        /// </summary>
        public bool IsOnUiThread => _dispatcher.CheckAccess();
    }
}
