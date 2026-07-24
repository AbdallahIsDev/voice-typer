// lib/theme-utils.ts — pure helpers extracted from
// ``components/settings/ThemeSettingsSection.tsx`` (BG-56 Phase 4.5
// spaghetti split). All functions here are either pure (no React, no
// DOM side-effects beyond guarded localStorage) or read-only DOM
// helpers — they belong in a utility module, not a 1241-LOC component
// file.
//
// Public API (consumed by ``ThemeSettingsSection.tsx``):
//   - ``contrastRatio(fg, bg)`` — WCAG 2.x contrast ratio, returns
//     ``number | null`` (null when either colour is unparseable).
//   - ``getContrastPair(varName, draft, mode)`` — returns the
//     {fg, bg} colour pair used to evaluate WCAG contrast for a given
//     custom-colour row, or ``null`` when contrast validation doesn't
//     apply (e.g. ``--border``).
//   - ``saveDraftToLS(data)`` / ``loadDraftFromLS()`` / ``clearDraftLS()``
//     — persist the custom-theme draft to ``localStorage`` so unsaved
//     colour picks survive a crash.
//   - ``getThemePreviewColors(themeId, isDark, customDraft)`` —
//     {bg, fg} pair shown inside the square preview next to each theme
//     in the dropdown and in the trigger.
//   - ``getCurrentThemeColors(currentPresetId, customDraft)`` —
//     {light, dark} maps of the 6 core colours for the
//     currently-selected preset (used to seed the custom-theme editor
//     when the user enables the custom-theme toggle).
//
// BG-R18: ``getContrastPair`` was updated to mirror
// ``deriveCustomVars``'s new contrast-based foreground selection.
// Previously it always returned ``fg: "#ffffff"`` for the ``--primary``
// row (because deriveCustomVars hardcoded --primary-foreground to
// white). It now picks whichever of white/black has better contrast
// against the user-chosen primary and warns when NEITHER clears AA.

import {
	CUSTOM_COLOR_KEYS,
	type CustomThemeData,
	DEFAULT_CUSTOM_DARK,
	DEFAULT_CUSTOM_LIGHT,
	THEMES,
} from "@/themes";
import { cssColorToHex } from "@/lib/color-utils";
import { _themeColorCache } from "@/components/settings/themeColorCache";

// ── WCAG 2.x contrast ───────────────────────────────────────────────

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
export function contrastRatio(fg: string, bg: string): number | null {
	const l1 = _relativeLuminance(fg);
	const l2 = _relativeLuminance(bg);
	if (l1 === null || l2 === null) return null;
	const lighter = Math.max(l1, l2);
	const darker = Math.min(l1, l2);
	return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Return whichever of ``#ffffff`` / ``#000000`` has the higher WCAG
 * contrast ratio against ``bgHex``. Used by ``getContrastPair`` for
 * the ``--primary`` row to mirror the BG-R18 fix in ``deriveCustomVars``
 * (which now picks the foreground dynamically instead of hardcoding
 * white). Falls back to white when ``bgHex`` is unparseable.
 */
function _pickContrastForeground(bgHex: string): string {
	const white = contrastRatio("#ffffff", bgHex);
	const black = contrastRatio("#000000", bgHex);
	if (white === null || black === null) return "#ffffff";
	return white >= black ? "#ffffff" : "#000000";
}

// ── Contrast-pair selection for the custom-colour grid ─────────────

/**
 * Return the {fg, bg} colour pair used to evaluate WCAG contrast for
 * a given custom-colour row.  Returns ``null`` for rows where contrast
 * validation doesn't apply (e.g. ``--border``, which is a divider
 * colour, not a text/background pair).
 *
 * The mapping is:
 *   - ``--background``   → foreground vs background (text on page bg)
 *   - ``--foreground``   → foreground vs background (same pair, shown
 *                          on the foreground row too so editing either
 *                          colour surfaces the warning)
 *   - ``--primary``      → contrast-picked foreground vs primary
 *                          (mirrors BG-R18 deriveCustomVars behaviour:
 *                          the foreground is whichever of white/black
 *                          has higher contrast; the warning fires when
 *                          NEITHER clears AA — i.e. the user picked a
 *                          mid-tone primary).
 *   - ``--bg-subtle``    → foreground vs bg-subtle (text on cards)
 *   - ``--text-muted``   → text-muted vs background (secondary text)
 *   - ``--border``       → null (no text-on-border pair)
 *
 * Falls back to the DEFAULT_CUSTOM_LIGHT/DARK value when the draft
 * is missing a key, so the warning still fires for the default theme.
 */
export function getContrastPair(
	varName: string,
	draft: CustomThemeData | null,
	mode: "light" | "dark",
): { fg: string; bg: string } | null {
	const src = draft?.[mode];
	const fallback = mode === "light" ? DEFAULT_CUSTOM_LIGHT : DEFAULT_CUSTOM_DARK;
	const get = (k: string): string => src?.[k] ?? fallback[k] ?? "#000000";
	switch (varName) {
		case "--background":
			return { fg: get("--foreground"), bg: get("--background") };
		case "--foreground":
			return { fg: get("--foreground"), bg: get("--background") };
		case "--primary":
			// BG-R18: deriveCustomVars now picks the foreground dynamically
			// (white or black, whichever has higher contrast). The warning
			// fires only when NEITHER clears AA — i.e. the user picked a
			// mid-tone primary that can't carry either text colour.
			return { fg: _pickContrastForeground(get("--primary")), bg: get("--primary") };
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

export function saveDraftToLS(data: CustomThemeData): void {
	try {
		localStorage.setItem(_LS_DRAFT_KEY, JSON.stringify(data));
	} catch (e) {
		// localStorage may be full or unavailable — non-fatal.
		// The backend save will still proceed; we just lose the
		// crash-recovery draft for the next page visit.
		console.warn("[theme-utils] saveDraftToLS failed:", e);
	}
}

export function loadDraftFromLS(): CustomThemeData | null {
	try {
		const raw = localStorage.getItem(_LS_DRAFT_KEY);
		if (!raw) return null;
		return JSON.parse(raw) as CustomThemeData;
	} catch {
		return null;
	}
}

export function clearDraftLS(): void {
	try {
		localStorage.removeItem(_LS_DRAFT_KEY);
	} catch (e) {
		// non-fatal — a leftover draft will just be overwritten
		// on the next save or rejected as stale on the next load.
		console.warn("[theme-utils] clearDraftLS failed:", e);
	}
}

// ── Theme preview / current-colour helpers ──────────────────────────

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
export function getCurrentThemeColors(
	currentPresetId: string,
	customDraft: CustomThemeData | null = null,
): {
	light: Record<string, string>;
	dark: Record<string, string>;
} {
	const cached = _themeColorCache.get(currentPresetId);
	if (cached) return cached;

	const keys = CUSTOM_COLOR_KEYS.map((k) => k.var);

	// 'default' preset — return the hardcoded DEFAULT_CUSTOM_* values
	// (these match the stylesheet defaults exactly, so reading them via
	// getComputedStyle was a layout-thrash for nothing).
	if (currentPresetId === "default" || currentPresetId === "") {
		const result = {
			light: { ...DEFAULT_CUSTOM_LIGHT },
			dark: { ...DEFAULT_CUSTOM_DARK },
		};
		_themeColorCache.set(currentPresetId || "default", result);
		return result;
	}

	// 'custom' preset — derive from the in-memory customDraft (no DOM
	// read).  When no draft is available yet (the very first render
	// before setCustomDraft has run), fall back to the DEFAULT_CUSTOM_*
	// values so the editor still has sensible starting colours.
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
	return {
		light: { ...DEFAULT_CUSTOM_LIGHT },
		dark: { ...DEFAULT_CUSTOM_DARK },
	};
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
export function getThemePreviewColors(
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

/**
 * BG-5: defensive accessor for ``ThemePreset.nameKey``.
 *
 * Returns the ``nameKey`` string (e.g. ``"theme.preset.amoled"``) or
 * ``null`` when the field is absent.  Callers fall back to the preset's
 * hardcoded English ``name`` when ``null`` is returned.  Although the
 * ``ThemePreset`` interface now declares ``nameKey: string`` (BG-5),
 * the helper is kept as a defensive accessor for callers that may
 * receive a partial preset object (e.g. test fixtures, lazy-loaded
 * fragments) — it never throws.
 */
export function getThemeNameKey(theme: unknown): string | null {
	if (typeof theme !== "object" || theme === null) return null;
	const k = (theme as { nameKey?: unknown }).nameKey;
	return typeof k === "string" && k.length > 0 ? k : null;
}
