// @vitest-environment node
/**
 * NH-3 (session NH) unit tests for the re-added `i18n:set-locale` IPC
 * handler in `window-handlers.ts`.
 *
 * Verifies that:
 *   - The handler is registered under the `i18n:set-locale` channel.
 *   - It calls `setMainLocale(locale)` with the locale string from the
 *     payload (both `{locale: "ar"}` object form and bare `"ar"` string
 *     form are accepted — the bare-string form matches the preload's
 *     `ipcRenderer.invoke("i18n:set-locale", locale)` call shape).
 *   - It resolves with `{ ok: true }` on success.
 *   - It resolves with `{ ok: false, error }` (rather than throwing)
 *     when the payload is missing or empty, so the renderer's
 *     `.catch(() => {})` swallow on the IPC call doesn't fire — the
 *     push is best-effort.
 *   - It logs and returns `{ ok: false, error }` if `setMainLocale`
 *     throws synchronously (defensive — `setMainLocale` currently
 *     never throws, but the handler must not crash the main process
 *     if a future refactor introduces a throw path).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// R6-F10 note: vitest 4 hoists `vi.mock()` above all top-level statements.
// Any variable referenced inside the factory must be declared via
// `vi.hoisted()` so it's available when the factory runs.
const mocks = vi.hoisted(() => {
	return {
		ipcHandle: vi.fn(),
		setMainLocale: vi.fn<(locale: string) => void>(),
		dialogShowOpenDialog: vi.fn(),
		shellOpenPath: vi.fn<(path: string) => Promise<string>>(),
		computeConfigDir: vi.fn<() => string>(),
	};
});

vi.mock("electron", () => ({
	app: {
		getPath: vi.fn(() => "/tmp/vt-mock-userdata"),
		isPackaged: false,
	},
	dialog: { showOpenDialog: mocks.dialogShowOpenDialog },
	ipcMain: { handle: mocks.ipcHandle },
	shell: { openPath: mocks.shellOpenPath },
}));

vi.mock("../single_instance", () => ({
	computeConfigDir: mocks.computeConfigDir,
}));

vi.mock("../i18n", () => ({
	// `mainT` is used by `model:import-dialog` handler — keep it as a
	// passthrough so any test that triggers that handler doesn't crash.
	mainT: (key: string) => key,
	// `setMainLocale` is the function under test for NH-3. The mock
	// records calls so we can assert the handler dispatches correctly.
	setMainLocale: mocks.setMainLocale,
}));

vi.mock("../state", () => ({
	state: { mainWindow: null },
}));

describe("NH-3: i18n:set-locale IPC handler", () => {
	let setLocaleHandler: (event: unknown, payload: unknown) => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		mocks.computeConfigDir.mockReturnValue("/mock/config/dir");
		mocks.shellOpenPath.mockResolvedValue("");
		// Re-import the handler module so it picks up the fresh mocks.
		const mod = await import("../ipc/window-handlers");
		mocks.ipcHandle.mockClear();
		mod.registerWindowHandlers();
		const call = mocks.ipcHandle.mock.calls.find(
			(c) => c[0] === "i18n:set-locale",
		);
		if (!call) {
			throw new Error("i18n:set-locale handler not registered");
		}
		setLocaleHandler = call[1] as typeof setLocaleHandler;
	});

	it("is registered under the 'i18n:set-locale' channel", () => {
		const channels = mocks.ipcHandle.mock.calls.map((c) => c[0]);
		expect(channels).toContain("i18n:set-locale");
	});

	it("accepts a bare-string payload and calls setMainLocale with it", async () => {
		const result = await setLocaleHandler(null, "ar");
		expect(mocks.setMainLocale).toHaveBeenCalledTimes(1);
		expect(mocks.setMainLocale).toHaveBeenCalledWith("ar");
		expect(result).toEqual({ ok: true });
	});

	it("accepts a {locale} object payload and calls setMainLocale with the locale field", async () => {
		const result = await setLocaleHandler(null, { locale: "fr" });
		expect(mocks.setMainLocale).toHaveBeenCalledTimes(1);
		expect(mocks.setMainLocale).toHaveBeenCalledWith("fr");
		expect(result).toEqual({ ok: true });
	});

	it("passes the locale string through unchanged (no normalisation)", async () => {
		// The handler does NOT validate / normalise the locale — that's
		// `setMainLocale`'s job (it falls back to "en" with a warning on
		// unknown locales). Asserting here that the handler forwards the
		// raw string so the validation behaviour stays in one place.
		await setLocaleHandler(null, "en-US");
		expect(mocks.setMainLocale).toHaveBeenCalledWith("en-US");
	});

	it("returns { ok: false, error } when the payload is an empty string", async () => {
		const result = await setLocaleHandler(null, "");
		expect(mocks.setMainLocale).not.toHaveBeenCalled();
		expect(result).toEqual({ ok: false, error: "empty locale" });
	});

	it("returns { ok: false, error } when the payload is an empty {locale} object", async () => {
		const result = await setLocaleHandler(null, { locale: "" });
		expect(mocks.setMainLocale).not.toHaveBeenCalled();
		expect(result).toEqual({ ok: false, error: "empty locale" });
	});

	it("returns { ok: false, error } when the payload is null/undefined", async () => {
		const result = await setLocaleHandler(null, undefined);
		expect(mocks.setMainLocale).not.toHaveBeenCalled();
		expect(result).toEqual({ ok: false, error: "empty locale" });
	});

	it("returns { ok: false, error } when the payload is a non-string, non-object value", async () => {
		const result = await setLocaleHandler(null, 42);
		expect(mocks.setMainLocale).not.toHaveBeenCalled();
		expect(result).toEqual({ ok: false, error: "empty locale" });
	});

	it("does NOT throw if setMainLocale throws synchronously (defensive)", async () => {
		mocks.setMainLocale.mockImplementationOnce(() => {
			throw new Error("boom");
		});
		const result = await setLocaleHandler(null, "ar");
		// The handler must catch the throw and return a structured error
		// so an IPC-layer exception doesn't crash the main process.
		expect(result).toMatchObject({ ok: false, error: "boom" });
	});
});
