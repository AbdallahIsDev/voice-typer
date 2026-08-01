// @vitest-environment node
/**
 * renderer-internal-allowlist-split: renderer-vs-internal IPC allowlist split.
 *
 * Background
 * ----------
 * `ALLOWED_COMMANDS` in `src/main/allowed-commands.ts` is the union of
 * (a) commands the renderer may invoke via the `python-call` IPC bridge
 * (get_config, toggle_dictation, ...) and (b) commands only the
 * Electron main process itself invokes (quit_app, restart_app,
 * heartbeat, relaunch_ack). Before this fix, `sendToPython` checked
 * only the union, so a compromised renderer could construct a
 * `{type: "quit_app"}` payload and invoke `python-call` to kill the
 * backend, or `{type: "heartbeat"}` to starve the watchdog.
 *
 * Fix
 * ---
 * `sendToPython` now treats a non-null `senderId` (the renderer's
 * `WebContents.id`, passed by `python-call-handler.ts`) as a renderer
 * caller and rejects any command in the private `_INTERNAL_ONLY_COMMANDS`
 * Set with the same "Disallowed IPC command" error used by the
 * allowlist gate (so the failure class is uniform and no information
 * about the internal command set leaks). Main-process callers pass
 * `senderId === null` and bypass this gate so heartbeat / shutdown /
 * relaunch-ack still work.
 *
 * This file also pins the parity invariant: every entry in
 * `_INTERNAL_ONLY_COMMANDS_FOR_TEST` must also be in the REAL
 * `ALLOWED_COMMANDS` (imported via `vi.importActual` so the mocked
 * ALLOWED_COMMANDS used by the behavior tests doesn't shadow it).
 * Without this guard, a future contributor could remove `quit_app`
 * from `ALLOWED_COMMANDS` (breaking main-process shutdown) while
 * leaving it in `_INTERNAL_ONLY_COMMANDS`, and the drift would be
 * silent.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
	return {
		socketWrite: vi.fn<(data: string) => boolean>(() => true),
	};
});

// Mock `allowed-commands` with a set that includes BOTH renderer-
// reachable commands AND the four internal-only commands. This mirrors
// the real `ALLOWED_COMMANDS` shape (union of renderer + internal) so
// the behavior tests exercise the new renderer-vs-internal gate rather
// than the pre-existing allowlist gate.
vi.mock("../allowed-commands", () => ({
	ALLOWED_COMMANDS: new Set<string>([
		// renderer-reachable (subset)
		"get_config",
		"set_config",
		"get_status",
		"toggle_dictation",
		"download_model",
		// internal-only (the four the new gate must reject for renderers)
		"quit_app",
		"restart_app",
		"heartbeat",
		"relaunch_ack",
	]),
}));

vi.mock("../state", () => ({
	MAX_PENDING_REQUESTS: 1000,
	RATE_LIMIT_MAX_CALLS: 100,
	RATE_LIMIT_WINDOW_MS: 1000,
	state: {
		// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
		tcpSocket: { write: mocks.socketWrite } as any,
		pendingRequests: new Map<
			number,
			{ resolve: (v: unknown) => void; reject: (e: unknown) => void }
		>(),
		nextId: 1,
		_relaunching: false,
	},
}));

import {
	_INTERNAL_ONLY_COMMANDS_FOR_TEST,
	_resetIpcBackpressure,
	sendToPython,
} from "../python/send-to-python";
import { state } from "../state";

describe("renderer-internal-allowlist-split: renderer-vs-internal allowlist split", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.pendingRequests.clear();
		state.nextId = 1;
		state._relaunching = false;
		// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
		state.tcpSocket = { write: mocks.socketWrite } as any;
		_resetIpcBackpressure();
	});

	describe("renderer callers (senderId !== null) are blocked from internal-only commands", () => {
		for (const cmd of [
			"quit_app",
			"restart_app",
			"heartbeat",
			"relaunch_ack",
		]) {
			it(`rejects renderer call for ${cmd} BEFORE writing to the socket`, async () => {
				await expect(sendToPython({ type: cmd }, 12345)).rejects.toThrow(
					`Disallowed IPC command: ${cmd}`,
				);

				// The new gate runs after the allowlist gate but
				// before the socket write — so no byte should hit
				// the wire and no pendingRequests entry leak.
				expect(mocks.socketWrite).not.toHaveBeenCalled();
				expect(state.pendingRequests.size).toBe(0);
			});
		}

		it("uses the SAME 'Disallowed IPC command' error wording as the allowlist gate (no information leak)", async () => {
			// A compromised renderer must not be able to distinguish
			// "command not in allowlist" from "command is internal-
			// only" — both surface as the same generic error.
			const allowlistErr = await sendToPython(
				{ type: "totally_made_up_command" },
				12345,
			).then(
				() => null,
				(e: unknown) => (e as Error).message,
			);
			const internalErr = await sendToPython({ type: "quit_app" }, 12345).then(
				() => null,
				(e: unknown) => (e as Error).message,
			);
			expect(allowlistErr).toBe(
				"Disallowed IPC command: totally_made_up_command",
			);
			expect(internalErr).toBe("Disallowed IPC command: quit_app");
			// Both share the same prefix — the renderer can't tell
			// which rejection path fired.
			expect(allowlistErr?.startsWith("Disallowed IPC command:")).toBe(true);
			expect(internalErr?.startsWith("Disallowed IPC command:")).toBe(true);
		});
	});

	describe("renderer callers can still invoke renderer-reachable commands", () => {
		it("forwards get_config from a renderer to the socket", async () => {
			// Don't await — the promise won't resolve until a reply
			// arrives. We just want to assert the side effects.
			void sendToPython({ type: "get_config" }, 12345);

			expect(mocks.socketWrite).toHaveBeenCalledTimes(1);
			const line = mocks.socketWrite.mock.calls[0]?.[0] ?? "";
			const parsed = JSON.parse(line);
			expect(parsed.type).toBe("get_config");
			expect(typeof parsed.id).toBe("number");
			expect(state.pendingRequests.has(parsed.id)).toBe(true);
		});

		it("forwards toggle_dictation from a renderer (the most common renderer call)", async () => {
			void sendToPython({ type: "toggle_dictation" }, 1);

			expect(mocks.socketWrite).toHaveBeenCalledTimes(1);
			const line = mocks.socketWrite.mock.calls[0]?.[0] ?? "";
			expect(JSON.parse(line).type).toBe("toggle_dictation");
		});
	});

	describe("main-process callers (senderId === null) bypass the internal-only gate", () => {
		for (const cmd of [
			"quit_app",
			"restart_app",
			"heartbeat",
			"relaunch_ack",
		]) {
			it(`allows main-process call for ${cmd} (lifecycle paths still work)`, async () => {
				// These are the production call sites:
				//   - stop-python.ts sends quit_app
				//   - relaunch-app.ts sends restart_app
				//   - tcp-connect.ts sends heartbeat
				//   - handle-message.ts sends relaunch_ack
				// All pass senderId === null (no event.sender).
				void sendToPython({ type: cmd }, null);

				expect(mocks.socketWrite).toHaveBeenCalledTimes(1);
				const line = mocks.socketWrite.mock.calls[0]?.[0] ?? "";
				expect(JSON.parse(line).type).toBe(cmd);
			});
		}
	});

	describe("ordering: the internal-only gate runs AFTER the allowlist gate and BEFORE the _relaunching check", () => {
		it("rejects an unknown command with the allowlist error even during relaunch (not 'Application is restarting')", async () => {
			// The allowlist gate runs first; an unknown command is
			// rejected before the internal-only or relaunching checks.
			state._relaunching = true;
			await expect(
				sendToPython({ type: "totally_made_up_command" }, 12345),
			).rejects.toThrow(/Disallowed IPC command/);
		});

		it("rejects a renderer's internal-only command with 'Disallowed' (NOT 'Application is restarting') even during relaunch", async () => {
			// The internal-only gate runs before the _relaunching
			// check so a renderer cannot probe for the relaunching
			// state by sending internal-only commands and watching
			// for the "Application is restarting" error.
			state._relaunching = true;
			await expect(sendToPython({ type: "quit_app" }, 12345)).rejects.toThrow(
				/Disallowed IPC command/,
			);
			expect(mocks.socketWrite).not.toHaveBeenCalled();
		});
	});
});

describe("renderer-internal-allowlist-split parity: _INTERNAL_ONLY_COMMANDS entries are all in the REAL ALLOWED_COMMANDS", () => {
	it("every internal-only command is also an allowed command (no drift)", async () => {
		// `vi.importActual` bypasses the `vi.mock("../allowed-commands")`
		// factory above so we get the REAL `ALLOWED_COMMANDS` Set from
		// `src/main/allowed-commands.ts`. Without this guard, a future
		// contributor could remove `quit_app` from `ALLOWED_COMMANDS`
		// (breaking main-process shutdown) while leaving it in
		// `_INTERNAL_ONLY_COMMANDS`, and the drift would be silent.
		const real = await vi.importActual<typeof import("../allowed-commands")>(
			"../allowed-commands",
		);
		const stale: string[] = [];
		for (const cmd of _INTERNAL_ONLY_COMMANDS_FOR_TEST) {
			if (!real.ALLOWED_COMMANDS.has(cmd)) {
				stale.push(cmd);
			}
		}
		expect(stale).toEqual([]);
	});

	it("the four documented internal-only commands are present (regression guard)", () => {
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("quit_app")).toBe(true);
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("restart_app")).toBe(true);
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("heartbeat")).toBe(true);
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("relaunch_ack")).toBe(true);
	});

	it("the internal-only set is exactly 4 entries (no accidental additions)", () => {
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.size).toBe(4);
	});

	it("renderer-reachable commands are NOT in the internal-only set", () => {
		// Sanity: the split is non-trivial. These renderer-reachable
		// commands must NOT appear in the internal-only set.
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("get_config")).toBe(false);
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("toggle_dictation")).toBe(
			false,
		);
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("set_config")).toBe(false);
		expect(_INTERNAL_ONLY_COMMANDS_FOR_TEST.has("download_model")).toBe(false);
	});
});
