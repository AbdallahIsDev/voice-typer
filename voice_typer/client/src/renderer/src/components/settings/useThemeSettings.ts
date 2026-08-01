// components/settings/useThemeSettings.ts — state machine for the
//custom-theme editor ( partial split).
//
// Extracted from ThemeSettingsSection.tsx so the component file can
// shrink to just JSX rendering. This hook owns:
//
//   - All useState calls (customEditorMode, customDraft, hexDrafts)
//   - All useRef calls (savedPresetRef, userHoveredRef,
//     customDraftRef, lastNonCustomRef, customThemeInitRef)
//   - All useEffect calls (draft persistence, theme application,
//     cache cleanup, hex-input re-seeding, ref tracking)
//   - All useCallback event handlers (handleCustomColorChange,
//     handleThemePresetChange, handleColorInputChange, etc.)
//
// The hook also exposes two pure module-level helpers
// (``getThemePreviewColors`` and ``_getThemeNameKey``) that the
// component's JSX calls directly to render the preset dropdown
// trigger / items. ``getCurrentThemeColors`` stays module-private —
// only the hook's ``handleCustomThemeToggle`` calls it.
//
// Behaviour is byte-identical to the previous in-component
// implementation: the hook is a pure refactor that moves code without
// changing the observable semantics.

import type { ChangeEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cssColorToHex } from "@/lib/color-utils";
import {
	clearDraftLS,
	loadDraftFromLS,
	saveDraftToLS,
} from "@/lib/theme-draft-storage";
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

import { _themeColorCache } from "./themeColorCache";

// ── Module-level pure helpers (no React dependency) ─────────────────

/**
 * Defensive accessor for ``ThemePreset.nameKey``.
 *
 * I18N-NAMEKEY: the ``ThemePreset`` interface in
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
export function _getThemeNameKey(theme: unknown): string | null {
	if (typeof theme !== "object" || theme === null) return null;
	const k = (theme as { nameKey?: unknown }).nameKey;
	return typeof k === "string" && k.length > 0 ? k : null;
}

/**
 * Read the 6 core theme colors for BOTH light and dark modes of the
 * currently-selected built-in preset.
 *
 * For ``'default'`` we return the hardcoded ``DEFAULT_CUSTOM_LIGHT`` /
 * ``DEFAULT_CUSTOM_DARK`` maps directly — these are byte-identical to
 * what the stylesheet defines, so reading them via ``getComputedStyle``
 * was a waste of two layout passes per call.  For ``'custom'`` we
 * derive the core colours from the in-memory ``customDraft`` (when
 * available) via ``deriveCustomVars`` — the draft is already in
 * memory, so no DOM read is needed.  Built-in presets still read from
 * the ``THEMES`` array (also in-memory).
 *
 * The DOM-read fallback is kept ONLY for the legacy callers that pass
 * neither ``currentPresetId`` nor ``customDraft`` AND whose preset id
 * isn't in the THEMES array — which in practice never happens.  It
 * exists to preserve the pre-fix behaviour for any caller we missed,
 * and is gated behind a feature-detect so it doesn't run in jsdom
 * tests that lack ``getComputedStyle``.
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
	// before ``setCustomDraft`` has run), fall back to the
	// DEFAULT_CUSTOM_* values so the editor still has sensible starting
	// colours.
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
	// practice — the THEMES array covers every valid id).  Kept for
	// defensive compatibility with the pre-fix behaviour.
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
 * For built-in presets the values come straight from the theme's
 * light / dark var maps.  For 'default' (which has empty var maps) we
 * fall back to the theme's ``swatch`` field (the primary blue from the
 * stylesheet).  For 'custom', we use the primary colour from the
 * user's draft.
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

// ── Hook types ──────────────────────────────────────────────────────

/**
 * Inputs to ``useThemeSettings``. Mirrors the props the
 * ``ThemeSettingsSection`` component receives that the hook needs to
 * own the state machine: the config + the two update callbacks + the
 * three App-level theme overrides (``themeModeProp``,
 * ``onThemeChange``, ``themePresetProp``).
 */
export interface UseThemeSettingsConfig {
	config: VoiceTyperConfig | null;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
	updateConfigDebounced: (
		key: keyof VoiceTyperConfig,
		value: unknown,
		delayMs?: number,
	) => void;
	themeModeProp?: VoiceTyperConfig["theme_mode"];
	onThemeChange?: (mode: VoiceTyperConfig["theme_mode"]) => void;
	themePresetProp?: VoiceTyperConfig["theme_preset"];
}

/**
 * Outputs from ``useThemeSettings``. The component consumes the state
 * values directly in its JSX and passes the handlers to the
 * corresponding controls (``SegmentedControl``, ``Select``, ``Switch``,
 * ``Input``, ``button``, ``RangeSlider``).
 */
export interface UseThemeSettingsReturn {
	/** Currently-active tab in the custom-theme editor (``"light"`` / ``"dark"``). */
	customEditorMode: "light" | "dark";
	/** In-memory custom-theme draft (``null`` until the init effect runs). */
	customDraft: CustomThemeData | null;
	/** Per-row hex-input partial-typing drafts (keyed by CSS var name). */
	hexDrafts: Record<string, string>;
	/** Effective preset (prefers ``themePresetProp`` over ``config.theme_preset``). */
	effectivePreset: VoiceTyperConfig["theme_preset"];
	/** ``true`` when the draft matches the built-in DEFAULT_CUSTOM_* maps. */
	customDraftIsDefault: boolean;
	// ── Event handlers ──
	handleColorSchemeChange: (v: string) => void;
	handleThemePresetChange: (v: string) => void;
	handleSelectOpenChange: (open: boolean) => void;
	handleThemeHover: (themeId: string) => () => void;
	handleCustomThemeToggle: (enabled: boolean) => void;
	handleSetLightMode: () => void;
	handleSetDarkMode: () => void;
	handleColorInputChange: (
		varName: string,
	) => (e: ChangeEvent<HTMLInputElement>) => void;
	handleHexInputChange: (
		varName: string,
	) => (e: ChangeEvent<HTMLInputElement>) => void;
	handleHexInputBlur: (varName: string, committedHex: string) => () => void;
	handleResetCustomColors: () => void;
	handleTextSizeChange: (v: number) => void;
	handleSelectMouseMove: () => void;
	revertToSavedPreset: () => void;
}

// ── Hook implementation ─────────────────────────────────────────────

export function useThemeSettings({
	config,
	updateConfig,
	updateConfigDebounced,
	onThemeChange,
	themePresetProp,
}: UseThemeSettingsConfig): UseThemeSettingsReturn {
	// Track the last saved theme preset so hover previews can revert
	// to the user's saved choice (not the initial default) if they
	// hover without clicking.  Previously this was an inline
	// ``if (config) ref.current = ...`` block executed during render
	// (a ref mutation during render — React forbids writing to refs in
	// the render phase).  The useEffect form below runs the write
	// after commit, preserving the same "track the latest saved
	// preset" semantic without the render-phase side effect.
	const savedPresetRef = useRef<VoiceTyperConfig["theme_preset"]>(
		config?.theme_preset ?? "default",
	);
	useEffect(() => {
		if (config) savedPresetRef.current = config.theme_preset ?? "default";
	}, [config]);

	// Track whether the user has actually moved the mouse inside the
	// dropdown content.  Radix Select mounts the content portal directly
	// under the cursor when the dropdown opens, which can fire a
	// spurious ``onMouseEnter`` on the first item — applying that
	// item's theme as a "hover preview" even though the user hasn't
	// interacted.  We only honour ``onMouseEnter`` after the first
	// real ``onMouseMove`` inside the content.
	const userHoveredRef = useRef(false);

	// Ref mirror of ``customDraft`` so the stable ``revertToSavedPreset``
	// callback (empty deps) can re-apply custom vars when the saved
	// preset is ``CUSTOM_THEME_ID`` without needing ``customDraft`` as a
	// dependency (which would make the callback unstable and churn the
	// mouseleave listener on every color edit).  Initialized with
	// ``null`` because the ``customDraft`` ``const`` is declared later
	// (via ``useState``) and is in the temporal dead zone here.  The
	// tracking effect below updates the ref with the real value after
	// every render.
	const customDraftRef = useRef<CustomThemeData | null>(null);
	useEffect(() => {
		customDraftRef.current = customDraft;
	});

	// Track the last non-custom preset so we can revert when the
	// custom-theme toggle is turned off.  Previously this was an inline
	// ``if (config?.theme_preset && ...) ref.current = ...`` block
	// executed during render — a render-phase ref mutation.  Moved into
	// a useEffect so the write happens after commit.  The initial
	// ``useRef`` value still seeds from the first-seen config so the
	// first render has a sensible default before the effect runs.
	const lastNonCustomRef = useRef(
		config?.theme_preset && config.theme_preset !== "custom"
			? config.theme_preset
			: "default",
	);
	useEffect(() => {
		if (config?.theme_preset && config.theme_preset !== "custom") {
			lastNonCustomRef.current = config.theme_preset;
		}
	}, [config]);

	// ── Custom theme editor state ───────────────────────────────────
	// Initial tab matches the user's current dark/light mode so the
	// editor opens on the tab the user is most likely to edit (was
	// always "light" — Dark Mode users had to switch tabs manually).
	const [customEditorMode, setCustomEditorMode] = useState<"light" | "dark">(
		() =>
			typeof document !== "undefined" &&
			document.documentElement.classList.contains("dark")
				? "dark"
				: "light",
	);
	const [customDraft, setCustomDraft] = useState<CustomThemeData | null>(null);
	const customThemeInitRef = useRef(false);

	// Per-row hex input draft state.  The text input is a controlled
	// component whose value can be a partial hex (e.g. ``#1a2`` while
	// the user is typing).  We track each row's draft locally so:
	//   - The user can type partial values without the input snapping
	//     back to the last-committed hex on every keystroke.
	//   - We can show a red-border error state when the draft doesn't
	//     match the strict ``#rrggbb`` regex.
	//   - On blur we either commit (strict match) or revert to the
	//     last-committed hex (so an abandoned edit doesn't leave a
	//     half-typed value in the input).
	//
	// The drafts are re-seeded from ``customDraft`` whenever the draft
	// changes via the ``useEffect`` below — this keeps the text input
	// in sync when the colour is changed via the native colour picker
	// (which calls ``handleCustomColorChange`` directly, bypassing the
	// text input).
	const [hexDrafts, setHexDrafts] = useState<Record<string, string>>({});

	// The effective preset prefers the optimistic value from
	// ``useTheme.themePreset`` (passed in as ``themePresetProp``) over
	// the persisted ``config.theme_preset`` so the dropdown / switch /
	// picker update immediately on click rather than waiting for the
	// backend ``set_config`` round-trip.
	const effectivePreset: VoiceTyperConfig["theme_preset"] =
		themePresetProp ?? config?.theme_preset ?? "default";

	// ``customDraftIsDefault`` is true when the draft matches the
	// built-in DEFAULT_CUSTOM_LIGHT / DEFAULT_CUSTOM_DARK maps.  The
	// Reset button is disabled in that state — re-enabled the moment
	// the user edits any colour.  Compared by JSON.stringify on the 6
	// core vars (DEFAULT_CUSTOM_* only contain those 6 keys, so this is
	// exact).
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

	// One-time init moved into a useEffect. Previously this block
	// called setCustomDraft during render — a React anti-pattern that
	// forces a synchronous re-render before commit and breaks
	// concurrent-rendering invariants. Running it in an effect costs
	// one extra render (the draft is ``null`` on the first commit) but
	// is React-blessed. The ``customThemeInitRef`` guard ensures the
	// init runs only once even if ``config`` identity changes. Prefers
	// localStorage draft over config value.
	useEffect(() => {
		if (!config || customThemeInitRef.current) return;
		customThemeInitRef.current = true;
		const draft = loadDraftFromLS();
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
	// persist across page navigations. The cache is module-level
	// (shared across all instances), so this ensures the next time the
	// user visits Settings, colors are re-read from the DOM/THEMES
	// array rather than using potentially stale cached values.
	useEffect(() => {
		return () => {
			_themeColorCache.clear();
		};
	}, []);

	// Keep the hex-input drafts in sync with the committed
	// ``customDraft`` values.  Whenever the draft changes (via the
	// colour picker, the reset button, the toggle-on init, or a config
	// push), re-seed every row's text input with the new committed
	// hex.  Without this, picking a colour via the native picker would
	// leave the text input showing the previous hex until the user
	// manually re-typed it.
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

	// ── Event handlers ──────────────────────────────────────────────

	// Apply a custom color change immediately for preview, then debounce save.
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
				// save completes) so the draft survives a crash or
				// disconnect.
				saveDraftToLS(updated);

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
			// ``applyThemeVars("custom", isDark)`` without a third
			// argument clears all custom vars from the DOM (via
			// ``clearThemeVars()``) but then can't re-apply them because
			// the ``custom`` ThemePreset has empty light/dark var maps —
			// the user's actual colors live in the config/customDraft.
			// Derive the full var set from the draft so the revert
			// actually restores the saved custom theme.
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

	const handleColorSchemeChange = useCallback(
		(v: string) => {
			const m = v as VoiceTyperConfig["theme_mode"];
			// App-level handler owns the save + state update.
			onThemeChange?.(m);
		},
		[onThemeChange],
	);

	const handleThemePresetChange = useCallback(
		(v: string) => {
			const preset = v as VoiceTyperConfig["theme_preset"];
			savedPresetRef.current = preset;
			updateConfig({ theme_preset: preset });
		},
		[updateConfig],
	);

	const handleSelectOpenChange = useCallback(
		(open: boolean) => {
			if (open) {
				userHoveredRef.current = false;
			} else {
				revertToSavedPreset();
			}
		},
		[revertToSavedPreset],
	);

	const handleThemeHover = useCallback(
		(themeId: string) => () => {
			if (userHoveredRef.current) {
				applyHoverPreview(themeId);
			}
		},
		[applyHoverPreview],
	);

	const handleCustomThemeToggle = useCallback(
		(enabled: boolean) => {
			if (enabled) {
				const isDarkOn = document.documentElement.classList.contains("dark");
				setCustomEditorMode(isDarkOn ? "dark" : "light");
				const currentColors = getCurrentThemeColors(
					config?.theme_preset ?? "default",
					customDraftRef.current,
				);
				setCustomDraft(currentColors);
				saveDraftToLS(currentColors);
				updateConfig({ theme_preset: "custom", custom_theme: currentColors });
			} else {
				const fallback = lastNonCustomRef.current ?? "default";
				savedPresetRef.current = fallback;
				clearDraftLS();
				updateConfig({ theme_preset: fallback });
			}
		},
		[config, updateConfig],
	);

	const handleSetLightMode = useCallback(
		() => setCustomEditorMode("light"),
		[],
	);
	const handleSetDarkMode = useCallback(() => setCustomEditorMode("dark"), []);

	const handleColorInputChange = useCallback(
		(varName: string) => (e: ChangeEvent<HTMLInputElement>) =>
			handleCustomColorChange(customEditorMode, varName, e.target.value),
		[handleCustomColorChange, customEditorMode],
	);

	// Hex input handler — allows partial typing via the loose regex
	// (so the user can type ``#``, ``#1``, ``#1a``, ``#1a2``, … without
	// the input rejecting intermediate states), commits to
	// ``handleCustomColorChange`` only when the strict ``#rrggbb``
	// regex matches (so a half-typed value doesn't preview an invalid
	// colour on the document), and updates the local ``hexDrafts`` so
	// the input shows what the user typed.
	const handleHexInputChange = useCallback(
		(varName: string) => (e: ChangeEvent<HTMLInputElement>) => {
			const val = e.target.value;
			// Allow the user to clear the input entirely (so they can
			// retype from scratch) — the blur handler will revert if
			// the value is left empty.
			if (val === "" || /^#[0-9a-fA-F]{0,6}$/.test(val)) {
				setHexDrafts((prev) => ({ ...prev, [varName]: val }));
				if (/^#[0-9a-fA-F]{6}$/.test(val)) {
					handleCustomColorChange(customEditorMode, varName, val);
				}
			}
		},
		[handleCustomColorChange, customEditorMode],
	);

	// On blur, commit the strict-match value or revert to the
	// last-committed hex.  Reverting prevents a half-typed value (e.g.
	// ``#1a2``) from lingering in the input after the user clicks away
	// — the input snaps back to the colour the document is actually
	// using.
	const handleHexInputBlur = useCallback(
		(varName: string, committedHex: string) => () => {
			const val = hexDrafts[varName] ?? committedHex;
			if (/^#[0-9a-fA-F]{6}$/.test(val)) {
				// Already committed on change — nothing to do.
				return;
			}
			setHexDrafts((prev) => ({ ...prev, [varName]: committedHex }));
		},
		[hexDrafts],
	);

	const handleResetCustomColors = useCallback(() => {
		const defaults: CustomThemeData = {
			light: { ...DEFAULT_CUSTOM_LIGHT },
			dark: { ...DEFAULT_CUSTOM_DARK },
		};
		setCustomDraft(defaults);
		saveDraftToLS(defaults);
		const isDark = document.documentElement.classList.contains("dark");
		const modeVars = isDark ? defaults.dark : defaults.light;
		const derived = deriveCustomVars(modeVars, isDark);
		applyThemeVars("custom", isDark, derived);
		_themeColorCache.delete("custom");
		_themeColorCache.delete("default");
		updateConfig({ custom_theme: defaults });
	}, [updateConfig]);

	const handleTextSizeChange = useCallback(
		(v: number) => updateConfig({ text_size: v }),
		[updateConfig],
	);

	const handleSelectMouseMove = useCallback(() => {
		userHoveredRef.current = true;
	}, []);

	return {
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
	};
}
