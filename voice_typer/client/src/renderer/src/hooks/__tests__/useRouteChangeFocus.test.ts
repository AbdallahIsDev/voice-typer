/**
 * Tests for useRouteChangeFocus (extracted from App.tsx).
 *
 * Contract: move keyboard focus to ``<main id="main-content">`` on
 * every route change — EXCEPT the initial mount (the skip-first-run
 * guard: the user hasn't navigated yet, so stealing focus would be
 * rude).
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useRouteChangeFocus } from "@/hooks/useRouteChangeFocus";
import type { Page } from "@/types/ipc";

let currentPage: Page = "home";
let mainEl: HTMLDivElement;

beforeEach(() => {
	currentPage = "home";
	mainEl = document.createElement("div");
	mainEl.id = "main-content";
	mainEl.tabIndex = -1;
	document.body.appendChild(mainEl);
});

afterEach(() => {
	mainEl.remove();
});

describe("useRouteChangeFocus", () => {
	it("does NOT steal focus on the initial mount (skip-first-run guard)", () => {
		renderHook(() => useRouteChangeFocus(currentPage));
		expect(document.activeElement).not.toBe(mainEl);
	});

	it("moves focus to #main-content after a route change", () => {
		const { rerender } = renderHook(() => useRouteChangeFocus(currentPage));
		currentPage = "history";
		rerender();
		expect(document.activeElement).toBe(mainEl);
	});

	it("does nothing when the page does not change", () => {
		const { rerender } = renderHook(() => useRouteChangeFocus(currentPage));
		rerender();
		rerender();
		// Still on the first run — no focus steal, no crash.
		expect(document.activeElement).not.toBe(mainEl);
	});

	it("is a no-op when the #main-content landmark is absent", () => {
		mainEl.remove();
		const { rerender } = renderHook(() => useRouteChangeFocus(currentPage));
		currentPage = "models";
		expect(() => rerender()).not.toThrow();
	});
});
