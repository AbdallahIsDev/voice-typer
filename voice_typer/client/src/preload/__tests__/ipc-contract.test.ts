// @vitest-environment node
/**
 * : shared channel-name contract tests for the preload layer.
 *
 * The preload files (`preload/index.ts` + `preload/bubble.ts` +
 * `preload/_bubble-channels.ts`) declare the renderer→main IPC
 * channel-name surface. The matching `ipcMain.on` / `ipcMain.handle`
 * listeners live in `src/main/ipc/` and `src/main/windows/`. A channel
 * rename on one side without the other is a silent breakage (the IPC
 * message is just dropped by Electron's default "no listener" behavior).
 *
 * This file pins the channel-name table so a rename on either side
 * surfaces here. It does NOT exercise the IPC behavior itself (that
 * requires a full Electron BrowserWindow + ipcMain surface, which is
 * covered by the per-handler test files in `src/main/__tests__/`).
 *
 * The test reads the source of:
 *   - `preload/index.ts` (main-renderer preload)
 *   - `preload/_bubble-channels.ts` (shared bubble-channel factory)
 *
 * and asserts every `ipcRenderer.invoke("X")` / `ipcRenderer.send("X")`
 * / `ipcRenderer.on("X")` channel name is present in the canonical
 * table below. Adding a new channel requires updating the table —
 * a deliberate speed bump so the main-side listener isn't forgotten.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Canonical channel-name table shared between preload (renderer→main)
 * and the matching `ipcMain.on` / `ipcMain.handle` listeners in
 * `src/main/`.
 *
 * Adding a new channel:
 *   1. Add it here (so this test stays green).
 *   2. Add the preload `ipcRenderer.invoke/send/on("X", ...)` call.
 *   3. Add the matching `ipcMain.handle/on("X", ...)` listener in
 *      `src/main/ipc/` or `src/main/windows/`.
 *
 * Removing a channel: do all three in reverse, in the same commit.
 */
const CANONICAL_CHANNELS: ReadonlySet<string> = new Set([
	// python-call (renderer → main → Python).
	"python-call",
	"python-event",
	// window controls.
	"window:minimize",
	"window:toggle-maximize",
	"window:close",
	"window:is-maximized",
	"window:maximized-changed",
	"window:open-logs",
	// exports.
	"history:export",
	"vocabulary:export",
	"templates:export",
	"config:export",
	// model import.
	"model:import-dialog",
	// i18n.
	"i18n:set-locale",
	// renderer error logging.
	"renderer:log-error",
	// bubble channels (shared between main + bubble preload).
	"bubble:level",
	"bubble:show-from-renderer",
	"bubble:ready",
	"bubble:set-position",
	"bubble:draggable",
	"bubble:show",
	"bubble:hide",
	"bubble:move-by",
	// bubble channels (restricted — bubble window only).
	"bubble:set-state",
	"bubble:config",
	"bubble:hidden",
	"bubble:resize",
	"bubble:toggle-dictation",
	"bubble:dismiss",
]);

/** Extract all `ipcRenderer.<method>("X", ...)` channel names from src. */
function extractChannels(src: string): string[] {
	const channels = new Set<string>();
	// Match ipcRenderer.invoke("X", ...) / ipcRenderer.send("X", ...) /
	// ipcRenderer.on("X", ...). The channel name is always the first
	// string-literal argument.
	const re =
		/ipcRenderer\.(?:invoke|send|on|once|removeListener)\s*\(\s*["']([^"']+)["']/g;
	let m: RegExpExecArray | null;
	m = re.exec(src);
	while (m !== null) {
		channels.add(m[1] as string);
		m = re.exec(src);
	}
	return [...channels];
}

describe("XS-78: preload ↔ main IPC channel-name contract", () => {
	const preloadIndexPath = path.resolve(__dirname, "../index.ts");
	const bubbleChannelsPath = path.resolve(__dirname, "../_bubble-channels.ts");

	it("preload/index.ts and preload/_bubble-channels.ts both exist", () => {
		expect(fs.existsSync(preloadIndexPath)).toBe(true);
		expect(fs.existsSync(bubbleChannelsPath)).toBe(true);
	});

	it("every channel referenced in preload/index.ts is in the canonical table", () => {
		const src = fs.readFileSync(preloadIndexPath, "utf-8");
		const channels = extractChannels(src);
		expect(channels.length).toBeGreaterThan(0);
		const unknown = channels.filter((c) => !CANONICAL_CHANNELS.has(c));
		expect(unknown).toEqual([]);
	});

	it("every channel referenced in preload/_bubble-channels.ts is in the canonical table", () => {
		const src = fs.readFileSync(bubbleChannelsPath, "utf-8");
		const channels = extractChannels(src);
		expect(channels.length).toBeGreaterThan(0);
		const unknown = channels.filter((c) => !CANONICAL_CHANNELS.has(c));
		expect(unknown).toEqual([]);
	});

	it("every channel in the canonical table is actually used by at least one preload file", () => {
		// Catches the opposite drift: a channel declared in the table
		// but never sent by the preload (dead channel). Either the
		// preload was supposed to use it (regression) or the table
		// entry is stale (cleanup).
		const indexSrc = fs.readFileSync(preloadIndexPath, "utf-8");
		const bubbleSrc = fs.readFileSync(bubbleChannelsPath, "utf-8");
		const used = new Set([
			...extractChannels(indexSrc),
			...extractChannels(bubbleSrc),
		]);
		const unused = [...CANONICAL_CHANNELS].filter((c) => !used.has(c));
		// Note: `python-event` is registered via `ipcRenderer.on` in
		// preload/index.ts (verified by extractChannels). If it appears
		// in `unused`, the regex missed it — check the preload source
		// before adjusting this assertion.
		expect(unused).toEqual([]);
	});
});
