/**
 * `backend:restart` IPC handler — restart the Python backend process
 * from the renderer (the "Lost connection" Retry escalation).
 *
 * SEC: this channel does NOT bridge to the Python backend (no TCP
 * message is sent) — the backend is dead by definition when this is
 * invoked. It is a renderer→main process-control channel like
 * `window:open-logs`, so it is not subject to the SEC-002 command
 * allowlist / ALLOWED_COMMANDS parity contract.
 */

import type { IpcMainInvokeEvent } from "electron";
import { ipcMain } from "electron";
import { logger } from "../logging";
import { restartBackend } from "../python/restart-backend";
import { BackendChannels } from "./channels";

export function registerBackendRestartHandler(): void {
	ipcMain.removeHandler?.(BackendChannels.restart);
	ipcMain.handle(
		BackendChannels.restart,
		async (
			_event: IpcMainInvokeEvent,
		): Promise<{ ok: boolean; reason?: string }> => {
			try {
				const result = restartBackend();
				logger.info("[IPC] backend:restart →", {
					ok: result.ok,
					reason: result.reason ?? null,
				});
				return result;
			} catch (e) {
				logger.warn("[IPC] backend:restart failed", {
					error: (e as Error).message,
				});
				return { ok: false, reason: "handler-error" };
			}
		},
	);
}
