/**
 * @vitest-environment node
 *
 * Test: `bootstrapRuntime()` calls `app.setAppUserModelId("VoiceTyper")`
 * between `setupUserData()` and `setupCsp()`.
 *
 * Background
 * ----------
 * The `app.setAppUserModelId("VoiceTyper")` call was moved out of
 * `index.ts` (the wiring-only entry point) and into
 * `bootstrapRuntime()` so `index.ts` stays ≤ ~300 lines (C-ARCH-1).
 * The call is now wrapped in a try/catch so non-Windows platforms
 * no-op silently (the Windows-only API throws on macOS/Linux in some
 * Electron versions).
 *
 * This test mocks `electron`'s `app.setAppUserModelId` and verifies
 * `bootstrapRuntime()` invokes it with the exact literal
 * `"VoiceTyper"`. The literal is an AppUserModelID — a programmatic
 * registry identifier, NOT a user-facing brand string — so it is
 * exempt from C-BRAND-1 (the `{appName}` placeholder rule applies to
 * user-visible strings only).
 *
 * The test mirrors the `main-process-reliability-fixes.test.ts`
 * bootstrapRuntime test pattern (mocks electron, single_instance,
 * python, state, i18n, logging; then dynamic-imports bootstrap and
 * calls `bootstrapRuntime()`).
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("bootstrapRuntime calls app.setAppUserModelId('VoiceTyper')", () => {
	let tmpDir: string;
	let setAppUserModelIdMock: ReturnType<typeof vi.fn>;
	let setPathMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.clearAllMocks();
		vi.resetModules();
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-jb9-appmodel-"));

		setAppUserModelIdMock = vi.fn();
		setPathMock = vi.fn();

		// Mock `electron` — the full shape bootstrap.ts touches:
		// app.{getPath, setPath, isPackaged, on, setAppUserModelId},
		// dialog.showErrorBox, session.defaultSession.webRequest,
		// crashReporter.start.
		vi.doMock("electron", () => ({
			app: {
				getPath: vi.fn(() => tmpDir),
				setPath: setPathMock,
				isPackaged: true,
				on: vi.fn(),
				quit: vi.fn(),
				exit: vi.fn(),
				setAppUserModelId: setAppUserModelIdMock,
			},
			crashReporter: { start: vi.fn() },
			dialog: { showErrorBox: vi.fn() },
			session: {
				defaultSession: {
					webRequest: { onHeadersReceived: vi.fn() },
				},
			},
		}));

		// Mock `./single_instance` — its real implementation
		// transitively imports `./windows` (heavy BrowserWindow
		// machinery). bootstrap.ts imports `computeConfigDir` +
		// `clearElectronPidFile` from it.
		vi.doMock("../single_instance", () => ({
			computeConfigDir: () => tmpDir,
			clearElectronPidFile: vi.fn(),
		}));

		// Mock `./python` — bootstrap.ts imports `stopPython`
		// for the production exit hook.
		vi.doMock("../python", () => ({ stopPython: vi.fn() }));

		// Mock `./state` — bootstrap.ts reads/writes
		// `state.sessionNonce`.
		vi.doMock("../state", () => ({ state: { sessionNonce: "" } }));

		// Mock `./i18n` — bootstrap.ts uses `mainT(...)` inside
		// the breaker dialog. The test never trips the breaker,
		// but the import + symbol binding must resolve.
		vi.doMock("../i18n", () => ({ mainT: (k: string) => k }));

		// Mock `./logging` — bootstrap.ts calls log.info / log.warn
		// in several setup steps.
		vi.doMock("../logging", () => ({
			DEFAULT_CRASH_LOG_MAX_BYTES: 1_048_576,
			rotateIfNeeded: vi.fn(),
			log: {
				error: vi.fn(),
				info: vi.fn(),
				warn: vi.fn(),
			},
		}));
	});

	afterEach(() => {
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
		vi.doUnmock("electron");
		vi.doUnmock("../single_instance");
		vi.doUnmock("../python");
		vi.doUnmock("../state");
		vi.doUnmock("../i18n");
		vi.doUnmock("../logging");
	});

	it("calls app.setAppUserModelId exactly once with the literal 'VoiceTyper'", async () => {
		const { bootstrapRuntime } = await import("../bootstrap");
		bootstrapRuntime();
		expect(setAppUserModelIdMock).toHaveBeenCalledTimes(1);
		expect(setAppUserModelIdMock).toHaveBeenCalledWith("VoiceTyper");
	});

	it("does not throw if app.setAppUserModelId throws (non-Windows / registry write failure)", async () => {
		setAppUserModelIdMock.mockImplementation(() => {
			throw new Error("not supported on this platform");
		});
		const { bootstrapRuntime } = await import("../bootstrap");
		// The try/catch in setupAppUserModelId must swallow the
		// error so bootstrapRuntime continues to the CSP step
		// and returns normally.
		expect(() => bootstrapRuntime()).not.toThrow();
		// The mock was still invoked (the call happened, it just
		// threw — the catch logs a warning and moves on).
		expect(setAppUserModelIdMock).toHaveBeenCalledTimes(1);
	});

	it("calls setAppUserModelId AFTER setupUserData (userData path is set first)", async () => {
		// Order matters: setAppUserModelId is between setupUserData
		// and setupCsp. We verify the order by spying on
		// app.setPath (called by setupUserData) and asserting
		// setAppUserModelId was called AFTER setPath.
		// NOTE: `setPathMock` is the shared spy established in
		// `beforeEach` (the same mock object `bootstrap` resolves
		// when it dynamic-imports `electron`) — NOT a re-mock inside
		// `it()`. Re-mocking `electron` via `vi.doMock` inside `it()`
		// is order-dependent / flaky under the full-suite run (the
		// dynamic `import` can resolve the module before the late
		// mock factory is applied, leaving `setPathMock` with 0
		// calls). Using the `beforeEach`-registered mock eliminates
		// the race.
		const { bootstrapRuntime } = await import("../bootstrap");
		bootstrapRuntime();

		// Both must have been called.
		expect(setPathMock).toHaveBeenCalledWith(
			"userData",
			path.join(tmpDir, "electron-profile"),
		);
		expect(setAppUserModelIdMock).toHaveBeenCalledWith("VoiceTyper");

		// Assert ordering: setPath's first invocation must come
		// BEFORE setAppUserModelId's first invocation. We compare
		// the relative order of the mock.invocation_call_order
		// arrays.
		const setPathOrder = setPathMock.mock.invocationCallOrder[0];
		const setAppUserModelIdOrder =
			setAppUserModelIdMock.mock.invocationCallOrder[0];
		// noUncheckedIndexedAccess: indices are `number | undefined`;
		// the toHaveBeenCalledWith assertions above prove both were
		// invoked, so narrow with non-null assertions.
		expect(setPathOrder).toBeDefined();
		expect(setAppUserModelIdOrder).toBeDefined();
		expect(setPathOrder as number).toBeLessThan(
			setAppUserModelIdOrder as number,
		);
	});

	it("logs the 'userData set to' line once across the double setupUserData invocation", async () => {
		// Production calls `setupUserData()` twice per boot — at
		// index.ts module-load (so early Chromium utility processes
		// inherit the unified data root) and again inside
		// `bootstrapRuntime()` (app.whenReady). Both re-set the SAME
		// path; the lifecycle line must appear in electron-stdout.log
		// only once, or every boot logs a duplicate pair.
		const { bootstrapRuntime, setupUserData } = await import("../bootstrap");
		const { log } = await import("../logging");
		const logInfoMock = vi.mocked(log.info);

		setupUserData();
		bootstrapRuntime();

		const userDataLogs = logInfoMock.mock.calls.filter((call) =>
			String(call[0]).includes("userData set to"),
		);
		expect(userDataLogs).toHaveLength(1);
	});
});
