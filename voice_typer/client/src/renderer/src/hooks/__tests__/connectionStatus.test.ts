/**
 * Unit tests for the connection-status pure helpers.
 * These helpers are the single source of truth for:
 * - validating backend `RecordingState` strings before they enter
 *     status pill and its description line never desync (C-HOME-1)
 */
import { describe, expect, it, vi } from "vitest";

import {
	applyStatusWithReason,
	asRecordingState,
	BACKGROUND_RECONNECT_INTERVAL_MS,
	CONNECTION_PROBE_MAX_RETRIES,
	CONNECTION_PROBE_RETRY_DELAY_MS,
	HEALTH_CHECK_EVENT_GRACE_MS,
	HEALTH_CHECK_INTERVAL_MS,
	HEALTH_CHECK_MAX_RETRIES,
	HEALTH_CHECK_RETRY_DELAY_MS,
	MAX_BACKGROUND_RECONNECTS,
	RESPAWN_EXHAUSTED_CODE,
} from "@/hooks/connectionStatus";

describe("asRecordingState", () => {
	it("accepts every RecordingState member", () => {
		for (const value of [
			"idle",
			"recording",
			"transcribing",
			"loading",
			"cancelling",
			"error",
		] as const) {
			expect(asRecordingState(value)).toBe(value);
		}
	});

	it("rejects unknown strings", () => {
		expect(asRecordingState("paused")).toBeNull();
		expect(asRecordingState("")).toBeNull();
		expect(asRecordingState("ERROR")).toBeNull();
	});

	it("rejects non-strings without coercing them", () => {
		expect(asRecordingState(undefined)).toBeNull();
		expect(asRecordingState(null)).toBeNull();
		expect(asRecordingState(0)).toBeNull();
		expect(asRecordingState({ status: "idle" })).toBeNull();
	});
});

describe("applyStatusWithReason", () => {
	it("writes recordingState and lastError from the same tuple (C-HOME-1)", () => {
		const setRecordingState = vi.fn();
		const setLastError = vi.fn();
		applyStatusWithReason(
			"error",
			"No speech model is selected.",
			setRecordingState,
			setLastError,
		);
		expect(setRecordingState).toHaveBeenCalledWith("error");
		expect(setLastError).toHaveBeenCalledWith("No speech model is selected.");
	});

	it("clears lastError on non-error statuses even when a message is present", () => {
		const setRecordingState = vi.fn();
		const setLastError = vi.fn();
		applyStatusWithReason(
			"idle",
			"stale reason",
			setRecordingState,
			setLastError,
		);
		expect(setRecordingState).toHaveBeenCalledWith("idle");
		expect(setLastError).toHaveBeenCalledWith(null);
	});

	it("clears lastError when error has no message", () => {
		const setRecordingState = vi.fn();
		const setLastError = vi.fn();
		applyStatusWithReason("error", null, setRecordingState, setLastError);
		applyStatusWithReason("error", undefined, setRecordingState, setLastError);
		applyStatusWithReason("error", "", setRecordingState, setLastError);
		expect(setRecordingState).toHaveBeenCalledTimes(3);
		expect(setLastError).toHaveBeenCalledWith(null);
		expect(setLastError).toHaveBeenCalledTimes(3);
	});
});

describe("connection timing constants", () => {
	it("pins the structured respawn-exhausted code", () => {
		expect(RESPAWN_EXHAUSTED_CODE).toBe("respawn_exhausted");
	});

	it("keeps the initial probe budget at 5 × 2s", () => {
		expect(CONNECTION_PROBE_MAX_RETRIES).toBe(5);
		expect(CONNECTION_PROBE_RETRY_DELAY_MS).toBe(2000);
	});

	it("keeps the health-check at 2 retries / 500ms / 15s interval", () => {
		expect(HEALTH_CHECK_MAX_RETRIES).toBe(2);
		expect(HEALTH_CHECK_RETRY_DELAY_MS).toBe(500);
		expect(HEALTH_CHECK_INTERVAL_MS).toBe(15_000);
	});

	it("keeps the 60s event-grace window", () => {
		expect(HEALTH_CHECK_EVENT_GRACE_MS).toBe(60_000);
	});

	it("keeps the background reconnect poll at 12 × 10s", () => {
		expect(MAX_BACKGROUND_RECONNECTS).toBe(12);
		expect(BACKGROUND_RECONNECT_INTERVAL_MS).toBe(10_000);
	});
});
