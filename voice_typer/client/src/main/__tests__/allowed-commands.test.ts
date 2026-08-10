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
		//These are the commands explicitly mentioned in  and
		//docs as must-haves / must-not-haves. Verifying them
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

	it("does NOT contain the GT-32 (session-6) removed stale entries", () => {
		//16 entries removed because no renderer code invokes them
		// (the 17th, ``check_accessibility``, was re-added on
		// 2026-08-10 — finding #919 part b gave it a renderer
		// caller in the Settings → Troubleshooting section).
		// They previously appeared only in this Set (sometimes also in a
		// doc comment). The matching Python-side `_COMMAND_REGISTRY`
		//entries should also be removed by  (owns ipc_server.py);
		// until that lands the cross-file parity test in
		// `tests/test_electron_ipc_and_build.py` will flag them as
		// "missing from allowlist".
		const gt32Removed = [
			"apply_vocabulary_suggestion",
			"delete_all_personal_data",
			"dismiss_vocabulary_suggestion",
			"export_diagnostics",
			"export_gdpr_bundle",
			"get_audio_status",
			"get_rms_level",
			"get_vocabulary_suggestions",
			"level_monitor_status",
			"microphone_test_status",
			"onboarding_get_model_catalog",
			"onboarding_get_step",
			"onboarding_request_keyboard_permission",
			"refresh_microphones",
			"show_electron_notification",
			"test_llm_connection",
		];
		for (const cmd of gt32Removed) {
			expect(
				ALLOWED_COMMANDS.has(cmd),
				`expected ${cmd} NOT in allowlist (GT-32 removed)`,
			).toBe(false);
		}
	});

	it("contains a non-trivial number of commands (sanity)", () => {
		//As of the R6-F10 move there were ~76 commands;  (session-6)
		// removed 17 stale entries, bringing the count to ~59. This guard
		// prevents an accidental wholesale drop. The exact count is
		// allowed to grow over time, so we assert a lower bound.
		expect(ALLOWED_COMMANDS.size).toBeGreaterThanOrEqual(50);
	});

	it("every entry is a non-empty string with no surrounding whitespace", () => {
		for (const cmd of ALLOWED_COMMANDS) {
			expect(typeof cmd).toBe("string");
			expect(cmd.length).toBeGreaterThan(0);
			expect(cmd).toBe(cmd.trim());
		}
	});
});
