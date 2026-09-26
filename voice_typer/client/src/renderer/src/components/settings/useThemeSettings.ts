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
import type { LausuConfig } from "@/types/config";
import type { ChangeEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { _themeColorCache } from "./themeColorCache";

/**
 * Defensive accessor for ``ThemePreset.nameKey``.
 *
 */
export function _getThemeNameKey(theme: unknown): string | null {
	if (typeof theme !== "object" || theme === null) return null;
	const k = (theme as { nameKey?: unknown }).nameKey;
	return typeof k === "string" && k.length > 0 ? k : null;
}

/**
 * Read the 6 core theme colors for BOTH light and dark modes of the
 * currently-selected built-in preset.
 */
type ThemeColorResult = {
	light: Record<string, string>;
	dark: Record<string, string>;
};

type ThemeColorSourceContext = {
	presetId: string;
	customDraft: CustomThemeData | null;
	keys: readonly string[];
};

/**
 * A single resolution strategy. Returns the resolved colours, or
 * ``null`` to signal "not applicable, fall through to the next
 */
type ThemeColorSource = {
	getColors: (ctx: ThemeColorSourceContext) => ThemeColorResult | null;
};

const DEFAULT_COLOR_RESULT: ThemeColorResult = {
	light: { ...DEFAULT_CUSTOM_LIGHT },
	dark: { ...DEFAULT_CUSTOM_DARK },
};

/**
 * Strategy table for ``getCurrentThemeColors``. Order matters: the
 * resolver walks this list (after picking the primary strategy) and
 */
const THEME_COLOR_SOURCES: Record<string, ThemeColorSource> = {
	default: {
		getColors: () => ({
			light: { ...DEFAULT_CUSTOM_LIGHT },
			dark: { ...DEFAULT_CUSTOM_DARK },
		}),
	},

	custom: {
		getColors: ({ customDraft, keys }: ThemeColorSourceContext) => {
			const lightCore = customDraft?.light ?? { ...DEFAULT_CUSTOM_LIGHT };
			const darkCore = customDraft?.dark ?? { ...DEFAULT_CUSTOM_DARK };
			const light: Record<string, string> = {};
			const dark: Record<string, string> = {};
			for (const key of keys) {
				light[key] = lightCore[key] ?? DEFAULT_CUSTOM_LIGHT[key] ?? "#000000";
				dark[key] = darkCore[key] ?? DEFAULT_CUSTOM_DARK[key] ?? "#000000";
			}
			return { light, dark };
		},
	},

	builtin: {
		getColors: ({ presetId, keys }: ThemeColorSourceContext) => {
			const theme = THEMES.find((t) => t.id === presetId);
			if (!theme) return null;
			const light: Record<string, string> = {};
			const dark: Record<string, string> = {};
			for (const key of keys) {
				light[key] = cssColorToHex(theme.light[key] ?? "");
				dark[key] = cssColorToHex(theme.dark[key] ?? "");
			}
			return { light, dark };
		},
	},

	dom: {
		getColors: ({ keys }: ThemeColorSourceContext) => {
			if (
				typeof document === "undefined" ||
				typeof getComputedStyle !== "function"
			) {
				return null;
			}
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
			return { light, dark };
		},
	},

	fallback: {
		getColors: () => ({
			light: { ...DEFAULT_CUSTOM_LIGHT },
			dark: { ...DEFAULT_CUSTOM_DARK },
		}),
	},
};

/**
 * Pick the primary resolution strategy for a preset id. Returns the
 * key into ``THEME_COLOR_SOURCES``. ``builtin`` returns null inside
 */
function pickColorSource(presetId: string): keyof typeof THEME_COLOR_SOURCES {
	if (presetId === "default" || presetId === "") return "default";
	if (presetId === "custom") return "custom";
	return "builtin";
}

/**
 * Resolution chain after the primary strategy. ``builtin`` may return
 * null (unknown preset id); we then try ``dom`` (may also return null
 */
const COLOR_SOURCE_FALLTHROUGH: ReadonlyArray<
	keyof typeof THEME_COLOR_SOURCES
> = ["dom", "fallback"];

function getCurrentThemeColors(
	currentPresetId: string,
	customDraft: CustomThemeData | null = null,
): ThemeColorResult {
	const cached = _themeColorCache.get(currentPresetId);
	if (cached) return cached;

	const keys = CUSTOM_COLOR_KEYS.map((k) => k.var);
	const ctx: ThemeColorSourceContext = {
		presetId: currentPresetId,
		customDraft,
		keys,
	};

	const primary = pickColorSource(currentPresetId);
	const primarySource = THEME_COLOR_SOURCES[primary];
	let result: ThemeColorResult | null =
		primarySource !== undefined ? primarySource.getColors(ctx) : null;

	if (result === null) {
		for (const fallbackKey of COLOR_SOURCE_FALLTHROUGH) {
			const source = THEME_COLOR_SOURCES[fallbackKey];
			if (source === undefined) continue;
			result = source.getColors(ctx);
			if (result !== null) break;
		}
	}

	if (result === null) result = DEFAULT_COLOR_RESULT;

	_themeColorCache.set(currentPresetId || "default", result);
	return result;
}

/**
 * Compute the { background, foreground } pair shown inside the square
 * preview next to each theme in the dropdown and in the trigger.
 */
export function getThemePreviewColors(
	themeId: string,
	isDark: boolean,
	customDraft: CustomThemeData | null,
): { bg: string; fg: string } {
	if (themeId === "custom" && customDraft) {
		const vars = isDark ? customDraft.dark : customDraft.light;
		return {
			bg: vars["--primary"] ?? (isDark ? "#6b7fd4" : "#5469d4"),
			fg: vars["--foreground"] ?? (isDark ? "#ededed" : "#0a0a0a"),
		};
	}
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
 * Inputs to ``useThemeSettings``. Mirrors the props the
 * ``ThemeSettingsSection`` component receives that the hook needs to
 */
export interface UseThemeSettingsConfig {
	config: LausuConfig | null;
	updateConfig: (updates: Partial<LausuConfig>) => void;
	updateConfigDebounced: (
		key: keyof LausuConfig,
		value: unknown,
		delayMs?: number,
	) => void;
	themeModeProp?: LausuConfig["theme_mode"];
	onThemeChange?: (mode: LausuConfig["theme_mode"]) => void;
	themePresetProp?: LausuConfig["theme_preset"];
}

/**
 * Outputs from ``useThemeSettings``. The component consumes the state
 * values directly in its JSX and passes the handlers to the
 */
export interface UseThemeSettingsReturn {
	/**
	 * Currently-active tab in the custom-theme editor (``"light"`` / ``"dark"``).
	 */
	customEditorMode: "light" | "dark";
	/**
	 * In-memory custom-theme draft (``null`` until the init effect runs).
	 */
	customDraft: CustomThemeData | null;
	/**
	 * Per-row hex-input partial-typing drafts (keyed by CSS var name).
	 */
	hexDrafts: Record<string, string>;
	/**
	 * Effective preset (prefers ``themePresetProp`` over ``config.theme_preset``).
	 */
	effectivePreset: LausuConfig["theme_preset"];
	/**
	 * ``true`` when the draft matches the built-in DEFAULT_CUSTOM_* maps.
	 */
	customDraftIsDefault: boolean;
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

export function useThemeSettings({
	config,
	updateConfig,
	updateConfigDebounced,
	onThemeChange,
	themePresetProp,
}: UseThemeSettingsConfig): UseThemeSettingsReturn {
	const savedPresetRef = useRef<LausuConfig["theme_preset"]>(
		config?.theme_preset ?? "default",
	);
	useEffect(() => {
		if (config) savedPresetRef.current = config.theme_preset ?? "default";
	}, [config]);

	const userHoveredRef = useRef(false);

	const customDraftRef = useRef<CustomThemeData | null>(null);
	useEffect(() => {
		customDraftRef.current = customDraft;
	});

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

	const [customEditorMode, setCustomEditorMode] = useState<"light" | "dark">(
		() =>
			typeof document !== "undefined" &&
			document.documentElement.classList.contains("dark")
				? "dark"
				: "light",
	);
	const [customDraft, setCustomDraft] = useState<CustomThemeData | null>(null);
	const customThemeInitRef = useRef(false);

	const [hexDrafts, setHexDrafts] = useState<Record<string, string>>({});

	const effectivePreset: LausuConfig["theme_preset"] =
		themePresetProp ?? config?.theme_preset ?? "default";

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

	useEffect(() => {
		return () => {
			_themeColorCache.clear();
		};
	}, []);

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

	// React state updaters must stay PURE: StrictMode double-invokes them
	const handleCustomColorChange = useCallback(
		(mode: "light" | "dark", colorKey: string, hex: string) => {
			const prev = customDraftRef.current;
			if (!prev) return;
			const updated: CustomThemeData = {
				...prev,
				[mode]: { ...prev[mode], [colorKey]: hex },
			};
			customDraftRef.current = updated;
			setCustomDraft(() => updated);

			const isDark = document.documentElement.classList.contains("dark");
			const modeVars = isDark ? updated.dark : updated.light;
			const derived = deriveCustomVars(modeVars, isDark);
			applyThemeVars("custom", isDark, derived);

			_themeColorCache.delete("custom");
			_themeColorCache.delete("default");

			saveDraftToLS(updated);

			updateConfigDebounced("custom_theme", updated, 300);
		},
		[updateConfigDebounced],
	);

	const applyHoverPreview = useCallback((presetId: string) => {
		const isDark = document.documentElement.classList.contains("dark");
		applyThemeVars(presetId, isDark);
	}, []);

	const revertToSavedPreset = useCallback(() => {
		const isDark = document.documentElement.classList.contains("dark");
		const preset = savedPresetRef.current;
		if (preset === CUSTOM_THEME_ID) {
			const draft = customDraftRef.current;
			if (draft) {
				const modeVars = isDark ? draft.dark : draft.light;
				const derived = deriveCustomVars(modeVars, isDark);
				applyThemeVars(CUSTOM_THEME_ID, isDark, derived);
			} else {
				applyThemeVars("default", isDark);
			}
		} else {
			applyThemeVars(preset, isDark);
		}
	}, []);

	const handleColorSchemeChange = useCallback(
		(v: string) => {
			const m = v as LausuConfig["theme_mode"];
			onThemeChange?.(m);
		},
		[onThemeChange],
	);

	const handleThemePresetChange = useCallback(
		(v: string) => {
			const preset = v as LausuConfig["theme_preset"];
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
				customDraftRef.current = currentColors;
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

	const handleHexInputChange = useCallback(
		(varName: string) => (e: ChangeEvent<HTMLInputElement>) => {
			const val = e.target.value;
			if (val === "" || /^#[0-9a-fA-F]{0,6}$/.test(val)) {
				setHexDrafts((prev) => ({ ...prev, [varName]: val }));
				if (/^#[0-9a-fA-F]{6}$/.test(val)) {
					handleCustomColorChange(customEditorMode, varName, val);
				}
			}
		},
		[handleCustomColorChange, customEditorMode],
	);

	const handleHexInputBlur = useCallback(
		(varName: string, committedHex: string) => () => {
			const val = hexDrafts[varName] ?? committedHex;
			if (/^#[0-9a-fA-F]{6}$/.test(val)) {
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
		customDraftRef.current = defaults;
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
