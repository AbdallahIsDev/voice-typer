import { getLocale, t } from "@/i18n/i18n";
import { detectPlatform } from "./hotkey-validation";

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

export function formatHotkey(hotkey: string): string {
	if (!hotkey) return t("hotkey.none");
	// macOS glyph table + per-locale KEY_LABEL_ALIAS are now resolved
	// via module-scope helpers (see ``MAC_MODIFIER_GLYPHS`` and
	// every call, which dominated ``formatHotkey``'s runtime.
	const KEY_LABEL_ALIAS = getKeyLabelAlias();
	const parts = hotkey
		.split("+")
		.map((part) => part.replace(/[<>]/g, "").trim());
	//re-detect platform on every call so a stale
	// module-level detection (e.g. from predecessor UA spoofing or
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

export const formatHotkeyLabel = formatHotkey;

export const HOTKEY_DEFAULT = "<caps_lock>";

export const REPASTE_HOTKEY_DEFAULT = "<ctrl>+<alt>+v";

export interface ConfigHotkeys {
	hotkey?: string | null;
	repaste_hotkey?: string | null;
}

export function configHotkeyLabels(config: ConfigHotkeys): {
	dictationLabel: string;
	repasteLabel: string;
} {
	return {
		dictationLabel: formatHotkey(config.hotkey ?? HOTKEY_DEFAULT),
		repasteLabel: formatHotkey(config.repaste_hotkey ?? REPASTE_HOTKEY_DEFAULT),
	};
}

export function formatHotkeyForPlatform(keys: string): string {
	if (!keys) return keys;
	// Re-detect on every call (same rationale as ``formatHotkey``: a
	// stale module-level detection from predecessor UA spoofing / headless
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
