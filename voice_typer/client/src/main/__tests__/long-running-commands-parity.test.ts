/**
 * @vitest-environment node
 *
 *  parity test: every entry in `_LONG_RUNNING_COMMANDS` must also
 * exist in `ALLOWED_COMMANDS`.
 *
 * Background
 * ----------
 * The `_LONG_RUNNING_COMMANDS` Set in `send-to-python.ts` was
 * previously populated with 3 stale entries (`"cancel_download"`,
 * `"pause_download"`, `"transcribe_audio"`) that did NOT exist in
 * `ALLOWED_COMMANDS` or the server's `_COMMAND_REGISTRY`. Because the
 * `ALLOWED_COMMANDS` gate at the top of `sendToPython` rejects unknown
 * commands BEFORE the long-running-timeout lookup runs, these 3 stale
 * entries were dead, `_isLongRunningCommand(cmd)` always returned
 * `false` for them in practice.
 *
 * The practical impact was that the REAL commands
 * (`cancel_model_download`, `pause_model_download`,
 * `resume_model_download`) got the SHORTER 15s timeout instead of the
 * documented 120s "long-running" budget, meaning a slow HuggingFace
 * cancel/resume could false-positive as a timeout, leaving the
 * pending-request map in an inconsistent state.
 *
 * This test pins the invariant "every long-running command is also an
 * allowed command" so the drift cannot recur silently. If a future
 * contributor adds a typo'd entry to `_LONG_RUNNING_COMMANDS`, this
 * test fails before the broken code ships.
 *
 * The test does NOT assert the inverse (every allowed command is long-
 * running), that would be wrong, because most commands correctly use
 * the 15s timeout. It only asserts the forward direction.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ALLOWED_COMMANDS } from "../allowed-commands";
import { IPC_TIMEOUT_DOWNLOAD_MS } from "../constants";
import {
	_DOWNLOAD_COMMANDS_FOR_TEST,
	_LONG_RUNNING_COMMANDS_FOR_TEST,
} from "../python/send-to-python";

describe("YJ-35: _LONG_RUNNING_COMMANDS entries are all in ALLOWED_COMMANDS (parity)", () => {
	it("every long-running command is also an allowed command", () => {
		const staleEntries: string[] = [];
		for (const cmd of _LONG_RUNNING_COMMANDS_FOR_TEST) {
			if (!ALLOWED_COMMANDS.has(cmd)) {
				staleEntries.push(cmd);
			}
		}
		// The stale-entries list must be empty. If it isn't, the
		// error message names the offending entries so the failure is
		// self-diagnosing.
		expect(staleEntries).toEqual([]);
	});

	it("the expected long-running commands are present (regression guard)", () => {
		//fix: the 3 real model-download control commands are
		// present, AND the 3 stale entries are absent.
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("download_model")).toBe(true);
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("import_model")).toBe(true);
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("delete_model")).toBe(true);
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("cancel_model_download")).toBe(
			true,
		);
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("pause_model_download")).toBe(
			true,
		);
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("resume_model_download")).toBe(
			true,
		);
	});

	it("the 3 stale YJ-35 entries are ABSENT (regression guard)", () => {
		// These 3 entries were the original bug, they referenced
		// commands that did not exist. If a future contributor
		// re-adds any of them, this assertion fails.
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("cancel_download")).toBe(false);
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("pause_download")).toBe(false);
		expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has("transcribe_audio")).toBe(false);
	});
});

/**
 * MO-116 parity: the download-scale timeout must agree between the two
 * hosts, and the command set it applies to must be the same one.
 *
 * The divergences this pins:
 *   - Electron timed out download/import at 120s while Tauri allowed 1 h,
 *     so the same slow download failed on one shell and succeeded on the
 *     other over an in-flight transfer.
 *   - A future edit to either side's download list (or to the Rust
 *     constant) would silently reintroduce it; the Rust source is read
 *     directly here, so the two files must be changed together.
 */
describe("MO-116: download-scale dispatch timeout parity (TS <-> Rust)", () => {
	const dispatchRs = readFileSync(
		resolve(
			__dirname,
			"../../../../../src-tauri/src/commands/sidecar_cmds/dispatch.rs",
		),
		"utf8",
	);
	const utilRs = readFileSync(
		resolve(__dirname, "../../../../../src-tauri/src/util.rs"),
		"utf8",
	);

	/**
	 * Extract the string entries of a Rust `const NAME: &[&str] = &[...];`.
	 *
	 * Anchors on the `= &[` INITIALIZER, not a bare `&[`: the type
	 * annotation itself contains `&[` (`: &[&str]`), so searching for
	 * `&[` can land inside the type and slice an empty region (which is
	 * how this parser silently produced `[]` after the declaration was
	 * collapsed onto one line).
	 */
	function rustStringList(source: string, constName: string): string[] {
		const start = source.indexOf(`const ${constName}`);
		expect(start, `${constName} not found in the Rust source`).toBeGreaterThan(
			-1,
		);
		const open = source.indexOf("= &[", start);
		expect(open, `${constName} initializer '= &[' not found`).toBeGreaterThan(
			-1,
		);
		const close = source.indexOf("]", open);
		expect(open).toBeGreaterThan(-1);
		expect(close).toBeGreaterThan(open);
		return [...source.slice(open, close).matchAll(/"([a-z_]+)"/g)].map(
			(m) => m[1] ?? "",
		);
	}

	it("applies to exactly the same commands as the Rust host", () => {
		const rustDownload = rustStringList(dispatchRs, "_DOWNLOAD_COMMANDS");
		expect([..._DOWNLOAD_COMMANDS_FOR_TEST].sort()).toEqual(
			rustDownload.sort(),
		);
	});

	it("download commands are also long-running + allowed commands", () => {
		for (const cmd of _DOWNLOAD_COMMANDS_FOR_TEST) {
			expect(_LONG_RUNNING_COMMANDS_FOR_TEST.has(cmd)).toBe(true);
			expect(ALLOWED_COMMANDS.has(cmd)).toBe(true);
		}
	});

	it("the 1-hour budget matches DISPATCH_DOWNLOAD_TIMEOUT_SECS", () => {
		const match = utilRs.match(
			/const DISPATCH_DOWNLOAD_TIMEOUT_SECS: u64 = ([0-9_]+);/,
		);
		expect(match, "DISPATCH_DOWNLOAD_TIMEOUT_SECS not found").not.toBeNull();
		const rustSecs = Number((match?.[1] ?? "").replace(/_/g, ""));
		expect(IPC_TIMEOUT_DOWNLOAD_MS).toBe(rustSecs * 1000);
	});
});
