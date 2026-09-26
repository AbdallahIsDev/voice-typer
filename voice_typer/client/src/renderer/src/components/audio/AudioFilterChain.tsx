// AudioFilterChain, the shared individual-filter UI used by both
// `Settings → Audio` (custom preset) and the Microphone test page's
// collapsible preset selector.
// the high-pass / noise-suppression / noise-gate / EQ / compressor /
// limiter / notch rows, with subtly different wrappers (SettingRow in
// the Settings page vs custom ToggleRow/SliderRow in the microphone
// page) and the microphone page lagging on i18n. This component
// consolidates them into one canonical implementation that uses the
// shared `SettingRow` + `RangeSlider` primitives and the shared
// `t()` translation keys.
// Both call sites pass the same `config` (the full LausuConfig)
// and an `onConfigChange` callback that receives a partial update.
// The component is purely presentational, it does not mutate config
// directly.
// The optional `isVisible` prop wires the per-row search filter that
// the Settings page already uses for its own SettingRows. When the
// user types a query into the Settings search box, the parent
// Settings page hands `isVisible` down through AudioSettingsSection →
// AudioFilterChain, and each SettingRow here is wrapped in
// `filterIsVisible(label, infoSearch, audioSectionTitle) && …` so a
// query like "compressor ratio" surfaces ONLY the matching row
// instead of the entire custom filter chain. Non-Settings call sites
// (e.g. the Microphone test page's preset selector) omit the
// prop and fall back to a permissive `() => true`, preserving their
// existing behaviour.
//the body is now a single `.map` over
// `audioFilterRowDescriptors` (the registry IS the render spec). All
// per-row rendering metadata, configKey, kind, min/max/step, suffix,
// aria/info keys, defaultValue, parentToggle, lives in the
// descriptor. `FilterRow` does the actual `<SettingRow>` + control
// rendering. The labels dictionary is built once per locale via
// `buildAudioFilterLabels(t)`. The original 935-LOC file is now
// ~150 LOC.

import { useCallback, useMemo, useSyncExternalStore } from "react";
import { getLocaleSnapshot, subscribeLocale, t } from "@/i18n/i18n";
import type { LausuConfig } from "@/types/config";
import { buildAudioFilterLabels } from "./audioFilterLabels";
import { audioFilterRowDescriptors } from "./audioFilterRowDescriptors";
import { type AudioFilterSet, FilterRow } from "./FilterRow";

export interface AudioFilterChainProps {
	config: LausuConfig;
	onConfigChange: (updates: Partial<LausuConfig>) => void;
	isVisible?: (label: string, info: string, sectionTitle: string) => boolean;
}

export function AudioFilterChain({
	config,
	onConfigChange,
	isVisible,
}: AudioFilterChainProps) {
	// Permissive default so non-Settings call sites (the
	// Microphone test page's preset selector) keep rendering
	// every row unconditionally.
	const filterIsVisible = isVisible ?? (() => true);

	// Subscribe to locale changes so the memoised labels below
	// re-resolve when the user switches language. Without this
	// `useSyncExternalStore` call, `AudioFilterChain` would never
	// re-render on locale change (the imported `t` is a plain function
	// with no React subscription), so the labels would stay in the old
	// language until a parent re-rendered for some other reason.
	const _locale = useSyncExternalStore(
		subscribeLocale,
		getLocaleSnapshot,
		getLocaleSnapshot,
	);

	// A single generic helper replaces the 25 per-field inline
	//handlers from the pre- JSX. The generic `K` ensures the
	// key/value pair stays type-checked against `LausuConfig`
	// (call sites that pass a literal `configKey` get full inference;
	// the data-driven `FilterRow` path casts through `never`).
	// Wrapped in `useCallback` keyed on `onConfigChange` so the
	// identity is stable across re-renders (the inline closures passed
	// to `Switch.onCheckedChange` / `RangeSlider.onChange` capture
	// `set`, if `set` changed identity every render, those closures
	// would too, defeating memoisation downstream).
	const set: AudioFilterSet = useCallback(
		<K extends keyof LausuConfig>(k: K, v: LausuConfig[K]): void => {
			onConfigChange({ [k]: v } as Partial<LausuConfig>);
		},
		[onConfigChange],
	);

	// Resolve the translated search-visible labels ONCE per locale
	// per render = 0.5–1 ms wasted per Settings interaction).
	// The memo key is `_locale` (a stable string from
	// `useSyncExternalStore`); the `t` function reads the current
	// locale's translation map at call time, so all calls inside the
	// factory resolve against the same locale.
	// biome-ignore lint/correctness/useExhaustiveDependencies: _locale triggers re-run on language switch
	const labels = useMemo(() => buildAudioFilterLabels(t), [_locale]);

	// Stable bundle of the props `<FilterRow>` needs (so it can be
	// spread with `{...sectionProps}` without creating fresh object
	// identity per render, `<FilterRow>` is not memoised, but a
	// stable `sectionProps` keeps the GC churn down).
	const sectionProps = useMemo(
		() => ({ config, set, labels }),
		[config, set, labels],
	);

	return (
		<>
			{audioFilterRowDescriptors.map((descriptor) => {
				const label = labels[descriptor.labelKey] ?? descriptor.labelKey;
				const infoSearch =
					labels[descriptor.infoSearchKey] ?? descriptor.infoSearchKey;
				const sectionTitle =
					labels[descriptor.sectionTitleKey] ?? descriptor.sectionTitleKey;
				const rowVisible = filterIsVisible(label, infoSearch, sectionTitle);
				if (!rowVisible) return null;
				return (
					<FilterRow
						key={descriptor.configKey as string}
						descriptor={descriptor}
						{...sectionProps}
					/>
				);
			})}
		</>
	);
}
