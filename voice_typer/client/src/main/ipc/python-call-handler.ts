/**
 * `python-call` IPC bridge: renderer → Electron main → Python backend.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * SEC-022: catch errors in the python-call handler so they are
 * returned as structured error responses instead of throwing an
 * unhandled exception that Electron logs as:
 *   "Error occurred in handler for 'python-call': Error: Timeout"
 */
import { ipcMain } from "electron";
import { sendToPython } from "../python";
import { state } from "../state";

export function registerPythonCallHandler(): void {
	ipcMain.handle("python-call", async (_event, msg) => {
		if (!state.tcpSocket) {
			if (state.pythonExitedEarly) {
				return {
					_error: "Python backend exited early — another instance is running",
				};
			}
			return { _error: "Python backend is not connected" };
		}
		try {
			return await sendToPython(msg);
		} catch (err) {
			return { _error: (err as Error).message };
		}
	});
}
