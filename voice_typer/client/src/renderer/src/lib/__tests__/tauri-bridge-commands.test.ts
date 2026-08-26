/**
 * Tests for the Tauri bridge command wiring ( + ).
 *
 * ADR-0020 §2 (bridge parity), §6 (bubble), §16 (frozen contract).
 *
 * These tests verify that `tauri-bridge.ts` correctly invokes the
 * Rust host commands added alongside each bridge method:
 *
 *   (export commands):
 *   - `window.window_.exportHistory(data, format)` → `invoke('export_history', { data, format })`
 *   - `window.window_.exportVocabulary(data, format)` → `invoke('export_vocabulary', { data, format })`
 *
 *   (locale push):
 *   - `window.window_.setLocale(locale)` → `invoke('set_host_locale', { locale })`
 *     (Electron `i18n:set-locale` parity — the Rust host stores the
 *      value in `SidecarState::host_locale`)
 *
 *   (bubble commands):
 *   - `window.bubble.show()` → `invoke('bubble_show')`
 *   - `window.bubble.signalReady()` → `invoke('bubble_signal_ready')`
 *   - `window.bubble.setPosition(position)` → `invoke('bubble_set_position', { x: position, y: position })`
 *     (: `position: "top" | "bottom"` — the Rust host takes
 *      `(x: Value, y: Value)` and resolves the strings to absolute
 *      physical coords based on monitor bounds)
 *   - `window.bubble.setDraggable(draggable)` → `invoke('bubble_set_draggable', { draggable })`
 *   - `window.bubble.moveBy(dx, dy)` → `invoke('bubble_move_by', { dx, dy })`
 *   - `window.bubble.hideComplete()` → `invoke('bubble_hide_complete')`
 *
 * The bridge uses `window.__TAURI__.core.invoke` (Tauri v2 global API
 * when `withGlobalTauri: true` in `tauri.conf.json`). We mock that
 * global with `vi.fn()` and assert each bridge method invokes it with
 * the expected command name and argument envelope.
 *
 * The test also verifies the Electron-mode no-op contract: when
 * `window.__TAURI__` is absent (Electron runtime), the bridge MUST NOT
 * override the namespaces already installed by the Electron preload
 * (`window.python`, `window.bubble`, `window.window_`). This is the
 * Phase 3 UI port invariant — the renderer code is identical on both
 * paths because the bridge auto-installs the right namespace.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Minimal stub of `window.__TAURI__` matching the shape consumed by
// tauri-bridge.ts (core.invoke, event.listen, window.getCurrentWindow).
// The real shape is injected by `tauri::Builder` when
// `app.withGlobalTauri = true` (see tauri.conf.json). We deliberately
// do NOT import `@tauri-apps/api/core` (the bridge avoids that dep to
// keep the bundle lean); instead we mock the global `invoke` directly.
function makeTauriStub() {
	return {
		core: {
			invoke: vi.fn(() => Promise.resolve({})),
		},
		event: {
			listen: vi.fn(() => Promise.resolve(() => {})),
		},
		window: {
			getCurrentWindow: vi.fn(() => ({
				minimize: vi.fn(() => Promise.resolve()),
				toggleMaximize: vi.fn(() => Promise.resolve()),
				close: vi.fn(() => Promise.resolve()),
				isMaximized: vi.fn(() => Promise.resolve(false)),
				onResized: vi.fn(() => Promise.resolve(() => {})),
			})),
		},
	};
}

// Structural shape of `window` for the properties the bridge touches.
// We don't import the real `Window` augmentation from `@/types/ipc`
// because the test asserts the runtime invoke-payload shape, not the
// TS type-level contract.
interface WindowBridgeState {
	__TAURI__?: ReturnType<typeof makeTauriStub>;
	python?: {
		call: (msg: {
			type: string;
			data?: Record<string, unknown>;
		}) => Promise<unknown>;
		onEvent: (cb: (e: unknown) => void) => () => void;
	};
	bubble?: {
		onLevel: (cb: (d: { rms: number; peak: number }) => void) => () => void;
		show?: () => void;
		signalReady?: () => void;
		setPosition?: (position: "top" | "bottom") => void;
		setDraggable?: (draggable: boolean) => void;
		moveBy?: (dx: number, dy: number) => void;
		hideComplete?: () => void;
		//`dismiss` is now wired in the Tauri bridge
		// (invoke("bubble_dismiss")). Optional here because the
		// main-renderer bridge (label "main") doesn't install
		// it — the bubble-dismiss test below overrides
		// `getCurrentWindow` to return `label: "bubble"` so the
		// full BubbleWindowBubble (including dismiss) is installed.
		dismiss?: () => void;
	};
	window_?: {
		minimize: () => void;
		exportHistory?: (
			data: Record<string, unknown>[],
			format: "json" | "csv",
		) => Promise<{ success: boolean; path?: string; error?: string }>;
		exportVocabulary?: (
			data: Record<string, unknown>,
			format: "json" | "csv",
		) => Promise<{ success: boolean; path?: string; error?: string }>;
		setLocale?: (locale: string) => Promise<{
			ok: boolean;
			error?: string;
		}>;
	};
}

describe("tauri-bridge commands (MIG-1.1 + MIG-1.2)", () => {
	let original: WindowBridgeState;

	beforeEach(() => {
		// Snapshot existing window state so we can restore between tests
		// (jsdom persists window across tests in the same file).
		const w = window as unknown as WindowBridgeState;
		original = {
			__TAURI__: w.__TAURI__,
			python: w.python,
			bubble: w.bubble,
			window_: w.window_,
		};
		// Reset module registry so the auto-install side effect runs
		// fresh on each `await import("@/lib/tauri-bridge/install")`.
		//(: the side effect moved from `index.ts` to the sibling
		// `install.ts` module — tests below also import
		// `@/lib/tauri-bridge/install` for the side effect.)
		vi.resetModules();
	});

	afterEach(() => {
		const w = window as unknown as WindowBridgeState;
		if (original.__TAURI__ === undefined) {
			delete w.__TAURI__;
		} else {
			w.__TAURI__ = original.__TAURI__;
		}
		if (original.python === undefined) {
			delete w.python;
		} else {
			w.python = original.python;
		}
		if (original.bubble === undefined) {
			delete w.bubble;
		} else {
			w.bubble = original.bubble;
		}
		if (original.window_ === undefined) {
			delete w.window_;
		} else {
			w.window_ = original.window_;
		}
		vi.restoreAllMocks();
	});

	//export commands ────────────────────────────────

	it("exportHistory invokes 'export_history' with { data, format }", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const windowBridge = (window as unknown as WindowBridgeState).window_;
		expect(windowBridge?.exportHistory).toBeDefined();

		const data = [
			{ id: 1, text: "hello" },
			{ id: 2, text: "world" },
		];
		// Default the mock to a success-with-path shape so the
		// `await` resolves cleanly (the test only asserts the invoke
		// payload, not the return shape — that's covered separately).
		stub.core.invoke.mockResolvedValueOnce({
			success: true,
			path: "/tmp/history.json",
		});
		await windowBridge?.exportHistory?.(data, "json");

		expect(stub.core.invoke).toHaveBeenCalledWith("export_history", {
			data,
			format: "json",
		});
	});

	it("exportVocabulary invokes 'export_vocabulary' with { data, format }", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const windowBridge = (window as unknown as WindowBridgeState).window_;
		expect(windowBridge?.exportVocabulary).toBeDefined();

		const data = { entries: [{ original: "teh", correction: "the" }] };
		stub.core.invoke.mockResolvedValueOnce({
			success: true,
			path: "/tmp/vocab.csv",
		});
		await windowBridge?.exportVocabulary?.(data, "csv");

		expect(stub.core.invoke).toHaveBeenCalledWith("export_vocabulary", {
			data,
			format: "csv",
		});
	});

	it("exportHistory maps Rust `{canceled: true}` to `{success: false}` (Electron parity)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const windowBridge = (window as unknown as WindowBridgeState).window_;
		stub.core.invoke.mockResolvedValueOnce({ canceled: true });
		const result = await windowBridge?.exportHistory?.([], "json");

		// Electron's `history:export` IPC handler returns
		// `{success: false}` (no path, no error) when the user
		// dismisses the save dialog. The Tauri bridge must map
		// `{canceled: true}` to the same shape so the renderer
		// (History.tsx export button) treats cancel identically on
		// both paths.
		expect(result).toEqual({ success: false });
	});

	it("exportHistory maps Rust throw to `{success: false, error}` (Electron parity)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const windowBridge = (window as unknown as WindowBridgeState).window_;
		stub.core.invoke.mockRejectedValueOnce(new Error("disk full"));
		const result = await windowBridge?.exportHistory?.([], "json");

		expect(result).toEqual({ success: false, error: "disk full" });
	});

	//bubble commands ────────────────────────────────

	it("bubble.show invokes 'bubble_show' with no args", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.show).toBeDefined();
		bubble?.show?.();

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_show");
	});

	it("bubble.signalReady invokes 'bubble_signal_ready' with no args", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.signalReady).toBeDefined();
		bubble?.signalReady?.();

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_signal_ready");
	});

	it("bubble.setPosition invokes 'bubble_set_position' with { x, y } (XPLAT-6)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.setPosition).toBeDefined();
		//setPosition takes a single string ("top" | "bottom"),
		// matching the MainRendererBubbleMutators.setPosition?: (pos: string) => void
		// contract. The Rust host's signature is
		// `bubble_set_position(x: Value, y: Value)` — Tauri v2 rejects
		// a `{ position }` payload (missing required args), so the
		// bridge forwards the string as BOTH x and y. The Rust host
		// resolves "top"/"bottom" to absolute physical coords based on
		// the primary monitor's bounds (x → centered, y → 0 for "top";
		// see src-tauri/src/commands/bubble.rs). Both production call
		// sites (`useConnection.ts:117` and
		// `GeneralSettingsSection.tsx:151`) pass a single string.
		bubble?.setPosition?.("top");

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_set_position", {
			position: "top",
		});
	});

	it("bubble.setPosition forwards 'bottom' as { x, y } (XPLAT-6 — string shape used by production)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.setPosition).toBeDefined();
		// Production's second call shape — GeneralSettingsSection.tsx:151
		// passes `"bottom"` when the user picks the bottom anchor from the
		// bubble-position dropdown. The bridge forwards the string as
		// both x and y so the Rust host can resolve it to
		// `y = monitor.height - bubble.height`.
		bubble?.setPosition?.("bottom");

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_set_position", {
			position: "bottom",
		});
	});

	it("bubble.setDraggable invokes 'bubble_set_draggable' with { draggable }", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.setDraggable).toBeDefined();
		bubble?.setDraggable?.(true);

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_set_draggable", {
			draggable: true,
		});
	});

	it("bubble.moveBy invokes 'bubble_move_by' with { dx, dy } (renamed from deltaX/deltaY)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.moveBy).toBeDefined();
		//keyboard-based move uses relative deltas. The
		// renderer calls `moveBy(deltaX, deltaY)`; the bridge renames
		// to the snake_case Rust convention `{dx, dy}` so the Rust
		// command signature matches the rest of the host API.
		bubble?.moveBy?.(10, -5);

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_move_by", {
			dx: 10,
			dy: -5,
		});
	});

	it("bubble.hideComplete invokes 'bubble_hide_complete' with no args", async () => {
		// SEC-026: `hideComplete` is bubble-window-only (it's on
		// `BubbleWindowExtras`, not `MainRendererBubbleMutators`), so
		// the default `makeTauriStub` (window label "main") would NOT
		// install it. Override `getCurrentWindow` to return
		// `label: "bubble"` so the bridge installs the full
		// `BubbleWindowBubble` (including `hideComplete`) — same
		// pattern as the bubble.dismiss test below.
		const stub = makeTauriStub();
		stub.window.getCurrentWindow = vi.fn(() => ({
			label: "bubble",
			minimize: vi.fn(() => Promise.resolve()),
			toggleMaximize: vi.fn(() => Promise.resolve()),
			close: vi.fn(() => Promise.resolve()),
			isMaximized: vi.fn(() => Promise.resolve(false)),
			onResized: vi.fn(() => Promise.resolve(() => {})),
		}));
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.hideComplete).toBeDefined();
		bubble?.hideComplete?.();

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_hide_complete");
	});

	it("bubble.dismiss invokes 'bubble_dismiss' with no args (UE-14)", async () => {
		//the Tauri bridge now wires `dismiss` to
		// `invoke("bubble_dismiss")`. The `dismiss` method is
		// bubble-window-only (it's on `BubbleWindowExtras`, not
		// `MainRendererBubbleMutators`), so the default
		// `makeTauriStub` (which returns a window without a
		// `label`, defaulting to "main") would NOT install it.
		// Override `getCurrentWindow` to return `label: "bubble"`
		// so the bridge installs the full `BubbleWindowBubble`
		// (including `dismiss`). This mirrors how the real
		// bubble window's bridge is installed in production
		// (`installTauriBridge()` in index.ts reads
		// `getCurrentWindow().label` and passes "bubble" to
		// `createBubbleNamespace` when the label is "bubble").
		const stub = makeTauriStub();
		stub.window.getCurrentWindow = vi.fn(() => ({
			label: "bubble",
			minimize: vi.fn(() => Promise.resolve()),
			toggleMaximize: vi.fn(() => Promise.resolve()),
			close: vi.fn(() => Promise.resolve()),
			isMaximized: vi.fn(() => Promise.resolve(false)),
			onResized: vi.fn(() => Promise.resolve(() => {})),
		}));
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble?.dismiss).toBeDefined();
		bubble?.dismiss?.();

		expect(stub.core.invoke).toHaveBeenCalledWith("bubble_dismiss");
	});

	//locale push ─────────────────────────────────────

	it("window_.setLocale invokes 'set_host_locale' with { locale } and passes the envelope through", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const windowBridge = (window as unknown as WindowBridgeState).window_;
		expect(windowBridge?.setLocale).toBeDefined();

		stub.core.invoke.mockResolvedValueOnce({ ok: true });
		const result = await windowBridge?.setLocale?.("fr-FR");

		expect(stub.core.invoke).toHaveBeenCalledWith("set_host_locale", {
			locale: "fr-FR",
		});
		expect(result).toEqual({ ok: true });
	});

	it("window_.setLocale maps a rejection to { ok: false, error } (never throws)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const windowBridge = (window as unknown as WindowBridgeState).window_;
		stub.core.invoke.mockRejectedValueOnce(new Error("command unavailable"));
		const result = await windowBridge?.setLocale?.("de-DE");

		expect(stub.core.invoke).toHaveBeenCalledWith("set_host_locale", {
			locale: "de-DE",
		});
		expect(result).toEqual({ ok: false, error: "command unavailable" });
	});

	//Electron-mode no-op (Phase 3 UI port invariant) ──

	it("is a no-op in Electron mode (does not override existing window.python/bubble/window_)", async () => {
		// Simulate the Electron preload having already installed the
		// three namespaces via contextBridge.exposeInMainWorld.
		const electronPython = {
			call: vi.fn(() => Promise.resolve({ type: "result", data: {} })),
			onEvent: vi.fn(() => () => {}),
		};
		const electronBubble = {
			onLevel: vi.fn(() => () => {}),
			show: vi.fn(),
			signalReady: vi.fn(),
			setPosition: vi.fn(),
			setDraggable: vi.fn(),
			moveBy: vi.fn(),
			hideComplete: vi.fn(),
		};
		const electronWindow = {
			minimize: vi.fn(),
			exportHistory: vi.fn(() =>
				Promise.resolve({ success: true, path: "/tmp/x.json" }),
			),
			exportVocabulary: vi.fn(() =>
				Promise.resolve({ success: true, path: "/tmp/v.csv" }),
			),
		};
		const w = window as unknown as WindowBridgeState;
		w.python = electronPython;
		w.bubble = electronBubble;
		w.window_ = electronWindow;
		// Ensure no Tauri global is present (Electron path).
		delete w.__TAURI__;

		// Importing the install side-effect module triggers
		// `installTauriBridge()`, but isTauri() returns false so it
		// should bail out immediately without touching the existing
		// namespaces.
		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		// The Electron-installed namespaces must be untouched (same
		// referential identity — not replaced, not wrapped).
		expect(w.python).toBe(electronPython);
		expect(w.bubble).toBe(electronBubble);
		expect(w.window_).toBe(electronWindow);
	});
});
