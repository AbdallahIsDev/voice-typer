// @vitest-environment node
/**
 * Unit tests for `stats-image-handlers.ts`.
 *
 * Mocks the Electron surface (`app.getPath`, `dialog.showSaveDialog`,
 * `clipboard.writeImage`, `nativeImage`, `shell.showItemInFolder`) and
 * the filesystem so no real dialog / clipboard / disk is touched.
 *
 * Covers:
 *   - save mode "downloads": instant write to the OS Downloads folder
 *     (no dialog), colliding filenames get a " (1)" suffix, no dialog.
 *   - save mode "saveAs": native save dialog + write to the chosen path.
 *   - user-canceled dialog → { success: false, canceled: true } (silent).
 *   - PNG data-URL validation: non-PNG / oversized / non-string inputs
 *     are rejected before any I/O.
 *   - copy: writes the PNG to the clipboard.
 *   - reveal: only existing absolute .png paths are passed to the shell.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetPath = vi.fn();
const mockShowSaveDialog = vi.fn();
const mockWriteImage = vi.fn();
const mockCreateFromBuffer = vi.fn();
const mockShowItemInFolder = vi.fn();
const mockIpcHandle = vi.fn();
const mockIpcRemoveHandler = vi.fn();

vi.mock("electron", () => ({
	app: { getPath: mockGetPath },
	clipboard: { writeImage: mockWriteImage },
	dialog: { showSaveDialog: mockShowSaveDialog },
	ipcMain: { handle: mockIpcHandle, removeHandler: mockIpcRemoveHandler },
	nativeImage: { createFromBuffer: mockCreateFromBuffer },
	shell: { showItemInFolder: mockShowItemInFolder },
}));

vi.mock("../i18n", () => ({ mainT: (k: string) => k }));

// A tiny real PNG payload (signature + a few bytes) so the data-URL
// validation passes its PNG-signature check.
const PNG_BYTES = Buffer.from([
	0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
]);
const PNG_DATA_URL = `data:image/png;base64,${PNG_BYTES.toString("base64")}`;

const writeSpy = vi
	.spyOn(fs.promises, "writeFile")
	.mockImplementation(() => Promise.resolve());

async function getHandler(channel: string) {
	const mod = await import("../ipc/stats-image-handlers");
	mod.registerStatsImageHandlers();
	const call = mockIpcHandle.mock.calls.find((c) => c[0] === channel);
	if (!call) throw new Error(`handler not registered: ${channel}`);
	return call[1] as (event: unknown, args: unknown) => Promise<unknown>;
}

describe("stats-image handlers", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.resetModules();
		mockGetPath.mockReturnValue(path.join("/home", "user", "Downloads"));
		mockShowSaveDialog.mockResolvedValue({
			canceled: false,
			filePath: path.join("/home", "user", "Pictures", "stats.png"),
		});
		mockCreateFromBuffer.mockReturnValue({ isEmpty: () => false });
		// Files don't exist by default → nonCollidingPath writes directly.
		vi.spyOn(fs.promises, "access").mockRejectedValue(new Error("ENOENT"));
	});

	it("save mode 'downloads' writes to the OS Downloads folder without a dialog", async () => {
		const handler = await getHandler("stats-image:save");
		const result = (await handler(null, {
			dataUrl: PNG_DATA_URL,
			defaultName: "voice-typer-stats",
			mode: "downloads",
		})) as { success: boolean; path?: string };

		expect(result.success).toBe(true);
		expect(mockShowSaveDialog).not.toHaveBeenCalled();
		expect(mockGetPath).toHaveBeenCalledWith("downloads");
		expect(writeSpy).toHaveBeenCalledWith(
			path.join("/home", "user", "Downloads", "voice-typer-stats.png"),
			PNG_BYTES,
		);
	});

	it("save mode 'downloads' appends a numeric suffix on filename collision", async () => {
		// The base filename exists → the (1) suffixed path is used.
		const accessSpy = vi
			.spyOn(fs.promises, "access")
			.mockImplementation(async (p: unknown) => {
				const file = String(p);
				if (file.endsWith("voice-typer-stats.png")) return; // exists
				throw new Error("ENOENT");
			});
		const handler = await getHandler("stats-image:save");
		const result = (await handler(null, {
			dataUrl: PNG_DATA_URL,
			defaultName: "voice-typer-stats",
			mode: "downloads",
		})) as { success: boolean; path?: string };

		expect(result.success).toBe(true);
		expect(result.path).toContain("voice-typer-stats (1).png");
		expect(accessSpy).toHaveBeenCalled();
	});

	it("save mode 'saveAs' opens the native save dialog and writes there", async () => {
		const handler = await getHandler("stats-image:save");
		const result = (await handler(null, {
			dataUrl: PNG_DATA_URL,
			defaultName: "voice-typer-stats",
			mode: "saveAs",
		})) as { success: boolean; path?: string };

		expect(result.success).toBe(true);
		expect(mockShowSaveDialog).toHaveBeenCalledTimes(1);
		expect(mockGetPath).not.toHaveBeenCalled();
		expect(writeSpy).toHaveBeenCalledWith(
			path.join("/home", "user", "Pictures", "stats.png"),
			PNG_BYTES,
		);
	});

	it("save mode 'saveAs' returns canceled without an error when the user dismisses the dialog", async () => {
		mockShowSaveDialog.mockResolvedValue({
			canceled: true,
			filePath: undefined,
		});
		const handler = await getHandler("stats-image:save");
		const result = (await handler(null, {
			dataUrl: PNG_DATA_URL,
			defaultName: "x",
			mode: "saveAs",
		})) as { success: boolean; canceled?: boolean; error?: string };

		expect(result.success).toBe(false);
		expect(result.canceled).toBe(true);
		expect(result.error).toBeUndefined();
		expect(writeSpy).not.toHaveBeenCalled();
	});

	it.each([
		["non-string dataUrl", 42, "Invalid PNG data"],
		["wrong MIME prefix", "data:image/jpeg;base64,AA==", "Invalid PNG data"],
		["not a PNG payload", "data:image/png;base64,QUJDRA==", "Invalid PNG data"],
	])("rejects %s", async (_label, dataUrl, error) => {
		const handler = await getHandler("stats-image:save");
		const result = (await handler(null, {
			dataUrl,
			defaultName: "x",
			mode: "downloads",
		})) as { success: boolean; error?: string };

		expect(result.success).toBe(false);
		expect(result.error).toBe(error);
		expect(writeSpy).not.toHaveBeenCalled();
	});

	it("sanitizes a path-traversal default name", async () => {
		const handler = await getHandler("stats-image:save");
		const result = (await handler(null, {
			dataUrl: PNG_DATA_URL,
			defaultName: "../../evil/stats",
			mode: "downloads",
		})) as { success: boolean; path?: string };

		expect(result.success).toBe(true);
		expect(result.path).not.toContain("..");
		// The traversal segments are stripped; the sanitized stem stays.
		expect(result.path).toContain("evil-stats.png");
	});

	it("copy writes the PNG to the OS clipboard", async () => {
		const handler = await getHandler("stats-image:copy");
		const result = (await handler(null, {
			dataUrl: PNG_DATA_URL,
		})) as { success: boolean };

		expect(result.success).toBe(true);
		expect(mockCreateFromBuffer).toHaveBeenCalledWith(PNG_BYTES);
		expect(mockWriteImage).toHaveBeenCalledTimes(1);
	});

	it("copy rejects an invalid payload", async () => {
		const handler = await getHandler("stats-image:copy");
		const result = (await handler(null, {
			dataUrl: "data:image/png;base64,QUJDRA==",
		})) as { success: boolean; error?: string };

		expect(result.success).toBe(false);
		expect(mockWriteImage).not.toHaveBeenCalled();
	});

	it("reveal passes an existing absolute .png path to the shell", async () => {
		vi.spyOn(fs.promises, "access").mockResolvedValue();
		const handler = await getHandler("stats-image:reveal");
		const result = (await handler(null, {
			path: "/home/user/Downloads/voice-typer-stats.png",
		})) as { success: boolean };

		expect(result.success).toBe(true);
		expect(mockShowItemInFolder).toHaveBeenCalledWith(
			"/home/user/Downloads/voice-typer-stats.png",
		);
	});

	it("reveal rejects relative / non-png / missing paths", async () => {
		vi.spyOn(fs.promises, "access").mockRejectedValue(new Error("ENOENT"));
		const handler = await getHandler("stats-image:reveal");

		for (const p of [
			"relative/path.png",
			"/abs/file.txt",
			"/abs/missing.png",
		]) {
			const result = (await handler(null, { path: p })) as {
				success: boolean;
			};
			expect(result.success).toBe(false);
		}
		expect(mockShowItemInFolder).not.toHaveBeenCalled();
	});
});
