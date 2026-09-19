import { describe, expect, it } from "vitest";

import type { ErrorCodes } from "../enums";

describe("ErrorCodes union, cross-language parity codes are present", () => {
	it("'pending_full' is assignable to ErrorCodes (Rust PENDING_FULL_CODE parity)", () => {
		// (allowlist.rs PENDING_FULL_CODE), the namespaced
		// `client.pending_full` is a future-migration target only.
		const code: ErrorCodes = "pending_full";
		expect(code).toBeTruthy();
	});

	it("'data_too_large' is assignable to ErrorCodes (Rust dispatch cap parity)", () => {
		// (dispatch.rs 256 KiB payload cap), `client.payload_too_large_dispatch`
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
