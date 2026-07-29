// Pure builder for the AudioFilterChain labels dictionary.
//
// DR-15: previously this logic lived inline in `AudioFilterChain.tsx`
// as a `useMemo` factory with 80+ `t()` calls and 48 string keys
// hard-coded. Extracted here as a pure function so the i18n key list
// is owned by the registry (`audioFilterRowDescriptors`) — adding a
// new filter row only requires adding one descriptor, and the labels
// dictionary is auto-built from the registry.
//
// The function is pure (no React, no module state) — the caller
// wraps it in `useMemo` keyed on locale to avoid re-resolving labels
// on every render.

import { audioFilterRowDescriptors } from "./audioFilterRowDescriptors";

/**
 * Type signature of the i18n `t` function — kept local so this file
 * has no React/i18n module dependency and is unit-testable in
 * isolation.
 */
export type TFunction = (
	key: string,
	params?: Record<string, string>,
) => string;

/**
 * Labels dictionary — a flat `Record<i18nKey, resolvedString>`.
 * Each descriptor contributes its `labelKey`, `infoSearchKey`, and
 * `sectionTitleKey`; the section title key is shared across all
 * descriptors but resolved only once (deduplicated).
 *
 * Keyed by the full i18n key (e.g. `"settings.audioEnhancement.title"`)
 * so callers can look up by `descriptor.labelKey` /
 * `descriptor.infoSearchKey` / `descriptor.sectionTitleKey` without
 * any name-mangling.
 */
export type AudioFilterLabels = Record<string, string>;

/**
 * Resolves every i18n key referenced by `audioFilterRowDescriptors`
 * against the current locale via the supplied `t` function.
 *
 * Calls `t()` exactly once per unique key (the section title key is
 * shared across all descriptors but resolved only once). Total call
 * count: 24 (labelKey) + 24 (infoSearchKey) + 1 (sectionTitleKey) =
 * 49 — identical to the pre-DR-15 inline `useMemo` factory, so the
 * `TY-37` memoisation regression test's `firstRenderCount > 20`
 * sanity check continues to hold.
 */
export function buildAudioFilterLabels(t: TFunction): AudioFilterLabels {
	const labels: AudioFilterLabels = {};
	const seen = new Set<string>();
	for (const descriptor of audioFilterRowDescriptors) {
		for (const key of [
			descriptor.labelKey,
			descriptor.infoSearchKey,
			descriptor.sectionTitleKey,
		]) {
			if (seen.has(key)) continue;
			seen.add(key);
			labels[key] = t(key);
		}
	}
	return labels;
}
