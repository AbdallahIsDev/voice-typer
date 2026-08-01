// @vitest-environment node
/**
 * tray-logging / loadurl-logging / single-instance-logging regression tests for main-process logging fixes.
 *
 * Covers three disjoint root causes — each previously swallowed a
 * failure silently, leaving operators with no diagnostic signal in
 * the runtime log:
 *
 *   - tray-logging: tray_available.ts gdbus-probe `console.warn` bypassed
 *     the structured logger (no `[WARN]` prefix, no `electron-runtime.log`
 *     tee). Replaced with `log.warn`.
 *   - loadurl-logging: main-window.ts `loadURL` / `loadFile` Promises were
 *     dropped with no `.catch` — a load failure fed the SEC-021
 *     unhandled-rejection breaker. Now `void promise.catch(log.warn)`.
 *   - single-instance-logging: single_instance.ts had three silent `catch {}` blocks
 *     (isPidVoiceTyper, readStaleElectronPid inner, readStaleElectronPid
 *     outer). Each now logs `log.warn` before the conservative fallback
 *     return.
 *
 * Mock strategy: hoisted `vi.mock` for `../logging` + `node:child_process`
 * so every dynamic import in the file sees the same mocked `log` (with
 * a spyable `warn`) and the throwing `execFileSync`. Per-test module
 * mocks (`electron`, `../state`, `../constants`, `../ipc/channels`,
 * `../windows`) are installed via `vi.doMock` in the relevant describe's
 * `beforeEach` so they don't conflict with the tray-logging / single-instance-logging paths that
 * don't need them.
 */
import fs from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ────────────────────────────────────────────────────────────────────
// Hoisted spies + global mocks. `vi.hoisted` runs before `vi.mock`
// factories are evaluated, so the factories can close over the spies.
// ────────────────────────────────────────────────────────────────────

const hoisted = vi.hoisted(() => ({
	logWarnSpy: vi.fn(),
	execFileSyncMock: vi.fn(),
}));

// Mock the structured logger barrel so every module under test sees
// the same `log.warn` spy. The mock factory returns a stable object
// reference; `logWarnSpy` lives across tests (cleared via
// `logWarnSpy.mockClear()` in `beforeEach`).
vi.mock("../logging", () => ({
	log: {
		info: vi.fn(),
		warn: hoisted.logWarnSpy,
		error: vi.fn(),
	},
}));

// Mock `node:child_process.execFileSync` so the tray_available test
// doesn't actually shell out. Default implementation throws (gdbus
// missing); individual tests can override.
vi.mock("node:child_process", () => ({
	execFileSync: hoisted.execFileSyncMock,
}));

// ────────────────────────────────────────────────────────────────────
// tray-logging: tray_available.ts uses log.warn for gdbus-probe failure
// ────────────────────────────────────────────────────────────────────

describe("tray-logging: tray_available.ts uses structured log.warn for gdbus probe failure", () => {
	let originalPlatform: string;
	let originalSession: string | undefined;

	beforeEach(() => {
		vi.clearAllMocks();
		// First call (gdbus) throws — the catch must log.warn.
		// Second call (dbus-send) also throws so the function falls
		// through to the conservative "no SNI" fallback.
		hoisted.execFileSyncMock.mockImplementation(() => {
			throw new Error("gdbus: command not found");
		});
		originalPlatform = process.platform;
		originalSession = process.env.XDG_SESSION_TYPE;
	});

	afterEach(() => {
		Object.defineProperty(process, "platform", {
			value: originalPlatform,
			configurable: true,
		});
		if (originalSession === undefined) {
			delete process.env.XDG_SESSION_TYPE;
		} else {
			process.env.XDG_SESSION_TYPE = originalSession;
		}
	});

	it("calls log.warn (NOT console.warn) when the gdbus probe throws", async () => {
		// Force Linux + Wayland so the function actually probes the bus.
		Object.defineProperty(process, "platform", {
			value: "linux",
			configurable: true,
		});
		process.env.XDG_SESSION_TYPE = "wayland";

		// Spy on console.warn to assert it was NOT called — this is
		// the regression: the old code bypassed the structured logger.
		const consoleWarnSpy = vi
			.spyOn(console, "warn")
			.mockImplementation(() => {});

		const mod = await import("../tray_available");
		mod._resetTrayAvailableCache();
		mod.isLinuxWaylandWithoutSni();

		// The structured logger must have been called with the
		// gdbus-probe-failed message.
		expect(hoisted.logWarnSpy).toHaveBeenCalled();
		const warnArgs = hoisted.logWarnSpy.mock.calls[0];
		expect(warnArgs).toBeDefined();
		expect(String(warnArgs?.[0])).toMatch(/gdbus probe failed/);

		// The bypass console.warn must NOT have fired — that was
		// the tray-logging root cause.
		expect(consoleWarnSpy).not.toHaveBeenCalled();
	});
});

// ────────────────────────────────────────────────────────────────────
// loadurl-logging: main-window.ts loadURL Promise rejection is caught + logged
// ────────────────────────────────────────────────────────────────────

describe("loadurl-logging: main-window.ts loadURL Promise rejection is caught + logged", () => {
	let unhandledRejectionCount: number;
	let unhandledHandler: (reason: unknown) => void;
	let originalRendererUrl: string | undefined;

	beforeEach(() => {
		vi.clearAllMocks();
		vi.resetModules();
		unhandledRejectionCount = 0;
		unhandledHandler = () => {
			unhandledRejectionCount += 1;
		};
		process.on("unhandledRejection", unhandledHandler);
		originalRendererUrl = process.env.ELECTRON_RENDERER_URL;
	});

	afterEach(() => {
		process.off("unhandledRejection", unhandledHandler);
		if (originalRendererUrl === undefined) {
			delete process.env.ELECTRON_RENDERER_URL;
		} else {
			process.env.ELECTRON_RENDERER_URL = originalRendererUrl;
		}
		vi.doUnmock("electron");
		vi.doUnmock("../state");
		vi.doUnmock("../constants");
		vi.doUnmock("../ipc/channels");
	});

	// Helper: register per-test electron/state/constants/ipc.channels
	// mocks with a fake BrowserWindow whose loadURL / loadFile return
	// the supplied promise factories.
	function installMainWindowMocks(fakeWindow: Record<string, unknown>): void {
		vi.doMock("electron", () => ({
			app: { isQuitting: false, isPackaged: false },
			// BrowserWindow must be a constructor — `new` on an arrow
			// function throws "not a constructor", and biome's
			// useArrowFunction rule rewrites `function () {}` into
			// `() => {}`. The class below copies the fakeWindow
			// properties onto `this` via Object.assign so the result
			// of `new BrowserWindow(...)` has all the methods the
			// production code calls (loadURL, webContents.on, etc.).
			BrowserWindow: class {
				constructor() {
					Object.assign(this, fakeWindow);
				}
			},
			Menu: { setApplicationMenu: vi.fn() },
			nativeTheme: {
				shouldUseDarkColors: false,
				on: vi.fn(),
				off: vi.fn(),
			},
			dialog: { showErrorBox: vi.fn() },
		}));
		vi.doMock("../state", () => ({
			state: {
				mainWindow: null,
				bubbleWindow: null,
				bubblePosition: "top",
			},
		}));
		vi.doMock("../constants", () => ({
			START_HIDDEN: false,
			RENDER_RELOAD_BACKOFF_MS: 2000,
		}));
		vi.doMock("../ipc/channels", () => ({
			WindowChannels: { maximizedChanged: "window:maximized-changed" },
		}));
	}

	function buildFakeWindow(
		loadURLImpl: () => Promise<void>,
		loadFileImpl: () => Promise<void>,
	): Record<string, unknown> {
		return {
			on: vi.fn(),
			webContents: {
				on: vi.fn(),
				send: vi.fn(),
				toggleDevTools: vi.fn(),
			},
			loadURL: vi.fn(loadURLImpl),
			loadFile: vi.fn(loadFileImpl),
			setIcon: vi.fn(),
			isDestroyed: vi.fn(() => false),
			show: vi.fn(),
			hide: vi.fn(),
			isVisible: vi.fn(() => true),
			isMinimized: vi.fn(() => false),
			restore: vi.fn(),
			focus: vi.fn(),
			setSkipTaskbar: vi.fn(),
			reload: vi.fn(),
		};
	}

	it("catches a rejected loadURL Promise and logs warn (no unhandled rejection)", async () => {
		// Force the ELECTRON_RENDERER_URL branch (loadURL, not loadFile).
		process.env.ELECTRON_RENDERER_URL = "http://localhost:5173";
		const mockLoadURL = vi.fn(() =>
			Promise.reject(new Error("loadURL simulated failure")),
		);
		const mockLoadFile = vi.fn(() => Promise.resolve());
		installMainWindowMocks(buildFakeWindow(mockLoadURL, mockLoadFile));

		const mod = await import("../windows/main-window");
		mod._resetNativeThemeListenerForTest();
		mod.createMainWindow();

		// The loadURL call returns a rejected Promise synchronously
		// (the mock rejects on invocation). The .catch handler is
		// scheduled as a microtask — flush it before asserting.
		await new Promise<void>((resolve) => {
			// Two microtask ticks: the first settles the rejection,
			// the second runs the .catch handler.
			queueMicrotask(() => queueMicrotask(resolve));
		});

		expect(mockLoadURL).toHaveBeenCalledWith("http://localhost:5173");
		expect(hoisted.logWarnSpy).toHaveBeenCalled();
		// Find the loadURL-specific warn call.
		const loadURLWarn = hoisted.logWarnSpy.mock.calls.find((c: unknown[]) =>
			c.some((arg) => String(arg).includes("loadURL rejected")),
		);
		expect(loadURLWarn).toBeDefined();

		// Allow the event loop to drain any pending unhandled-rejection
		// callbacks (the test fails if the loadurl-logging fix regressed).
		await new Promise<void>((resolve) => setImmediate(resolve));
		expect(unhandledRejectionCount).toBe(0);
	});

	it("catches a rejected loadFile Promise and logs warn (no unhandled rejection)", async () => {
		// No ELECTRON_RENDERER_URL → loadFile branch.
		delete process.env.ELECTRON_RENDERER_URL;
		const mockLoadURL = vi.fn(() => Promise.resolve());
		const mockLoadFile = vi.fn(() =>
			Promise.reject(new Error("loadFile simulated failure")),
		);
		installMainWindowMocks(buildFakeWindow(mockLoadURL, mockLoadFile));

		const mod = await import("../windows/main-window");
		mod._resetNativeThemeListenerForTest();
		mod.createMainWindow();

		await new Promise<void>((resolve) => {
			queueMicrotask(() => queueMicrotask(resolve));
		});

		expect(mockLoadFile).toHaveBeenCalled();
		const loadFileWarn = hoisted.logWarnSpy.mock.calls.find((c: unknown[]) =>
			c.some((arg) => String(arg).includes("loadFile rejected")),
		);
		expect(loadFileWarn).toBeDefined();

		await new Promise<void>((resolve) => setImmediate(resolve));
		expect(unhandledRejectionCount).toBe(0);
	});
});

// ────────────────────────────────────────────────────────────────────
// single-instance-logging: single_instance.ts isPidVoiceTyper logs warn on /proc read failure
// ────────────────────────────────────────────────────────────────────

describe("single-instance-logging: single_instance.ts isPidVoiceTyper logs warn on /proc/<pid>/cmdline read failure", () => {
	let originalPlatform: string;
	let readSpy: ReturnType<typeof vi.spyOn> | undefined;

	beforeEach(() => {
		vi.clearAllMocks();
		vi.resetModules();
		vi.doMock("electron", () => ({
			app: {
				requestSingleInstanceLock: vi.fn(() => true),
				releaseSingleInstanceLock: vi.fn(),
				exit: vi.fn(),
				on: vi.fn(),
			},
		}));
		vi.doMock("../windows", () => ({ showMainWindow: vi.fn() }));
		originalPlatform = process.platform;
	});

	afterEach(() => {
		if (readSpy) {
			readSpy.mockRestore();
			readSpy = undefined;
		}
		Object.defineProperty(process, "platform", {
			value: originalPlatform,
			configurable: true,
		});
		vi.doUnmock("electron");
		vi.doUnmock("../windows");
	});

	it("logs warn and returns false when /proc/<pid>/cmdline read throws (Linux)", async () => {
		// Force Linux so the function takes the /proc/<pid>/cmdline
		// branch (the macOS/Windows branches shell out via ps/wmic).
		Object.defineProperty(process, "platform", {
			value: "linux",
			configurable: true,
		});

		const realReadFileSync = fs.readFileSync;
		readSpy = vi
			.spyOn(fs, "readFileSync")
			.mockImplementation((file, ...rest) => {
				if (typeof file === "string" && file.includes("/cmdline")) {
					throw new Error("EACCES: permission denied");
				}
				return realReadFileSync(file as fs.PathOrFileDescriptor, ...rest);
			});

		const mod = await import("../single_instance");
		const result = mod.isPidVoiceTyper(process.pid);

		// The conservative fallback return value is unchanged.
		expect(result).toBe(false);
		// The previously-silent catch must now log.warn so the
		// failure is diagnosable in the runtime log.
		expect(hoisted.logWarnSpy).toHaveBeenCalled();
		const warnArgs = hoisted.logWarnSpy.mock.calls[0];
		expect(warnArgs).toBeDefined();
		expect(String(warnArgs?.[0])).toMatch(/isPidVoiceTyper failed/);
	});
});
