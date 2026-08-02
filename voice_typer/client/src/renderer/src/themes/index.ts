/**
 * Aggregator for individual theme-preset modules.
 *
 * Lazy theme registry: the 10 non-default/non-custom preset
 * modules are loaded ON DEMAND via a dynamic ``import()`` registry
 * instead of being statically imported at module load. Only ``default``
 * and ``custom`` (the fallback pair — both are no-ops with empty
 * light/dark maps) remain statically imported so the renderer always
 * has a valid preset to fall back to without an async fetch.
 *
 * Each preset lives in its own file under ``./themes/`` so a caller can
 * dynamically ``import()`` only the theme it needs (rather than pulling
 * in the whole catalogue). Vite emits each preset as a SEPARATE async
 * chunk; the initial renderer bundle no longer contains the 10 preset
 * light/dark var maps (~20 KB of CSS strings).
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
 * ── lazy-load contract ─────────────────────────────────────
 *
 * ``THEMES`` is a 12-entry array whose entries carry FULL metadata
 * (``id`` / ``name`` / ``nameKey`` / ``swatch``) so the Settings
 * dropdown renders without a Suspense fallback. The 10 lazy entries
 * start with EMPTY ``light`` / ``dark`` maps; ``loadThemePreset(id)``
 * dynamically ``import()``s the preset file and POPULATES the entry
 * IN PLACE (mutating the same object reference so ``THEMES.find(...)``
 * and ``THEME_PRESETS[id]`` both see the populated vars). The
 * ``default`` and ``custom`` entries are full from the start (static
 * import).
 *
 * ``theme-bootstrap.ts`` calls ``await loadThemePreset(presetId)`` before
 * ``applyThemeVars(presetId, ...)`` so the bootstrap path applies the
 * correct CSS vars before React mounts (top-level await in the
 * bootstrap module guarantees ordering).
 *
 * Runtime callers of ``applyThemeVars`` (``useTheme.ts``,
 * ``useThemeSettings.ts``, ``useThemeSync.ts``) read
 * ``THEMES.find(t => t.id === presetId)`` which returns the (now
 * populated) entry. For the active preset this is always populated
 * (the bootstrap ran first). For presets the user SWITCHES to at
 * runtime, ``loadThemePreset`` is idempotent and cached, so the
 * caller can ``await loadThemePreset(newId)`` before
 * ``applyThemeVars(newId, ...)``. The bootstrap's background pre-fetch
 * (in ``theme-bootstrap.ts``) populates the cache for all presets
 * shortly after first paint so runtime switching works without
 * per-caller changes.
 *
 * The parity test (``themes/__tests__/parity.test.ts``) and the
 * status-tokens test (``themes/__tests__/status-tokens.test.ts``)
 * were updated to ``await loadThemePreset(id)`` for each lazy preset
 * before asserting light/dark var coverage (the source TODO called
 * this out).
 */
import type { ThemePreset } from "../themes";
import { customTheme } from "./custom";
import { defaultTheme } from "./default";

// ── Static metadata for the 10 lazy presets ───────────────────────────
//
// Each lazy preset's ``id`` / ``name`` / ``swatch`` is duplicated here
// (the canonical source is the individual preset file) so the Settings
// dropdown can render the full preset list WITHOUT loading any of the
// 10 lazy preset modules. The duplication is intentional and
// documented: the metadata is ~3 string fields per preset, while the
// full preset (light + dark var maps) is ~60 fields per preset. Loading
// 60 fields eagerly to avoid duplicating 3 is a bad trade.
//
// When a preset's ``name`` / ``swatch`` changes in its source file,
// update the entry here too. The ``loadThemePreset`` function below
// ignores the loaded module's ``name`` / ``swatch`` (it only copies
// ``light`` / ``dark``) so the metadata stays the single source for
// the dropdown label/swatch.
interface LazyPresetMetadata {
	id: string;
	name: string;
	swatch: string;
	/** Named export in the preset file (e.g. ``amoledTheme``). */
	exportName: string;
	/** Static ``import()`` loader — Vite creates a separate chunk per entry. */
	loader: () => Promise<Record<string, unknown>>;
}

const LAZY_PRESETS: LazyPresetMetadata[] = [
	{
		id: "amoled",
		name: "Amoled",
		swatch: "oklch(0 0 0)",
		exportName: "amoledTheme",
		loader: () => import("./amoled"),
	},
	{
		id: "nord",
		name: "Nord",
		swatch: "oklch(0.5 0.06 240)",
		exportName: "nordTheme",
		loader: () => import("./nord"),
	},
	{
		id: "dracula",
		name: "Dracula",
		swatch: "oklch(0.5 0.16 320)",
		exportName: "draculaTheme",
		loader: () => import("./dracula"),
	},
	{
		id: "sepia",
		name: "Sepia",
		swatch: "oklch(0.6 0.08 50)",
		exportName: "sepiaTheme",
		loader: () => import("./sepia"),
	},
	{
		id: "monokai",
		name: "Monokai",
		swatch: "oklch(0.75 0.15 100)",
		exportName: "monokaiTheme",
		loader: () => import("./monokai"),
	},
	{
		id: "ayu",
		name: "Ayu",
		swatch: "oklch(0.7 0.14 70)",
		exportName: "ayuTheme",
		loader: () => import("./ayu"),
	},
	{
		id: "github",
		name: "GitHub",
		swatch: "oklch(0.5 0.12 260)",
		exportName: "githubTheme",
		loader: () => import("./github"),
	},
	{
		id: "catppuccin",
		name: "Catppuccin",
		swatch: "oklch(0.65 0.12 330)",
		exportName: "catppuccinTheme",
		loader: () => import("./catppuccin"),
	},
	{
		id: "tokyo-night",
		name: "Tokyo Night",
		swatch: "oklch(0.55 0.14 280)",
		exportName: "tokyoNightTheme",
		loader: () => import("./tokyo-night"),
	},
	{
		id: "solarized",
		name: "Solarized",
		swatch: "oklch(0.6 0.1 200)",
		exportName: "solarizedTheme",
		loader: () => import("./solarized"),
	},
];

/**
 * Lazy-loader registry: maps preset id → ``() => Promise<Omit<ThemePreset,
 * "nameKey">>``. Each loader dynamically ``import()``s the preset file
 * and extracts the named export (e.g. ``amoledTheme``).
 *
 * Vite statically analyses each ``import("./amoled")`` call in the
 * ``LAZY_PRESETS`` array literal and emits a SEPARATE chunk per preset
 * file. The chunk is only fetched when ``loadThemePreset(id)`` is
 * actually called for that id.
 */
export const lazyThemeLoaders: Record<
	string,
	() => Promise<Omit<ThemePreset, "nameKey">>
> = Object.fromEntries(
	LAZY_PRESETS.map((p) => [
		p.id,
		async () => {
			const mod = await p.loader();
			const preset = mod[p.exportName] as Omit<ThemePreset, "nameKey">;
			return preset;
		},
	]),
);

// ── In-place population cache ─────────────────────────────────────────
//
// ``loadThemePreset`` mutates the ``THEMES`` entry in place so that
// ``THEMES.find(t => t.id === id).light`` reflects the loaded vars
// WITHOUT requiring callers to thread the loaded preset through. This
// keeps ``applyThemeVars`` in ``themes.ts`` (which reads ``THEMES``
// directly) unchanged.
//
// The ``loaded`` set guards against redundant re-imports: once a
// preset is loaded, subsequent ``loadThemePreset(id)`` calls are
// instant no-ops (the dynamic ``import()`` is cached by the module
// system, but the set avoids even the microtask cost of re-reading
// the module).
const loadedLazyPresets = new Set<string>();

/**
 * Dynamically ``import()`` the preset file for ``id`` and populate the
 * corresponding ``THEMES`` / ``THEME_PRESETS`` entry's ``light`` /
 * ``dark`` maps in place. Idempotent — safe to call multiple times.
 *
 * No-op for ``default`` and ``custom`` (already full from static
 * imports) and for unknown ids (defensive — logs a warning).
 *
 * @returns A Promise that resolves when the entry is populated (or
 *   immediately for ``default`` / ``custom`` / unknown ids).
 */
export async function loadThemePreset(id: string): Promise<void> {
	// ``default`` and ``custom`` are statically imported — always full.
	if (id === "default" || id === "custom") return;
	// Already loaded — avoid the redundant dynamic import.
	if (loadedLazyPresets.has(id)) return;

	const loader = lazyThemeLoaders[id];
	if (!loader) {
		console.warn(`[themes] loadThemePreset: unknown preset id "${id}"`);
		return;
	}

	try {
		const preset = await loader();
		const entry = THEMES.find((t) => t.id === id);
		if (entry) {
			// Mutate in place so all references (THEMES, THEME_PRESETS,
			// DEFAULT_THEME_PRESET if it were this id — it never is, since
			// DEFAULT is index 0 = ``default``) see the populated vars.
			entry.light = preset.light;
			entry.dark = preset.dark;
		}
		loadedLazyPresets.add(id);
	} catch (err) {
		console.error(`[themes] loadThemePreset("${id}") failed:`, err);
		// Leave the entry with empty light/dark — the caller falls back
		// to the stylesheet default (same as the ``default`` preset).
	}
}

/**
 * Dynamically load a preset by id and return the full ``ThemePreset``
 * (with ``nameKey`` injected). Use this when you need the preset object
 * itself (not just the side effect of populating ``THEMES``).
 *
 * Calls ``loadThemePreset(id)`` internally so the ``THEMES`` entry is
 * also populated as a side effect.
 */
export async function getThemeByIdLazy(id: string): Promise<ThemePreset> {
	await loadThemePreset(id);
	const entry = THEMES.find((t) => t.id === id);
	// ``THEMES`` is non-empty and ``default`` is always at index 0, so
	// the fallback is always valid under ``noUncheckedIndexedAccess``.
	return entry ?? THEMES[0] ?? DEFAULT_THEME_PRESET;
}

// ── Build the THEMES array ────────────────────────────────────────────
//
// ``default`` and ``custom`` are full entries (static import). The 10
// lazy presets start with EMPTY ``light`` / ``dark`` maps —
// ``loadThemePreset(id)`` populates them in place on demand.
//
// The array order matches the pre-refactor literal in ``themes.ts``
// exactly so the Settings dropdown, default fallback (``THEMES[0]``),
// and any index-sensitive callers continue to behave identically.

/**
 * Build a metadata-only ``ThemePreset`` entry for a lazy preset. The
 * ``light`` / ``dark`` maps start empty and are populated in place by
 * ``loadThemePreset(id)``.
 */
function makeLazyThemeEntry(meta: LazyPresetMetadata): ThemePreset {
	return {
		id: meta.id,
		name: meta.name,
		nameKey: `theme.preset.${meta.id}`,
		swatch: meta.swatch,
		light: {}, // populated by ``loadThemePreset(id)``
		dark: {}, // populated by ``loadThemePreset(id)``
	};
}

/**
 * raw preset list (no ``nameKey`` — injected below). The ``default``
 * and ``custom`` entries are full (static import); the 10 lazy entries
 * are metadata-only (light/dark empty until ``loadThemePreset`` runs).
 *
 * The list below is the source of both ``THEME_PRESETS`` (record) and
 * ``THEMES`` (ordered array) — keeping them in sync is enforced by
 * deriving both from the same constant.
 */
// LAZY_PRESETS is a static literal whose length is known at compile
// time (10 entries, indices 0-9). The indexed access is in-bounds by
// construction; under noUncheckedIndexedAccess TypeScript widens
// LAZY_PRESETS[i] to LazyPresetMetadata | undefined, so a type
// assertion is required. A non-null assertion (`!`) is banned by
// biome's noNonNullAssertion, so the explicit `as` cast is used
// instead (same documented in-bounds guarantee).
const RAW_THEMES: Omit<ThemePreset, "nameKey">[] = [
	defaultTheme,
	makeLazyThemeEntry(LAZY_PRESETS[0] as LazyPresetMetadata), // amoled
	makeLazyThemeEntry(LAZY_PRESETS[1] as LazyPresetMetadata), // nord
	makeLazyThemeEntry(LAZY_PRESETS[2] as LazyPresetMetadata), // dracula
	makeLazyThemeEntry(LAZY_PRESETS[3] as LazyPresetMetadata), // sepia
	customTheme,
	makeLazyThemeEntry(LAZY_PRESETS[4] as LazyPresetMetadata), // monokai
	makeLazyThemeEntry(LAZY_PRESETS[5] as LazyPresetMetadata), // ayu
	makeLazyThemeEntry(LAZY_PRESETS[6] as LazyPresetMetadata), // github
	makeLazyThemeEntry(LAZY_PRESETS[7] as LazyPresetMetadata), // catppuccin
	makeLazyThemeEntry(LAZY_PRESETS[8] as LazyPresetMetadata), // tokyo-night
	makeLazyThemeEntry(LAZY_PRESETS[9] as LazyPresetMetadata), // solarized
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
 *
 * NOTE: for lazy presets, the entry's ``light`` / ``dark`` maps are
 * empty until ``loadThemePreset(id)`` is called. Callers that need
 * the full var maps should ``await loadThemePreset(id)`` first.
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
 *
 * NOTE: for lazy presets (all except ``default`` and ``custom``), the
 * entry's ``light`` / ``dark`` maps are empty until
 * ``loadThemePreset(id)`` is called. The bootstrap
 * (``theme-bootstrap.ts``) calls ``loadThemePreset`` for the active
 * preset before React mounts; the parity / status-tokens tests call
 * it for every lazy preset before asserting var coverage.
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

// Re-export the two statically-imported presets so direct consumers
// (tests, tooling) can access them without a dynamic import. The 10
// lazy presets are NOT re-exported here — use ``loadThemePreset(id)``
// or ``getThemeByIdLazy(id)`` to access them.
export { customTheme, defaultTheme };
