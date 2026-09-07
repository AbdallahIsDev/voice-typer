/**
 * Tests for useSidebarAutoCollapse (extracted from App.tsx).
 *
 * Contract: own the sidebar collapse state; only the wide→narrow
 * TRANSITION (and the initial narrow mount) force a collapse. Once
 * collapsed, the user's manual expand wins until the next wide→narrow
 * transition. Narrow→wide does NOT auto-expand.
 */
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Controllable stand-in for the viewport media query result.
const { viewport } = vi.hoisted(() => ({ viewport: { narrow: false } }));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (query: string) =>
		query === "(max-width: 640px)" && viewport.narrow,
}));

import { useSidebarAutoCollapse } from "@/hooks/useSidebarAutoCollapse";

beforeEach(() => {
	viewport.narrow = false;
});

describe("useSidebarAutoCollapse", () => {
	it("starts expanded on a wide viewport", () => {
		const { result } = renderHook(() => useSidebarAutoCollapse());
		expect(result.current.sidebarCollapsed).toBe(false);
	});

	it("collapses on the wide→narrow transition", () => {
		const { result, rerender } = renderHook(() => useSidebarAutoCollapse());
		expect(result.current.sidebarCollapsed).toBe(false);

		viewport.narrow = true;
		rerender();
		expect(result.current.sidebarCollapsed).toBe(true);
	});

	it("collapses on the initial narrow mount (prev === null)", () => {
		viewport.narrow = true;
		const { result } = renderHook(() => useSidebarAutoCollapse());
		expect(result.current.sidebarCollapsed).toBe(true);
	});

	it("does NOT auto-expand on the narrow→wide transition", () => {
		viewport.narrow = true;
		const { result, rerender } = renderHook(() => useSidebarAutoCollapse());
		expect(result.current.sidebarCollapsed).toBe(true);

		viewport.narrow = false;
		rerender();
		// The user may have intentionally collapsed the sidebar on a
		// wide window — the manual toggle wins.
		expect(result.current.sidebarCollapsed).toBe(true);
	});

	it("collapses again on a second wide→narrow transition after a manual expand", () => {
		const { result, rerender } = renderHook(() => useSidebarAutoCollapse());
		viewport.narrow = true;
		rerender();
		expect(result.current.sidebarCollapsed).toBe(true);

		// Manual expand while narrow (Ctrl+B / TitleBar toggle).
		act(() => {
			result.current.setSidebarCollapsed(false);
		});
		expect(result.current.sidebarCollapsed).toBe(false);

		// Re-renders while narrow keep the manual state (prev === true
		// → no-op).
		rerender();
		rerender();
		expect(result.current.sidebarCollapsed).toBe(false);

		// The NEXT wide→narrow transition forces the collapse again.
		viewport.narrow = false;
		rerender();
		viewport.narrow = true;
		rerender();
		expect(result.current.sidebarCollapsed).toBe(true);
	});

	it("exposes the raw setter for manual toggling", () => {
		const { result } = renderHook(() => useSidebarAutoCollapse());
		act(() => {
			result.current.setSidebarCollapsed(true);
		});
		expect(result.current.sidebarCollapsed).toBe(true);
		act(() => {
			result.current.setSidebarCollapsed(false);
		});
		expect(result.current.sidebarCollapsed).toBe(false);
	});
});
