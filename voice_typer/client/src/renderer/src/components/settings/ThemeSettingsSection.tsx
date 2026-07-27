// ThemeSettingsSection — Appearance section of the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx to keep the page
// file under ~500 lines.  Renders the "Appearance" SettingsSection
// (Color Scheme, Theme Preset, Custom Theme color picker, Text Size).
//
// Behaviour is identical to the previous monolithic implementation:
// - Hover-preview of built-in presets (reverts on close/leave).
// - Custom-theme color picker with light/dark tabs and a 6-color grid.
// - localStorage draft backup so unsaved colour picks survive a crash.
// - 300 ms debounced save of the custom-theme draft.
// - Per-row search-filter visibility via the ``isVisible`` prop.
//
// DT-32 partial split: the state machine, refs, effects, and event
// handlers now live in ``./useThemeSettings`` (a custom hook). The
// WCAG contrast helpers and the localStorage draft helpers live in
// ``@/lib/theme-contrast`` and ``@/lib/theme-draft-storage``
// respectively. This file is now JSX-only: it calls the hook, reads
// translations, and renders the section.

import {
	ModernTvIcon,
	Moon02Icon,
	Sun01Icon,
} from "@hugeicons/core-free-icons";
import { memo } from "react";
import { RangeSlider } from "@/components/common/RangeSlider";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { useT } from "@/i18n/i18n";
import { computeRowContrast, HEX_STRICT_RE } from "@/lib/theme-contrast";
import { cn } from "@/lib/utils";
import {
	CUSTOM_COLOR_KEYS,
	DEFAULT_CUSTOM_DARK,
	DEFAULT_CUSTOM_LIGHT,
	THEMES,
} from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import { SettingsSkeleton } from "./SettingsSkeleton";
import type { SettingsSectionSharedProps } from "./types";
import {
	_getThemeNameKey,
	getThemePreviewColors,
	useThemeSettings,
} from "./useThemeSettings";

// IMPL-C: option value → icon + aria-label for the icon-only theme toggle.
// The visible label is empty — only the icon is shown, matching the
// ThemeSwitch in the sidebar.  The aria-label on the radiogroup and the
// title on each option provide screen-reader context.
const _THEME_OPTION_KEYS = [
	{
		value: "system",
		icon: ModernTvIcon,
		labelKey: "settings.appearance.systemDefault",
	},
	{ value: "light", icon: Sun01Icon, labelKey: "settings.appearance.light" },
	{ value: "dark", icon: Moon02Icon, labelKey: "settings.appearance.dark" },
] as const;

interface ThemeSettingsSectionProps extends SettingsSectionSharedProps {
	/** Theme mode provided by the App-level useTheme hook (overrides config while a save is in-flight). */
	themeModeProp?: VoiceTyperConfig["theme_mode"];
	/** App-level theme-change handler — persists the mode via the debounced save in useTheme. */
	onThemeChange?: (mode: VoiceTyperConfig["theme_mode"]) => void;
	/**
	 * Theme preset provided by the App-level useTheme hook (overrides
	 * ``config.theme_preset`` while a save is in-flight).  Without
	 * this prop, the preset dropdown showed a stale
	 * value during the 300 ms debounced save window — the user
	 * clicked a preset, the dropdown reverted to the old value, then
	 * snapped to the new value when the backend confirmed.  Passing
	 * the optimistic value from ``useTheme.themePreset`` lets the
	 * dropdown update immediately, matching the colour-scheme
	 * segmented control's behaviour.
	 */
	themePresetProp?: VoiceTyperConfig["theme_preset"];
}

export const ThemeSettingsSection = memo(function ThemeSettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
	isVisible,
	themeModeProp,
	onThemeChange,
	themePresetProp,
}: ThemeSettingsSectionProps) {
	const {
		customEditorMode,
		customDraft,
		hexDrafts,
		effectivePreset,
		customDraftIsDefault,
		handleColorSchemeChange,
		handleThemePresetChange,
		handleSelectOpenChange,
		handleThemeHover,
		handleCustomThemeToggle,
		handleSetLightMode,
		handleSetDarkMode,
		handleColorInputChange,
		handleHexInputChange,
		handleHexInputBlur,
		handleResetCustomColors,
		handleTextSizeChange,
		handleSelectMouseMove,
		revertToSavedPreset,
	} = useThemeSettings({
		config,
		updateConfig,
		updateConfigDebounced,
		themeModeProp,
		onThemeChange,
		themePresetProp,
	});

	const t = useT();

	if (!config) return <SettingsSkeleton rows={3} />;

	// IMPL-C: resolve i18n keys once per render so the isVisible predicate
	// and the rendered output share the same translated strings.
	const colorSchemeLabel = t("settings.appearance.colorScheme");
	const colorSchemeInfoSearch = t("settings.appearance.colorSchemeInfo");
	const themePresetLabel = t("settings.appearance.themePreset");
	const themePresetInfoSearch = t("settings.appearance.themePresetInfo");
	const customThemeLabel = t("settings.appearance.customTheme");
	const customThemeInfoSearch = t("settings.appearance.customThemeInfo");
	const textSizeLabel = t("settings.appearance.textSize");
	const textSizeInfoSearch = t("settings.appearance.textSizeInfo");
	const themeOptions = _THEME_OPTION_KEYS.map((opt) => ({
		value: opt.value,
		// Icon-only toggle — no visible text, matching the sidebar ThemeSwitch.
		label: "",
		icon: opt.icon,
		title: t(opt.labelKey),
	}));

	// FIX (Task ID 6 / Settings Search): capture the section title in a
	// local constant so the SAME value feeds both the
	// ``<SettingsSection title="…">`` prop AND the ``isVisible(...)``
	// predicate's new third argument. This lets the search match the
	// section heading (e.g. typing "appearance" surfaces every row in
	// this section even if the row's own label/info don't contain it).
	const sectionTitle = t("settings.appearance.title");

	// UX-028: section-level visibility check — if no row matches the
	// search filter, hide the entire section.
	const sectionItems = [
		{ label: colorSchemeLabel, info: colorSchemeInfoSearch },
		{ label: themePresetLabel, info: themePresetInfoSearch },
		{ label: customThemeLabel, info: customThemeInfoSearch },
		{ label: textSizeLabel, info: textSizeInfoSearch },
	];
	if (
		!sectionItems.some((item) => isVisible(item.label, item.info, sectionTitle))
	) {
		return null;
	}

	return (
		<SettingsSection
			title={sectionTitle}
			description={t("settings.appearance.description")}
		>
			{/* ── Color Scheme (light / dark / system) ───────────── */}
			{isVisible(colorSchemeLabel, colorSchemeInfoSearch, sectionTitle) && (
				<SettingRow
					label={colorSchemeLabel}
					info={t("settings.appearance.colorSchemeInfo")}
				>
					<SegmentedControl
						options={themeOptions}
						value={themeModeProp ?? config.theme_mode}
						onChange={handleColorSchemeChange}
						ariaLabel={t("settings.appearance.colorSchemeAria")}
					/>
				</SettingRow>
			)}

			{/* ── Theme Preset selector with hover preview ──────── */}
			{isVisible(themePresetLabel, themePresetInfoSearch, sectionTitle) && (
				<SettingRow
					label={themePresetLabel}
					info={t("settings.appearance.themePresetInfoRendered")}
				>
					<Select
						value={effectivePreset}
						onValueChange={handleThemePresetChange}
						onOpenChange={handleSelectOpenChange}
					>
						<SelectTrigger
							className="w-44"
							aria-label={t("settings.appearance.themePresetAria")}
						>
							{(() => {
								const currentId = effectivePreset;
								const current =
									THEMES.find((t) => t.id === currentId) ?? THEMES[0];
								// Part C1/C2: rounded-rectangle "E" preview.  For the
								// custom theme, use the actual custom colours from the
								// in-memory draft instead of the static placeholder swatch.
								const isDark =
									document.documentElement.classList.contains("dark");
								const { bg, fg } = getThemePreviewColors(
									currentId,
									isDark,
									customDraft,
								);
								// I18N-NAMEKEY: prefer the localised theme name (via
								// ``t(theme.nameKey)``) when the preset declares a
								// ``nameKey`` field.  Falls back to the preset's
								// hardcoded English ``name`` when the field is absent.
								const nameKey = _getThemeNameKey(current);
								const displayName = nameKey ? t(nameKey) : current.name;
								return (
									<span className="flex items-center gap-2">
										<span
											className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold"
											style={{ backgroundColor: bg, color: fg }}
										>
											A
										</span>
										<span>{displayName}</span>
									</span>
								);
							})()}
						</SelectTrigger>
						<SelectContent
							position="popper"
							align="start"
							onMouseMove={handleSelectMouseMove}
							onMouseLeave={revertToSavedPreset}
						>
							{/* render a DISABLED "Custom (use toggle below)" SelectItem
									when the saved preset is 'custom'.  Without this, the
									dropdown's trigger showed a blank value when the preset
									was 'custom' (the SelectItem list filtered 'custom' out),
									making it look like the dropdown was broken.  The disabled
									item is non-selectable — users toggle the custom theme via
									the switch below the dropdown.  Always rendered so the
									trigger's selected value always has a matching SelectItem
									(Radix Select otherwise warns about a missing value). */}
							{(() => {
								const customThemeDef = THEMES.find((t) => t.id === "custom");
								if (!customThemeDef) return null;
								const isDark =
									document.documentElement.classList.contains("dark");
								const { bg, fg } = getThemePreviewColors(
									"custom",
									isDark,
									customDraft,
								);
								const customLabel = t(
									"settings.appearance.customDropdownLabel",
								);
								return (
									<SelectItem
										key="custom-disabled"
										value="custom"
										disabled
										className="opacity-60"
									>
										<span className="flex items-center gap-2.5">
											<span
												className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold"
												style={{
													backgroundColor: bg,
													color: fg,
												}}
											>
												A
											</span>
											<span className="text-sm font-medium">{customLabel}</span>
										</span>
									</SelectItem>
								);
							})()}
							{/* Built-in presets (excluding 'custom' — handled by the
									disabled item above). */}
							{THEMES.filter((t) => t.id !== "custom").map((theme) => {
								const isDark =
									document.documentElement.classList.contains("dark");
								const { bg, fg } = getThemePreviewColors(
									theme.id,
									isDark,
									customDraft,
								);
								const nameKey = _getThemeNameKey(theme);
								const displayName = nameKey ? t(nameKey) : theme.name;
								return (
									<SelectItem
										key={theme.id}
										value={theme.id}
										onMouseEnter={handleThemeHover(theme.id)}
									>
										<span className="flex items-center gap-2.5">
											<span
												className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold"
												style={{ backgroundColor: bg, color: fg }}
											>
												A
											</span>
											<span className="text-sm font-medium">{displayName}</span>
										</span>
									</SelectItem>
								);
							})}
						</SelectContent>
					</Select>
				</SettingRow>
			)}

			{/* ── Custom Theme toggle ────────────────────────────── */}
			{/* A switch that enables/disables the custom color editor.
					When ON, theme_preset is forced to 'custom' and the color
					picker appears.  When OFF, the preset reverts to the
					previously-selected preset. */}
			{isVisible(customThemeLabel, customThemeInfoSearch, sectionTitle) && (
				<SettingRow
					label={customThemeLabel}
					info={t("settings.appearance.customThemeInfoRendered")}
				>
					<Switch
						checked={effectivePreset === "custom"}
						onCheckedChange={handleCustomThemeToggle}
						aria-label={t("settings.appearance.customThemeAria")}
					/>
				</SettingRow>
			)}

			{/* ── Custom Theme color Picker ─────────────────────── */}
			{/* Only visible when the custom theme toggle is ON */}
			{effectivePreset === "custom" && customDraft && (
				<div className="animate-fade-in px-3.5 pb-4">
					{/* Light / Dark mode tabs */}
					<div className="mb-3 flex gap-1 rounded-lg bg-(--bg-subtle) p-0.5">
						<button
							type="button"
							onClick={handleSetLightMode}
							className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
								customEditorMode === "light"
									? "bg-(--bg) text-(--text-primary) shadow-xs"
									: "text-(--text-muted) hover:text-(--text-primary)"
							}`}
						>
							{t("settings.appearance.light")}
						</button>
						<button
							type="button"
							onClick={handleSetDarkMode}
							className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
								customEditorMode === "dark"
									? "bg-(--bg) text-(--text-primary) shadow-xs"
									: "text-(--text-muted) hover:text-(--text-primary)"
							}`}
						>
							{t("settings.appearance.dark")}
						</button>
					</div>

					{/* Color swatch grid — 6 core colors */}
					<div className="grid grid-cols-2 gap-2.5">
						{CUSTOM_COLOR_KEYS.map(({ var: varName, label, description }) => {
							const currentHex =
								customDraft[customEditorMode]?.[varName] ??
								(customEditorMode === "light"
									? DEFAULT_CUSTOM_LIGHT[varName]
									: DEFAULT_CUSTOM_DARK[varName]);
							// the text input reads from ``hexDrafts`` (the
							// local partial-typing state) and falls back to the
							// committed hex when no draft is present.
							// ``isHexInvalid`` drives the red-border error state.
							const hexDraftValue = hexDrafts[varName] ?? currentHex;
							const isHexInvalid =
								hexDraftValue !== "" && !HEX_STRICT_RE.test(hexDraftValue);

							// Compute the WCAG contrast ratio for the colour pair
							// most relevant to this row.  Returns ``null`` for the
							// ``ratio`` / ``ratioRounded`` fields when no pair
							// applies (e.g. the border row), in which case no
							// warning is shown.
							const { ratioRounded, showWarning: showContrastWarning } =
								computeRowContrast(varName, customDraft, customEditorMode);

							return (
								<div
									key={varName}
									className="flex items-center gap-2.5 rounded-lg border border-border bg-(--bg) p-2"
								>
									<div className="relative shrink-0">
										<Input
											type="color"
											value={currentHex}
											onChange={handleColorInputChange(varName)}
											className="absolute inset-0 h-full w-full cursor-pointer opacity-0 p-0 border-none"
											aria-label={t("settings.appearance.colorAria", { label })}
										/>
										<div
											className="h-8 w-8 rounded-md border border-border shadow-xs"
											style={{ backgroundColor: currentHex }}
										/>
									</div>
									<div className="min-w-0 flex-1">
										<span className="block text-xs font-medium text-(--text-primary)">
											{label}
										</span>
										<p className="truncate text-xs text-(--text-muted)">
											{description}
										</p>
									</div>
									{/* contrast warning icon — shown when the row's
											relevant colour pair falls below the WCAG AA 4.5:1
											threshold.  Tooltip shows the actual ratio and the AA
											requirement. */}
									{showContrastWarning && ratioRounded !== null && (
										<TooltipProvider delayDuration={200}>
											<Tooltip>
												<TooltipTrigger asChild>
													<button
														type="button"
														className="shrink-0 text-amber-500 dark:text-amber-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded-full"
														aria-label={t(
															"settings.appearance.contrastWarning",
															{ ratio: String(ratioRounded) },
														)}
													>
														<svg
															width="14"
															height="14"
															viewBox="0 0 16 16"
															fill="none"
															xmlns="http://www.w3.org/2000/svg"
															aria-hidden="true"
														>
															<path
																d="M8 1.5L0.5 14.5H15.5L8 1.5Z"
																stroke="currentColor"
																strokeWidth="1.5"
																strokeLinejoin="round"
																fill="currentColor"
																fillOpacity="0.15"
															/>
															<path
																d="M8 6V9.5"
																stroke="currentColor"
																strokeWidth="1.5"
																strokeLinecap="round"
															/>
															<circle
																cx="8"
																cy="12"
																r="0.85"
																fill="currentColor"
															/>
														</svg>
													</button>
												</TooltipTrigger>
												<TooltipContent
													side="top"
													align="center"
													className="max-w-64"
												>
													{t("settings.appearance.contrastWarning", {
														ratio: String(ratioRounded),
													})}
												</TooltipContent>
											</Tooltip>
										</TooltipProvider>
									)}
									<Input
										type="text"
										value={hexDraftValue}
										onChange={handleHexInputChange(varName)}
										onBlur={handleHexInputBlur(varName, currentHex)}
										className={cn(
											"w-18 shrink-0 text-center text-[11px] font-mono text-(--text-primary)",
											// red border when the draft value is non-empty and
											// doesn't match the strict ``#rrggbb`` regex.
											// ``border-destructive`` is the existing design-system
											// token for error borders (used by form validation
											// throughout the app).
											isHexInvalid &&
												"border-destructive focus-visible:ring-destructive/30",
										)}
										spellCheck={false}
										aria-label={t("settings.appearance.hexValueAria", {
											label,
										})}
										// expose the invalid state to assistive tech via
										// aria-invalid so screen-reader users hear "invalid
										// entry" when focused on a bad hex.
										aria-invalid={isHexInvalid || undefined}
										title={
											isHexInvalid
												? t("settings.appearance.hexInvalid")
												: undefined
										}
									/>
								</div>
							);
						})}
					</div>

					{/* Reset to defaults.
							Part C5: the previously-broken "#888" 3-digit hex in
							DEFAULT_CUSTOM_DARK["--text-muted"] is now "#888888" (6-digit)
							so the validator accepts the payload — no more "Failed to
							save settings" toast.
							Part C6: button is disabled while the draft already matches
							the defaults (re-enables the moment the user edits a color). */}
					<button
						type="button"
						disabled={customDraftIsDefault}
						onClick={handleResetCustomColors}
						className="mt-3 w-full rounded-lg border border-border px-3 py-2 text-xs text-(--text-muted) transition-colors hover:bg-(--surface-hover) hover:text-(--text-primary) disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-(--text-muted)"
					>
						{t("settings.appearance.resetToDefaultColors")}
					</button>
				</div>
			)}

			{/* ── Text Size ──────────────────────────────────────── */}
			{isVisible(textSizeLabel, textSizeInfoSearch, sectionTitle) && (
				<SettingRow
					label={textSizeLabel}
					info={t("settings.appearance.textSizeInfoRendered")}
				>
					<div className="flex items-center gap-3 w-44">
						<RangeSlider
							value={config.text_size ?? 14}
							onChange={handleTextSizeChange}
							min={10}
							max={20}
							step={1}
							ariaLabel={t("settings.appearance.textSizeAria")}
							suffix="px"
							// UX (Task ID 5): defer the IPC write until the user releases
							// the slider thumb.  Without this, each pixel of drag fires a
							// separate updateConfig({ text_size }) IPC call, flooding the
							// backend with set_config writes and re-rendering the entire
							// UI on every step (the --font-scale CSS var propagates to
							// rem-based sizes).
							deferApply
						/>
					</div>
				</SettingRow>
			)}
		</SettingsSection>
	);
});
