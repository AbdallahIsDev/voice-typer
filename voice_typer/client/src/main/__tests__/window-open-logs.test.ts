// @vitest-environment node
/**
 *  unit tests for `window:open-logs` IPC handler.
 *
 * Verifies that the handler resolves the log directory via
 * `computeConfigDir()` (NOT the legacy hardcoded `~/.voice-typer`)
 * and that it NO LONGER creates the directory as a side effect.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

// R6-F10 note: vitest 4 hoists `vi.mock()` above all top-level statements.
// Any variable referenced inside the factory must be declared via
// `vi.hoisted()` so it's available when the factory runs.
const mocks = vi.hoisted(() => {
	return {
		shellOpenPath: vi.fn<(path: string) => Promise<string>>(),
		ipcHandle: vi.fn(),
		dialogShowOpenDialog: vi.fn(),
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
	mainT: (key: string) => key,
	setMainLocale: () => {},
}));

vi.mock("../windows", () => ({
	showMainWindow: vi.fn(),
}));

vi.mock("../state", () => ({
	state: { mainWindow: null },
}));

const mkdirSpy = vi.spyOn(fs, "mkdirSync");
// Mock fs.statSync so the handler's directory-validation guard
// sees a valid directory at LOGS_DIR (the O1 `<config-dir>/logs`
// path). Without this mock, statSync returns undefined for the
// fake path and the handler short-circuits with "log directory
// not found".
const statSyncSpy = vi.spyOn(fs, "statSync").mockImplementation(((
	filePath: fs.PathOrFileDescriptor,
) => {
	if (typeof filePath === "string" && filePath === LOGS_DIR) {
		return { isDirectory: () => true } as fs.Stats;
	}
	return { isDirectory: () => false } as fs.Stats;
}) as unknown as typeof fs.statSync);

const COMPUTED_DIR = "/mock/config/dir/voice-typer";
// Use path.join so the mock matches whatever separator the handler's
// `path.join(computeConfigDir(), "logs")` produces on this platform.
const LOGS_DIR = path.join(COMPUTED_DIR, "logs");

describe("CR-33: window:open-logs handler", () => {
	let openLogsHandler: () => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		mocks.computeConfigDir.mockReturnValue(COMPUTED_DIR);
		mocks.shellOpenPath.mockResolvedValue("");
		// Re-establish the statSync mock (clearAllMocks resets the mock but
		// the implementation we set with mockImplementation persists; this is
		// defensive in case a future vitest version changes that behavior).
		statSyncSpy.mockImplementation(((filePath: fs.PathOrFileDescriptor) => {
			if (typeof filePath === "string" && filePath === LOGS_DIR) {
				return { isDirectory: () => true } as fs.Stats;
			}
			return { isDirectory: () => false } as fs.Stats;
		}) as unknown as typeof fs.statSync);
		// Re-import the handler module so it picks up the fresh mocks.
		const mod = await import("../ipc/window-handlers");
		mocks.ipcHandle.mockClear();
		mod.registerWindowHandlers();
		const call = mocks.ipcHandle.mock.calls.find(
			(c) => c[0] === "window:open-logs",
		);
		if (!call) throw new Error("window:open-logs handler not registered");
		openLogsHandler = call[1] as () => Promise<unknown>;
	});

	it("resolves the log dir via computeConfigDir()/logs (NOT ~/.voice-typer)", async () => {
		await openLogsHandler();
		expect(mocks.computeConfigDir).toHaveBeenCalledTimes(1);
		const passedPath = mocks.shellOpenPath.mock.calls[0]?.[0];
		expect(passedPath).toBe(LOGS_DIR);
		// Sanity: the legacy hardcoded path must NOT appear.
		expect(passedPath).not.toMatch(/\.voice-typer$/);
	});

	it("does NOT call fs.mkdirSync (CR-33: stray dir creation removed)", async () => {
		await openLogsHandler();
		expect(mkdirSpy).not.toHaveBeenCalled();
	});

	it("returns { success: true } on shell.openPath success (the path-disclosure fix: path no longer leaked to renderer)", async () => {
		mocks.shellOpenPath.mockResolvedValueOnce("");
		const result = await openLogsHandler();
		expect(result).toEqual({ success: true });
	});

	it("returns { success: false, error } when shell.openPath fails (the path-disclosure fix: path no longer leaked)", async () => {
		mocks.shellOpenPath.mockResolvedValueOnce("File does not exist");
		const result = await openLogsHandler();
		expect(result).toEqual({
			success: false,
			error: "File does not exist",
		});
	});

	it("returns { success: false, error } if computeConfigDir throws", async () => {
		mocks.computeConfigDir.mockImplementationOnce(() => {
			throw new Error("boom");
		});
		const result = await openLogsHandler();
		// s5's window-handlers.ts catch uses `String(e)` which produces
		// "Error: boom" for an Error instance (not just the .message).
		expect(result).toMatchObject({ success: false, error: "Error: boom" });
	});
});
