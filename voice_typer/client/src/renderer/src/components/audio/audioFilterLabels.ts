// Pure builder for the AudioFilterChain labels dictionary.
// as a `useMemo` factory with 80+ `t()` calls and 48 string keys
// hard-coded. Extracted here as a pure function so the i18n key list
// is owned by the registry (`audioFilterRowDescriptors`), adding a
// new filter row only requires adding one descriptor, and the labels
// dictionary is auto-built from the registry.
// The function is pure (no React, no module state), the caller
// wraps it in `useMemo` keyed on locale to avoid re-resolving labels
// on every render.

import { audioFilterRowDescriptors } from "./audioFilterRowDescriptors";

export type TFunction = (
	key: string,
	params?: Record<string, string>,
) => string;

export type AudioFilterLabels = Record<string, string>;

export function buildAudioFilterLabels(t: TFunction): AudioFilterLabels {
	const labels: AudioFilterLabels = {};
	const seen = new Set<string>();
	for (const descriptor of audioFilterRowDescriptors) {
		for (const key of [
			descriptor.labelKey,
			descriptor.infoSearchKey,
			descriptor.sectionTitleKey,
			descriptor.infoKey,
			descriptor.ariaKey,
			...(descriptor.options ?? [])
				.map((opt) => opt.labelKey)
				.filter((k): k is string => typeof k === "string"),
		]) {
			if (seen.has(key)) continue;
			seen.add(key);
			labels[key] = t(key);
		}
	}
	return labels;
}
