/**
 * Module-level cache for theme color computations.
 *
 * Extracted from ThemeSettingsSection.tsx to avoid the
 * react-refresh/only-export-components lint warning (mixing component
 * and non-component exports in the same file breaks Fast Refresh).
 *
 * The cache stores computed {light, dark} color objects keyed by preset
 * ID, avoiding redundant getComputedStyle + cssColorToHex DOM queries
 * on repeated calls with the same preset.
 *
 * Cache invalidation:
 *   - 'custom' and 'default' entries are deleted when the user changes
 *     a custom color (see handleCustomColorChange in ThemeSettingsSection)
 *   - All entries are cleared on ThemeSettingsSection unmount (see the
 *     cleanup useEffect in ThemeSettingsSection)
 */

export type ThemeColorCacheEntry = {
	light: Record<string, string>;
	dark: Record<string, string>;
};

export const _themeColorCache = new Map<string, ThemeColorCacheEntry>();
