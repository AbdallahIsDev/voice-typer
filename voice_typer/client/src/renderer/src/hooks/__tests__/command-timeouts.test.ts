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

	it("returns 115_000ms for `download_model` (just under the Rust 120s hard cap)", () => {
		//the Rust `dispatch` command enforces a
		// hard 120s timeout on `download_model` (see
		// `src-tauri/src/commands/sidecar_cmds.rs:50-73` and
		// `src-tauri/src/util.rs:53` `DISPATCH_TIMEOUT_SECS = 120`).
		// The previous 600_000ms (10 min) entry was dead code: the
		// Rust host always rejected first at 120s with the generic
		// "dispatch timeout (120s)" string.
		//
		// The renderer-side value is now capped at 115s — 5s BELOW
		// the Rust 120s hard cap so the renderer surfaces a clearer,
		// command-specific timeout error
		// (`IPC command "download_model" timed out after 115000ms`)
		// before the Rust side rejects with the generic message.
		expect(getTimeout("download_model")).toBe(115_000);
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
		expect(err!.message).toBe("IPC timed out");
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
		expect(err!.message).toBe("consent needed");
	});
});
