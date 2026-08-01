/**
 * Tests for the Tauri bridge detection logic (Phase 3 UI port).
 *
 * ADR-0020 §6.3: `tauri-bridge.ts` auto-installs `window.python`,
 * `window.bubble`, and `window.window_` using Tauri's global
 * `__TAURI__` API when running inside a Tauri WebView, and is a no-op
 * in Electron mode (where the Electron preload already installed the
 * same namespaces via `contextBridge.exposeInMainWorld`).
 *
 * These tests verify the three contract guarantees the renderer relies
 * on so `usePython.ts` (and every page/component) works unchanged on
 * both runtimes:
 *  1. In Tauri mode, `window.python.call({type, data})` routes to
 *     `invoke('dispatch', {cmd: type, data})`.
 *  2. In Electron mode, the module is a no-op — it does NOT override
 *     the namespaces the preload installed.
 *  3. In Tauri mode, `window.bubble.onLevel(cb)` registers a Tauri
 *     event listener on the `bubble_level` channel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Minimal stub of `window.__TAURI__` matching the shape consumed by
// tauri-bridge.ts (core.invoke, event.listen, window.getCurrentWindow).
// The real shape is injected by `tauri::Builder` when
// `app.withGlobalTauri = true` (see tauri.conf.json).
//
// The `label` option controls which Tauri window the stub reports as.
// The bubble-namespace installer uses this label to decide whether to
// install the full BubbleWindowBubble API (label "bubble") or only the
// MainRendererBubbleMutators subset (label "main" — the SEC-026 split).
// Tests that exercise bubble-only event subscriptions (onLevel / onShow /
// onHide / onDraggable) MUST pass `{ label: "bubble" }` so the installer
// returns the full bubble namespace.
function makeTauriStub(options: { label?: "main" | "bubble" } = {}) {
	const { label = "main" } = options;
	return {
		core: {
			invoke: vi.fn(() => Promise.resolve({})),
		},
		event: {
			listen: vi.fn(() => Promise.resolve(() => {})),
		},
		window: {
			getCurrentWindow: vi.fn(() => ({
				label,
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
// because that would tighten the types past what these stub tests need.
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
	};
	window_?: {
		minimize: () => void;
	};
}

describe("tauri-bridge detection", () => {
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
		// `install.ts` module — importing `@/lib/tauri-bridge` alone no
		// longer triggers the installer; tests below also import
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

	it("installs window.python and routes calls to invoke('dispatch', ...) in Tauri mode", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		// Auto-install runs at module import time (the side-effect
		// call at the bottom of `install.ts`: `installTauriBridge();`).
		//the side effect moved out of `index.ts` into the
		// sibling `install.ts` module, so we import that explicitly
		// to trigger it. `index.ts` alone is now side-effect-free.
		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const python = (window as unknown as WindowBridgeState).python;
		expect(python).toBeDefined();
		expect(typeof python?.call).toBe("function");

		// A typical get_config dispatch — the renderer uses this shape
		// via `usePython().call("get_config")`.
		await python?.call({ type: "get_config" });

		expect(stub.core.invoke).toHaveBeenCalledTimes(1);
		expect(stub.core.invoke).toHaveBeenCalledWith("dispatch", {
			cmd: "get_config",
			data: {},
		});
	});

	it("passes `data` through when provided and defaults to {} when omitted", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const python = (window as unknown as WindowBridgeState).python;
		await python?.call({ type: "set_config", data: { theme: "nord" } });

		expect(stub.core.invoke).toHaveBeenCalledWith("dispatch", {
			cmd: "set_config",
			data: { theme: "nord" },
		});
	});

	it("is a no-op in Electron mode (does not override existing window.python)", async () => {
		// Simulate the Electron preload having already installed the
		// three namespaces via contextBridge.exposeInMainWorld.
		const electronPython = {
			call: vi.fn(() => Promise.resolve({ type: "result", data: {} })),
			onEvent: vi.fn(() => () => {}),
		};
		const electronBubble = { onLevel: vi.fn(() => () => {}) };
		const electronWindow = { minimize: vi.fn() };
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

		// The Electron-installed namespaces must be untouched.
		expect(w.python).toBe(electronPython);
		expect(w.bubble).toBe(electronBubble);
		expect(w.window_).toBe(electronWindow);
	});

	it("registers a Tauri event listener for window.bubble.onLevel in Tauri mode", async () => {
		// The bubble namespace's event subscriptions (onLevel / onShow /
		// onHide / onDraggable) are ONLY installed on the bubble window
		// (SEC-026 — the main renderer gets only the 5 shared mutators).
		// Pass `{ label: "bubble" }` so the installer returns the full
		// BubbleWindowBubble shape with onLevel present.
		const stub = makeTauriStub({ label: "bubble" });
		(window as unknown as WindowBridgeState).__TAURI__ = stub;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		const bubble = (window as unknown as WindowBridgeState).bubble;
		expect(bubble).toBeDefined();
		expect(typeof bubble?.onLevel).toBe("function");

		// Subscribe — should synchronously register a Tauri event listener
		// on the `bubble_level` channel (the Rust host coalesces this to
		// ≤30 Hz before emitting — see main.rs:427-442).
		const cb = vi.fn();
		bubble?.onLevel(cb);

		expect(stub.event.listen).toHaveBeenCalledWith(
			"bubble_level",
			expect.any(Function),
		);
	});

	it("does not install anything when window.__TAURI__ is present but lacks core.invoke", async () => {
		// Defensive: a partial / future Tauri global without the invoke
		// method should be treated as Electron (no-op), not crash.
		(window as unknown as { __TAURI__?: unknown }).__TAURI__ = {
			event: { listen: vi.fn() },
		};

		// test-setup.ts installs a default no-op `window.bubble` stub
		// so components that touch it at import time don't crash. Clear
		// the pre-existing stubs so we can assert the bridge did NOT
		// install its own namespaces (the assertion below would otherwise
		// see the test-setup default and fail).
		const w = window as unknown as WindowBridgeState;
		delete w.python;
		delete w.bubble;
		delete w.window_;

		await import("@/lib/tauri-bridge");
		await import("@/lib/tauri-bridge/install");

		// No namespaces should have been installed by the bridge.
		expect(w.python).toBeUndefined();
		expect(w.bubble).toBeUndefined();
		expect(w.window_).toBeUndefined();
	});
});
