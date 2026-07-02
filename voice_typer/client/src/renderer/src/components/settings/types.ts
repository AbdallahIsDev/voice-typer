// Shared prop types for settings section components.
//
// The Settings page (src/renderer/src/pages/Settings.tsx) owns the
// VoiceTyperConfig state, the updateConfig / updateConfigDebounced
// callbacks, and the search-filter function. Each section component
// receives these as props so it can render its rows identically to the
// previous monolithic implementation (no behaviour change).

import type { VoiceTyperConfig } from "@/types/config";

/** Visible-state predicate — matches the page-level `_filter_settings` helper. */
export type IsVisibleFn = (label: string, info?: string) => boolean;

/** Props every settings section accepts. */
export interface SettingsSectionSharedProps {
	/** Current Voice Typer config (null while the page is still loading). */
	config: VoiceTyperConfig | null;
	/** Commit a partial config update (also persists to the Python backend). */
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
	/**
	 * Debounced commit for inputs that fire on every keystroke (text fields,
	 * sliders). Updates local state immediately and schedules an IPC commit
	 * after `delayMs` of idle.
	 */
	updateConfigDebounced: (
		key: keyof VoiceTyperConfig,
		value: unknown,
		delayMs?: number,
	) => void;
	/**
	 * Search-filter predicate. Returns true when the row should be shown
	 * (either because the filter is empty or because the label/info matches
	 * the current query). Sections use this for both per-row visibility and
	 * the section-level "any item visible?" check.
	 */
	isVisible: IsVisibleFn;
}
