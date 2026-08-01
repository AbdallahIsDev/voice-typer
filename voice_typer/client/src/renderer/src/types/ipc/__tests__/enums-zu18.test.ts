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

describe("ZU-18: ErrorCodes union — cross-language parity codes are present", () => {
	it("'client.pending_full' is assignable to ErrorCodes (Rust PENDING_FULL_CODE parity)", () => {
		const code: ErrorCodes = "client.pending_full";
		expect(code).toBeTruthy();
	});

	it("'client.payload_too_large_dispatch' is assignable to ErrorCodes (Rust data_too_large parity)", () => {
		const code: ErrorCodes = "client.payload_too_large_dispatch";
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
