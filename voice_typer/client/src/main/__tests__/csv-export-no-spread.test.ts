// @vitest-environment node
/**
 * Regression tests for `export-handlers.ts` CSV join path.
 *
 * Background
 * ----------
 * The previous implementation joined the CSV header + rows via:
 *   [header, ...csvRows].join("\n")
 * The spread materializes a NEW array of 1 + csvRows.length elements on the
 * V8 heap before join even runs. At MAX_EXPORT_ROWS (100k), this spikes
 * memory AND risks the V8 argument-count ceiling (~65k on older V8).
 *
 * The fix avoids the spread:
 *   const body = csvRows.join("\n");
 *   atomicWriteFileSync(filePath, header + "\n" + body, "utf-8");
 *
 * Test strategy
 * -------------
 * (a) Source-text: assert the source does NOT use the spread-then-join
 *     pattern and DOES use csvRows.join + header + body concat.
 * (b) Runtime: mock fs so atomicWriteFileSync's internal writeFileSync is
 *     a no-op, invoke the history:export handler with CSV format + rows,
 *     and verify the content passed to the write path is exactly
 *     header + "\n" + body (correct concatenation, no data loss).
 * (c) Runtime: verify a 100k-row export completes without throwing.
 *
 * ON LINUX (sandbox): runtime test via mocked fs.
 * ON WINDOWS / macOS: same join logic — platform-agnostic.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ────────────────────────────────────────────────────────────────────
// vi.hoisted: define mock fn references BEFORE vi.mock() calls.
// ────────────────────────────────────────────────────────────────────

const { mockShowSaveDialog, mockIpcHandle } = vi.hoisted(() => ({
	mockShowSaveDialog: vi.fn(),
	mockIpcHandle: vi.fn(),
}));

vi.mock("electron", () => ({
	dialog: { showSaveDialog: mockShowSaveDialog },
	ipcMain: { handle: mockIpcHandle },
}));

vi.mock("../i18n", () => ({ mainT: (k: string) => k }));

// Mock fs so atomicWriteFile's internal fs.promises.writeFile /
// rename / unlink are no-ops. We spy on fs.promises.writeFile to
// capture the content (the export handlers migrated from the sync
// atomicWriteFileSync to the async atomicWriteFile).
const writeSpy = vi
	.spyOn(fs.promises, "writeFile")
	.mockImplementation(() => Promise.resolve());
vi.spyOn(fs.promises, "rename").mockImplementation(() => Promise.resolve());
vi.spyOn(fs.promises, "unlink").mockImplementation(() => Promise.resolve());

// ────────────────────────────────────────────────────────────────────
// Source-text helpers
// ────────────────────────────────────────────────────────────────────

function readExportHandlersSrc(): string {
	return fs.readFileSync(
		path.resolve(__dirname, "../ipc/export-handlers.ts"),
		"utf-8",
	);
}

// ────────────────────────────────────────────────────────────────────
// Source-text contract
// ────────────────────────────────────────────────────────────────────

describe("export-handlers.ts CSV join does NOT use spread", () => {
	const src = readExportHandlersSrc();

	it("does NOT use the spread-then-join anti-pattern", () => {
		// The spread materializes a new array of 1 + csvRows.length
		// elements — memory spike + V8 arg-count risk at 100k rows.
		expect(src).not.toMatch(/\[header,\s*\.\.\.csvRows\]\.join\("\\n"\)/);
	});

	it("uses csvRows.join to build the body", () => {
		expect(src).toMatch(/csvRows\.join\("\\n"\)/);
	});

	it("prepends the header via string concat (header + newline + body)", () => {
		// The fix joins the body in place and prepends the header via
		// string concat — O(n) time, O(1) extra heap. The production code
		// writes `${header}\n${body}` (template literal).
		expect(src).toMatch(/\$\{header\}\s*\\n\s*\$\{body\}/);
	});
});

// ────────────────────────────────────────────────────────────────────
// Runtime test: verify the write path receives header + body
// ────────────────────────────────────────────────────────────────────

describe("history:export CSV write path receives correct concatenation", () => {
	let historyHandler: (event: unknown, args: unknown) => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		mockShowSaveDialog.mockResolvedValue({
			canceled: false,
			filePath: "/tmp/vt-uu48-test.csv",
		});
		const mod = await import("../ipc/export-handlers");
		mod.registerExportHandlers();
		const historyCall = mockIpcHandle.mock.calls.find(
			(c) => c[0] === "history:export",
		);
		if (!historyCall) throw new Error("history:export handler not registered");
		historyHandler = historyCall[1] as typeof historyHandler;
	});

	it("writes header + rows joined by newline (no spread, correct content)", async () => {
		const rows = [
			{ id: 1, text: "hello" },
			{ id: 2, text: "world" },
			{ id: 3, text: "foo" },
		];

		await historyHandler(null, { data: rows, format: "csv" });

		expect(writeSpy).toHaveBeenCalledTimes(1);
		const written = writeSpy.mock.calls[0]?.[1] as string;
		expect(typeof written).toBe("string");

		// The written content must be exactly:
		//   header + "\n" + row1 + "\n" + row2 + "\n" + row3
		const lines = written.split("\n");
		expect(lines).toHaveLength(4); // 1 header + 3 rows
		// Header is the escaped column names joined by comma.
		expect(lines[0]).toBe("id,text");
		// Rows are escaped values joined by comma.
		expect(lines[1]).toBe("1,hello");
		expect(lines[2]).toBe("2,world");
		expect(lines[3]).toBe("3,foo");
	});

	it("handles a single row (header + 1 row, no off-by-one)", async () => {
		await historyHandler(null, {
			data: [{ a: "x", b: "y" }],
			format: "csv",
		});

		const written = writeSpy.mock.calls[0]?.[1] as string;
		const lines = written.split("\n");
		expect(lines).toHaveLength(2);
		expect(lines[0]).toBe("a,b");
		expect(lines[1]).toBe("x,y");
	});

	it("handles empty data gracefully (no crash)", async () => {
		// rows[0] ?? {} → {} → Object.keys({}) = [] → header is "".
		// csvRows is [] → body is "" → written is "" + "\n" + "" = "\n".
		// This matches the previous behavior — the join path is robust
		// to empty arrays.
		await historyHandler(null, { data: [], format: "csv" });

		const written = writeSpy.mock.calls[0]?.[1] as string;
		// No crash — the join path is robust to empty arrays.
		expect(typeof written).toBe("string");
	});

	it("completes a 100k-row export without throwing (no V8 arg-count ceiling)", async () => {
		// The previous spread-then-join would spread 100_001 elements as
		// function arguments — risking the V8 ~65k argument ceiling on
		// older engines. The fix's csvRows.join operates on the array
		// in place.
		const huge = Array.from({ length: 100_000 }, () => ({ a: "x" }));
		const result = await historyHandler(null, { data: huge, format: "csv" });
		expect(result).toMatchObject({ success: true });
		expect(writeSpy).toHaveBeenCalledTimes(1);
		const written = writeSpy.mock.calls[0]?.[1] as string;
		const lines = written.split("\n");
		expect(lines).toHaveLength(100_001); // 1 header + 100k rows
	});
});
