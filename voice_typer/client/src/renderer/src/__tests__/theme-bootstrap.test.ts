import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Expose `window.matchMedia` BEFORE the module under test is imported so
// the top-level prefers-color-scheme listener-install block runs (which
// also covers the change-handler arrow). vi.hoisted runs before imports.
const mediaSpies = vi.hoisted(() => {
	const listeners = new Set<() => void>();
	const mql = {
		matches: false,
		addEventListener: vi.fn((_evt: string, cb: () => void) => {
			listeners.add(cb);
		}),
		addListener: vi.fn((cb: () => void) => listeners.add(cb)),
	};
	const matchMedia = vi.fn(() => mql);
	if (typeof window !== "undefined") {
		(window as unknown as { matchMedia: unknown }).matchMedia = matchMedia;
	}
	return {
		mql,
		matchMedia,
		dispatchChange: () => {
			for (const l of [...listeners]) l();
		},
	};
});

const themeMocks = vi.hoisted(() => ({
	applyThemeVars: vi.fn(),
	deriveCustomVars: vi.fn((vars: unknown, isDark: boolean) => ({
		derived: true,
		vars,
		isDark,
	})),
	loadThemePreset: vi.fn(async (id: string) => {
		if (id === "dracula") throw new Error("chunk missing");
		return null;
	}),
	lazyThemeLoaders: { nord: {}, dracula: {} },
}));

vi.mock("@/themes", () => ({
	applyThemeVars: themeMocks.applyThemeVars,
	deriveCustomVars: themeMocks.deriveCustomVars,
}));

vi.mock("@/themes/index", () => ({
	lazyThemeLoaders: themeMocks.lazyThemeLoaders,
	loadThemePreset: themeMocks.loadThemePreset,
}));

import {
	LS_CUSTOM_THEME,
	LS_THEME_MODE,
	LS_THEME_PRESET,
} from "@/lib/theme-storage-keys";
import { applyBootstrapTheme } from "../theme-bootstrap";

describe("theme-bootstrap.ts", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		localStorage.clear();
		mediaSpies.matchMedia.mockImplementation(() => mediaSpies.mql);
		(window as unknown as { matchMedia: unknown }).matchMedia =
			mediaSpies.matchMedia;
		mediaSpies.mql.matches = false;
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
	});

	it("applies the dark class and loads the cached preset", async () => {
		localStorage.setItem(LS_THEME_MODE, "dark");
		localStorage.setItem(LS_THEME_PRESET, "nord");

		await applyBootstrapTheme();

		expect(document.documentElement.classList.contains("dark")).toBe(true);
		expect(themeMocks.loadThemePreset).toHaveBeenCalledWith("nord");
		expect(themeMocks.applyThemeVars).toHaveBeenCalledWith("nord", true, null);
	});

	it("does not apply the dark class for light mode and falls back to the default preset", async () => {
		localStorage.setItem(LS_THEME_MODE, "light");

		await applyBootstrapTheme();

		expect(document.documentElement.classList.contains("dark")).toBe(false);
		expect(themeMocks.applyThemeVars).toHaveBeenCalledWith(
			"default",
			false,
			null,
		);
	});

	it("resolves the system preference through matchMedia", async () => {
		localStorage.setItem(LS_THEME_MODE, "system");

		mediaSpies.mql.matches = true;
		await applyBootstrapTheme();
		expect(document.documentElement.classList.contains("dark")).toBe(true);

		mediaSpies.mql.matches = false;
		await applyBootstrapTheme();
		expect(document.documentElement.classList.contains("dark")).toBe(false);
	});

	it("derives custom vars from the cached custom theme for the custom preset", async () => {
		localStorage.setItem(LS_THEME_MODE, "dark");
		localStorage.setItem(LS_THEME_PRESET, "custom");
		localStorage.setItem(
			LS_CUSTOM_THEME,
			JSON.stringify({ light: { a: "1" }, dark: { a: "2" } }),
		);

		await applyBootstrapTheme();

		expect(themeMocks.deriveCustomVars).toHaveBeenCalledWith({ a: "2" }, true);
		expect(themeMocks.applyThemeVars).toHaveBeenCalledWith(
			"custom",
			true,
			expect.objectContaining({ derived: true }),
		);
	});

	it("ignores invalid stored mode/preset values and falls back to system/default", async () => {
		localStorage.setItem(LS_THEME_MODE, "neon");
		localStorage.setItem(LS_THEME_PRESET, "");

		await applyBootstrapTheme();

		expect(themeMocks.applyThemeVars).toHaveBeenCalledWith(
			"default",
			false,
			null,
		);
	});

	it("ignores malformed custom-theme JSON", async () => {
		localStorage.setItem(LS_THEME_PRESET, "custom");
		localStorage.setItem(LS_CUSTOM_THEME, "{not json");

		await applyBootstrapTheme();

		expect(themeMocks.applyThemeVars).toHaveBeenCalledWith(
			"custom",
			false,
			null,
		);
	});

	it("falls back to defaults when localStorage access throws", async () => {
		// Replace the whole localStorage global with an owned stub whose
		// getItem throws. Spying on the jsdom instance (or its
		// Storage.prototype) is CI-unreliable — the sound-manager tests
		// proved the instance-spy pattern does not intercept on CI's
		// Node 24. The module reads the bare global at call time, so the
		// stub is environment-independent. Restored via
		// vi.unstubAllGlobals() in afterEach.
		vi.stubGlobal("localStorage", {
			getItem: () => {
				throw new Error("denied");
			},
		});
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

		await applyBootstrapTheme();

		expect(themeMocks.applyThemeVars).toHaveBeenCalledWith(
			"default",
			false,
			null,
		);
		expect(warnSpy).toHaveBeenCalled();
	});

	it("defaults to light when matchMedia throws or is unavailable", async () => {
		localStorage.setItem(LS_THEME_MODE, "system");

		mediaSpies.matchMedia.mockImplementation(() => {
			throw new Error("no media");
		});
		await applyBootstrapTheme();
		expect(document.documentElement.classList.contains("dark")).toBe(false);

		// Simulate an environment with no matchMedia at all.
		(window as unknown as { matchMedia?: unknown }).matchMedia = undefined;
		await applyBootstrapTheme();
		expect(document.documentElement.classList.contains("dark")).toBe(false);
	});

	it("re-applies the theme when the OS colour scheme changes", async () => {
		mediaSpies.dispatchChange();

		await vi.waitFor(() => {
			expect(themeMocks.applyThemeVars).toHaveBeenCalled();
		});
	});
});
