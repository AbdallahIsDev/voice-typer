/**
 * Canonical location for hotkey display formatting.
 *
 * Extracted from the former ``hotkey-utils.ts`` monolith.
 * Pure, side-effect-free formatting helpers that turn pynput-form
 * hotkey strings (e.g. ``"<ctrl>+<alt>+v"``) into platform-localized
 * display labels (e.g. "Ctrl+Alt+V" on Windows/Linux, "⌃⌥V" on macOS).
 *
 * Also holds the canonical default-hotkey constants
 * (``HOTKEY_DEFAULT`` / ``REPASTE_HOTKEY_DEFAULT``) — they're the
 * fallback values fed into ``configHotkeyLabels``, so keeping them
 * next to the formatter avoids a keymap↔format circular import
 * (``configHotkeyLabels`` reads both).
 *
 * Platform detection lives in ``hotkey-keymap.ts`` (the IS_* constants)
 * and ``hotkey-validation.ts`` (``detectPlatform``); the shared
 * validation API is imported from ``hotkey-validation.ts``.
 */

import { getLocale, t } from "@/i18n/i18n";
import { detectPlatform } from "./hotkey-validation";

/**
 * macOS glyph table for the four primary modifiers. Applied only when
 * the detected platform is darwin. Keys not in this map fall through
 * to the text-label path inside ``formatHotkey``.
 *
 * Hoisted to module scope: this is a pure Unicode-symbol table (⌘ ⇧ ⌥ ⌃)
 * with no locale dependence, so it never changes between renders or
 * locale switches. The previous in-function allocation re-built a
 * ~14-entry object on every ``formatHotkey`` call — at ~6 calls per
 * Settings render (one per preset row) and 1 call per ``HotkeyPicker``
 * mount, this was measurable on slow devices.
 */
const MAC_MODIFIER_GLYPHS: Readonly<Record<string, string>> = {
	ctrl: "\u2303", // ⌃
	ctrl_l: "\u2303",
	ctrl_r: "\u2303",
	shift: "\u21E7", // ⇧
	shift_l: "\u21E7",
	shift_r: "\u21E7",
	alt: "\u2325", // ⌥
	alt_l: "\u2325",
	alt_r: "\u2325",
	alt_gr: "\u2325",
	cmd: "\u2318", // ⌘
	cmd_l: "\u2318",
	cmd_r: "\u2318",
};

/**
 * Per-locale cache of the ``KEY_LABEL_ALIAS`` map used by
 * ``formatHotkey``. The map contains ~28 entries whose values come
 * from ``t("hotkeyKeys.*")`` — building it requires 28 ``t()``
 * lookups. Without caching, every ``formatHotkey`` call rebuilt the
 * map (and re-resolved every translation), which dominated the
 * function's runtime in the Settings panel where ``formatHotkey`` is
 * called ~6× per render (preset labels) plus once per ``HotkeyPicker``
 * mount.
 *
 * Cache invalidation: keyed by ``getLocale()``. The cache is rebuilt
 * lazily on the first ``formatHotkey`` call after a locale switch.
 * ``getLocale`` reads the i18n store's current locale synchronously,
 * so this works in both React and non-React contexts (tests,
 * utilities).
 */
let _keyLabelAliasCache: {
	locale: string;
	map: Readonly<Record<string, string>>;
} | null = null;

function getKeyLabelAlias(): Readonly<Record<string, string>> {
	const locale = getLocale();
	if (_keyLabelAliasCache?.locale === locale) {
		return _keyLabelAliasCache.map;
	}
	const map: Record<string, string> = {
		ctrl: t("hotkeyKeys.ctrl"),
		ctrl_l: t("hotkeyKeys.ctrl"),
		ctrl_r: t("hotkeyKeys.ctrl"),
		shift: t("hotkeyKeys.shift"),
		shift_l: t("hotkeyKeys.shift"),
		shift_r: t("hotkeyKeys.shift"),
		alt: t("hotkeyKeys.alt"),
		alt_l: t("hotkeyKeys.alt"),
		alt_r: t("hotkeyKeys.alt"),
		alt_gr: t("hotkeyKeys.altGr"),
		cmd: t("hotkeyKeys.cmd"),
		cmd_l: t("hotkeyKeys.cmd"),
		cmd_r: t("hotkeyKeys.cmd"),
		win: t("hotkeyKeys.win"),
		super: t("hotkeyKeys.super"),
		fn: t("hotkeyKeys.fn"),
		globe: "\u{1F310}",
		space: t("hotkeyKeys.space"),
		enter: t("hotkeyKeys.enter"),
		tab: t("hotkeyKeys.tab"),
		esc: t("hotkeyKeys.esc"),
		caps_lock: t("hotkeyKeys.capsLock"),
		num_lock: t("hotkeyKeys.numLock"),
		scroll_lock: t("hotkeyKeys.scrollLock"),
		print_screen: t("hotkeyKeys.printScreen"),
		pause: t("hotkeyKeys.pause"),
		insert: t("hotkeyKeys.insert"),
		delete: t("hotkeyKeys.delete"),
		home: t("hotkeyKeys.home"),
		end: t("hotkeyKeys.end"),
		page_up: t("hotkeyKeys.pageUp"),
		page_down: t("hotkeyKeys.pageDown"),
		up: "\u2191",
		down: "\u2193",
		left: "\u2190",
		right: "\u2192",
	};
	_keyLabelAliasCache = { locale, map };
	return map;
}

/**
 * Format a pynput-format hotkey (e.g. "<ctrl>+<alt>+v") for display
 * in the UI (e.g. "Ctrl+Alt+V" on Windows/Linux, "⌃⌥V" on macOS).
 *
 * Returns "None" if the hotkey is empty/falsy.
 *
 * on macOS, the four primary modifiers are rendered as
 * platform-native glyphs (⌘ Cmd, ⌃ Ctrl, ⌥ Alt/Option, ⇧ Shift) and
 * joined WITHOUT separators — matching the macOS Human Interface
 * Guidelines (e.g. "⌘⇧V" rather than "Cmd+Shift+V"). On Windows and
 * Linux the existing text labels ("Ctrl", "Shift", etc.) joined with
 * "+" are kept — that convention is what users on those platforms
 * expect, and existing tests + snapshot files assert on it.
 *
 * The ``win`` / ``super`` / ``fn`` / ``globe`` modifiers are NOT
 * mapped to glyphs on macOS because they are not native to the
 * platform (a Mac keyboard has no Win key, and Fn/Globe are special
 * firmware keys); their text labels are kept for clarity.
 */
export function formatHotkey(hotkey: string): string {
	if (!hotkey) return t("hotkey.none");
	// macOS glyph table + per-locale KEY_LABEL_ALIAS are now resolved
	// via module-scope helpers (see ``MAC_MODIFIER_GLYPHS`` and
	// ``getKeyLabelAlias`` above) — they used to be re-allocated on
	// every call, which dominated ``formatHotkey``'s runtime.
	const KEY_LABEL_ALIAS = getKeyLabelAlias();
	const parts = hotkey
		.split("+")
		.map((part) => part.replace(/[<>]/g, "").trim());
	//re-detect platform on every call so a stale
	// module-level detection (e.g. from Electron UA spoofing or
	// headless mode) doesn't produce the wrong glyphs.
	const isMac = detectPlatform() === "darwin";
	const formattedParts = parts.map((key) => {
		if (isMac && MAC_MODIFIER_GLYPHS[key]) return MAC_MODIFIER_GLYPHS[key];
		if (KEY_LABEL_ALIAS[key]) return KEY_LABEL_ALIAS[key];
		if (/^f\d{1,2}$/.test(key)) return key.toUpperCase();
		if (key.length === 1) return key.toUpperCase();
		return key.charAt(0).toUpperCase() + key.slice(1);
	});
	// On macOS, modifier glyphs are concatenated without separators
	// (e.g. "⌘⇧V"). On other platforms, all parts are joined with
	// "+" (e.g. "Ctrl+Shift+V").
	if (isMac) {
		return formattedParts.join("");
	}
	return formattedParts.join("+");
}

/**
 * Alias for {@link formatHotkey}. Several components and tests import the
 * label formatter as `formatHotkeyLabel`; keep this named export so both
 * call sites resolve to the same implementation.
 */
export const formatHotkeyLabel = formatHotkey;

/**
 * Default dictation hotkey (pynput form). Must match the backend's
 * canonical default — see `OnboardingController.selected_hotkey` and the
 * comment history in `pages/onboarding/lib/constants.ts` (which
 * re-exports this constant) for the lockstep contract.
 */
export const HOTKEY_DEFAULT = "<caps_lock>";

/**
 * Default repaste-last-transcription hotkey (pynput form). Previously a
 * magic literal inside App.tsx; named here so every config-driven hotkey
 * default lives in the hotkey module with the formatter.
 */
export const REPASTE_HOTKEY_DEFAULT = "<ctrl>+<alt>+v";

/**
 * Config fields consumed by {@link configHotkeyLabels} — the renderer
 * config shape (`VoiceTyperConfig`), narrowed to just the hotkeys.
 */
export interface ConfigHotkeys {
	hotkey?: string | null;
	repaste_hotkey?: string | null;
}

/**
 * Compute the user-facing dictation + repaste hotkey labels from the
 * app config, falling back to the canonical defaults when a field is
 * unset. Single place App, the Help overlay, and any future surface
 * derive these labels from, so the config→label computation can't
 * drift between call sites.
 */
export function configHotkeyLabels(config: ConfigHotkeys): {
	dictationLabel: string;
	repasteLabel: string;
} {
	return {
		dictationLabel: formatHotkey(config.hotkey ?? HOTKEY_DEFAULT),
		repasteLabel: formatHotkey(config.repaste_hotkey ?? REPASTE_HOTKEY_DEFAULT),
	};
}

/**
 * Platform-aware variant for a DISPLAY-FORM hotkey string (e.g. the
 * `SHORTCUTS` catalog's canonical "Ctrl+B").
 *
 * On macOS the modifier labels are mapped to their platform-native
 * glyphs (Ctrl→⌃, Alt→⌥, Shift→⇧, Cmd→⌘) and the combo parts are
 * joined WITHOUT "+" separators — the exact output `formatHotkey`
 * produces from the pynput form (e.g. "⌃B" for "<ctrl>+<b>"), so
 * tooltip chips match the Sidebar's `formatHotkey`-driven rendering.
 * On Windows/Linux the string is returned unchanged (the canonical
 * display form is already what those platforms render). " / "
 * alternative separators are preserved.
 *
 * Idempotent for already-formatted input: glyph output contains no
 * modifier labels, so re-applying is a no-op. `HotkeyChips` runs every
 * input through this, whether the caller passed catalog `keys` or
 * `formatHotkey` output.
 */
export function formatHotkeyForPlatform(keys: string): string {
	if (!keys) return keys;
	// Re-detect on every call (same rationale as ``formatHotkey``: a
	// stale module-level detection from Electron UA spoofing / headless
	// mode must not render the wrong glyphs).
	if (detectPlatform() !== "darwin") return keys;
	return keys
		.split(" / ")
		.map((alt) =>
			alt
				.split("+")
				.map((part) => {
					const trimmed = part.trim();
					// Reuse the pynput-modifier glyph table via a
					// lowercased lookup ("Ctrl" → "ctrl" → ⌃). Keys not
					// in the table (letters, Tab, Space, win/super/fn —
					// not native to macOS) fall through unchanged.
					return MAC_MODIFIER_GLYPHS[trimmed.toLowerCase()] ?? trimmed;
				})
				.join(""),
		)
		.join(" / ");
}
