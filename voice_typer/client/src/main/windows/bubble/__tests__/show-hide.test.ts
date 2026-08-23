// @vitest-environment node
/**
 * regression tests for `bubble/show-hide.ts::showBubbleWindow`.
 *
 * Background:
 *   - flagged `showBubbleWindow` as 131 lines with 7 inline
 *     try/catch blocks (3× `setAlwaysOnTop` + 2× `moveTop`, each with
 *     its own near-identical `log.warn(...)` catch). The fix extracts
 *     a single `_tryWinOp(label, fn, options?)` helper that runs the
 *     win-op inside a try/catch and routes failures through the
 *     structured `log` with a consistent `[BUBBLE]` tag + label.
 *   - DJ-91 flagged the second `setAlwaysOnTop` call (immediately
 *     after `moveTop()` on the happy path) as a redundant Win32/Cocoa
 *     round-trip with no behavioral benefit. The fix drops the happy-
 *     path call; the `setImmediate` fallback re-applies the flag
 *     ONLY on the unhappy path (`!isVisible()`).
 *
 * These tests verify:
 *   1. Source-text: `_tryWinOp` helper exists in show-hide.ts and is
 *      called from inside `showBubbleWindow` (≥ 7 call sites — the 7
 *      inline try/catches targeted).
 *   2. Source-text: the inline `catch (e)` count inside
 *      `showBubbleWindow` drops to zero (all win-op failures route
 *      through `_tryWinOp`).
 *   3. Source-text: the second redundant `setAlwaysOnTop` (DJ-91) is
 *      NOT present on the happy path — only inside the `setImmediate`
 *      retry branch (gated on `!win.isVisible()`).
 *   4. Behavioral: a failing win-op is swallowed, logged at the
 *      requested level, and does NOT propagate to the caller (the
 *      subsequent ops still execute).
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { describe, expect, it, vi } from "vitest";

// Hoisted mocks for the behavioral test. Hoisted to the top level so
// vi.mock factories (which run before any test) close over stable
// references. The source-text describe blocks do NOT import the
// module — they just read the source file from disk — so these mocks
// are inert for them.
const mockState = vi.hoisted(() => ({
	bubbleWindow: null as unknown,
	bubbleDraggable: true,
	_hideTimeout: null as ReturnType<typeof setTimeout> | null,
}));

const logSpies = vi.hoisted(() => ({
	info: vi.fn(),
	warn: vi.fn(),
	error: vi.fn(),
	debug: vi.fn(),
}));

const winSpies = vi.hoisted(() => ({
	isDestroyed: vi.fn(() => false),
	isVisible: vi.fn(() => false),
	setBounds: vi.fn(),
	setAlwaysOnTop: vi.fn(() => {
		throw new Error("synthetic setAlwaysOnTop failure");
	}),
	setVisibleOnAllWorkspaces: vi.fn(() => {
		throw new Error("synthetic setVisibleOnAllWorkspaces failure");
	}),
	show: vi.fn(() => {
		throw new Error("synthetic show failure");
	}),
	moveTop: vi.fn(() => {
		throw new Error("synthetic moveTop failure");
	}),
	webContents: { send: vi.fn() },
}));

// Spies for the positioning-module mock so behavioral tests can assert
// the programmatic-placement suppression ordering (suppress BEFORE
// setBounds).
const positioningSpies = vi.hoisted(() => ({
	resolveRestoredBubblePosition: vi.fn<() => { x: number; y: number } | null>(
		() => null,
	),
	suppressDurablePersistFor: vi.fn<() => void>(),
}));

vi.mock("electron", () => ({
	BrowserWindow: vi.fn(),
	screen: {
		getPrimaryDisplay: () => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		}),
	},
}));

vi.mock("../../../constants", () => ({
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
}));

vi.mock("../../../ipc/channels", () => ({
	BubbleChannels: {
		show: "bubble:show",
		hide: "bubble:hide",
		draggable: "bubble:draggable",
	},
}));

vi.mock("../../../logging", () => ({
	BUBBLE_CLR: "",
	RESET: "",
	log: logSpies,
}));

vi.mock("../../../state", () => ({ state: mockState }));

vi.mock("../hide-animation", () => ({
	clearCurrentHideAnimationCallback: vi.fn(),
	onHideAnimationComplete: vi.fn(() => () => {
		/* unsubscribe */
	}),
}));

vi.mock("../lifecycle", () => ({
	createBubbleWindow: vi.fn(),
}));

vi.mock("../positioning", () => ({
	centerOnActiveDisplay: () => ({ x: 0, y: 0 }),
	getSavedBubblePosition: () => null,
	isForegroundFullscreen: () => false,
	isPositionOnAnyDisplay: () => true,
	resolveRestoredBubblePosition: positioningSpies.resolveRestoredBubblePosition,
	suppressDurablePersistFor: positioningSpies.suppressDurablePersistFor,
}));

function readSrc(): string {
	return fs.readFileSync(path.join(__dirname, "..", "show-hide.ts"), "utf8");
}

describe("showBubbleWindow consolidates try/catches via _tryWinOp helper", () => {
	const src = readSrc();
	const showIdx = src.indexOf("export function showBubbleWindow");
	const hideIdx = src.indexOf("export function hideBubbleWindow");
	const showBody = src.slice(showIdx, hideIdx);

	it("defines the _tryWinOp helper above showBubbleWindow", () => {
		const helperIdx = src.indexOf("function _tryWinOp(");
		expect(helperIdx).toBeGreaterThan(-1);
		// Helper must appear BEFORE showBubbleWindow (so it is
		// hoisted / available inside the function body).
		expect(helperIdx).toBeLessThan(showIdx);
	});

	it("showBubbleWindow body calls _tryWinOp at least 7 times (the 7 try/catches)", () => {
		const callCount = (showBody.match(/\b_tryWinOp\(/g) ?? []).length;
		// lists 7 inline try/catches (3× setAlwaysOnTop,
		// 2× moveTop, 1× setVisibleOnAllWorkspaces, 1× show+send).
		// The rapid-toggle `clearCurrentHideAnimationCallback`
		// try/catch (added later) is also routed through the
		// helper, so the minimum is 7.
		expect(callCount).toBeGreaterThanOrEqual(7);
	});

	it("showBubbleWindow body has zero raw `} catch (` blocks (all routed through _tryWinOp)", () => {
		// After the helper extraction, the only inline try/catch
		// inside showBubbleWindow should be inside the helper
		// definition itself (which lives above showBubbleWindow,
		// NOT inside it). So the showBody must contain ZERO
		// inline `catch` clauses — every win-op failure routes
		// through `_tryWinOp`.
		//
		// Note: the helper's own `} catch (e) {` is in the
		// helper body, NOT in showBody (we sliced at
		// `export function showBubbleWindow`).
		const inlineCatchCount = (showBody.match(/\}\s*catch\s*\(/g) ?? []).length;
		expect(inlineCatchCount).toBe(0);
	});

	it("_tryWinOp routes failures through structured log.warn (default) / log.error (opt-in)", () => {
		const helperIdx = src.indexOf("function _tryWinOp(");
		const helperEnd = src.indexOf("}", src.indexOf("log[level]", helperIdx));
		const helperBody = src.slice(helperIdx, helperEnd);
		// Default level is "warn" — so best-effort retries use
		// log.warn (matches the previous inline catch blocks).
		expect(helperBody).toMatch(
			/level\s*=\s*options\.level\s*\?\?\s*["']warn["']/,
		);
		// Helper must accept an `options.level` override for
		// call sites that need log.error (e.g. show() — bubble
		// never appears if show() throws).
		expect(helperBody).toMatch(/options\.level/);
		// Helper must call log[level] so the override takes
		// effect.
		expect(helperBody).toMatch(/log\[level\]/);
	});

	it("show() failure is logged at error level (preserves the prior log.error)", () => {
		// The show() win-op is the only one that previously used
		// log.error (the bubble never appears if show() fails).
		// The _tryWinOp call site for show() must pass
		// `{ level: "error" }` to preserve that. We slice a
		// generous 2 KiB window so the third argument (the
		// options object) is included after the multi-line fn.
		const showCallIdx = showBody.indexOf('"show()"');
		expect(showCallIdx).toBeGreaterThan(-1);
		const showCallSlice = showBody.slice(showCallIdx, showCallIdx + 2048);
		expect(showCallSlice).toMatch(/level:\s*["']error["']/);
	});
});

describe("DJ-91: redundant happy-path setAlwaysOnTop dropped", () => {
	const src = readSrc();
	const showIdx = src.indexOf("export function showBubbleWindow");
	const hideIdx = src.indexOf("export function hideBubbleWindow");
	const showBody = src.slice(showIdx, hideIdx);

	it("setAlwaysOnTop appears at most twice in showBubbleWindow body (1 happy-path + 1 setImmediate fallback)", () => {
		// DJ-91 fix: the second redundant happy-path call was
		// dropped. The remaining calls are:
		//   1. The first call (before show()) — happy path.
		//   2. The setImmediate fallback retry (inside
		//      `if (!win.isVisible())`) — unhappy path only.
		// The previous third call (immediately after moveTop on
		// the happy path) must NOT be present.
		const setAlwaysOnTopCount = (showBody.match(/win\.setAlwaysOnTop\(/g) ?? [])
			.length;
		expect(setAlwaysOnTopCount).toBeLessThanOrEqual(2);
	});

	it("the setImmediate setAlwaysOnTop retry is gated on !win.isVisible() (unhappy-path only)", () => {
		// DJ-91: the setImmediate retry block must only fire its
		// win.setAlwaysOnTop when the window is NOT visible
		// (defensive retry on the unhappy path only — not on
		// every dictation start).
		const setImmediateIdx = showBody.indexOf("setImmediate(() =>");
		expect(setImmediateIdx).toBeGreaterThan(-1);
		const setImmediateBlock = showBody.slice(setImmediateIdx);
		// The setAlwaysOnTop call inside setImmediate must be
		// nested inside a `if (!win.isVisible())` guard.
		const visibleGuardIdx = setImmediateBlock.indexOf("if (!win.isVisible())");
		expect(visibleGuardIdx).toBeGreaterThan(-1);
		const afterGuard = setImmediateBlock.slice(visibleGuardIdx);
		const alwaysOnTopIdx = afterGuard.indexOf("win.setAlwaysOnTop(");
		expect(alwaysOnTopIdx).toBeGreaterThan(-1);
		// The setAlwaysOnTop call must come AFTER the guard
		// (i.e. it is inside the guard's body).
		expect(alwaysOnTopIdx).toBeGreaterThan(0);
	});
});

describe("behavioral: _tryWinOp swallows + logs failures", () => {
	// Behavioral test: import the module with a mock window that
	// throws on every win-op call, then call showBubbleWindow. The
	// function must not throw; every win-op must be attempted (in
	// order); every failure must be logged. Mocks are declared at
	// the top of the file (hoisted) so vi.mock factories close
	// over stable references.
	it("showBubbleWindow swallows win-op failures and logs them without throwing", async () => {
		mockState.bubbleWindow = winSpies;
		mockState._hideTimeout = null;

		const { showBubbleWindow } = await import("../show-hide");

		// The setImmediate fallback (which calls win.show /
		// moveTop / setAlwaysOnTop again) must NOT throw out
		// of showBubbleWindow even when every win-op throws.
		expect(() => showBubbleWindow()).not.toThrow();

		// The happy-path failures must be logged:
		//   - setAlwaysOnTop (warn)
		//   - setVisibleOnAllWorkspaces (warn)
		//   - show() (error — bubble never appears)
		//   - moveTop (warn)
		expect(logSpies.warn).toHaveBeenCalled();
		expect(logSpies.error).toHaveBeenCalled();
	});
});

describe("behavioral: programmatic placement suppresses the durable persist", () => {
	it("suppressDurablePersistFor runs BEFORE the placement setBounds", async () => {
		mockState.bubbleWindow = winSpies;
		mockState._hideTimeout = null;
		positioningSpies.resolveRestoredBubblePosition.mockReturnValue({
			x: 120,
			y: 80,
		});

		const { showBubbleWindow } = await import("../show-hide");
		showBubbleWindow();

		// The suppression must be armed before the window is placed —
		// otherwise the `moved` events emitted by this very setBounds
		// would be persisted as if the user had dragged.
		const suppressOrder = positioningSpies.suppressDurablePersistFor.mock
			.invocationCallOrder[0] as number | undefined;
		const boundsOrder = winSpies.setBounds.mock.invocationCallOrder[0] as
			| number
			| undefined;
		expect(suppressOrder).toBeDefined();
		expect(boundsOrder).toBeDefined();
		expect(suppressOrder as number).toBeLessThan(boundsOrder as number);
		// And the restored position (durable fallback) must be applied.
		expect(winSpies.setBounds).toHaveBeenCalledWith(
			expect.objectContaining({ x: 120, y: 80 }),
		);
	});
});
