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
// - Per-row search-filter visibility via the `isVisible` prop.
//
// CR-147: the colour-conversion helpers (cssColorToHex and its private
// oklch / DOM fallbacks) now live in ``@/lib/color-utils`` so they can
// be unit-tested independently and reused. The inline copies that
// used to live in this file were byte-identical to the lib versions
// — keeping both was dead-code duplication. The remaining helpers
// (getCurrentThemeColors, the localStorage draft helpers) still live
// here because no other section uses them.

import {
	ModernTvIcon,
	Moon02Icon,
	Sun01Icon,
} from "@hugeicons/core-free-icons";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { cssColorToHex } from "@/lib/color-utils";
import { cn } from "@/lib/utils";
import {
	applyThemeVars,
	CUSTOM_COLOR_KEYS,
	CUSTOM_THEME_ID,
	type CustomThemeData,
	DEFAULT_CUSTOM_DARK,
	DEFAULT_CUSTOM_LIGHT,
	deriveCustomVars,
	THEMES,
} from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

// ── WCAG contrast-ratio helpers (PVT-043) ───────────────────────────
// Defined locally because ``@/lib/color-utils`` (owned by another
// sub-agent) doesn't currently export a contrast helper.  If a future
// refactor adds ``contrastRatio`` to ``color-utils.ts``, the local
// copy here can be deleted in favour of the import — the public
// surface is the same.

/** Parse a 6-digit ``#rrggbb`` hex string into RGB components. */
function _parseHex6(hex: string): { r: number; g: number; b: number } | null {
	const m = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
	if (!m) return null;
	const n = Number.parseInt(m[1], 16);
	return { r: (n >> 16) & 0xff, g: (n >> 8) & 0xff, b: n & 0xff };
}

/** Linearise an sRGB channel value in [0, 1] for relative-luminance calc. */
function _srgbChannelToLinear(c: number): number {
	return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** WCAG 2.x relative luminance of a hex colour, or ``null`` if unparseable. */
function _relativeLuminance(hex: string): number | null {
	const rgb = _parseHex6(hex);
	if (!rgb) return null;
	const r = _srgbChannelToLinear(rgb.r / 255);
	const g = _srgbChannelToLinear(rgb.g / 255);
	const b = _srgbChannelToLinear(rgb.b / 255);
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * WCAG 2.x contrast ratio between two hex colours.  Returns ``null``
 * when either colour is unparseable (so callers can skip the warning
 * UI gracefully).  Range is 1.0 (identical colours) to 21.0 (black vs
 * white).  AA requires ≥ 4.5:1 for normal text, ≥ 3:1 for large text.
 */
function contrastRatio(fg: string, bg: string): number | null {
	const l1 = _relativeLuminance(fg);
	const l2 = _relativeLuminance(bg);
	if (l1 === null || l2 === null) return null;
	const lighter = Math.max(l1, l2);
	const darker = Math.min(l1, l2);
	return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Defensive accessor for ``ThemePreset.nameKey``.
 *
 * PVT-043 / I18N-NAMEKEY: the ``ThemePreset`` interface in
 * ``themes.ts`` may or may not declare a ``nameKey`` field (depends on
 * whether another sub-agent has added it).  This helper reads the
 * field via bracket notation so this file compiles regardless, and
 * returns the ``nameKey`` string (e.g. ``"theme.preset.amoled"``) or
 * ``null`` when the field is absent.  Callers fall back to the
 * preset's hardcoded English ``name`` when ``null`` is returned.
 *
 * Accepts ``unknown`` so it can be called with a ``ThemePreset``
 * without requiring an index signature on the interface (which would
 * weaken type-safety elsewhere).  The runtime check is purely
 * structural — if the value isn't an object, or doesn't have a string
 * ``nameKey`` field, we return ``null``.
 */
function _getThemeNameKey(theme: unknown): string | null {
	if (typeof theme !== "object" || theme === null) return null;
	const k = (theme as { nameKey?: unknown }).nameKey;
	return typeof k === "string" && k.length > 0 ? k : null;
}

// ── Hex input validation regex (PVT-043 / FIX-#4) ───────────────────
// Loose regex (allows partial typing):  #  followed by 0–6 hex digits.
const HEX_PARTIAL_RE = /^#[0-9a-fA-F]{0,6}$/;
// Strict regex (used for commit-on-blur): # followed by exactly 6 hex digits.
const HEX_STRICT_RE = /^#[0-9a-fA-F]{6}$/;

/** WCAG AA normal-text contrast threshold. */
const CONTRAST_AA_THRESHOLD = 4.5;

/**
 * PVT-043 / FIX-#3: return the {fg, bg} colour pair used to evaluate
 * WCAG contrast for a given custom-colour row.  Returns ``null`` for
 * rows where contrast validation doesn't apply (e.g. ``--border``,
 * which is a divider colour, not a text/background pair).
 *
 * The mapping is:
 *   - ``--background``   → foreground vs background (text on page bg)
 *   - ``--foreground``   → foreground vs background (same pair, shown
 *                          on the foreground row too so editing either
 *                          colour surfaces the warning)
 *   - ``--primary``      → white vs primary (derived --primary-foreground
 *                          is always ``#ffffff`` — see deriveCustomVars)
 *   - ``--bg-subtle``    → foreground vs bg-subtle (text on cards)
 *   - ``--text-muted``   → text-muted vs background (secondary text)
 *   - ``--border``       → null (no text-on-border pair)
 *
 * Falls back to the DEFAULT_CUSTOM_LIGHT/DARK value when the draft
 * is missing a key, so the warning still fires for the default theme.
 */
function _getContrastPair(
	varName: string,
	draft: CustomThemeData | null,
	mode: "light" | "dark",
): { fg: string; bg: string } | null {
	const src = draft?.[mode];
	const fallback =
		mode === "light" ? DEFAULT_CUSTOM_LIGHT : DEFAULT_CUSTOM_DARK;
	const get = (k: string): string => src?.[k] ?? fallback[k] ?? "#000000";
	switch (varName) {
		case "--background":
			return { fg: get("--foreground"), bg: get("--background") };
		case "--foreground":
			return { fg: get("--foreground"), bg: get("--background") };
		case "--primary":
			// deriveCustomVars always sets --primary-foreground to #ffffff.
			return { fg: "#ffffff", bg: get("--primary") };
		case "--bg-subtle":
			return { fg: get("--foreground"), bg: get("--bg-subtle") };
		case "--text-muted":
			return { fg: get("--text-muted"), bg: get("--background") };
		case "--border":
			return null;
		default:
			return null;
	}
}

// ── localStorage draft backup ───────────────────────────────────────
// Persists the custom theme color picker draft to localStorage on every
// change.  If the backend save fails (process crash, network blip, etc.),
// the user's unsaved colors are recovered on the next page visit.
// Cleared when the backend confirms the save.
const _LS_DRAFT_KEY = "vt_custom_theme_draft";

function _saveDraftToLS(data: CustomThemeData): void {
	try {
		localStorage.setItem(_LS_DRAFT_KEY, JSON.stringify(data));
	} catch {
		// localStorage may be full or unavailable — non-fatal
	}
}

function _loadDraftFromLS(): CustomThemeData | null {
	try {
		const raw = localStorage.getItem(_LS_DRAFT_KEY);
		if (!raw) return null;
		return JSON.parse(raw) as CustomThemeData;
	} catch {
		return null;
	}
}

function _clearDraftLS(): void {
	try {
		localStorage.removeItem(_LS_DRAFT_KEY);
	} catch {
		// non-fatal
	}
}

// CR-147: the four inline colour-conversion helpers that used to live
// here (cssColorToHex, _cssColorToHexViaDOM, _cssColorToHexViaOklch,
// _srgbGamma) have been deleted — they were byte-identical copies of
// the implementations in ``@/lib/color-utils``. The component now
// imports ``cssColorToHex`` from there (see the import block above).

/**
 * Read the 6 core theme colors for BOTH light and dark modes of the
 * currently-selected built-in preset.
 *
 * Uses the THEMES array directly (converting OKLCH → hex) instead of
 * reading from the DOM.  This is necessary because preset CSS vars are
 * applied as inline styles on ``document.documentElement`` — toggling
 * the ``.dark`` class doesn't change which inline vars are set, so
 * ``getComputedStyle`` returns the same values for both modes.
 *
 * Falls back to DOM reading for ``'default'`` and ``'custom'`` presets
 * (which have no defined vars in the THEMES array).
 */
// PERF: module-level cache for getCurrentThemeColors results, keyed by
// preset ID.  The DOM-reading path (for 'default' and 'custom' presets)
// does 2× getComputedStyle calls + N cssColorToHex conversions per call,
// which is expensive.  Since the preset's colors don't change between
// renders (they only change when the user picks a different preset or
// edits a custom color), we cache the result and invalidate it when
// the custom draft changes.
//
// The cache is defined in a separate file (themeColorCache.ts) to avoid
// the react-refresh/only-export-components lint warning that would occur
// if we exported both the cache and the component from the same file.
import { _themeColorCache } from "./themeColorCache";

/**
 * Read the 6 core theme colors for BOTH light and dark modes of the
 * currently-selected built-in preset.
 *
 * PVT-043 / FIX-#7 (layout thrashing): for ``'default'`` we return
 * the hardcoded ``DEFAULT_CUSTOM_LIGHT`` / ``DEFAULT_CUSTOM_DARK``
 * maps directly — these are byte-identical to what the stylesheet
 * defines, so reading them via ``getComputedStyle`` was a waste of
 * two layout passes per call.  For ``'custom'`` we derive the core
 * colours from the in-memory ``customDraft`` (when available) via
 * ``deriveCustomVars`` — the draft is already in memory, so no DOM
 * read is needed.  Built-in presets still read from the ``THEMES``
 * array (also in-memory).
 *
 * The DOM-read fallback is kept ONLY for the legacy callers that
 * pass neither ``currentPresetId`` nor ``customDraft`` AND whose
 * preset id isn't in the THEMES array — which in practice never
 * happens.  It exists to preserve the pre-fix behaviour for any
 * caller we missed, and is gated behind a feature-detect so it
 * doesn't run in jsdom tests that lack ``getComputedStyle``.
 */
function getCurrentThemeColors(
	currentPresetId: string,
	customDraft: CustomThemeData | null = null,
): {
	light: Record<string, string>;
	dark: Record<string, string>;
} {
	const cached = _themeColorCache.get(currentPresetId);
	if (cached) return cached;

	const keys = CUSTOM_COLOR_KEYS.map((k) => k.var);

	// FIX-#7: 'default' preset — return the hardcoded DEFAULT_CUSTOM_*
	// values (these match the stylesheet defaults exactly, so reading
	// them via getComputedStyle was a layout-thrash for nothing).
	if (currentPresetId === "default" || currentPresetId === "") {
		const result = {
			light: { ...DEFAULT_CUSTOM_LIGHT },
			dark: { ...DEFAULT_CUSTOM_DARK },
		};
		_themeColorCache.set(currentPresetId || "default", result);
		return result;
	}

	// FIX-#7: 'custom' preset — derive from the in-memory customDraft
	// (no DOM read).  When no draft is available yet (the very first
	// render before ``setCustomDraft`` has run), fall back to the
	// DEFAULT_CUSTOM_* values so the editor still has sensible
	// starting colours.
	if (currentPresetId === "custom") {
		const lightCore = customDraft?.light ?? { ...DEFAULT_CUSTOM_LIGHT };
		const darkCore = customDraft?.dark ?? { ...DEFAULT_CUSTOM_DARK };
		const light: Record<string, string> = {};
		const dark: Record<string, string> = {};
		for (const key of keys) {
			light[key] = lightCore[key] ?? DEFAULT_CUSTOM_LIGHT[key] ?? "#000000";
			dark[key] = darkCore[key] ?? DEFAULT_CUSTOM_DARK[key] ?? "#000000";
		}
		const result = { light, dark };
		_themeColorCache.set("custom", result);
		return result;
	}

	// Built-in preset with defined vars — read from THEMES array directly
	// (in-memory, no DOM access).
	const theme = THEMES.find((t) => t.id === currentPresetId);
	if (theme) {
		const light: Record<string, string> = {};
		const dark: Record<string, string> = {};
		for (const key of keys) {
			light[key] = cssColorToHex(theme.light[key] ?? "");
			dark[key] = cssColorToHex(theme.dark[key] ?? "");
		}
		const result = { light, dark };
		_themeColorCache.set(currentPresetId, result);
		return result;
	}

	// Last-resort fallback: read from the DOM.  This path is only
	// reached for unknown preset ids (which shouldn't happen in
	// practice — the THEMES array covers every valid id).  Kept
	// for defensive compatibility with the pre-fix behaviour.
	if (
		typeof document !== "undefined" &&
		typeof getComputedStyle === "function"
	) {
		const root = document.documentElement;
		const hadDark = root.classList.contains("dark");

		root.classList.remove("dark");
		const lightStyle = getComputedStyle(root);
		const light: Record<string, string> = {};
		for (const key of keys) {
			light[key] = cssColorToHex(lightStyle.getPropertyValue(key).trim());
		}

		root.classList.add("dark");
		const darkStyle = getComputedStyle(root);
		const dark: Record<string, string> = {};
		for (const key of keys) {
			dark[key] = cssColorToHex(darkStyle.getPropertyValue(key).trim());
		}

		root.classList.toggle("dark", hadDark);
		const result = { light, dark };
		_themeColorCache.set(currentPresetId, result);
		return result;
	}

	// No DOM available (SSR / restricted test env) — fall back to the
	// hardcoded defaults so the caller always gets a valid object.
	const fallback = {
		light: { ...DEFAULT_CUSTOM_LIGHT },
		dark: { ...DEFAULT_CUSTOM_DARK },
	};
	return fallback;
}

/**
 * Compute the { background, foreground } pair shown inside the square
 * preview next to each theme in the dropdown and in the trigger.
 *
 * The square background uses the theme's **primary/accent** colour so
 * the user immediately sees the dominant accent of each preset.  The
 * "A" letter is rendered in the theme's **foreground/text** colour so
 * it stays readable against the primary background.
 *
 * For built-in presets the values come straight from the theme's light /
 * dark var maps.  For 'default' (which has empty var maps) we fall back
 * to the theme's ``swatch`` field (the primary blue from the stylesheet).
 * For 'custom', we use the primary colour from the user's draft.
 */
function getThemePreviewColors(
	themeId: string,
	isDark: boolean,
	customDraft: CustomThemeData | null,
): { bg: string; fg: string } {
	// Custom theme — use the primary/accent colour from the custom draft.
	if (themeId === "custom" && customDraft) {
		const vars = isDark ? customDraft.dark : customDraft.light;
		return {
			bg: vars["--primary"] ?? (isDark ? "#6b7fd4" : "#5469d4"),
			fg: vars["--foreground"] ?? (isDark ? "#ededed" : "#0a0a0a"),
		};
	}
	// Default preset — no CSS var overrides, use the swatch as primary.
	if (themeId === "default") {
		const defaultTheme = THEMES[0];
		const swatch = defaultTheme?.swatch ?? "oklch(0.488 0.243 264.376)";
		const fgSwatch = isDark ? "oklch(0.985 0 0)" : "oklch(0.141 0.005 285.823)";
		return { bg: swatch, fg: fgSwatch };
	}
	const theme = THEMES.find((t) => t.id === themeId);
	if (!theme) {
		return { bg: "#5469d4", fg: "#000000" };
	}
	const vars = isDark ? theme.dark : theme.light;
	return {
		bg: vars["--primary"] ?? (isDark ? "#6b7fd4" : "#5469d4"),
		fg: vars["--foreground"] ?? (isDark ? "#ededed" : "#0a0a0a"),
	};
}

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
	 * ``config.theme_preset`` while a save is in-flight).  PVT-043 /
	 * FIX-#8: without this prop, the preset dropdown showed a stale
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
	// Track the last saved theme preset so hover previews can revert
	// to the user's saved choice (not the initial default) if they
	// hover without clicking.
	const savedPresetRef = useRef(config?.theme_preset ?? "default");
	if (config) savedPresetRef.current = config.theme_preset ?? "default";
	// Task ID 7 / Part C3: track whether the user has actually moved the
	// mouse inside the dropdown content.  Radix Select mounts the content
	// portal directly under the cursor when the dropdown opens, which can
	// fire a spurious `onMouseEnter` on the first item — applying that
	// item's theme as a "hover preview" even though the user hasn't
	// interacted.  We only honour `onMouseEnter` after the first real
	// `onMouseMove` inside the content, so opening + closing the dropdown
	// without moving the mouse leaves the applied theme untouched.
	const userHoveredRef = useRef(false);
	// ESC-THEME-FIX-003: ref mirror of ``customDraft`` so the stable
	// ``revertToSavedPreset`` callback (empty deps) can re-apply custom
	// vars when the saved preset is ``CUSTOM_THEME_ID`` without needing
	// ``customDraft`` as a dependency (which would make the callback
	// unstable and churn the mouseleave listener on every color edit).
	// ESC-THEME-FIX-003-TDZ: initialized with ``null`` instead of
	// ``customDraft`` because the ``customDraft`` ``const`` is declared
	// later (via ``useState``) and is in the temporal dead zone here.
	// The tracking effect below updates the ref with the real value
	// after every render.
	const customDraftRef = useRef<CustomThemeData | null>(null);
	useEffect(() => {
		customDraftRef.current = customDraft;
	});
	// Track the last non-custom preset so we can revert when the
	// custom-theme toggle is turned off.
	const lastNonCustomRef = useRef(
		config?.theme_preset && config.theme_preset !== "custom"
			? config.theme_preset
			: "default",
	);
	if (config?.theme_preset && config.theme_preset !== "custom") {
		lastNonCustomRef.current = config.theme_preset;
	}

	// ── Custom theme editor state ───────────────────────────────────
	// Task ID 7 / Part C4: initial tab matches the user's current dark/light
	// mode so the editor opens on the tab the user is most likely to edit
	// (was always "light" — Dark Mode users had to switch tabs manually).
	const [customEditorMode, setCustomEditorMode] = useState<"light" | "dark">(
		() =>
			typeof document !== "undefined" &&
			document.documentElement.classList.contains("dark")
				? "dark"
				: "light",
	);
	const [customDraft, setCustomDraft] = useState<CustomThemeData | null>(null);
	const customThemeInitRef = useRef(false);

	// PVT-043 / FIX-#4: per-row hex input draft state.  The text
	// input is a controlled component whose value can be a partial
	// hex (e.g. ``#1a2`` while the user is typing).  We track each
	// row's draft locally so:
	//   - The user can type partial values without the input
	//     snapping back to the last-committed hex on every keystroke.
	//   - We can show a red-border error state when the draft
	//     doesn't match the strict ``#rrggbb`` regex.
	//   - On blur we either commit (strict match) or revert to the
	//     last-committed hex (so an abandoned edit doesn't leave a
	//     half-typed value in the input).
	//
	// The drafts are re-seeded from ``customDraft`` whenever the
	// draft changes via the ``useEffect`` below — this keeps the
	// text input in sync when the colour is changed via the native
	// colour picker (which calls ``handleCustomColorChange``
	// directly, bypassing the text input).
	const [hexDrafts, setHexDrafts] = useState<Record<string, string>>({});

	// PVT-043 / FIX-#8: the effective preset prefers the optimistic
	// value from ``useTheme.themePreset`` (passed in as
	// ``themePresetProp``) over the persisted ``config.theme_preset``
	// so the dropdown / switch / picker update immediately on click
	// rather than waiting for the backend ``set_config`` round-trip.
	const effectivePreset: VoiceTyperConfig["theme_preset"] =
		themePresetProp ?? config?.theme_preset ?? "default";

	// Part C6: ``customDraftIsDefault`` is true when the draft matches the
	// built-in DEFAULT_CUSTOM_LIGHT / DEFAULT_CUSTOM_DARK maps.  The Reset
	// button is disabled in that state — re-enabled the moment the user
	// edits any colour.  Compared by JSON.stringify on the 6 core vars
	// (DEFAULT_CUSTOM_* only contain those 6 keys, so this is exact).
	const customDraftIsDefault = useMemo(() => {
		if (!customDraft) return false;
		const lightKeys = Object.keys(customDraft.light).sort();
		const darkKeys = Object.keys(customDraft.dark).sort();
		const defaultKeys = Object.keys(DEFAULT_CUSTOM_LIGHT).sort();
		if (
			lightKeys.length !== defaultKeys.length ||
			darkKeys.length !== defaultKeys.length
		) {
			return false;
		}
		for (const k of defaultKeys) {
			if (customDraft.light[k] !== DEFAULT_CUSTOM_LIGHT[k]) return false;
			if (customDraft.dark[k] !== DEFAULT_CUSTOM_DARK[k]) return false;
		}
		return true;
	}, [customDraft]);

	// PVT-25: One-time init moved into a useEffect. Previously this
	// block called setCustomDraft during render — a React anti-pattern
	// that forces a synchronous re-render before commit and breaks
	// concurrent-rendering invariants. Running it in an effect costs
	// one extra render (the draft is `null` on the first commit) but
	// is React-blessed. The `customThemeInitRef` guard ensures the
	// init runs only once even if `config` identity changes. Prefers
	// localStorage draft over config value.
	useEffect(() => {
		if (!config || customThemeInitRef.current) return;
		customThemeInitRef.current = true;
		const draft = _loadDraftFromLS();
		if (draft) {
			setCustomDraft(draft);
		} else if (config.custom_theme) {
			setCustomDraft(config.custom_theme);
		} else {
			setCustomDraft({
				light: { ...DEFAULT_CUSTOM_LIGHT },
				dark: { ...DEFAULT_CUSTOM_DARK },
			});
		}
	}, [config]);

	// PERF: clear the color cache on unmount so stale entries don't
	// persist across page navigations. The cache is module-level (shared
	// across all instances), so this ensures the next time the user
	// visits Settings, colors are re-read from the DOM/THEMES array
	// rather than using potentially stale cached values.
	useEffect(() => {
		return () => {
			_themeColorCache.clear();
		};
	}, []);

	// FIX-#4: keep the hex-input drafts in sync with the committed
	// ``customDraft`` values.  Whenever the draft changes (via the
	// colour picker, the reset button, the toggle-on init, or a
	// config push), re-seed every row's text input with the new
	// committed hex.  Without this, picking a colour via the
	// native picker would leave the text input showing the
	// previous hex until the user manually re-typed it.
	useEffect(() => {
		if (!customDraft) return;
		const next: Record<string, string> = {};
		const src = customDraft[customEditorMode];
		const fallback =
			customEditorMode === "light" ? DEFAULT_CUSTOM_LIGHT : DEFAULT_CUSTOM_DARK;
		for (const { var: varName } of CUSTOM_COLOR_KEYS) {
			next[varName] = src?.[varName] ?? fallback[varName] ?? "#000000";
		}
		setHexDrafts(next);
	}, [customDraft, customEditorMode]);

	const _handleThemeChange = (mode: string) => {
		const m = mode as VoiceTyperConfig["theme_mode"];
		// App-level handler owns the save + state update.
		onThemeChange?.(m);
	};

	// Apply a custom color change immediately for preview, then debounce save
	const handleCustomColorChange = useCallback(
		(mode: "light" | "dark", colorKey: string, hex: string) => {
			setCustomDraft((prev) => {
				if (!prev) return prev;
				const updated: CustomThemeData = {
					...prev,
					[mode]: { ...prev[mode], [colorKey]: hex },
				};
				// Preview immediately on the document
				const isDark = document.documentElement.classList.contains("dark");
				const modeVars = isDark ? updated.dark : updated.light;
				const derived = deriveCustomVars(modeVars, isDark);
				applyThemeVars("custom", isDark, derived);

				_themeColorCache.delete("custom");
				_themeColorCache.delete("default");

				// Persist to localStorage immediately (before the backend
				// save completes) so the draft survives a crash or disconnect.
				_saveDraftToLS(updated);

				// Debounced save to backend
				updateConfigDebounced("custom_theme", updated, 300);

				return updated;
			});
		},
		[updateConfigDebounced],
	);

	// ── Theme preset hover preview ─────────────────────────────────
	const applyHoverPreview = useCallback((presetId: string) => {
		const isDark = document.documentElement.classList.contains("dark");
		applyThemeVars(presetId, isDark);
	}, []);

	const revertToSavedPreset = useCallback(() => {
		const isDark = document.documentElement.classList.contains("dark");
		const preset = savedPresetRef.current;
		if (preset === CUSTOM_THEME_ID) {
			// ESC-THEME-FIX-003: ``applyThemeVars("custom", isDark)``
			// without a third argument clears all custom vars from the DOM
			// (via ``clearThemeVars()``) but then can't re-apply them because
			// the ``custom`` ThemePreset has empty light/dark var maps — the
			// user's actual colors live in the config/customDraft.  Derive
			// the full var set from the draft so the revert actually restores
			// the saved custom theme.
			const draft = customDraftRef.current;
			if (draft) {
				const modeVars = isDark ? draft.dark : draft.light;
				const derived = deriveCustomVars(modeVars, isDark);
				applyThemeVars(CUSTOM_THEME_ID, isDark, derived);
			} else {
				// No draft available — fall back to default (no vars to apply).
				applyThemeVars("default", isDark);
			}
		} else {
			applyThemeVars(preset, isDark);
		}
	}, []);

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

	// ── Inline handler extraction ─────────────────────────────────
	const handleColorSchemeChange = (v: string) => {
		const m = v as VoiceTyperConfig["theme_mode"];
		_handleThemeChange(m);
	};
	const handleThemePresetChange = (v: string) => {
		const preset = v as VoiceTyperConfig["theme_preset"];
		savedPresetRef.current = preset;
		updateConfig({ theme_preset: preset });
	};
	const handleSelectOpenChange = (open: boolean) => {
		if (open) {
			userHoveredRef.current = false;
		} else {
			revertToSavedPreset();
		}
	};
	const handleThemeHover = (themeId: string) => () => {
		if (userHoveredRef.current) {
			applyHoverPreview(themeId);
		}
	};
	const handleCustomThemeToggle = (enabled: boolean) => {
		if (enabled) {
			const isDarkOn = document.documentElement.classList.contains("dark");
			setCustomEditorMode(isDarkOn ? "dark" : "light");
			const currentColors = getCurrentThemeColors(
				config.theme_preset ?? "default",
				customDraft,
			);
			setCustomDraft(currentColors);
			_saveDraftToLS(currentColors);
			updateConfig({ theme_preset: "custom", custom_theme: currentColors });
		} else {
			const fallback = lastNonCustomRef.current ?? "default";
			savedPresetRef.current = fallback;
			_clearDraftLS();
			updateConfig({ theme_preset: fallback });
		}
	};
	const handleSetLightMode = () => setCustomEditorMode("light");
	const handleSetDarkMode = () => setCustomEditorMode("dark");
	const handleColorInputChange =
		(varName: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
			handleCustomColorChange(customEditorMode, varName, e.target.value);

	// FIX-#4: hex input handler — allows partial typing via the
	// loose regex (so the user can type ``#``, ``#1``, ``#1a``,
	// ``#1a2``, … without the input rejecting intermediate states),
	// commits to ``handleCustomColorChange`` only when the strict
	// ``#rrggbb`` regex matches (so a half-typed value doesn't
	// preview an invalid colour on the document), and updates the
	// local ``hexDrafts`` so the input shows what the user typed.
	const handleHexInputChange =
		(varName: string) => (e: React.ChangeEvent<HTMLInputElement>) => {
			const val = e.target.value;
			// Allow the user to clear the input entirely (so
			// they can retype from scratch) — the blur
			// handler will revert if the value is left empty.
			if (val === "" || HEX_PARTIAL_RE.test(val)) {
				setHexDrafts((prev) => ({ ...prev, [varName]: val }));
				if (HEX_STRICT_RE.test(val)) {
					handleCustomColorChange(customEditorMode, varName, val);
				}
			}
		};

	// FIX-#4: on blur, commit the strict-match value or revert to
	// the last-committed hex.  Reverting prevents a half-typed
	// value (e.g. ``#1a2``) from lingering in the input after the
	// user clicks away — the input snaps back to the colour the
	// document is actually using.
	const handleHexInputBlur = (varName: string, committedHex: string) => () => {
		const val = hexDrafts[varName] ?? committedHex;
		if (HEX_STRICT_RE.test(val)) {
			// Already committed on change — nothing to do.
			return;
		}
		setHexDrafts((prev) => ({ ...prev, [varName]: committedHex }));
	};
	const handleResetCustomColors = () => {
		const defaults: CustomThemeData = {
			light: { ...DEFAULT_CUSTOM_LIGHT },
			dark: { ...DEFAULT_CUSTOM_DARK },
		};
		setCustomDraft(defaults);
		_saveDraftToLS(defaults);
		const isDark = document.documentElement.classList.contains("dark");
		const modeVars = isDark ? defaults.dark : defaults.light;
		const derived = deriveCustomVars(modeVars, isDark);
		applyThemeVars("custom", isDark, derived);
		_themeColorCache.delete("custom");
		_themeColorCache.delete("default");
		updateConfig({ custom_theme: defaults });
	};
	const handleTextSizeChange = (v: number) => updateConfig({ text_size: v });
	const handleSelectMouseMove = () => {
		userHoveredRef.current = true;
	};

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
								// PVT-043 / I18N-NAMEKEY: prefer the localised
								// theme name (via ``t(theme.nameKey)``) when the
								// preset declares a ``nameKey`` field.  Falls back
								// to the preset's hardcoded English ``name`` when
								// the field is absent (so this file compiles whether
								// or not another sub-agent has added ``nameKey`` to
								// the ``ThemePreset`` interface).
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
							{/* PVT-043 / FIX-#9: render a DISABLED "Custom
                                                                (use toggle below)" SelectItem when the saved
                                                                preset is 'custom'.  Without this, the dropdown's
                                                                trigger showed a blank value when the preset was
                                                                'custom' (the SelectItem list filtered 'custom'
                                                                out), making it look like the dropdown was broken.
                                                                The disabled item is non-selectable — users
                                                                toggle the custom theme via the switch below
                                                                the dropdown.  Always rendered so the trigger's
                                                                selected value always has a matching SelectItem
                                                                (Radix Select otherwise warns about a missing
                                                                value). */}
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
							{/* Built-in presets (excluding 'custom' — handled
                                                                by the disabled item above). */}
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
							// FIX-#4: the text input reads from ``hexDrafts`` (the
							// local partial-typing state) and falls back to the
							// committed hex when no draft is present.  ``isHexInvalid``
							// drives the red-border error state.
							const hexDraftValue = hexDrafts[varName] ?? currentHex;
							const isHexInvalid =
								hexDraftValue !== "" && !HEX_STRICT_RE.test(hexDraftValue);

							// PVT-043 / FIX-#3: compute the WCAG contrast ratio
							// for the colour pair most relevant to this row.
							// Returns ``null`` when no pair applies (e.g. the
							// border row), in which case no warning is shown.
							const contrastPair = _getContrastPair(
								varName,
								customDraft,
								customEditorMode,
							);
							const ratio =
								contrastPair === null
									? null
									: contrastRatio(contrastPair.fg, contrastPair.bg);
							const ratioRounded =
								ratio === null ? null : Math.round(ratio * 10) / 10;
							const showContrastWarning =
								ratio !== null && ratio < CONTRAST_AA_THRESHOLD;

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
									{/* PVT-043 / FIX-#3: contrast warning icon — shown
                                                                                when the row's relevant colour pair falls below
                                                                                the WCAG AA 4.5:1 threshold.  Tooltip shows the
                                                                                actual ratio and the AA requirement. */}
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
											// FIX-#4: red border when the draft value is
											// non-empty and doesn't match the strict
											// ``#rrggbb`` regex.  ``border-destructive`` is
											// the existing design-system token for error
											// borders (used by form validation throughout
											// the app).
											isHexInvalid &&
												"border-destructive focus-visible:ring-destructive/30",
										)}
										spellCheck={false}
										aria-label={t("settings.appearance.hexValueAria", {
											label,
										})}
										// FIX-#4: expose the invalid state to assistive
										// tech via aria-invalid so screen-reader users
										// hear "invalid entry" when focused on a bad hex.
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
							// the slider thumb.  Without this, each pixel of drag fires
							// a separate updateConfig({ text_size }) IPC call, flooding
							// the backend with set_config writes and re-rendering the
							// entire UI on every step (the --font-scale CSS var
							// propagates to rem-based sizes).
							deferApply
						/>
					</div>
				</SettingRow>
			)}
		</SettingsSection>
	);
});
