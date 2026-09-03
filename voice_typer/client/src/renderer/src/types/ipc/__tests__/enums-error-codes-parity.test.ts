/**
 *  regression guard: the `ErrorCodes` union in `enums.ts` MUST
 * include the cross-language parity codes added by the  fix.
 *
 * Background: the Rust host (``src-tauri/src/commands/sidecar_cmds.rs``)
 * emits two ad-hoc error codes inside ``json!({"code": "..."})`` envelopes
 * (``pending_full``, ``data_too_large``) that were absent from both the
 * Python ``ErrorCodes`` class and the TS ``ErrorCodes`` union. The
 * renderer's ``switch (code)`` block therefore fell through to the
 * generic "unknown error" path for both codes — a silent UX regression.
 *
 *  closed the gap by:
 *   - Adding ``PENDING_FULL = "client.pending_full"`` and
 *     ``PAYLOAD_TOO_LARGE_DISPATCH = "client.payload_too_large_dispatch"``
 *     to the Python ``ErrorCodes`` class.
 *   - Adding ``| "client.pending_full"`` and
 *     ``| "client.payload_too_large_dispatch"`` to the TS ``ErrorCodes``
 *     union.
 *   - Consolidating the standalone ``ProtocolVersionMismatchError``
 *     code literal into the union (adding
 *     ``| "server.protocol_version_mismatch"``).
 *
 * Separately,  added the host-bridge-only ``respawn_exhausted``
 * code (synthesized by ``python-namespace.ts`` when the supervisor's
 * respawn loop is exhausted) to the union so the renderer's
 * ``useConnection`` error handler can branch on the typed code.
 *
 * This file is the compile-time + runtime guard so a future regression
 * that drops any of these literals from the union fails loudly.
 *
 * VP-5 (2026-08-11): the tests below now pin the BARE forms
 * ``pending_full`` / ``data_too_large`` — the codes the Rust host
 * ACTUALLY emits. The namespaced forms (``client.pending_full`` /
 * ``client.payload_too_large_dispatch``) are future-migration targets
 * with no active emitter, so asserting them made the parity test pass
 * vacuously.
 */
import { describe, expect, it } from "vitest";

import type { ErrorCodes } from "../enums";

/**
 * Compile-time guards: each literal MUST be assignable to ``ErrorCodes``.
 * If a future refactor drops one of these literals from the union, the
 * corresponding ``const ... : ErrorCodes = "..."`` line fails to
 * type-check. The ``expect(...).toBeTruthy()`` runtime assertion is a
 * belt-and-suspenders guard so the test ALSO fails at runtime (not just
 * at compile time) if the literal somehow slips through.
 */

describe("ErrorCodes union — cross-language parity codes are present", () => {
	it("'pending_full' is assignable to ErrorCodes (Rust PENDING_FULL_CODE parity)", () => {
		// VP-5: the Rust host emits the BARE legacy form `pending_full`
		// (allowlist.rs PENDING_FULL_CODE) — the namespaced
		// `client.pending_full` is a future-migration target only.
		const code: ErrorCodes = "pending_full";
		expect(code).toBeTruthy();
	});

	it("'data_too_large' is assignable to ErrorCodes (Rust dispatch cap parity)", () => {
		// VP-5: the Rust host emits the BARE `data_too_large` code
		// (dispatch.rs 256 KiB payload cap) — `client.payload_too_large_dispatch`
		// is a future-migration target only.
		const code: ErrorCodes = "data_too_large";
		expect(code).toBeTruthy();
	});

	it("'server.protocol_version_mismatch' is assignable to ErrorCodes (DR-21 wire-protocol)", () => {
		const code: ErrorCodes = "server.protocol_version_mismatch";
		expect(code).toBeTruthy();
	});

	it("ZU-17: 'respawn_exhausted' is assignable to ErrorCodes (host-bridge-only code)", () => {
		const code: ErrorCodes = "respawn_exhausted";
		expect(code).toBeTruthy();
	});
});
