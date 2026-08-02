/**
 *  regression test: AudioFilterChain hoists ALL `info` / `ariaLabel` /
 * `aria-label` strings into a per-locale `useMemo`.
 *
 * Pre-: the component had ~48 INLINE `t(...)` calls in the JSX (one
 * per `info` / `ariaLabel` / `aria-label` prop on every SettingRow /
 * Switch / RangeSlider), in addition to the existing `labels` memo (which
 * only covered the search-visible label/infoSearch keys). Every parent
 * re-render (e.g. a slider drag) re-resolved all 48 inline strings.
 *
 * Post-: a second `uiText` useMemo (keyed on `[_locale]`) hoists
 * all 48 inline strings; the JSX now references `uiText.highPassFilterInfo`
 * / `uiText.highPassFilterAria` / etc. instead of calling `t(...)` inline.
 *
 * This test asserts:
 *   1. On the FIRST render, `t()` IS called for every label + uiText
 *      entry (initial resolution).
 *   2. On a SUBSEQUENT render with the SAME props, `t()` is NOT called
 *      at all (both memos hit; the JSX reads from the cached object).
 *   3. When `locale` changes, `t()` is called again (the memos re-resolve).
 */
import { act, cleanup, render } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) => {
	const wrapped = (node: React.ReactElement) => (
		<TooltipProvider delayDuration={200}>{node}</TooltipProvider>
	);
	const utils = render(wrapped(ui));
	return {
		...utils,
		rerender: (node: React.ReactElement) => utils.rerender(wrapped(node)),
	};
};

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AudioFilterChain } from "@/components/audio/AudioFilterChain";
import * as i18n from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";

// Stub the icons used by SettingRow / Radix Select / Switch so the
// render graph doesn't pull in the full hugeicons dependency tree.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return new Proxy(
		{
			ArrowDown01Icon: make("ArrowDown01Icon"),
			Tick02Icon: make("Tick02Icon"),
			ArrowUp01Icon: make("ArrowUp01Icon"),
			FilterIcon: make("FilterIcon"),
			UnfoldMoreIcon: make("UnfoldMoreIcon"),
		},
		{
			get(target, prop: string) {
				if (prop in target) {
					return (target as Record<string, unknown>)[prop];
				}
				return make(prop);
			},
		},
	);
});

function makeStubConfig(): VoiceTyperConfig {
	return {
		audio_preset: "custom",
		noise_filter_highpass: true,
		noise_filter_highpass_cutoff_hz: 80,
		noise_suppression_method: "rnnoise",
		noise_filter_gate: true,
		noise_filter_gate_open_threshold_db: -45,
		noise_filter_gate_close_threshold_db: -55,
		noise_filter_gate_attack_ms: 5,
		noise_filter_gate_hold_ms: 50,
		noise_filter_gate_release_ms: 100,
		noise_filter_eq_low_db: 0,
		noise_filter_eq_mid_db: 0,
		noise_filter_eq_high_db: 0,
		noise_filter_compressor: true,
		noise_filter_compressor_threshold_db: -20,
		noise_filter_compressor_ratio: 4,
		noise_filter_compressor_attack_ms: 5,
		noise_filter_compressor_release_ms: 50,
		noise_filter_compressor_output_gain_db: 0,
		noise_filter_limiter: true,
		noise_filter_limiter_ceiling_db: -1,
		noise_filter_limiter_release_ms: 100,
		noise_filter_notch: false,
		noise_filter_notch_frequency_hz: 50,
	} as unknown as VoiceTyperConfig;
}

describe("DJ-88: AudioFilterChain hoists ALL info/aria strings into a per-locale useMemo", () => {
	let tSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		// Spy on the module's `t` export. `AudioFilterChain` imports
		// `t` from `@/i18n/i18n`, so spying on the module export
		// intercepts every call (including the ones inside the
		// `useMemo` factories and the previously-inline JSX props).
		tSpy = vi.spyOn(i18n, "t");
		// Reset to a known locale so the test is deterministic.
		act(() => {
			i18n.setLocale("en");
		});
	});

	afterEach(() => {
		tSpy.mockRestore();
		cleanup();
	});

	it("does NOT call t() on re-render with the same props (both memos hit)", () => {
		const config = makeStubConfig();
		const onConfigChange = vi.fn();

		const { rerender } = renderWithProviders(
			<AudioFilterChain config={config} onConfigChange={onConfigChange} />,
		);

		// First render: t() is called for every label + uiText entry
		// (initial resolution). The exact count is ~97 (49 labels +
		// 49 uiText entries minus 1 duplicate title), but we just
		// assert "many" — the important check is the DELTA below.
		const firstRenderCount = tSpy.mock.calls.length;
		expect(firstRenderCount).toBeGreaterThan(40);

		// Clear the spy so we count ONLY second-render calls.
		tSpy.mockClear();

		// Re-render with the SAME props. Both `labels` and `uiText`
		// memos hit (locale is unchanged), so t() should NOT be called
		// at all — the JSX reads from the cached `uiText` object.
		rerender(
			<AudioFilterChain config={config} onConfigChange={onConfigChange} />,
		);

		expect(tSpy).not.toHaveBeenCalled();
	});

	it("re-resolves t() when the locale changes (memos invalidate)", () => {
		const config = makeStubConfig();
		const onConfigChange = vi.fn();

		const { rerender } = renderWithProviders(
			<AudioFilterChain config={config} onConfigChange={onConfigChange} />,
		);

		tSpy.mockClear();

		// Switch locale. The `useSyncExternalStore` subscription in
		// `AudioFilterChain` fires, the component re-renders, and BOTH
		// `labels` and `uiText` useMemos re-resolve because `locale`
		// changed.
		act(() => {
			i18n.setLocale("es");
		});

		// Force a re-render to ensure the locale-change re-render has
		// been flushed (the useSyncExternalStore re-render may be
		// batched).
		rerender(
			<AudioFilterChain config={config} onConfigChange={onConfigChange} />,
		);

		// After a locale change, BOTH memos re-resolve, so t() is
		// called for ~98 keys (49 labels + 49 uiText).
		const afterLocaleChangeCount = tSpy.mock.calls.length;
		expect(afterLocaleChangeCount).toBeGreaterThan(40);

		// Restore to English for any subsequent tests.
		act(() => {
			i18n.setLocale("en");
		});
	});
});
