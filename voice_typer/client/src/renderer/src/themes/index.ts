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
 *
 * ── lazy-load opportunity, deferred ──────────────────────────
 *
 * The 12 preset imports below are STATIC. Each preset module is small
 * (a handful of CSS variable strings), so the bundle-size cost of
 * eagerly loading all 12 is modest — but a future optimisation can
 * convert these to a registry + dynamic ``import()`` so only the
 * active preset + ``default`` + ``custom`` (the fallback pair) are
 * loaded eagerly:
 *
 *   export const THEME_IDS = [
 *     "default", "custom", "amoled", "nord", "dracula", ...
 *   ] as const;
 *   export const THEME_METADATA: ThemePresetMetadata[] = THEME_IDS.map(...);
 *   export async function getThemeByIdLazy(id: string): Promise<ThemePreset> {
 *     const mod = await import(`./${id}`);
 *     return mod[`${id}Theme`];
 *   }
 *
 * Why this refactor is deferred:
 *
 *  1. The parity test in ``themes/__tests__/parity.test.ts`` iterates
 *     ``THEMES`` SYNCHRONOUSLY to assert light/dark var coverage for
 *     every preset. A lazy-load registry would force that test to
 *     become async (or to await every preset), which is a wider-blast
 *     change than this performance pass should make.
 *
 *  2. ``ThemeSettingsSection.tsx`` renders the dropdown from the
 *     synchronous ``THEMES`` array (it needs ``name`` + ``swatch``
 *     for every preset up-front). A lazy-load refactor would require
 *     splitting the metadata (eager) from the full preset (lazy) and
 *     threading an async-load callback through the Settings UI.
 *
 *  3. The runtime cost of eagerly importing 12 small objects is
 *     negligible compared to the actual perf wins (e.g. row
 *     memoisation, visibility gating) addressed in this pass.
 *
 * When this refactor is eventually done, the metadata-only array
 * (id + name + swatch + nameKey) should stay eager so the Settings
 * dropdown renders without a Suspense fallback, and the full preset
 * (with light/dark var maps) should be loaded on selection.
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

/**
 * Fallback preset returned by ``getThemeById`` when the requested id is
 * unknown. The first preset in ``THEMES`` is treated as the canonical
 * default so the UI always has a valid preset to render. Typed as a
 * non-optional ``ThemePreset`` so callers don't have to guard against
 * undefined under `noUncheckedIndexedAccess`.
 */
export const DEFAULT_THEME_PRESET: ThemePreset =
	THEMES_WITH_NAME_KEY[0] as ThemePreset;

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
