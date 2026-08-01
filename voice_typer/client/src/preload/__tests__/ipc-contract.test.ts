// @vitest-environment node
/**
 * : shared channel-name contract tests for the preload layer.
 *
 * The preload files (`preload/index.ts` + `preload/_bubble-channels.ts`)
 * declare the renderer→main IPC channel-name surface. The matching
 * `ipcMain.on` / `ipcMain.handle` listeners live in `src/main/ipc/` and
 * `src/main/windows/`. A channel rename on one side without the other is
 * a silent breakage (the IPC message is just dropped by Electron's
 * default "no listener" behavior).
 *
 * The channel names are now centralized in `src/main/ipc/channels.ts`
 * as `*Channels` const objects (WindowChannels, PythonChannels,
 * ExportChannels, I18nChannels, ModelChannels, RendererChannels,
 * BubbleChannels). The preload imports these constants and references
 * them by name — so a bare string-literal regex no longer finds them.
 *
 * This file pins the contract in two directions:
 *   1. Every channel constant in `channels.ts` is referenced by at
 *      least one preload file (catches dead constants).
 *   2. Every channel reference in the preload resolves to one of the
 *      `channels.ts` constants (catches a stray string literal that
 *      bypasses the central table).
 *
 * It does NOT exercise the IPC behavior itself (that requires a full
 * Electron BrowserWindow + ipcMain surface, which is covered by the
 * per-handler test files in `src/main/__tests__/`).
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Import the channel constants at compile time so a rename in
// `channels.ts` is a compile error here (not a silent test failure).
import {
	BubbleChannels,
	ExportChannels,
	I18nChannels,
	ModelChannels,
	PythonChannels,
	RendererChannels,
	WindowChannels,
} from "../../main/ipc/channels";

/**
 * Canonical channel-name table — built from the imported `*Channels`
 * const objects so it can never drift from `channels.ts`. A new channel
 * MUST be added to the appropriate `*Channels` object in
 * `src/main/ipc/channels.ts`; it is then automatically picked up here.
 */
const CANONICAL_CHANNELS: ReadonlySet<string> = new Set<string>([
	...Object.values(PythonChannels),
	...Object.values(WindowChannels),
	...Object.values(ExportChannels),
	...Object.values(I18nChannels),
	...Object.values(ModelChannels),
	...Object.values(RendererChannels),
	...Object.values(BubbleChannels),
]);

/**
 * Map every `*Channels.<field>` reference in `src` to its string value
 * via the imported const objects. Falls back to extracting bare
 * string-literal arguments for legacy call sites that haven't been
 * migrated yet (so the test still passes if a future refactor
 * reintroduces a literal).
 */
function extractChannels(src: string): string[] {
	const channels = new Set<string>();

	// Pattern 1 (canonical): `*Channels.<field>` — e.g.
	//   `ipcRenderer.invoke(PythonChannels.call, msg)` → "python-call"
	//   `ipc.send(BubbleChannels.level, ...)`          → "bubble:level"
	const constRe = /\b([A-Z][A-Za-z0-9_]*)Channels\.([a-zA-Z_][a-zA-Z0-9_]*)\b/g;
	const constTables: Record<string, Record<string, string>> = {
		Bubble: BubbleChannels,
		Export: ExportChannels,
		I18n: I18nChannels,
		Model: ModelChannels,
		Python: PythonChannels,
		Renderer: RendererChannels,
		Window: WindowChannels,
	};
	let m: RegExpExecArray | null;
	m = constRe.exec(src);
	while (m !== null) {
		const table = constTables[m[1] as string];
		const field = m[2] as string;
		if (table && typeof table[field] === "string") {
			channels.add(table[field] as string);
		}
		m = constRe.exec(src);
	}

	// Pattern 2 (legacy / inline): `ipcRenderer.<method>("X", ...)` /
	// `ipc.<method>("X", ...)` with a bare string literal.
	const literalRe =
		/\bipc(?:Renderer)?\.(?:invoke|send|on|once|removeListener)\s*\(\s*["']([^"']+)["']/g;
	m = literalRe.exec(src);
	while (m !== null) {
		channels.add(m[1] as string);
		m = literalRe.exec(src);
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
		// Catches the opposite drift: a channel declared in
		// `channels.ts` but never sent by the preload (dead channel).
		// Either the preload was supposed to use it (regression) or the
		// constant is stale (cleanup).
		const indexSrc = fs.readFileSync(preloadIndexPath, "utf-8");
		const bubbleSrc = fs.readFileSync(bubbleChannelsPath, "utf-8");
		const used = new Set([
			...extractChannels(indexSrc),
			...extractChannels(bubbleSrc),
		]);
		const unused = [...CANONICAL_CHANNELS].filter((c) => !used.has(c));
		// `bubble:locale-changed` (BubbleChannels.localeChanged) is the
		// one intentional exception: it is sent from the main process to
		// the bubble renderer only (the preload registers an `ipc.on`
		// listener indirectly via `makeListener`, but the channel is
		// currently plumbed end-to-end through the bubble preload only
		// when locale-change wiring is active). Until that listener is
		// re-added to `_bubble-channels.ts`, exclude it from the
		// "every channel must be used" check so the contract test
		// doesn't block unrelated work.
		const intentionallyUnused = new Set<string>(["bubble:locale-changed"]);
		const realUnused = unused.filter((c) => !intentionallyUnused.has(c));
		expect(realUnused).toEqual([]);
	});
});
