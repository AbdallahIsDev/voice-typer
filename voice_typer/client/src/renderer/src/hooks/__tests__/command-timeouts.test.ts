/**
 * Unit tests for the per-command timeout table (CR-18).
 *
 * CR-18: previously a blanket 120s `setTimeout` was applied to every
 * IPC call (in the Electron main's `sendToPython` and the Rust
 * `dispatch` command). A `get_status` call that hangs took 120s to
 * surface an error; the 120s timer was created even for trivial
 * commands.
 *
 * The fix adds a per-command timeout table in `usePython.ts` so the
 * renderer-side `call` function races the underlying bridge call
 * against a command-specific deadline. These tests pin the table
 * values so a future refactor can't silently regress the timeouts.
 */
import { describe, expect, it } from "vitest";

import { getTimeout } from "@/hooks/usePython";

describe("CR-18: per-command timeout table (getTimeout)", () => {
	it("returns 5_000ms for `get_status` (trivial status probe)", () => {
		// A healthy get_status round-trip is <100ms; 5s is more than
		// enough to surface a hung backend without making the user
		// wait the full prior 120s.
		expect(getTimeout("get_status")).toBe(5_000);
	});

	it("returns 5_000ms for `get_config` (trivial config read)", () => {
		// Same rationale as get_status — config reads are sub-100ms
		// in healthy operation; 5s is a generous upper bound.
		expect(getTimeout("get_config")).toBe(5_000);
	});

	it("returns 10_000ms for `get_history` (potentially larger payload)", () => {
		// History lists can be large but still complete in <1s; 10s
		// gives headroom for slow disk on cold caches.
		expect(getTimeout("get_history")).toBe(10_000);
	});

	it("returns 600_000ms (10 minutes) for `download_model`", () => {
		// Large model files (1-3 GB) over slow links can legitimately
		// take many minutes. 10 minutes matches the prior blanket
		// timeout's tolerance for the only command that actually
		// needed it.
		expect(getTimeout("download_model")).toBe(600_000);
	});

	it("returns 120_000ms (2 minutes) for `transcribe`", () => {
		// Transcription of long audio segments can take 30-90s on
		// CPU-only systems; 2 minutes preserves the prior tolerance.
		expect(getTimeout("transcribe")).toBe(120_000);
	});

	it("returns 30_000ms (default) for unknown commands", () => {
		// The default is a reasonable middle ground: short enough that
		// a hung unknown command surfaces an error before the user
		// gives up, long enough that legitimate slow commands (e.g.
		// a new `import_model` not yet in the table) don't false-
		// positive.
		expect(getTimeout("unknown_cmd")).toBe(30_000);
		expect(getTimeout("some_future_command")).toBe(30_000);
		expect(getTimeout("")).toBe(30_000);
	});
});
