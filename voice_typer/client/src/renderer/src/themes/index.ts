import type { ThemePreset } from "../themes";
import { customTheme } from "./custom";
import { defaultTheme } from "./default";

// ── Static metadata for the 10 lazy presets ───────────────────────────
// Each lazy preset's ``id`` / ``name`` / ``swatch`` is duplicated here
// (the canonical source is the individual preset file) so the Settings
// dropdown can render the full preset list WITHOUT loading any of the
// 10 lazy preset modules. The duplication is intentional and
// documented: the metadata is ~3 string fields per preset, while the
// full preset (light + dark var maps) is ~60 fields per preset. Loading
// 60 fields eagerly to avoid duplicating 3 is a bad trade.
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
	/** Static ``import()`` loader, Vite creates a separate chunk per entry. */
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
// ``loadThemePreset`` mutates the ``THEMES`` entry in place so that
// ``THEMES.find(t => t.id === id).light`` reflects the loaded vars
// WITHOUT requiring callers to thread the loaded preset through. This
// keeps ``applyThemeVars`` in ``themes.ts`` (which reads ``THEMES``
// directly) unchanged.
// The ``loaded`` set guards against redundant re-imports: once a
// preset is loaded, subsequent ``loadThemePreset(id)`` calls are
// instant no-ops (the dynamic ``import()`` is cached by the module
// system, but the set avoids even the microtask cost of re-reading
// the module).
const loadedLazyPresets = new Set<string>();

export async function loadThemePreset(id: string): Promise<void> {
	// ``default`` and ``custom`` are statically imported, always full.
	if (id === "default" || id === "custom") return;
	// Already loaded, avoid the redundant dynamic import.
	if (loadedLazyPresets.has(id)) return;

	const loader = lazyThemeLoaders[id];
	if (!loader) {
		console.warn(
			`[renderer:themes] loadThemePreset: unknown preset id "${id}"`,
		);
		return;
	}

	try {
		const preset = await loader();
		const entry = THEMES.find((t) => t.id === id);
		if (entry) {
			// Mutate in place so all references (THEMES, THEME_PRESETS,
			// DEFAULT_THEME_PRESET if it were this id, it never is, since
			// DEFAULT is index 0 = ``default``) see the populated vars.
			entry.light = preset.light;
			entry.dark = preset.dark;
		}
		loadedLazyPresets.add(id);
	} catch (err) {
		console.error(`[renderer:themes] loadThemePreset("${id}") failed:`, err);
		// Leave the entry with empty light/dark, the caller falls back
		// to the stylesheet default (same as the ``default`` preset).
	}
}

export async function getThemeByIdLazy(id: string): Promise<ThemePreset> {
	await loadThemePreset(id);
	const entry = THEMES.find((t) => t.id === id);
	// ``THEMES`` is non-empty and ``default`` is always at index 0, so
	// the fallback is always valid under ``noUncheckedIndexedAccess``.
	return entry ?? THEMES[0] ?? DEFAULT_THEME_PRESET;
}

// ── Build the THEMES array ────────────────────────────────────────────
// ``default`` and ``custom`` are full entries (static import). The 10
// lazy presets start with EMPTY ``light`` / ``dark`` maps —
// ``loadThemePreset(id)`` populates them in place on demand.
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

const THEMES_WITH_NAME_KEY: ThemePreset[] = RAW_THEMES.map((t) => ({
	...t,
	nameKey: `theme.preset.${t.id}`,
}));

export const THEME_PRESETS: Record<string, ThemePreset> = Object.fromEntries(
	THEMES_WITH_NAME_KEY.map((t) => [t.id, t]),
);

export const THEMES: ThemePreset[] = THEMES_WITH_NAME_KEY;

export const DEFAULT_THEME_PRESET: ThemePreset =
	THEMES_WITH_NAME_KEY[0] as ThemePreset;

// Re-export the two statically-imported presets so direct consumers
// (tests, tooling) can access them without a dynamic import. The 10
// lazy presets are NOT re-exported here, use ``loadThemePreset(id)``
// or ``getThemeByIdLazy(id)`` to access them.
export { customTheme, defaultTheme };
