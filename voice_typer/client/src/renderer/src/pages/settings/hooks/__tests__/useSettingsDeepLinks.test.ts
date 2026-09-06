/**
 * Focused tests for `useSettingsDeepLinks` — the extracted Settings
 * consent + cross-page search deep-link machinery.
 *
 * Drives the REAL useNavigation + useGlobalSearch zustand stores (no
 * module mocks — the stores are the app's single source of truth) and
 * pins:
 *   - one-shot consumption of the pending consent field (armed exactly
 *     once, global search cleared, the Privacy surface's saved scroll
 *     offset zeroed),
 *   - one-shot consumption of the pending search scroll target rowHint,
 *   - the consent row scroll + 2600ms ring lifetime,
 *   - the 5000ms max-lifetime safety net for never-rendering targets.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { _resetNavigationForTest, useNavigation } from "@/hooks/useNavigation";
import { useSettingsDeepLinks } from "@/pages/settings/hooks/useSettingsDeepLinks";
import type { VoiceTyperConfig } from "@/types/config";

const config: VoiceTyperConfig = makeConfig({});

function mount(
	overrides: Partial<Parameters<typeof useSettingsDeepLinks>[0]> = {},
) {
	const scrollPositionsRef = { current: {} as Record<string, number> };
	const utils = renderHook(() => {
		// The REAL nav-store hook — the zustand store behind it is
		// the app's single source of truth (no module mocks). Its
		// result exposes the transient deep-link channels
		// reactively, so tests arm a target through the production
		// path (`navigate(..., { consentField } / { settingsScrollTarget })`)
		// and read the consumption back off the same store.
		const nav = useNavigation();
		const deepLinks = useSettingsDeepLinks({
			config,
			page: "settingsPrivacy",
			scrollPositionsRef,
			...overrides,
		});
		return { nav, deepLinks };
	});
	return { ...utils, scrollPositionsRef };
}

beforeEach(() => {
	localStorage.clear();
	_resetNavigationForTest();
	useGlobalSearch.setState({ query: "" });
	document.body.innerHTML = "";
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = "";
});

describe("useSettingsDeepLinks — pending target consumption", () => {
	it("consumes a pending consent field once, clears the query, zeroes the Privacy scroll offset", () => {
		useGlobalSearch.setState({ query: "stale filter" });

		const { result, scrollPositionsRef, rerender } = mount();
		// Arm the consent deep-link through the real production
		// path: navigate() stages `pendingConsentField` in the nav
		// store, the mounted hook consumes it.
		act(() => {
			result.current.nav.navigate("settings", {
				consentField: "voice_biometric_consent",
			});
		});
		rerender();

		expect(result.current.deepLinks.focusedConsentField).toBe(
			"voice_biometric_consent",
		);
		// The search filter is cleared so the consent row is visible.
		expect(useGlobalSearch.getState().query).toBe("");
		// The Privacy surface's saved offset is zeroed so the
		// surface-scroll restore never fights the deep-link scroll.
		expect(scrollPositionsRef.current.settingsPrivacy).toBe(0);
		// The pending target is consumed (store reset to null).
		expect(result.current.nav.pendingConsentField).toBeNull();

		// A re-render without a new pending target must NOT re-arm.
		rerender();
		expect(result.current.deepLinks.focusedConsentField).toBe(
			"voice_biometric_consent",
		);
	});

	it("consumes a pending search scroll target and arms the rowHint", () => {
		const { result, rerender } = mount();
		act(() => {
			result.current.nav.navigate("settings", {
				settingsScrollTarget: { rowHint: "Prewarm Status" },
			});
		});
		rerender();

		expect(result.current.deepLinks.searchScrollHint).toBe("Prewarm Status");
		expect(result.current.nav.pendingSettingsScrollTarget).toBeNull();
		// The consent channel is untouched.
		expect(result.current.deepLinks.focusedConsentField).toBeNull();
	});
});

describe("useSettingsDeepLinks — scroll + highlight lifetime", () => {
	it("scrolls the consent row into view once rendered and clears the ring after 2600ms", () => {
		vi.useFakeTimers();
		const scrollSpy = vi.fn();
		(
			Element.prototype as unknown as { scrollIntoView: unknown }
		).scrollIntoView = scrollSpy;
		const row = document.createElement("div");
		row.setAttribute("data-consent-field", "voice_biometric_consent");
		document.body.appendChild(row);

		const { result, rerender } = mount();
		act(() => {
			result.current.nav.navigate("settings", {
				consentField: "voice_biometric_consent",
			});
		});
		rerender();
		expect(result.current.deepLinks.focusedConsentField).toBe(
			"voice_biometric_consent",
		);

		// The bounded retry's first attempt runs on a 0ms timer.
		act(() => {
			vi.advanceTimersByTime(1);
		});
		expect(scrollSpy).toHaveBeenCalledWith(
			expect.objectContaining({ behavior: "smooth", block: "center" }),
		);

		// Ring lifetime starts when the row is found: 2600ms later the
		// highlight state is cleared.
		act(() => {
			vi.advanceTimersByTime(2600);
		});
		expect(result.current.deepLinks.focusedConsentField).toBeNull();
	});

	it("clears a never-rendering target via the 5000ms max-lifetime safety net", () => {
		vi.useFakeTimers();
		const { result, rerender } = mount();
		act(() => {
			result.current.nav.navigate("settings", {
				settingsScrollTarget: { rowHint: "ghost label" },
			});
		});
		rerender();
		expect(result.current.deepLinks.searchScrollHint).toBe("ghost label");

		// The bounded retry keeps polling (~3s); nothing renders.
		act(() => {
			vi.advanceTimersByTime(3000);
		});
		// But the safety net fires at 5s — a stale target can't linger.
		act(() => {
			vi.advanceTimersByTime(2100);
		});
		expect(result.current.deepLinks.searchScrollHint).toBeNull();
	});
});
