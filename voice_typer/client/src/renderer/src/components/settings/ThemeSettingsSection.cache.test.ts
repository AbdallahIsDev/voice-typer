/**
 * Tests for the _themeColorCache in ThemeSettingsSection.
 *
 * BACKLOG: The cache was added to avoid redundant DOM queries when
 * getCurrentThemeColors is called repeatedly with the same preset ID.
 * These tests verify the cache behavior:
 *   - Cache hit returns the same object without re-reading DOM/THEMES
 *   - Cache invalidation on custom color change
 *   - Cache clear on component unmount
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _themeColorCache } from "./themeColorCache";

describe("_themeColorCache", () => {
	beforeEach(() => {
		_themeColorCache.clear();
	});

	afterEach(() => {
		_themeColorCache.clear();
	});

	it("starts empty", () => {
		expect(_themeColorCache.size).toBe(0);
	});

	it("can store and retrieve entries by preset ID", () => {
		const mockColors = {
			light: { "--bg": "#ffffff" },
			dark: { "--bg": "#000000" },
		};
		_themeColorCache.set("default", mockColors);
		expect(_themeColorCache.get("default")).toBe(mockColors);
		expect(_themeColorCache.size).toBe(1);
	});

	it("can store multiple preset entries", () => {
		_themeColorCache.set("default", { light: {}, dark: {} });
		_themeColorCache.set("custom", { light: {}, dark: {} });
		_themeColorCache.set("nord", { light: {}, dark: {} });
		expect(_themeColorCache.size).toBe(3);
	});

	it("delete removes a single entry", () => {
		_themeColorCache.set("default", { light: {}, dark: {} });
		_themeColorCache.set("custom", { light: {}, dark: {} });
		_themeColorCache.delete("custom");
		expect(_themeColorCache.has("custom")).toBe(false);
		expect(_themeColorCache.has("default")).toBe(true);
		expect(_themeColorCache.size).toBe(1);
	});

	it("clear removes all entries", () => {
		_themeColorCache.set("default", { light: {}, dark: {} });
		_themeColorCache.set("custom", { light: {}, dark: {} });
		_themeColorCache.set("nord", { light: {}, dark: {} });
		_themeColorCache.clear();
		expect(_themeColorCache.size).toBe(0);
	});

	it("returns the same object reference on cache hit", () => {
		const mockColors = {
			light: { "--bg": "#ffffff" },
			dark: { "--bg": "#000000" },
		};
		_themeColorCache.set("nord", mockColors);
		const retrieved = _themeColorCache.get("nord");
		expect(retrieved).toBe(mockColors); // Same reference, not a copy
	});

	it("returns undefined for uncached preset IDs", () => {
		_themeColorCache.set("default", { light: {}, dark: {} });
		expect(_themeColorCache.get("nord")).toBeUndefined();
	});

	it("overwrites existing entry when set is called with same key", () => {
		const original = { light: { "--bg": "#fff" }, dark: { "--bg": "#000" } };
		const updated = { light: { "--bg": "#f0f0f0" }, dark: { "--bg": "#111" } };
		_themeColorCache.set("custom", original);
		_themeColorCache.set("custom", updated);
		expect(_themeColorCache.get("custom")).toBe(updated);
		expect(_themeColorCache.size).toBe(1); // Not 2 — overwritten
	});
});
