/**
 * CR-71: shared `bubble` namespace methods used by both Electron preloads.
 *
 * Previously the same 10 methods (`onLevel`, `show`, `signalReady`,
 * `setPosition`, `setDraggable`, `moveBy`, `onShow`, `onHide`,
 * `onDraggable`, `hideComplete`) were duplicated byte-for-byte across:
 *
 *   - `src/preload/index.ts` (main window preload)
 *   - `src/preload/bubble.ts` (bubble window preload)
 *   - `src/renderer/src/lib/tauri-bridge.ts` (Tauri-installed equivalent)
 *
 * This module deduplicates the Electron preload surface. Both preloads
 * spread this object into the `bubble` namespace they pass to
 * `contextBridge.exposeInMainWorld`. The bubble-only preload adds a
 * few extra methods (`onSetState`, `onConfig`, `resizeTo`,
 * `toggleDictation`) on top — these stay declared inline in
 * `preload/bubble.ts` because they're not part of the shared contract.
 *
 * CR-70: the dead mouse drag-to-move surface (`startDrag`, `drag`,
 * `endDrag`) was removed from both preloads and the corresponding
 * `ipcMain.on` handlers in `main/ipc/bubble-handlers.ts`. Bubble
 * dragging now uses native CSS `-webkit-app-region: drag` (see
 * `renderer/src/Bubble.tsx`), so the JS drag channel was dead code.
 *
 * TODO Fix-M: the Tauri-installed bubble namespace
 * (`renderer/src/lib/tauri-bridge.ts`) reimplements these 10 methods
 * using Tauri `invoke()` + `event.listen()`. It should be refactored
 * to consume this contract (or a shared type) so a behavioral fix
 * only needs to be applied in one place. That refactor is owned by
 * Fix-M because `tauri-bridge.ts` lives in the renderer tree.
 */
import { ipcRenderer } from "electron";

/**
 * The common bubble-bridge methods exposed by both Electron preloads.
 *
 * The Tauri-installed bridge (`tauri-bridge.ts`) provides the same
 * shape via Tauri APIs; see the TODO Fix-M note in the file header.
 */
export interface SharedBubbleApi {
	onLevel: (
		callback: (data: { rms: number; peak: number }) => void,
	) => () => void;
	show: () => void;
	signalReady: () => void;
	setPosition: (position: "top" | "bottom") => void;
	setDraggable: (draggable: boolean) => void;
	moveBy: (deltaX: number, deltaY: number) => void;
	onShow: (callback: () => void) => () => void;
	onHide: (callback: () => void) => () => void;
	onDraggable: (callback: (draggable: boolean) => void) => () => void;
	hideComplete: () => void;
}

/**
 * Build the shared `bubble` namespace methods. Both Electron preloads
 * call this and spread the result into their `contextBridge.exposeInMainWorld`
 * object. The returned methods close over `ipcRenderer` directly.
 */
export function makeSharedBubbleApi(): SharedBubbleApi {
	return {
		onLevel: (callback: (data: { rms: number; peak: number }) => void) => {
			const handler = (_event: Electron.IpcRendererEvent, data: unknown) =>
				callback(data as { rms: number; peak: number });
			ipcRenderer.on("bubble:level", handler);
			return () => {
				ipcRenderer.removeListener("bubble:level", handler);
			};
		},
		show: () => {
			ipcRenderer.send("bubble:show-from-renderer");
		},
		signalReady: () => {
			ipcRenderer.send("bubble:ready");
		},
		setPosition: (position: "top" | "bottom") => {
			ipcRenderer.send("set_bubble_position", position);
		},
		setDraggable: (draggable: boolean) => {
			ipcRenderer.send("bubble:draggable", draggable);
		},
		// NEW-A11Y-006: keyboard-based move (accessibility alternative
		// to native CSS drag). Main process clamps to screen bounds.
		moveBy: (deltaX: number, deltaY: number) => {
			ipcRenderer.send("bubble:move-by", { deltaX, deltaY });
		},
		// ── Enter/exit animations ────────────────────────────────
		onShow: (callback: () => void) => {
			const handler = () => callback();
			ipcRenderer.on("bubble:show", handler);
			return () => {
				ipcRenderer.removeListener("bubble:show", handler);
			};
		},
		onHide: (callback: () => void) => {
			const handler = () => callback();
			ipcRenderer.on("bubble:hide", handler);
			return () => {
				ipcRenderer.removeListener("bubble:hide", handler);
			};
		},
		onDraggable: (callback: (draggable: boolean) => void) => {
			const handler = (_event: Electron.IpcRendererEvent, draggable: unknown) =>
				callback(Boolean(draggable));
			ipcRenderer.on("bubble:draggable", handler);
			return () => {
				ipcRenderer.removeListener("bubble:draggable", handler);
			};
		},
		hideComplete: () => {
			ipcRenderer.send("bubble:hidden");
		},
	};
}
