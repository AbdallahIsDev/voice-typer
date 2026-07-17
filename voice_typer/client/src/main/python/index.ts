/**
 * Python backend lifecycle + TCP IPC bridge.
 *
 * Extracted from `index.ts` (REF-2). Re-exports the focused modules:
 *   - `pythonArgs`        — resolve the backend executable + args per platform
 *   - `sendToPython`      — outbound IPC over the authenticated TCP socket
 *   - `handleMessage`     — route inbound Python messages (replies + push events)
 *   - `tcpConnect`        — TCP client with retry + auth handshake
 *   - `startPython`       — spawn / adopt the backend
 *   - `stopPython`        — graceful shutdown (quit_app → SIGKILL after 3s)
 *   - `relaunchApp`       — full Electron + Python restart (tray "Restart")
 */

export { handleMessage } from "./handle-message";
export { pythonArgs } from "./python-args";
export { relaunchApp } from "./relaunch-app";
export { sendToPython } from "./send-to-python";
export { startPython } from "./start-python";
export { stopPython } from "./stop-python";
export { tcpConnect } from "./tcp-connect";
