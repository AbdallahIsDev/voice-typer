/**
 * Tests for useOnboardingRouteGuard (extracted from App.tsx).
 *
 * Contract: when the current page is "onboarding" but the shared config
 * says the wizard was already completed, redirect to "home" via
 * ``replace`` (history swap, no stack growth). Otherwise no-op.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { useOnboardingRouteGuard } from "@/hooks/useOnboardingRouteGuard";
import { useAppStore } from "@/stores/appStore";
import type { Page } from "@/types/ipc";

const mockReplace = vi.fn();

function setPage(page: Page) {
	// Drive the shared nav store the same way useNavigation would: a
	// plain state write (the guard reads currentPage as a prop, so the
	// test only needs the store to exist for the config selector).
	currentPage = page;
}

let currentPage: Page = "home";

beforeEach(() => {
	mockReplace.mockReset();
	currentPage = "home";
	useAppStore.setState({
		config: makeConfig({ onboarding_completed: true }),
	});
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("useOnboardingRouteGuard", () => {
	it("replaces the wizard route with home when onboarding is completed", () => {
		setPage("onboarding");
		renderHook(() =>
			useOnboardingRouteGuard({ currentPage, replace: mockReplace }),
		);
		expect(mockReplace).toHaveBeenCalledWith("home");
		expect(mockReplace).toHaveBeenCalledTimes(1);
	});

	it("does nothing while on any other page", () => {
		setPage("settings");
		renderHook(() =>
			useOnboardingRouteGuard({ currentPage, replace: mockReplace }),
		);
		expect(mockReplace).not.toHaveBeenCalled();
	});

	it("does nothing when onboarding is NOT completed", () => {
		useAppStore.setState({
			config: makeConfig({ onboarding_completed: false }),
		});
		setPage("onboarding");
		renderHook(() =>
			useOnboardingRouteGuard({ currentPage, replace: mockReplace }),
		);
		expect(mockReplace).not.toHaveBeenCalled();
	});

	it("re-fires when the page changes to onboarding after mount", () => {
		const { rerender } = renderHook(() =>
			useOnboardingRouteGuard({ currentPage, replace: mockReplace }),
		);
		expect(mockReplace).not.toHaveBeenCalled();

		act(() => {
			setPage("onboarding");
		});
		rerender();
		expect(mockReplace).toHaveBeenCalledWith("home");
	});

	it("re-fires when onboarding_completed flips to true while on the wizard", () => {
		useAppStore.setState({
			config: makeConfig({ onboarding_completed: false }),
		});
		setPage("onboarding");
		const { rerender } = renderHook(() =>
			useOnboardingRouteGuard({ currentPage, replace: mockReplace }),
		);
		expect(mockReplace).not.toHaveBeenCalled();

		// The shared store learns about completion (config_changed push)
		// while the user is still on the wizard page.
		act(() => {
			useAppStore.setState({
				config: makeConfig({ onboarding_completed: true }),
			});
		});
		rerender();
		expect(mockReplace).toHaveBeenCalledWith("home");
	});

	it("ignores config writes to unrelated fields (field-level selector)", () => {
		setPage("onboarding");
		const { rerender } = renderHook(() =>
			useOnboardingRouteGuard({ currentPage, replace: mockReplace }),
		);
		expect(mockReplace).toHaveBeenCalledTimes(1);

		// An unrelated config write replaces the top-level config object
		// (mergeConfig always allocates) — the guard must NOT re-fire
		// because onboarding_completed is unchanged.
		act(() => {
			useAppStore.setState({
				config: makeConfig({
					onboarding_completed: true,
					theme_mode: "dark",
				}),
			});
		});
		rerender();
		expect(mockReplace).toHaveBeenCalledTimes(1);
	});
});
