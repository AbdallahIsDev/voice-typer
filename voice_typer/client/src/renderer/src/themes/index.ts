/**
 * Aggregator for individual theme-preset modules.
 *
 * presets split into ./themes/ for lazy loading. Each preset
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
 * raw preset list (no ``nameKey``). The individual preset files
 * declare only ``id``, ``name``, ``swatch``, ``light``, ``dark`` — the
 * ``nameKey`` field is added here in the aggregator so there is a
 * single source of truth for the ``theme.preset.<id>`` i18n key shape.
 *
 * The list below is the source of both ``THEME_PRESETS`` (record) and
 * ``THEMES`` (ordered array) — keeping them in sync is enforced by
 * deriving both from the same constant.
 */
const RAW_THEMES: Omit<ThemePreset, "nameKey">[] = [
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

/**
 * inject ``nameKey: `theme.preset.${id}` `` for every preset.
 * Consumers (``ThemeSettingsSection.tsx``) render the localised preset
 * name via ``t(preset.nameKey)``, falling back to the hardcoded English
 * ``name`` only when the locale file is missing the key. The parity
 * test in ``themes/__tests__/parity.test.ts`` asserts every preset
 * carries a ``nameKey`` matching this exact shape and that the key
 * exists in every locale file.
 */
const THEMES_WITH_NAME_KEY: ThemePreset[] = RAW_THEMES.map((t) => ({
	...t,
	nameKey: `theme.preset.${t.id}`,
}));

/**
 * All built-in theme presets keyed by their ``id``.
 *
 * Use this when you need O(1) id → preset lookup. The order of keys is
 * not guaranteed — use ``THEMES`` if you need the canonical display
 * order.
 */
export const THEME_PRESETS: Record<string, ThemePreset> = Object.fromEntries(
	THEMES_WITH_NAME_KEY.map((t) => [t.id, t]),
);

/**
 * Canonical ordered list of all built-in theme presets.
 *
 * Order matches the pre-refactor array literal in ``themes.ts`` so the
 * Settings dropdown, default fallback (``THEMES[0]``), and any other
 * index-sensitive callers continue to behave identically.
 */
export const THEMES: ThemePreset[] = THEMES_WITH_NAME_KEY;

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
