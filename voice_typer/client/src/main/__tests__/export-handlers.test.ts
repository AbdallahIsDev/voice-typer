// @vitest-environment node
/**
 * R6-F9 unit tests for `export-handlers.ts` format validation + row cap.
 *
 * Verifies that:
 *   - history:export rejects unknown formats with { success: false, error: "Invalid format" }
 *   - vocabulary:export rejects unknown formats with the same shape
 *   - data rows beyond MAX_EXPORT_ROWS (100k) are sliced (not stored)
 *
 * These tests mock `dialog.showSaveDialog` so we can capture the
 * early-return path BEFORE the save dialog is invoked.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockShowSaveDialog = vi.fn();
const mockIpcHandle = vi.fn();
vi.mock("electron", () => ({
	dialog: { showSaveDialog: mockShowSaveDialog },
	ipcMain: { handle: mockIpcHandle },
}));

vi.mock("../i18n", () => ({ mainT: (k: string) => k }));

import fs from "node:fs";

// Production `atomicWriteFile` is ASYNC — it writes via
// `fs.promises.writeFile` + `fs.promises.rename` (see
// export-handlers.ts; the sync `atomicWriteFileSync` variant is
// deprecated and no longer used by the export handlers). Spy the
// promises API (instead of the sync `writeFileSync`) so the tests
// capture the exported content without touching the real filesystem,
// and stub rename/unlink so the atomic swap short-circuits instead of
// throwing ENOENT on the (never-created) staging file.
const writeSpy = vi
	.spyOn(fs.promises, "writeFile")
	.mockImplementation(() => Promise.resolve());
vi.spyOn(fs.promises, "rename").mockImplementation(() => Promise.resolve());
vi.spyOn(fs.promises, "unlink").mockImplementation(() => Promise.resolve());

describe("R6-F9: export-handlers format validation + row cap", () => {
	let historyHandler: (event: unknown, args: unknown) => Promise<unknown>;
	let vocabHandler: (event: unknown, args: unknown) => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		mockShowSaveDialog.mockResolvedValue({
			canceled: false,
			filePath: "/tmp/out",
		});
		const mod = await import("../ipc/export-handlers");
		mod.registerExportHandlers();
		const historyCall = mockIpcHandle.mock.calls.find(
			(c) => c[0] === "history:export",
		);
		const vocabCall = mockIpcHandle.mock.calls.find(
			(c) => c[0] === "vocabulary:export",
		);
		if (!historyCall || !vocabCall) {
			throw new Error("export handlers not registered");
		}
		historyHandler = historyCall[1] as typeof historyHandler;
		vocabHandler = vocabCall[1] as typeof vocabHandler;
	});

	describe("history:export", () => {
		it("rejects an unknown format with { success: false, error: 'Invalid format' }", async () => {
			const result = await historyHandler(null, {
				data: [{ a: 1 }],
				format: "xml",
			});
			expect(result).toEqual({ success: false, error: "Invalid format" });
		});

		it("rejects an empty-string format", async () => {
			const result = await historyHandler(null, { data: [], format: "" });
			expect(result).toEqual({ success: false, error: "Invalid format" });
		});

		it("does NOT invoke dialog.showSaveDialog when format is invalid", async () => {
			await historyHandler(null, { data: [], format: "xml" });
			expect(mockShowSaveDialog).not.toHaveBeenCalled();
		});

		it("accepts 'json' format (proceeds to save dialog)", async () => {
			await historyHandler(null, { data: [], format: "json" });
			expect(mockShowSaveDialog).toHaveBeenCalledTimes(1);
		});

		it("accepts 'csv' format (proceeds to save dialog)", async () => {
			await historyHandler(null, { data: [], format: "csv" });
			expect(mockShowSaveDialog).toHaveBeenCalledTimes(1);
		});

		it("caps data at 100k rows (CSV branch)", async () => {
			const huge: Array<Record<string, unknown>> = Array.from(
				{ length: 150_000 },
				() => ({
					a: "x",
				}),
			);
			await historyHandler(null, { data: huge, format: "csv" });
			expect(writeSpy).toHaveBeenCalledTimes(1);
			const written = writeSpy.mock.calls[0]?.[1] as string;
			// Header (1) + 100k rows = 100001 lines.
			const lines = written.split("\n");
			expect(lines.length).toBe(100_001);
		});

		it("caps data at 100k rows (JSON branch)", async () => {
			const huge: Array<Record<string, unknown>> = Array.from(
				{ length: 150_000 },
				() => ({
					a: "x",
				}),
			);
			await historyHandler(null, { data: huge, format: "json" });
			expect(writeSpy).toHaveBeenCalledTimes(1);
			const written = writeSpy.mock.calls[0]?.[1] as string;
			const parsed = JSON.parse(written) as unknown[];
			expect(parsed.length).toBe(100_000);
		});

		it("handles non-array data gracefully (treats as empty)", async () => {
			// Pass a non-array as `data`. The handler should coerce to []
			// via Array.isArray() check and not crash.
			const result = await historyHandler(null, {
				data: "not-an-array" as unknown,
				format: "json",
			});
			expect(result).toMatchObject({ success: true });
		});
	});

	describe("vocabulary:export", () => {
		it("rejects an unknown format with { success: false, error: 'Invalid format' }", async () => {
			const result = await vocabHandler(null, {
				data: { entries: [] },
				format: "xml",
			});
			expect(result).toEqual({ success: false, error: "Invalid format" });
		});

		it("caps vocabulary entries at 100k rows (CSV branch)", async () => {
			const huge = Array.from({ length: 150_000 }, () => ({
				original: "a",
				correction: "b",
			}));
			await vocabHandler(null, {
				data: { entries: huge },
				format: "csv",
			});
			const written = writeSpy.mock.calls[0]?.[1] as string;
			const lines = written.split("\n");
			// Header (1) + 100k rows = 100001 lines.
			expect(lines.length).toBe(100_001);
		});

		it("caps vocabulary entries at 100k rows (JSON branch)", async () => {
			const huge = Array.from({ length: 150_000 }, () => ({
				original: "a",
				correction: "b",
			}));
			await vocabHandler(null, {
				data: { entries: huge },
				format: "json",
			});
			const written = writeSpy.mock.calls[0]?.[1] as string;
			const parsed = JSON.parse(written) as unknown[];
			expect(parsed.length).toBe(100_000);
		});
	});
});
