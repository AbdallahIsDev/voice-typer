// @vitest-environment node
/**
 * R6-F10 unit tests for `src/main/allowed-commands.ts`.
 *
 * Verifies the canonical allowlist declaration moved out of `index.ts`
 * still contains the expected command strings (set membership preserved).
 */
import { describe, expect, it } from "vitest";
import { ALLOWED_COMMANDS } from "../allowed-commands";

describe("R6-F10: allowed-commands.ts", () => {
	it("exports a Set<string>", () => {
		expect(ALLOWED_COMMANDS).toBeInstanceOf(Set);
	});

	it("preserves the canonical command strings (subset sanity check)", () => {
		// These are the commands explicitly mentioned in ERR-IPC-002 and
		// ERR-IPC-003 docs as must-haves / must-not-haves. Verifying them
		// guards against accidental drops during the R6-F10 move.
		const mustHave = [
			"quit_app",
			"restart_app",
			"repaste_last",
			"get_status",
			"toggle_dictation",
			"set_config",
			"heartbeat",
			"relaunch_ack",
			"force_cancel_transcription",
			"refresh_microphones",
		];
		for (const cmd of mustHave) {
			expect(ALLOWED_COMMANDS.has(cmd), `expected ${cmd} in allowlist`).toBe(
				true,
			);
		}
	});

	it("does NOT contain the ERR-IPC-003 removed entries", () => {
		const mustNotHave = [
			"quit",
			"restart",
			"save_config",
			"save_vocabulary_with_diff",
			"complete_onboarding",
		];
		for (const cmd of mustNotHave) {
			expect(
				ALLOWED_COMMANDS.has(cmd),
				`expected ${cmd} NOT in allowlist`,
			).toBe(false);
		}
	});

	it("contains a non-trivial number of commands (sanity)", () => {
		// As of the R6-F10 move there are ~80 commands; this guards against
		// an accidental wholesale drop. The exact count is allowed to grow
		// over time, so we assert a lower bound.
		expect(ALLOWED_COMMANDS.size).toBeGreaterThanOrEqual(70);
	});

	it("every entry is a non-empty string with no surrounding whitespace", () => {
		for (const cmd of ALLOWED_COMMANDS) {
			expect(typeof cmd).toBe("string");
			expect(cmd.length).toBeGreaterThan(0);
			expect(cmd).toBe(cmd.trim());
		}
	});
});
