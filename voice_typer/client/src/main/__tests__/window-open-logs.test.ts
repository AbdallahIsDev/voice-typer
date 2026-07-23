// @vitest-environment node
/**
 * CR-33 unit tests for `window:open-logs` IPC handler.
 *
 * Verifies that the handler resolves the log directory via
 * `computeConfigDir()` (NOT the legacy hardcoded `~/.voice-typer`)
 * and that it NO LONGER creates the directory as a side effect.
 */
import fs from "node:fs";
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

const COMPUTED_DIR = "/mock/config/dir/voice-typer";

describe("CR-33: window:open-logs handler", () => {
	let openLogsHandler: () => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		mocks.computeConfigDir.mockReturnValue(COMPUTED_DIR);
		mocks.shellOpenPath.mockResolvedValue("");
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

	it("resolves the log dir via computeConfigDir (NOT ~/.voice-typer)", async () => {
		await openLogsHandler();
		expect(mocks.computeConfigDir).toHaveBeenCalledTimes(1);
		const passedPath = mocks.shellOpenPath.mock.calls[0]?.[0];
		expect(passedPath).toBe(COMPUTED_DIR);
		// Sanity: the legacy hardcoded path must NOT appear.
		expect(passedPath).not.toMatch(/\.voice-typer$/);
	});

	it("does NOT call fs.mkdirSync (CR-33: stray dir creation removed)", async () => {
		await openLogsHandler();
		expect(mkdirSpy).not.toHaveBeenCalled();
	});

	it("returns { success: true, path } on shell.openPath success", async () => {
		mocks.shellOpenPath.mockResolvedValueOnce("");
		const result = await openLogsHandler();
		expect(result).toEqual({ success: true, path: COMPUTED_DIR });
	});

	it("returns { success: false, error, path } when shell.openPath fails", async () => {
		mocks.shellOpenPath.mockResolvedValueOnce("File does not exist");
		const result = await openLogsHandler();
		expect(result).toEqual({
			success: false,
			error: "File does not exist",
			path: COMPUTED_DIR,
		});
	});

	it("returns { success: false, error } if computeConfigDir throws", async () => {
		mocks.computeConfigDir.mockImplementationOnce(() => {
			throw new Error("boom");
		});
		const result = await openLogsHandler();
		expect(result).toMatchObject({ success: false, error: "boom" });
	});
});
