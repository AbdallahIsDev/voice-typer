/**
 * @vitest-environment jsdom
 *
 * Regression test for the  split: the side effect that installs
 * `window.python` / `window.bubble` / `window.window_` MUST live in the
 * sibling `install.ts` module, NOT in `index.ts`.
 *
 * Background — the pre-split monolith auto-invoked
 * `installTauriBridge()` at the bottom of `tauri-bridge/index.ts`, so
 * any code that imported `@/lib/tauri-bridge` for its named exports
 * (`isTauri`, `makeListener`, etc.) — including unit tests under
 * `vi.resetModules()` isolation — got the namespace mutation as a
 * surprise side effect. The  prescription moved the auto-install
 * call into a dedicated `install.ts` so:
 *
 *   • Production entrypoints (`main.tsx`, `bubble-main.tsx`) import
 *     `@/lib/tauri-bridge/install` to opt back into the side effect.
 *   • Pure consumers of the named exports import `@/lib/tauri-bridge`
 *     alone and get no mutation.
 *
 * This test pins both halves of the contract:
 *   1. Importing `@/lib/tauri-bridge/install` triggers the bridge
 *      setup (installs `window.python`, `window.bubble`,
 *      `window.window_`) when `window.__TAURI__` is present.
 *   2. Importing `@/lib/tauri-bridge` alone does NOT install the
 *      namespaces (negative control — proves the side effect was
 *      actually moved, not duplicated).
 *   3. Importing `@/lib/tauri-bridge/install` is a no-op in Electron
 *      mode (when `window.__TAURI__` is absent) so the Electron
 *      preload-installed namespaces are left untouched.
 *
 * The test mirrors the structure of `tauri-bridge-detection.test.ts`
 * (same `makeTauriStub()` shape + same window-state snapshot/restore
 * pattern) so failures point clearly at the install-split contract
 * rather than at unrelated bridge behaviour.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Minimal stub of `window.__TAURI__` matching the shape consumed by
// the bridge (core.invoke, event.listen, window.getCurrentWindow).
// The real shape is injected by `tauri::Builder` when
// `app.withGlobalTauri = true` (see tauri.conf.json).
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
		show?: () => void;
	};
	window_?: {
		minimize: () => void;
	};
}

describe("tauri-bridge install side-effect module (ZR-30 split)", () => {
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
		// Reset module registry so the side effect runs fresh on each
		// `await import("@/lib/tauri-bridge/install")`.
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

	it("importing @/lib/tauri-bridge/install installs the three namespaces in Tauri mode", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;
		// test-setup.ts installs a default no-op `window.bubble` stub
		// (and not `window.python` / `window.window_`). Clear all
		// three so the assertion below sees only what the install
		// side effect writes.
		const w = window as unknown as WindowBridgeState;
		delete w.python;
		delete w.bubble;
		delete w.window_;

		await import("@/lib/tauri-bridge/install");

		// Re-fetch from `window` after the import so TypeScript doesn't
		// narrow `w.python` to `never` based on the `delete` above.
		const installed = window as unknown as WindowBridgeState;
		expect(installed.python).toBeDefined();
		expect(typeof installed.python?.call).toBe("function");
		expect(typeof installed.python?.onEvent).toBe("function");
		expect(installed.bubble).toBeDefined();
		// The default `makeTauriStub`'s `getCurrentWindow()` returns a
		// window without a `label`, so the bridge treats it as the main
		// renderer. `createBubbleNamespace(tauri, "main")` returns only
		// the shared mutators (SEC-026: `show` / `signalReady` /
		// `setPosition` / `setDraggable` / `moveBy`) — the bubble-only
		// event subscriptions (`onLevel` / `onShow` / ...) are gated
		// behind `label === "bubble"`. Asserting `show` (a shared
		// mutator) here proves the bubble namespace was installed
		// without coupling this test to the windowLabel-split contract
		// (which is `tauri-bridge-commands.test.ts`'s job).
		expect(typeof installed.bubble?.show).toBe("function");
		expect(installed.window_).toBeDefined();
		expect(typeof installed.window_?.minimize).toBe("function");
	});

	it("importing @/lib/tauri-bridge/install wires window.python.call to invoke('dispatch', ...)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;
		const w = window as unknown as WindowBridgeState;
		delete w.python;
		delete w.bubble;
		delete w.window_;

		await import("@/lib/tauri-bridge/install");

		// Re-fetch from `window` after the import so TypeScript doesn't
		// narrow `w.python` to `never` based on the `delete` above.
		const python = (window as unknown as WindowBridgeState).python;
		await python?.call({ type: "get_config" });
		expect(stub.core.invoke).toHaveBeenCalledWith("dispatch", {
			cmd: "get_config",
			data: {},
		});
	});

	it("importing @/lib/tauri-bridge alone does NOT install the namespaces (side effect lives in install.ts)", async () => {
		const stub = makeTauriStub();
		(window as unknown as WindowBridgeState).__TAURI__ = stub;
		const w = window as unknown as WindowBridgeState;
		delete w.python;
		delete w.bubble;
		delete w.window_;

		await import("@/lib/tauri-bridge");

		// No side effect should have fired — `index.ts` only exports
		// the named symbols and the installer function; it does NOT
		// auto-invoke it.
		expect(w.python).toBeUndefined();
		expect(w.bubble).toBeUndefined();
		expect(w.window_).toBeUndefined();
	});

	it("importing @/lib/tauri-bridge/install is a no-op in Electron mode (does not override existing namespaces)", async () => {
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

		await import("@/lib/tauri-bridge/install");

		// The Electron-installed namespaces must be untouched (same
		// referential identity — not replaced, not wrapped).
		expect(w.python).toBe(electronPython);
		expect(w.bubble).toBe(electronBubble);
		expect(w.window_).toBe(electronWindow);
	});

	it("production entrypoints source-assert the explicit install import", async () => {
		// Belt-and-suspenders static check: `main.tsx` and
		// `bubble-main.tsx` MUST import the install side-effect
		// module (NOT just `@/lib/tauri-bridge`). This catches a
		// future regression where someone reverts the import to
		// `./lib/tauri-bridge` and the bridge silently stops
		// installing in production. We read the source (rather than
		// importing the entrypoint) to avoid booting React inside a
		// unit test.
		const { readFileSync } = await import("node:fs");
		const { resolve } = await import("node:path");
		const mainSrc = readFileSync(resolve(__dirname, "../../main.tsx"), "utf-8");
		const bubbleSrc = readFileSync(
			resolve(__dirname, "../../bubble-main.tsx"),
			"utf-8",
		);
		expect(mainSrc).toMatch(/import\s+["']\.\/lib\/tauri-bridge\/install["']/);
		expect(bubbleSrc).toMatch(
			/import\s+["']\.\/lib\/tauri-bridge\/install["']/,
		);
		// Negative: ensure the bare `./lib/tauri-bridge` side-effect
		// import is gone (only named-import lines referencing
		// `@/lib/tauri-bridge` are allowed). Match a side-effect
		// import line `import "./lib/tauri-bridge";` (with optional
		// trailing semicolon) — must be absent.
		expect(mainSrc).not.toMatch(/import\s+["']\.\/lib\/tauri-bridge["'];?/);
		expect(bubbleSrc).not.toMatch(/import\s+["']\.\/lib\/tauri-bridge["'];?/);
	});
});
