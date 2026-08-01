/**
 * Tests for the Zustand appStore.
 *
 * BACKLOG-004: The store provides a single source of truth for
 * connection status, recording state, and config. These tests verify
 * the store's state transitions and merge logic.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "@/stores/appStore";

describe("appStore", () => {
	beforeEach(() => {
		// Reset the store to its initial state before each test.
		// All six top-level state slices are reset so a test that
		// mutated `lastErrorAt` or `navVersion` (added in Fix #25-5
		// and Fix #25-6) cannot leak into a later test. Earlier this
		// only reset 4 fields, which left stale `lastErrorAt` /
		// `navVersion` values across tests.
		useAppStore.setState({
			connectionStatus: "connecting",
			recordingState: "idle",
			lastError: null,
			lastErrorAt: null,
			config: null,
			navVersion: 0,
		});
	});

	describe("connection status", () => {
		it("starts in 'connecting' state", () => {
			expect(useAppStore.getState().connectionStatus).toBe("connecting");
		});

		it("setConnectionStatus updates the status", () => {
			useAppStore.getState().setConnectionStatus("connected");
			expect(useAppStore.getState().connectionStatus).toBe("connected");
		});

		it("can transition through all statuses", () => {
			const { setConnectionStatus } = useAppStore.getState();
			setConnectionStatus("connecting");
			expect(useAppStore.getState().connectionStatus).toBe("connecting");
			setConnectionStatus("connected");
			expect(useAppStore.getState().connectionStatus).toBe("connected");
			setConnectionStatus("restarting");
			expect(useAppStore.getState().connectionStatus).toBe("restarting");
			setConnectionStatus("disconnected");
			expect(useAppStore.getState().connectionStatus).toBe("disconnected");
		});
	});

	describe("recording state", () => {
		it("starts in 'idle' state", () => {
			expect(useAppStore.getState().recordingState).toBe("idle");
		});

		it("setRecordingState updates the state", () => {
			useAppStore.getState().setRecordingState("recording");
			expect(useAppStore.getState().recordingState).toBe("recording");
		});

		it("can transition through all recording states", () => {
			const { setRecordingState } = useAppStore.getState();
			for (const state of [
				"recording",
				"transcribing",
				"idle",
				"error",
				"loading",
				"cancelling",
			] as const) {
				setRecordingState(state);
				expect(useAppStore.getState().recordingState).toBe(state);
			}
		});
	});

	describe("lastError", () => {
		it("starts as null", () => {
			expect(useAppStore.getState().lastError).toBeNull();
		});

		it("setLastError updates the error", () => {
			useAppStore.getState().setLastError("Something went wrong");
			expect(useAppStore.getState().lastError).toBe("Something went wrong");
		});

		it("can be cleared back to null", () => {
			useAppStore.getState().setLastError("Error");
			useAppStore.getState().setLastError(null);
			expect(useAppStore.getState().lastError).toBeNull();
		});
	});

	describe("lastErrorAt", () => {
		// Cover the Fix #25-5 timestamp slice. Earlier this field had
		// no direct test — a regression that broke the timestamp
		// (e.g. leaving it set after `setLastError(null)`) would have
		// gone undetected. These tests pin the contract:
		//   - starts null alongside lastError
		//   - is set to a fresh epoch-ms when setLastError receives a
		//     non-null value
		//   - is cleared back to null alongside lastError
		//   - is auto-cleared on reconnection (the setConnectionStatus
		//     "connected" path also clears lastErrorAt — see appStore.ts)

		it("starts as null alongside lastError", () => {
			expect(useAppStore.getState().lastErrorAt).toBeNull();
		});

		it("is stamped with an epoch-ms number when setLastError receives a message", () => {
			const before = Date.now();
			useAppStore.getState().setLastError("boom");
			const after = Date.now();
			const stamped = useAppStore.getState().lastErrorAt;
			expect(typeof stamped).toBe("number");
			expect(stamped as number).toBeGreaterThanOrEqual(before);
			expect(stamped as number).toBeLessThanOrEqual(after);
		});

		it("is cleared back to null when setLastError receives null", () => {
			useAppStore.getState().setLastError("boom");
			expect(useAppStore.getState().lastErrorAt).not.toBeNull();
			useAppStore.getState().setLastError(null);
			expect(useAppStore.getState().lastErrorAt).toBeNull();
		});

		it("is auto-cleared when setConnectionStatus transitions to connected", () => {
			useAppStore.getState().setLastError("disconnected error");
			expect(useAppStore.getState().lastErrorAt).not.toBeNull();
			useAppStore.getState().setConnectionStatus("connected");
			expect(useAppStore.getState().lastError).toBeNull();
			expect(useAppStore.getState().lastErrorAt).toBeNull();
		});
	});

	describe("config", () => {
		it("starts as null", () => {
			expect(useAppStore.getState().config).toBeNull();
		});

		it("setConfig replaces the entire config", () => {
			useAppStore
				.getState()
				.setConfig({ hotkey: "<f2>", model_size: "small.en" });
			expect(useAppStore.getState().config).toEqual({
				hotkey: "<f2>",
				model_size: "small.en",
			});
		});

		it("mergeConfig merges partial updates into existing config", () => {
			useAppStore
				.getState()
				.setConfig({ hotkey: "<f2>", model_size: "small.en" });
			useAppStore.getState().mergeConfig({ model_size: "medium.en" });
			expect(useAppStore.getState().config).toEqual({
				hotkey: "<f2>",
				model_size: "medium.en",
			});
		});

		it("mergeConfig creates config from null when needed", () => {
			useAppStore.getState().mergeConfig({ theme_mode: "dark" });
			expect(useAppStore.getState().config).toEqual({ theme_mode: "dark" });
		});

		it("mergeConfig preserves existing keys not in the update", () => {
			useAppStore
				.getState()
				.setConfig({ hotkey: "<f2>", theme_mode: "system" });
			useAppStore.getState().mergeConfig({ theme_mode: "dark" });
			expect(useAppStore.getState().config).toEqual({
				hotkey: "<f2>",
				theme_mode: "dark",
			});
		});
	});

	describe("navVersion", () => {
		// Cover the Fix #25-6 navigation counter slice. Earlier this
		// field had no direct test — a regression that broke the
		// bumper (e.g. using a non-monotonic update) would have gone
		// undetected. The counter starts at 0, increments by exactly 1
		// per `bumpNavVersion()` call, and is reset by `beforeEach`.

		it("starts at 0", () => {
			expect(useAppStore.getState().navVersion).toBe(0);
		});

		it("bumpNavVersion increments the counter by exactly 1", () => {
			const before = useAppStore.getState().navVersion;
			useAppStore.getState().bumpNavVersion();
			expect(useAppStore.getState().navVersion).toBe(before + 1);
		});

		it("bumpNavVersion is idempotent per call (3 calls → +3)", () => {
			const before = useAppStore.getState().navVersion;
			useAppStore.getState().bumpNavVersion();
			useAppStore.getState().bumpNavVersion();
			useAppStore.getState().bumpNavVersion();
			expect(useAppStore.getState().navVersion).toBe(before + 3);
		});
	});

	describe("selector subscriptions", () => {
		it("components can subscribe to individual slices", () => {
			// Verify the store works with Zustand's selector pattern
			const statuses: string[] = [];
			const unsub = useAppStore.subscribe((state) => {
				statuses.push(state.connectionStatus);
			});
			useAppStore.getState().setConnectionStatus("connected");
			useAppStore.getState().setConnectionStatus("disconnected");
			unsub();
			// The subscribe callback fires on every state change (including
			// the setConnectionStatus calls). At least 2 changes should be
			// recorded.
			expect(statuses.length).toBeGreaterThanOrEqual(2);
			expect(statuses).toContain("connected");
			expect(statuses).toContain("disconnected");
		});
	});
});
