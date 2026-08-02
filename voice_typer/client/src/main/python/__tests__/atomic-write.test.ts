// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const fsSpy = vi.hoisted(() => ({
	writeFileSync: vi.fn(),
	openSync: vi.fn(() => 42),
	fsyncSync: vi.fn(),
	closeSync: vi.fn(),
	renameSync: vi.fn(),
}));

// `atomic-write.ts` uses a default import (`import fs from "node:fs"`),
// so the mock must expose the spies via `default` (plus named bindings
// for good measure). The factory must be fully inline — referencing an
// outer `const` here would hit the TDZ because vi.mock factories are
// hoisted above top-level statements.
vi.mock("node:fs", () => ({
	default: {
		writeFileSync: fsSpy.writeFileSync,
		openSync: fsSpy.openSync,
		fsyncSync: fsSpy.fsyncSync,
		closeSync: fsSpy.closeSync,
		renameSync: fsSpy.renameSync,
	},
	writeFileSync: fsSpy.writeFileSync,
	openSync: fsSpy.openSync,
	fsyncSync: fsSpy.fsyncSync,
	closeSync: fsSpy.closeSync,
	renameSync: fsSpy.renameSync,
}));

import { atomicWriteFile } from "../atomic-write";

describe("atomicWriteFile", () => {
	beforeEach(() => {
		// Clear per-test call history so `not.toHaveBeenCalled()`
		// assertions reflect only the current test's calls.
		vi.clearAllMocks();
	});

	it("writes to a sibling temp file, fsyncs, then renames atomically", () => {
		atomicWriteFile("/data/config.json", '{"a":1}');

		expect(fsSpy.writeFileSync).toHaveBeenCalledWith(
			"/data/config.json.tmp",
			'{"a":1}',
			{ encoding: "utf-8", flag: "w", mode: 0o600 },
		);
		expect(fsSpy.openSync).toHaveBeenCalledWith("/data/config.json.tmp", "r");
		expect(fsSpy.fsyncSync).toHaveBeenCalledWith(42);
		expect(fsSpy.closeSync).toHaveBeenCalledWith(42);
		expect(fsSpy.renameSync).toHaveBeenCalledWith(
			"/data/config.json.tmp",
			"/data/config.json",
		);
	});

	it("passes through custom mode and encoding", () => {
		atomicWriteFile("/data/x.json", "data", {
			mode: 0o644,
			encoding: "latin1",
		});

		expect(fsSpy.writeFileSync).toHaveBeenCalledWith(
			"/data/x.json.tmp",
			"data",
			{
				encoding: "latin1",
				flag: "w",
				mode: 0o644,
			},
		);
	});

	it("propagates the error and does not rename when the temp write fails", () => {
		fsSpy.writeFileSync.mockImplementationOnce(() => {
			throw new Error("ENOSPC");
		});

		expect(() => atomicWriteFile("/data/x.json", "data")).toThrow("ENOSPC");
		expect(fsSpy.renameSync).not.toHaveBeenCalled();
		expect(fsSpy.closeSync).not.toHaveBeenCalled();
	});

	it("does not close or rename when openSync throws", () => {
		fsSpy.openSync.mockImplementationOnce(() => {
			throw new Error("EACCES");
		});

		expect(() => atomicWriteFile("/data/x.json", "data")).toThrow("EACCES");
		expect(fsSpy.closeSync).not.toHaveBeenCalled();
		expect(fsSpy.renameSync).not.toHaveBeenCalled();
	});
});
