// @vitest-environment node
/**
 *  (session NH) regression test — preload bridge surface for
 * the `i18n:set-locale` IPC channel.
 *
 *  added an `i18n:set-locale` IPC handler on the main-process side
 * (`main/ipc/window-handlers.ts`) and the renderer's `setLocale()`
 * pushes the new locale to the main process via
 * `globalThis.window_?.setLocale?.(locale)` (see `renderer/src/i18n/i18n.ts`).
 *
 * The preload bridge surface (`preload/index.ts`) MUST expose a
 * `setLocale` channel that invokes `ipcRenderer.invoke(
 * "i18n:set-locale", locale)`. If the preload bridge surface is missing
 * the `setLocale` entry, the renderer's call silently no-ops
 * (optional chaining), and native dialogs always render in English —
 * the exact regression  was meant to fix.
 *
 * This test mocks `electron`'s `contextBridge.exposeInMainWorld` to
 * capture the namespace objects the preload exposes, then asserts:
 *   1. The `window_` namespace is exposed.
 *   2. The `window_` namespace exposes a `setLocale` function.
 *   3. Calling `setLocale("ar")` invokes `ipcRenderer.invoke(
 *      "i18n:set-locale", "ar")` with the bare-string payload shape
 *      the main-process handler accepts.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
	return {
		exposeInMainWorld: vi.fn(),
		ipcRendererInvoke: vi.fn(),
		ipcRendererOn: vi.fn(),
		ipcRendererRemoveListener: vi.fn(),
		ipcRendererSend: vi.fn(),
	};
});

vi.mock("electron", () => ({
	contextBridge: {
		exposeInMainWorld: mocks.exposeInMainWorld,
	},
	ipcRenderer: {
		invoke: mocks.ipcRendererInvoke,
		on: mocks.ipcRendererOn,
		removeListener: mocks.ipcRendererRemoveListener,
		send: mocks.ipcRendererSend,
	},
}));

/** Capture the namespace → API surface map exposed by the preload module. */
async function captureExposedSurfaces(): Promise<
	Record<string, Record<string, unknown>>
> {
	mocks.exposeInMainWorld.mockClear();
	vi.resetModules();
	await import("../index");
	return Object.fromEntries(
		mocks.exposeInMainWorld.mock.calls.map(([name, api]) => [
			name as string,
			api as Record<string, unknown>,
		]),
	);
}

describe("NH-3: preload bridge surface for i18n:set-locale", () => {
	beforeEach(() => {
		mocks.ipcRendererInvoke.mockResolvedValue({ ok: true });
	});

	it("exposes a window_ namespace with a setLocale channel", async () => {
		const namespaces = await captureExposedSurfaces();
		expect(namespaces.window_).toBeDefined();
		expect(typeof namespaces.window_?.setLocale).toBe("function");
	});

	it("setLocale invokes ipcRenderer.invoke('i18n:set-locale', locale) with bare-string payload", async () => {
		mocks.ipcRendererInvoke.mockClear();
		const namespaces = await captureExposedSurfaces();
		const setLocale = namespaces.window_?.setLocale as (
			locale: string,
		) => Promise<unknown>;
		expect(typeof setLocale).toBe("function");
		await setLocale("ar");
		expect(mocks.ipcRendererInvoke).toHaveBeenCalledWith(
			"i18n:set-locale",
			"ar",
		);
	});
});
