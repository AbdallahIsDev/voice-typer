// SettingsHub — the Settings landing page.
//
// ONE card whose rows are the Settings section pages (see
// `settingsSections.ts`). Each row shows the section title, a muted
// description, the row's CURRENT VALUE summary (iOS-Settings-style, so
// the state is scannable without entering), and a forward chevron;
// activating the row navigates to the focused section page.
//
// Search integration: when the global title-bar query is non-empty, rows
// whose section (title, description, or any row label) matches stay
// visible and the matched row labels render under the description — the
// user can see WHY a section matched without entering it. Rows for
// sections with no match are hidden; if nothing matches anywhere the
// caller-supplied empty state renders instead.

import { ArrowRight01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMemo } from "react";
import { EmptyState } from "@/components/feedback/EmptyState";
import { formatHotkey } from "@/components/hotkey/hotkey-format";
import {
	SETTINGS_SECTIONS,
	type SettingsSectionPage,
} from "@/components/settings/settingsSections";
import { getSectionLabels } from "@/components/settings/settingsTabLabels";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { getLocale, getLocaleLabel, t, useT } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import { LANGUAGE_OPTIONS } from "@/lib/utils/languages";
import type { VoiceTyperConfig } from "@/types/config";

/** Current-value label for an audio filter-chain preset. */
const AUDIO_PRESET_SUMMARY_KEYS: Record<
	VoiceTyperConfig["audio_preset"],
	string
> = {
	auto: "settings.audioEnhancement.presetAuto",
	studio: "settings.audioEnhancement.presetStudio",
	noisy_room: "settings.audioEnhancement.presetNoisyRoom",
	off: "settings.audioEnhancement.presetOff",
	custom: "settings.audioEnhancement.presetCustom",
};

/** Current-value label for the color scheme. */
const THEME_MODE_SUMMARY_KEYS: Record<VoiceTyperConfig["theme_mode"], string> =
	{
		system: "settings.appearance.systemDefault",
		light: "settings.appearance.light",
		dark: "settings.appearance.dark",
	};

/**
 * The right-edge summary for a hub row — the section's current value,
 * iOS-style. Returns null for sections whose state doesn't compress
 * into one short label (Privacy consents, Advanced tooling) — those
 * rows end at the chevron.
 *
 * Reads the CURRENT locale for locale-dependent summaries (app
 * language, transcription language) so the summary follows a locale
 * switch without a remount (the component subscribes via `useT`).
 */
function sectionSummary(
	page: SettingsSectionPage,
	config: VoiceTyperConfig,
): string | null {
	switch (page) {
		case "settingsGeneral":
			// The UI language itself — mirrors the App Language select.
			return getLocaleLabel(getLocale());
		case "settingsOverlay":
			return t(
				config.bubble_behavior === "always_visible"
					? "settings.bubbleBehaviorAlwaysVisible"
					: "settings.bubbleBehaviorShowOnRecord",
			);
		case "settingsHotkeys":
			return formatHotkey(config.hotkey);
		case "settingsTranscription": {
			const lang = config.language;
			if (!lang) return t("settings.languageAutoDetect");
			const option = LANGUAGE_OPTIONS.find((l) => l.value === lang);
			return option ? t(option.labelKey) : lang;
		}
		case "settingsAI":
			return config.llm_polish ? t("settings.hub.on") : t("settings.hub.off");
		case "settingsAudio":
			return t(
				AUDIO_PRESET_SUMMARY_KEYS[config.audio_preset] ??
					"settings.audioEnhancement.presetAuto",
			);
		case "settingsAppearance":
			return t(
				THEME_MODE_SUMMARY_KEYS[config.theme_mode] ??
					"settings.appearance.systemDefault",
			);
		case "settingsPrivacy":
			return null;
		case "settingsAdvanced":
			return null;
	}
}

export interface SettingsHubProps {
	/** Loaded config — the hub only renders once Settings has it. */
	config: VoiceTyperConfig;
	/** Navigate to a section page (wired to the nav store by Settings). */
	onNavigateSection: (page: SettingsSectionPage) => void;
}

export function SettingsHub({ config, onNavigateSection }: SettingsHubProps) {
	const t = useT();
	const query = useGlobalSearch((s) => s.query);
	const clearQuery = useGlobalSearch((s) => s.clearQuery);
	const q = query.trim().toLowerCase();

	// One row model per section: title/description/summary plus, when a
	// query is active, the matched row labels (deduped — two section
	// titles can translate to the same word). Sections with no match at
	// all drop out of the list entirely.
	const rows = useMemo(() => {
		const labelsBySection = getSectionLabels();
		return SETTINGS_SECTIONS.flatMap((def) => {
			const title = t(def.titleKey);
			const description = t(def.descriptionKey);
			if (!q) {
				return [
					{
						def,
						title,
						description,
						summary: sectionSummary(def.page, config),
						matchedLabels: [] as string[],
					},
				];
			}
			const matchedLabels = [...new Set(labelsBySection[def.page])].filter(
				(label) => label.toLowerCase().includes(q),
			);
			const sectionItselfMatches =
				title.toLowerCase().includes(q) ||
				description.toLowerCase().includes(q);
			if (matchedLabels.length === 0 && !sectionItselfMatches) return [];
			return [
				{
					def,
					title,
					description,
					summary: sectionSummary(def.page, config),
					matchedLabels,
				},
			];
		});
		// `q` and `config` drive everything; `t` is a stable module
		// function but included for lint honesty since the labels resolve
		// through the reactive locale subscription.
	}, [q, config, t]);

	if (q && rows.length === 0) {
		return (
			<EmptyState
				variant="info"
				icon={Search01Icon}
				title={t("settings.searchNoMatch", { query: query.trim() })}
				description={t("settings.noResultsMessage")}
				actionLabel={t("a11y.clearSearch")}
				onAction={clearQuery}
			/>
		);
	}

	return (
		<section aria-label={t("settings.title")}>
			{/* THE single hub card — same surface treatment as every
			    SettingsSection card in the app (border + subtle bg + row
			    dividers), rows as full-width buttons. overflow-hidden keeps
			    the hover highlight inside the rounded corners. */}
			<div className="overflow-hidden rounded-lg border border-border/5 bg-(--bg-subtle) divide-y divide-border/5">
				{rows.map((row) => (
					<button
						key={row.def.page}
						type="button"
						data-testid={`settings-hub-row-${row.def.page}`}
						className={cn(
							"flex w-full items-center gap-4 p-4 text-start",
							"transition-colors duration-150 hover:bg-foreground/5",
							// Focus contract (C-FOCUS-2/5): full-opacity ring token,
							// 3px — keyboard focus is always clearly visible.
							"focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none",
						)}
						onClick={() => onNavigateSection(row.def.page)}
					>
						<HugeiconsIcon
							icon={row.def.icon}
							strokeWidth={2}
							aria-hidden="true"
							className="h-5 w-5 shrink-0 text-(--text-muted)"
						/>
						<span className="flex min-w-0 flex-1 flex-col gap-2">
							<span className="flex flex-col gap-0.5">
								<span className="block text-sm font-medium text-(--text-primary)">
									{row.title}
								</span>
								<span className="block truncate text-sm text-(--text-muted)">
									{row.description}
								</span>
							</span>
							{row.matchedLabels.length > 0 && (
								<span className="flex flex-wrap gap-2">
									{row.matchedLabels.map((label) => (
										<span
											key={label}
											className="rounded-md border border-border/10 bg-(--bg) px-1.5 py-0.5 text-xs text-(--text-muted)"
										>
											{label}
										</span>
									))}
								</span>
							)}
						</span>
						<span className="flex shrink-0 items-center gap-2">
							{row.summary !== null && (
								<span className="max-w-45 truncate text-sm text-(--text-muted)">
									{row.summary}
								</span>
							)}
							<HugeiconsIcon
								icon={ArrowRight01Icon}
								strokeWidth={2}
								aria-hidden="true"
								className="nav-directional-icon h-4 w-4 text-(--text-muted)"
							/>
						</span>
					</button>
				))}
			</div>
		</section>
	);
}
