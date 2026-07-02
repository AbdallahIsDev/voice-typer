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
		// Reset the store to its initial state before each test
		useAppStore.setState({
			connectionStatus: "connecting",
			recordingState: "idle",
			lastError: null,
			config: null,
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
