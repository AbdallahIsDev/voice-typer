/**
 * Aggregator for individual theme-preset modules.
 *
 * PERF-001: presets split into ./themes/ for lazy loading. Each preset
 * lives in its own file so a caller can dynamically ``import()`` only
 * the theme it needs (rather than pulling in the whole catalogue).
 *
 * This module re-aggregates every preset back into the original shapes
 * consumed by ``themes.ts``:
 *
 * - ``THEME_PRESETS`` — a ``Record<string, ThemePreset>`` keyed by id.
 * - ``THEMES`` — the canonical ordered ``ThemePreset[]`` (matches the
 *   pre-refactor array literal in ``themes.ts`` exactly).
 *
 * ``themes.ts`` re-exports both, so existing consumers that import
 * from ``@/themes`` continue to work unchanged.
 */
import type { ThemePreset } from "../themes";
import { amoledTheme } from "./amoled";
import { ayuTheme } from "./ayu";
import { catppuccinTheme } from "./catppuccin";
import { customTheme } from "./custom";
import { defaultTheme } from "./default";
import { draculaTheme } from "./dracula";
import { githubTheme } from "./github";
import { monokaiTheme } from "./monokai";
import { nordTheme } from "./nord";
import { sepiaTheme } from "./sepia";
import { solarizedTheme } from "./solarized";
import { tokyoNightTheme } from "./tokyo-night";

/**
 * All built-in theme presets keyed by their ``id``.
 *
 * Use this when you need O(1) id → preset lookup. The order of keys is
 * not guaranteed — use ``THEMES`` if you need the canonical display
 * order.
 */
export const THEME_PRESETS: Record<string, ThemePreset> = {
	[defaultTheme.id]: defaultTheme,
	[amoledTheme.id]: amoledTheme,
	[nordTheme.id]: nordTheme,
	[draculaTheme.id]: draculaTheme,
	[sepiaTheme.id]: sepiaTheme,
	[customTheme.id]: customTheme,
	[monokaiTheme.id]: monokaiTheme,
	[ayuTheme.id]: ayuTheme,
	[githubTheme.id]: githubTheme,
	[catppuccinTheme.id]: catppuccinTheme,
	[tokyoNightTheme.id]: tokyoNightTheme,
	[solarizedTheme.id]: solarizedTheme,
};

/**
 * Canonical ordered list of all built-in theme presets.
 *
 * Order matches the pre-refactor array literal in ``themes.ts`` so the
 * Settings dropdown, default fallback (``THEMES[0]``), and any other
 * index-sensitive callers continue to behave identically.
 */
export const THEMES: ThemePreset[] = [
	defaultTheme,
	amoledTheme,
	nordTheme,
	draculaTheme,
	sepiaTheme,
	customTheme,
	monokaiTheme,
	ayuTheme,
	githubTheme,
	catppuccinTheme,
	tokyoNightTheme,
	solarizedTheme,
];

/** Re-export each preset for direct (lazy-loadable) access. */
export {
	amoledTheme,
	ayuTheme,
	catppuccinTheme,
	customTheme,
	defaultTheme,
	draculaTheme,
	githubTheme,
	monokaiTheme,
	nordTheme,
	sepiaTheme,
	solarizedTheme,
	tokyoNightTheme,
};
