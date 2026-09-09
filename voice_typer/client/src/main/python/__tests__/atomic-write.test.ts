// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const fsSpy = vi.hoisted(() => ({
	writeFileSync: vi.fn(),
	// The path + flags parameters are typed so `mock.calls[i]` carries a
	// two-element tuple (the spy's inferred signature otherwise takes no
	// arguments, making `mock.calls[0]?.[1]` a type error) and so
	// `mockImplementation((p, f) => …)` type-checks against it.
	openSync: vi.fn((_path: string, _flags: string) => 42),
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
		// The fsync handle MUST be opened read-write, NOT read-only.
		// On Windows, libuv maps fs.fsyncSync → Win32
		// FlushFileBuffers, which REQUIRES a handle opened with
		// GENERIC_WRITE; a read-only "r" handle (GENERIC_READ only)
		// makes fsyncSync throw EACCES/EPERM BEFORE the rename —
		// the durability guarantee silently disappears on the
		// primary platform. "r+" (O_RDWR) satisfies GENERIC_WRITE
		// and is POSIX-equivalent (fsync on O_RDWR is legal
		// everywhere). Pinning the flag here makes the defect
		// visible on Linux CI, where fsync on an O_RDONLY handle
		// is legal and would otherwise hide the bug.
		expect(fsSpy.openSync).toHaveBeenCalledWith("/data/config.json.tmp", "r+");
		expect(fsSpy.fsyncSync).toHaveBeenCalledWith(42);
		expect(fsSpy.closeSync).toHaveBeenCalledWith(42);
		expect(fsSpy.renameSync).toHaveBeenCalledWith(
			"/data/config.json.tmp",
			"/data/config.json",
		);
	});

	it("opens the temp file read-write for fsync (Windows FlushFileBuffers contract)", () => {
		// Dedicated regression pin for the fsync open-flag contract:
		// the fsync handle must carry write access so Win32
		// FlushFileBuffers (libuv's fsyncSync implementation) does
		// not reject it. A read-only "r" handle threw EACCES/EPERM
		// on Windows before the atomic rename — the restart-history
		// crash-loop breaker never persisted. See the comment in
		// the first test for the full mechanism.
		atomicWriteFile("/data/restart_history.json", "[]");

		const openCall = fsSpy.openSync.mock.calls[0];
		expect(openCall?.[1]).toBe("r+");
		// fsync must run on the SAME descriptor the open returned —
		// an open flag without a matching fsync would be meaningless.
		const openedFd = fsSpy.openSync.mock.results[0]?.value;
		expect(fsSpy.fsyncSync).toHaveBeenCalledWith(openedFd);
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
