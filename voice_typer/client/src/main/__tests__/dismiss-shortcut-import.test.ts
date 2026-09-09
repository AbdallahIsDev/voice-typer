// @vitest-environment node
/**
 * Contract tests for the cross-process bubble-dismiss shortcut binding.
 *
 * Background
 * ----------
 * The dismiss-bubble shortcut used to be defined twice, once per
 * process, linked only by comments:
 *   - accelerator form (`"CommandOrControl+Shift+D"`) in
 *     `main/shortcuts/global-shortcuts.ts`,
 *   - display form (`"Ctrl+Shift+D"`) in the renderer's hotkey
 *     catalog (`renderer/src/components/hotkey/shortcuts.ts`).
 *
 * A future shortcut change on one side would silently desync main
 * vs renderer. The canonical binding now lives in
 * `src/shared/dismiss-shortcut.ts` (`{ accelerator, display }`),
 * imported by BOTH processes (same convention as
 * `src/shared/python-call-error-code.ts`).
 *
 * Test strategy
 * -------------
 *   (a) The shared module exists, exports `DISMISS_SHORTCUT`, and
 *       pins both forms.
 *   (b) Source-text: the main process imports the shared constant
 *       (no local accelerator literal).
 *   (c) Source-text: the renderer catalog imports the shared
 *       constant (no local display literal).
 *   (d) Runtime: the main process's exported accelerator equals the
 *       shared constant (Electron + deps mocked).
 *
 * The renderer-side runtime pin lives in
 * `renderer/src/components/hotkey/__tests__/shortcuts.test.ts`
 * (`SHORTCUTS.dismissBubble.keys === DISMISS_SHORTCUT.display`) —
 * main-process tests avoid cross-tsconfig-project imports (see
 * `i18n-locale-contract.test.ts` for the same convention).
 *
 * ON LINUX (sandbox): source-text + runtime checks.
 * ON WINDOWS / macOS: same contract — the binding strings are
 * platform-agnostic.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

// --- Mocks so importing global-shortcuts.ts (→ bubble-handlers →
// electron/python/state) does not pull in the real Electron runtime.
// Mirrors global-shortcuts.test.ts.

vi.mock("electron", () => ({
	globalShortcut: {
		register: vi.fn(() => true),
		unregister: vi.fn(),
	},
	ipcMain: { on: vi.fn(), handle: vi.fn() },
}));

vi.mock("../constants", () => ({
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
}));

vi.mock("../logging", () => ({
	log: { warn: vi.fn(), info: vi.fn(), error: vi.fn(), debug: vi.fn() },
}));

vi.mock("../python", () => ({
	sendToPython: vi.fn(() => Promise.resolve()),
}));

vi.mock("../state", () => ({
	state: { bubbleWindow: null, bubbleDraggable: true, bubblePosition: "top" },
}));

vi.mock("../windows/bubble-window", () => ({
	cancelScheduledDurablePersist: vi.fn(),
	centerOnActiveDisplay: vi.fn(() => ({ x: 0, y: 0 })),
	consumeHideAnimationCallback: vi.fn(() => null),
	hideBubbleWindow: vi.fn(),
	resetSavedBubblePosition: vi.fn(),
	showBubbleWindow: vi.fn(),
	suppressDurablePersistFor: vi.fn(),
}));

import { DISMISS_SHORTCUT } from "../../shared/dismiss-shortcut";
import { BUBBLE_DISMISS_ACCELERATOR } from "../shortcuts/global-shortcuts";

// ────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────

function readSrc(rel: string): string {
	return fs.readFileSync(path.resolve(__dirname, rel), "utf-8");
}

// ────────────────────────────────────────────────────────────────────
// Shared constant contract
// ────────────────────────────────────────────────────────────────────

describe("shared DISMISS_SHORTCUT is the canonical bubble-dismiss binding", () => {
	it("the shared module file exists at src/shared/dismiss-shortcut.ts", () => {
		const sharedPath = path.resolve(
			__dirname,
			"../../shared/dismiss-shortcut.ts",
		);
		expect(fs.existsSync(sharedPath)).toBe(true);
	});

	it("pins the Electron accelerator form (main process registration)", () => {
		expect(DISMISS_SHORTCUT.accelerator).toBe("CommandOrControl+Shift+D");
	});

	it("pins the renderer display form (keycap-chip string, HotkeyChips path)", () => {
		expect(DISMISS_SHORTCUT.display).toBe("Ctrl+Shift+D");
	});
});

// ────────────────────────────────────────────────────────────────────
// Main process consumes the shared constant
// ────────────────────────────────────────────────────────────────────

describe("main/shortcuts/global-shortcuts.ts consumes the shared constant", () => {
	it("source: imports DISMISS_SHORTCUT from the shared module", () => {
		const src = readSrc("../shortcuts/global-shortcuts.ts");
		expect(src).toMatch(
			/import\s+\{\s*DISMISS_SHORTCUT\s*\}\s+from\s+["']\.\.\/\.\.\/shared\/dismiss-shortcut["']/,
		);
	});

	it("source: no local accelerator literal (the string lives only in the shared module)", () => {
		const src = readSrc("../shortcuts/global-shortcuts.ts");
		expect(src).not.toContain('"CommandOrControl+Shift+D"');
	});

	it("runtime: BUBBLE_DISMISS_ACCELERATOR equals the shared accelerator", () => {
		expect(BUBBLE_DISMISS_ACCELERATOR).toBe(DISMISS_SHORTCUT.accelerator);
		expect(BUBBLE_DISMISS_ACCELERATOR).toBe("CommandOrControl+Shift+D");
	});
});

// ────────────────────────────────────────────────────────────────────
// Renderer consumes the shared constant
// ────────────────────────────────────────────────────────────────────

describe("renderer hotkey catalog consumes the shared constant", () => {
	it("source: imports DISMISS_SHORTCUT from the shared module", () => {
		const src = readSrc("../../renderer/src/components/hotkey/shortcuts.ts");
		expect(src).toMatch(
			/import\s+\{\s*DISMISS_SHORTCUT\s*\}\s+from\s+["']\.\.\/\.\.\/\.\.\/\.\.\/shared\/dismiss-shortcut["']/,
		);
	});

	it("source: the dismissBubble catalog entry's keys field is the shared display form", () => {
		const src = readSrc("../../renderer/src/components/hotkey/shortcuts.ts");
		// The entry must reference the shared constant, not a literal.
		expect(src).toMatch(/keys:\s*DISMISS_SHORTCUT\.display/);
		expect(src).not.toContain('keys: "Ctrl+Shift+D"');
	});
});
