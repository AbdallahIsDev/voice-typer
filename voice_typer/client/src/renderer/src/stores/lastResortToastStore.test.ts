/**
 * Tests for the Zustand lastResortToastStore.
 *
 * The per-backend ``asr_last_resort_unloaded`` toast cooldown timestamps
 * live in this store (not a module-level ``Map`` in the hook) so Vite
 * HMR / hot-reload of the hook module can't reset them. These tests
 * verify the store's state transitions and the test-seam reset.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { useLastResortToastStore } from "@/stores/lastResortToastStore";

describe("lastResortToastStore", () => {
	beforeEach(() => {
		// Reset to the initial state before each test (mirrors the
		// appStore.test.ts beforeEach convention).
		useLastResortToastStore.getState().resetLastToastedAt();
	});

	it("starts with no cooldown timestamps and no dedupe timestamp", () => {
		expect(useLastResortToastStore.getState().lastToastedAt).toEqual({});
		expect(useLastResortToastStore.getState().lastToastShownAt).toBeNull();
	});

	it("setLastToastedAt records a per-backend timestamp", () => {
		useLastResortToastStore.getState().setLastToastedAt("whisper", 1_000_000);
		expect(useLastResortToastStore.getState().lastToastedAt).toEqual({
			whisper: 1_000_000,
		});
	});

	it("tracks multiple backends independently", () => {
		useLastResortToastStore.getState().setLastToastedAt("whisper", 1_000_000);
		useLastResortToastStore.getState().setLastToastedAt("qwen", 2_000_000);
		expect(useLastResortToastStore.getState().lastToastedAt).toEqual({
			whisper: 1_000_000,
			qwen: 2_000_000,
		});
	});

	it("overwrites an existing backend timestamp without touching others", () => {
		useLastResortToastStore.getState().setLastToastedAt("whisper", 1_000_000);
		useLastResortToastStore.getState().setLastToastedAt("qwen", 2_000_000);
		// Same backend re-fires inside the cooldown → timestamp refresh.
		useLastResortToastStore.getState().setLastToastedAt("whisper", 1_500_000);
		expect(useLastResortToastStore.getState().lastToastedAt).toEqual({
			whisper: 1_500_000,
			qwen: 2_000_000,
		});
	});

	it("setLastToastShownAt records the global dedupe timestamp", () => {
		expect(useLastResortToastStore.getState().lastToastShownAt).toBeNull();
		useLastResortToastStore.getState().setLastToastShownAt(1_000_000);
		expect(useLastResortToastStore.getState().lastToastShownAt).toBe(1_000_000);
	});

	it("resetLastToastedAt clears all timestamps (test seam)", () => {
		useLastResortToastStore.getState().setLastToastedAt("whisper", 1_000_000);
		useLastResortToastStore.getState().setLastToastShownAt(1_000_000);
		useLastResortToastStore.getState().resetLastToastedAt();
		expect(useLastResortToastStore.getState().lastToastedAt).toEqual({});
		expect(useLastResortToastStore.getState().lastToastShownAt).toBeNull();
	});
});
