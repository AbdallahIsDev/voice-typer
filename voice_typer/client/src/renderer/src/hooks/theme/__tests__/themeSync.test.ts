/**
 * Tests for hooks/theme/themeSync + themeBridge — the backend→store
 * sync concern extracted from useTheme.ts.
 *
 * Covers:
 *   1. ``ensureThemeSideEffects`` — the initOnce guard: the initial
 *      config reload and the ``beforeunload`` flush listener run
 *      EXACTLY ONCE across repeated calls; the bridge references are
 *      refreshed on every call.
 *   2. ``reloadThemeFromConfig`` — seeds the store + localStorage from
 *      the backend, syncs the sound-feedback flag, and flips
 *      ``hasInitialReloadCompleted`` even on failure.
 *   3. ``handleConfigChanged`` — merges backend-pushed partials into
 *      the app config cache + the theme store.
 *   4. The ``_resetThemeStoreForTest`` seam in ``hooks/useTheme.ts``
 *      fully resets the singleton state so the guard re-arms.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	setSoundFeedbackEnabled: vi.fn(),
}));

vi.mock("@/lib/sound-manager", () => ({
	setSoundFeedbackEnabled: mocks.setSoundFeedbackEnabled,
}));

import { clearActiveBridge, setActiveBridge } from "../themeBridge";
import {
	flushPendingThemeSave,
	removeBeforeUnloadFlush,
	resetThemePersistState,
	scheduleThemeSave,
} from "../themePersist";
import { resetThemeStoreToCachedState, useThemeStore } from "../themeStore";
import {
	ensureThemeSideEffects,
	handleConfigChanged,
	reloadThemeFromConfig,
	resetThemeSyncSingletons,
} from "../themeSync";

const callMock = vi.fn();
const mergeConfigMock = vi.fn();

/** The bridge ``call`` alias typed for ``ensureThemeSideEffects``. */
const callFn = callMock as unknown as Parameters<
	typeof ensureThemeSideEffects
>[0];

/** Register the bridge + clear mocks in the right order. */
function armBridge(): void {
	setActiveBridge(
		callMock as unknown as Parameters<typeof setActiveBridge>[0],
		mergeConfigMock,
	);
}

beforeEach(() => {
	callMock.mockReset();
	mergeConfigMock.mockReset();
	mocks.setSoundFeedbackEnabled.mockClear();
	callMock.mockResolvedValue({});
	localStorage.clear();
	resetThemeStoreToCachedState();
	clearActiveBridge();
	resetThemePersistState();
	resetThemeSyncSingletons();
	removeBeforeUnloadFlush();
});

afterEach(() => {
	removeBeforeUnloadFlush();
});

describe("themeSync — ensureThemeSideEffects (initOnce guard)", () => {
	it("runs the initial reload + beforeunload install EXACTLY ONCE", async () => {
		armBridge();
		const addSpy = vi.spyOn(window, "addEventListener");

		ensureThemeSideEffects(callFn, mergeConfigMock);
		await Promise.resolve();
		ensureThemeSideEffects(callFn, mergeConfigMock);
		await Promise.resolve();

		const getConfigCalls = callMock.mock.calls.filter(
			(c) => c[0] === "get_config",
		).length;
		expect(getConfigCalls).toBe(1);
		const beforeUnloadInstalls = addSpy.mock.calls.filter(
			(c) => c[0] === "beforeunload",
		).length;
		expect(beforeUnloadInstalls).toBe(1);

		addSpy.mockRestore();
	});

	it("refreshes the bridge references on every call", async () => {
		armBridge();
		ensureThemeSideEffects(callFn, mergeConfigMock);

		const callMock2 = vi.fn().mockResolvedValue({});
		const mergeConfigMock2 = vi.fn();
		ensureThemeSideEffects(callMock2 as never, mergeConfigMock2);

		// The refreshed bridge is used by the config_changed handler.
		handleConfigChanged({ theme_mode: "dark" });
		expect(mergeConfigMock2).toHaveBeenCalled();
		expect(mergeConfigMock).not.toHaveBeenCalled();
	});

	it("re-arms after the reset seam (initOnce flag cleared)", async () => {
		armBridge();
		ensureThemeSideEffects(callFn, mergeConfigMock);
		await Promise.resolve();
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_config").length,
		).toBe(1);

		// Simulate the _resetThemeStoreForTest composition.
		resetThemeSyncSingletons();
		ensureThemeSideEffects(callFn, mergeConfigMock);
		await Promise.resolve();
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_config").length,
		).toBe(2);
	});
});

describe("themeSync — reloadThemeFromConfig", () => {
	it("seeds the store + localStorage from the backend config", async () => {
		armBridge();
		callMock.mockResolvedValueOnce({
			theme_mode: "dark",
			theme_preset: "default",
			custom_theme: { light: { a: "1" }, dark: { a: "2" } },
			text_size: 16,
			sound_feedback_enabled: false,
		});

		await reloadThemeFromConfig();

		const state = useThemeStore.getState();
		expect(state.themeMode).toBe("dark");
		expect(state.themePreset).toBe("default");
		expect(state.customTheme).toEqual({
			light: { a: "1" },
			dark: { a: "2" },
		});
		expect(state.textSize).toBe(16);
		expect(state.hasInitialReloadCompleted).toBe(true);

		expect(localStorage.getItem("voice-typer-theme-mode")).toBe("dark");
		expect(localStorage.getItem("voice-typer-text-size")).toBe("16");
		expect(mocks.setSoundFeedbackEnabled).toHaveBeenCalledWith(false);
	});

	it("clears a stale custom-theme cache when the backend confirms a non-custom preset", async () => {
		armBridge();
		localStorage.setItem("voice-typer-custom-theme", '{"stale":true}');
		callMock.mockResolvedValueOnce({
			theme_preset: "default",
		});

		await reloadThemeFromConfig();

		expect(localStorage.getItem("voice-typer-custom-theme")).toBeNull();
	});

	it("flips hasInitialReloadCompleted even when get_config rejects", async () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		armBridge();
		callMock.mockRejectedValueOnce(new Error("backend down"));

		await reloadThemeFromConfig();

		expect(useThemeStore.getState().hasInitialReloadCompleted).toBe(true);
		const warn = warnSpy.mock.calls.find((c) =>
			String(c[0] ?? "").includes("[renderer:useTheme] get_config failed"),
		);
		expect(warn).toBeTruthy();
		warnSpy.mockRestore();
	});

	it("is a no-op without a registered bridge", async () => {
		await reloadThemeFromConfig();
		expect(callMock).not.toHaveBeenCalled();
		expect(useThemeStore.getState().hasInitialReloadCompleted).toBe(false);
	});
});

describe("themeSync — handleConfigChanged", () => {
	it("merges the partial into the app config cache and updates the store", () => {
		armBridge();
		handleConfigChanged({
			theme_mode: "light",
			text_size: 18,
			unrelated_field: "ignored",
		});

		expect(mergeConfigMock).toHaveBeenCalledOnce();
		const state = useThemeStore.getState();
		expect(state.themeMode).toBe("light");
		expect(state.textSize).toBe(18);
	});

	it("syncs the sound-feedback flag from ANY config_changed push", () => {
		armBridge();
		handleConfigChanged({ sound_feedback_enabled: true });
		expect(mocks.setSoundFeedbackEnabled).toHaveBeenCalledWith(true);
	});

	it("returns undefined and does nothing without data", () => {
		armBridge();
		expect(handleConfigChanged(undefined)).toBeUndefined();
		expect(mergeConfigMock).not.toHaveBeenCalled();
	});
});

describe("themeSync — bridge shared with the persist write path", () => {
	it("a save scheduled through themePersist fires against the bridge registered here", () => {
		armBridge();
		scheduleThemeSave({ theme_mode: "dark" });
		flushPendingThemeSave();
		expect(callMock).toHaveBeenCalledWith("set_config", {
			theme_mode: "dark",
		});
	});
});
