/**
 * Send an IPC message to the Python backend over the authenticated TCP
 * socket, returning a Promise that resolves with the reply data.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * The `ALLOWED_COMMANDS` set is imported from `../index` so that the
 * literal `ALLOWED_COMMANDS = new Set([...])` declaration continues to
 * live in `src/main/index.ts` (the Python `test_allowlist_matches_server_commands`
 * and vitest Section 13 tests slice that substring out of the source).
 */
import { ALLOWED_COMMANDS } from "../index";
import { state } from "../state";

export function sendToPython(msg: Record<string, unknown>): Promise<unknown> {
	return new Promise((resolve, reject) => {
		// If a full app relaunch is in flight, reject immediately so
		// pending IPC calls don't sit in pendingRequests until the
		// 5s timeout — the process is about to exit anyway.
		if (state._relaunching) {
			reject(new Error("Application is restarting"));
			return;
		}
		if (!state.tcpSocket) {
			reject(new Error("Python backend is not connected"));
			return;
		}
		// SEC-019: validate the command against an allowlist before
		// forwarding to the Python backend. Combined with SEC-018
		// (unauth TCP), this prevents a compromised renderer from
		// calling arbitrary IPC commands like set_config / quit_app.
		//
		// ERR-IPC-002 (fix): previously missing `quit_app` and `restart_app`,
		// which broke tray Quit/Restart (stopPython sends `quit_app`).
		// ERR-IPC-003 (fix): removed 6 dead/mismatched entries (`quit`,
		// `restart`, `save_config`, `save_vocabulary_with_diff`,
		// `repaste_last`, `complete_onboarding`) — none exist as server
		// IPC commands. The list now matches the server's actual command
		// names exactly (cross-checked against ipc_server.py _dispatch).
		//
		// NOTE: the canonical ALLOWED_COMMANDS declaration lives in
		// `src/main/index.ts` (not here) so that the Python
		// `test_allowlist_matches_server_commands` test and the
		// vitest Section 13 port can slice the literal
		// `ALLOWED_COMMANDS = new Set([` ... `]);` substring from
		// the source. Do NOT move the declaration into this file.
		const cmd = String(msg?.type ?? "").trim();
		if (!ALLOWED_COMMANDS.has(cmd)) {
			reject(new Error(`Disallowed IPC command: ${cmd}`));
			return;
		}
		const id = state.nextId++;
		(msg as Record<string, unknown>).id = id;
		state.pendingRequests.set(id, { resolve, reject });
		const line = `${JSON.stringify(msg)}\n`;
		state.tcpSocket.write(line); // 120s timeout for IPC calls  increased from 15s so model downloads
		// (which block the IPC dispatch loop for their entire duration) don't
		// trigger a false-positive timeout.  The Python-side heartbeat watchdog
		// was increased from 15s to 120s (ipc_server.py) for the same reason —
		// both timeouts must be in sync.
		setTimeout(() => {
			if (state.pendingRequests.has(id)) {
				state.pendingRequests.delete(id);
				const cmd = String(msg?.type ?? "unknown").trim();
				reject(new Error(`Timeout after 120s for command: ${cmd}`));
			}
		}, 120000);
	});
}
