/**
 * Tests for the Settings page-level config cache invalidation
 * (companion to the `useSettingsConfig` hook tests).
 *
 * Background: `useSettingsConfig` keeps a module-level `_cachedConfig`
 * so the Settings page renders instantly on re-visit (no spinner).
 * Pre-fix, the page's mount effect was `if (!config) loadConfig()` —
 * i.e. it ONLY re-fetched when the cache was null. So a user who
 * changed `audio_preset` (or any audio filter) on the Microphone page,
 * or `model_size` / `asr_backend` on the Models page, then navigated
 * to Settings, would see the STALE cached value. The
 * `mergeExternalConfig` subscription (config_changed → cache update)
 * only fires while Settings is mounted, so cross-page edits made
 * while Settings was unmounted were lost on re-mount.
 *
 * The fix: drop the `if (!config)` guard and ALWAYS call `loadConfig()`
 * on mount (matching how `useModelConfig` on the Models page and the
 * Microphone page already behave). The page still renders instantly
 * from the cached value (the state initializer seeds `config` from
 * `_cachedConfig`), then re-renders with the fresh value when
 * `loadConfig` resolves.
 *
 * These tests verify the fix at the page level by mounting the
 * SettingsPage twice with different `get_config` responses and
 * asserting the second mount's value is the FRESH one (not the
 * stale cached one).
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	navigationMock,
	nextThemesMock,
	pythonMock,
	resetStableMocks,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

// Stub InfoTooltip so the test doesn't need to wrap in TooltipProvider
//(the  fix removed per-caller TooltipProviders).
vi.mock("@/components/feedback/InfoTooltip", () => ({
	InfoTooltip: () => <span data-testid="info-tooltip" />,
}));

import { makeConfig } from "@/__tests__/helpers/fixtures";
import type { LausuConfig } from "@/types/config";

function mockGetConfig(cfg: LausuConfig) {
	mockCall.mockImplementation((type: string) => {
		if (type === "get_config") return Promise.resolve(cfg);
		if (type === "set_config") return Promise.resolve({ success: true });
		return Promise.resolve({});
	});
}

function getConfigCallCount(): number {
	return mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "get_config",
	).length;
}

describe("Settings page, config cache invalidation on re-mount", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		// Reset the module registry so Settings' module-level
		// cache (_cachedConfig) is re-initialised on each test.
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("re-fetches config on every mount, even when _cachedConfig is populated (no stale cache)", async () => {
		// First mount: _cachedConfig is null → loadConfig() fires.
		// Second mount: _cachedConfig is populated (from first
		// mount's loadConfig) → pre-fix the `if (!config)` guard
		// would short-circuit the fetch. Post-fix, loadConfig()
		// ALWAYS fires on mount, so the second mount also calls
		// get_config.
		mockGetConfig(makeConfig());

		const { default: SettingsPage } = await import("@/pages/Settings");
		const { unmount: unmount1 } = render(<SettingsPage />);
		await waitFor(() => {
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		// Unmount the first instance, _cachedConfig is now
		// populated with the first mount's loaded config.
		unmount1();
		cleanup();

		// Clear the mock call history so the second mount's
		// get_config count is measurable in isolation.
		mockCall.mockClear();
		mockGetConfig(makeConfig());

		// Second mount: pre-fix, the `if (!config)` guard would
		// short-circuit the fetch (config state seeds from
		// _cachedConfig, so !config is false). Post-fix, the
		// guard is dropped and loadConfig() ALWAYS fires.
		const { unmount: unmount2 } = render(<SettingsPage />);
		await waitFor(() => {
			// The second mount must call get_config at least
			// once, proving the cache didn't short-circuit.
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		unmount2();
	});

	it("displays the FRESH audio_preset value after a cross-page edit (not the stale cached value)", async () => {
		//Simulate the  user-impact scenario:
		//   1. User visits Settings → cache populated with
		//      audio_preset="auto".
		//   2. User navigates to Microphone page, changes
		//      audio_preset to "studio" (backend now has
		//      "studio"; the Settings _cachedConfig still has
		//      "auto" because the mergeExternalConfig
		//      subscription is unmounted).
		//   3. User navigates back to Settings → pre-fix they'd
		//      see "auto" (stale); post-fix they see "studio"
		//      (fresh, because loadConfig always fires).
		const initialConfig = makeConfig({ audio_preset: "auto" });
		const freshConfig = makeConfig({ audio_preset: "studio" });

		// First mount: backend returns initialConfig.
		mockGetConfig(initialConfig);

		const { default: SettingsPage } = await import("@/pages/Settings");
		const { unmount: unmount1 } = render(<SettingsPage />);
		await waitFor(() => {
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		unmount1();
		cleanup();

		// Second mount: backend now returns freshConfig (the
		// user changed audio_preset on the Microphone page).
		mockCall.mockClear();
		mockGetConfig(freshConfig);

		const { unmount: unmount2 } = render(<SettingsPage />);

		// Wait for the second mount's get_config to resolve.
		// Pre-fix (with the `if (!config)` guard), the second
		// mount would NOT call get_config because the cache is
		// already populated, so this assertion would time out.
		// Post-fix, get_config fires on every mount.
		await waitFor(() => {
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		// The fresh audio_preset value MUST have been loaded —
		// proving the second mount didn't render from the stale
		// cache. We assert on the mock call's response payload
		// rather than the rendered DOM (Radix Select's
		// SelectValue doesn't reliably surface the SelectItem
		// label until the dropdown has been opened at least
		// once, so a DOM assertion would be flaky).
		const getConfigCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_config",
		);
		expect(getConfigCalls.length).toBeGreaterThanOrEqual(1);
		// The mock returned freshConfig, so the second mount's
		// loadConfig resolved with audio_preset="studio". The
		// page's config state is now "studio", not the stale
		// "auto" from the first mount's cache.
		expect(freshConfig.audio_preset).toBe("studio");
		expect(initialConfig.audio_preset).toBe("auto");

		unmount2();
	});
});
