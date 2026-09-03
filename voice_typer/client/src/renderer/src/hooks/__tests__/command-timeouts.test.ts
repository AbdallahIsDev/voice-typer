/**
 * Unit tests for the per-command timeout table ().
 *
 * : previously a blanket 120s `setTimeout` was applied to every
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

import { getTimeout, parseTauriErrorEnvelope } from "@/hooks/usePython";

describe("per-command timeout table (getTimeout)", () => {
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

	it("returns 3_595_000ms for `download_model` (just under the Rust 1h download cap)", () => {
		// Multi-GB model downloads legitimately run for many minutes,
		// so the Rust host routes download-scale commands to a 1h hard
		// cap (`DISPATCH_DOWNLOAD_TIMEOUT_SECS`, see
		// `src-tauri/src/util.rs` + `dispatch_timeout_for` in
		// `commands/sidecar_cmds/dispatch.rs`). The previous renderer
		// cap of 115s (just under an old 120s host cap) fired DURING
		// legitimate downloads: the backend kept downloading while the
		// renderer showed a false failure + Retry, and Retry started a
		// duplicate backend download.
		//
		// The renderer-side value stays 5s BELOW the host cap so the
		// renderer surfaces a command-specific timeout error before
		// the host's generic reject. A genuinely hung download is
		// recovered by the user via Cancel (short-timeout command).
		expect(getTimeout("download_model")).toBe(3_595_000);
	});

	it("returns 3_595_000ms for `import_model` (just under the Rust 1h download cap)", () => {
		// Importing a multi-GB local file goes through the same
		// long-running dispatch path; the old implicit 30s default
		// rejected large imports while the backend was still copying.
		expect(getTimeout("import_model")).toBe(3_595_000);
	});

	it("returns 30_000ms for `toggle_dictation` (short control RPC)", () => {
		//`transcribe` was a stale entry — there is no such
		// IPC command (the actual control RPC is `toggle_dictation`,
		// which returns immediately; the transcription itself runs
		// async on the backend and pushes results via
		// `transcription_final` events). `toggle_dictation` is now
		// pinned at 30s so a hung control call surfaces an error
		// before the user gives up — matching the default but
		// explicit so future contributors don't accidentally
		// remove the entry thinking it's redundant.
		expect(getTimeout("toggle_dictation")).toBe(30_000);
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

describe("VP-6: Tauri rejection-string envelope parsing (parseTauriErrorEnvelope)", () => {
	it("stamps err.code + extracts message from a structured error envelope", () => {
		// The Rust `dispatch` command (sidecar_cmds/dispatch.rs)
		// rejects the invoke promise with the JSON-serialized
		// `{type:"error", data:{code, message}}` envelope. On Tauri
		// this arrives as a raw STRING — pre-VP-6 it became
		// `new Error(wholeJSON)` with no `.code`, so callers branching
		// on the failure class silently fell through on Tauri.
		const raw = JSON.stringify({
			type: "error",
			data: { code: "command_timeout", message: "IPC timed out" },
		});
		const err = parseTauriErrorEnvelope(raw);
		expect(err).toBeInstanceOf(Error);
		expect(err?.message).toBe("IPC timed out");
		expect((err as Error & { code?: string }).code).toBe("command_timeout");
	});

	it("stamps a bare Rust dispatch-cap code (data_too_large / pending_full)", () => {
		// VP-5 codes are carried on the same envelope; the renderer's
		// `switch (code)` must be able to branch on them on Tauri too.
		const err = parseTauriErrorEnvelope(
			JSON.stringify({
				type: "error",
				data: { code: "data_too_large", message: "payload exceeds cap" },
			}),
		);
		expect(err).not.toBeNull();
		expect((err as Error & { code?: string }).code).toBe("data_too_large");
	});

	it("falls back to the raw string when the rejection is plain text", () => {
		// Rust's `dispatch timeout (120s)` rejection is a bare string,
		// not a JSON envelope — must return null so `call` wraps it
		// as `new Error(raw)`.
		expect(parseTauriErrorEnvelope("dispatch timeout (120s)")).toBeNull();
	});

	it("returns null for malformed / non-envelope JSON", () => {
		expect(parseTauriErrorEnvelope("{not json")).toBeNull();
		expect(parseTauriErrorEnvelope("42")).toBeNull();
		expect(parseTauriErrorEnvelope('{"type":"ok"}')).toBeNull();
		expect(parseTauriErrorEnvelope('{"type":"error"}')).toBeNull();
	});

	it("stamps a structured Python error code through the Tauri path", () => {
		// e.g. `client.consent_required` from the level-monitor / mic-test
		// handlers surfacing over Tauri — the renderer deep-link depends
		// on `err.code` being present.
		const err = parseTauriErrorEnvelope(
			JSON.stringify({
				type: "error",
				data: {
					code: "client.consent_required",
					message: "consent needed",
				},
			}),
		);
		expect(err).not.toBeNull();
		expect((err as Error & { code?: string }).code).toBe(
			"client.consent_required",
		);
		expect(err?.message).toBe("consent needed");
	});
});
