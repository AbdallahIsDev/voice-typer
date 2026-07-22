/**
 * Hotkey utilities for the Settings UI.
 *
 * NATIVE-001: updated for the new native subprocess hotkey architecture.
 * The dropdown now offers only keys that are universally present on all
 * keyboards (Caps Lock, Alt, common modifier combos) plus the Fn key
 * (macOS only — firmware-only on Windows/Linux). The custom capture
 * button still lets users pick any key.
 *
 * HOTKEY-UNIFY-002: this module now re-exports the shared validation
 * system from ``hotkey-validation.ts``. The legacy ``validateHotkey``
 * function below is kept for backward compat with callers that pass
 * the ``mode`` argument (single/combo). Internally it delegates to
 * the shared ``validateHotkey`` from ``hotkey-validation.ts``, which
 * is the single source of truth for reserved-shortcut checking,
 * structural validation, and normalization.
 *
 * ISSUE-8: the preset lists are exposed via getter functions
 * ``getSingleKeyPresets()`` and ``getComboPresets()`` that re-detect
 * the platform on every call. New code should call the getters
 * directly so the presets always reflect the current platform — handy
 * in Electron where UA spoofing or headless mode can cause the initial
 * ``navigator.userAgent`` detection to be wrong.
 */

import {
	detectPlatform,
	isReserved,
	validateHotkey as validateHotkeyShared,
} from "./hotkey-validation";

// Re-export the shared validation API so callers can use either
// ``hotkey-utils`` or ``hotkey-validation`` — they're equivalent.
export {
	detectPlatform,
	isReserved,
	normalizeHotkey,
	RESERVED_SHORTCUTS,
} from "./hotkey-validation";

// Detect platform from navigator.userAgent (renderer process).
// In Electron, process.platform is also available via the preload bridge
// but navigator.userAgent is sufficient for UI filtering.
const PLATFORM: "darwin" | "win32" | "linux" | "unknown" = (() => {
	if (typeof navigator === "undefined") return "unknown";
	const ua = navigator.userAgent.toLowerCase();
	if (ua.includes("mac")) return "darwin";
	if (ua.includes("win")) return "win32";
	if (ua.includes("linux")) return "linux";
	return "unknown";
})();

export const IS_MAC = PLATFORM === "darwin";
export const IS_WIN = PLATFORM === "win32";
export const IS_LINUX = PLATFORM === "linux";

export const KEY_CODE_TO_PYNPUT: Record<string, string> = {
	// ISSUE-3 (Key-name maps): this table maps Browser key codes
	// (e.code) to pynput-style lowercase names. It is ONE OF THREE
	// independent key-name tables that share a common vocabulary:
	//
	//   Frontend:  KEY_CODE_TO_PYNPUT (hotkey-utils.ts) — e.code → pynput
	//   Backend:   _VK_MAP (hotkeys.py) — pynput name → Win32 VK code
	//   Native:    _normalize_key_name (native_hotkeys.py) — pynput name →
	//              wire-protocol name (CapsLock, Space, MediaNext, etc.)
	//
	// All three must agree on the set of names ("f1", "space",
	// "caps_lock", "page_up", etc.). _normalize_key_name is the
	// canonical name-to-name transformer — if you add a name here,
	// add it there too so the native backends can recognize it.
	//
	// HOTKEY-FIX-002: letters and digits were missing from
	// this table, so capturing combos like Alt+Q, Ctrl+Alt+V, or even
	// the default repaste hotkey Ctrl+Alt+V would fail with "Key 'v'
	// is not supported" — despite the error message literally
	// suggesting "Try letters, numbers, F-keys, or Space." Adding
	// letters and digits (keyed by e.code, which is layout-independent)
	// fixes sub-task 2.2 and the Alt+Q symptom of 2.2.5.
	KeyA: "a",
	KeyB: "b",
	KeyC: "c",
	KeyD: "d",
	KeyE: "e",
	KeyF: "f",
	KeyG: "g",
	KeyH: "h",
	KeyI: "i",
	KeyJ: "j",
	KeyK: "k",
	KeyL: "l",
	KeyM: "m",
	KeyN: "n",
	KeyO: "o",
	KeyP: "p",
	KeyQ: "q",
	KeyR: "r",
	KeyS: "s",
	KeyT: "t",
	KeyU: "u",
	KeyV: "v",
	KeyW: "w",
	KeyX: "x",
	KeyY: "y",
	KeyZ: "z",
	Digit0: "0",
	Digit1: "1",
	Digit2: "2",
	Digit3: "3",
	Digit4: "4",
	Digit5: "5",
	Digit6: "6",
	Digit7: "7",
	Digit8: "8",
	Digit9: "9",
	F1: "f1",
	F2: "f2",
	F3: "f3",
	F4: "f4",
	F5: "f5",
	F6: "f6",
	F7: "f7",
	F8: "f8",
	F9: "f9",
	F10: "f10",
	F11: "f11",
	F12: "f12",
	F13: "f13",
	F14: "f14",
	F15: "f15",
	F16: "f16",
	F17: "f17",
	F18: "f18",
	F19: "f19",
	Space: "space",
	Enter: "enter",
	Tab: "tab",
	Escape: "esc",
	Backspace: "backspace",
	Insert: "insert",
	Delete: "delete",
	Home: "home",
	End: "end",
	PageUp: "page_up",
	PageDown: "page_down",
	CapsLock: "caps_lock",
	NumLock: "num_lock",
	ScrollLock: "scroll_lock",
	PrintScreen: "print_screen",
	Pause: "pause",
	ContextMenu: "menu",
	ArrowUp: "up",
	ArrowDown: "down",
	ArrowLeft: "left",
	ArrowRight: "right",
	MediaPlay: "media_play_pause",
	MediaStop: "media_stop",
	MediaTrackNext: "media_next",
	MediaTrackPrevious: "media_previous",
	AudioVolumeMute: "volume_mute",
	AudioVolumeUp: "volume_up",
	AudioVolumeDown: "volume_down",
};

export const MODIFIER_KEYS = [
	"ctrl",
	"ctrl_l",
	"ctrl_r",
	"shift",
	"shift_l",
	"shift_r",
	"alt",
	"alt_l",
	"alt_r",
	"alt_gr",
	"cmd",
	"cmd_l",
	"cmd_r",
	"win",
	"fn",
	"globe",
	"caps_lock",
	"capslock",
] as const;

/**
 * Build the e.code → pynput-modifier-name map for the given platform.
 *
 * CR-058: ``MetaLeft`` / ``MetaRight`` previously always mapped to
 * ``"cmd"``, which is the macOS name for the modifier. On Windows and
 * Linux, committing a bare Win/Super key would emit ``"<cmd>"`` — a
 * name the native backend on those platforms can't register, silently
 * breaking the hotkey. This factory now branches on ``isMac`` so the
 * Meta keys map to ``"cmd"`` on macOS (where ``<cmd>`` is registered)
 * and to ``"win"`` on Windows/Linux (where ``<win>`` / ``<super>`` is
 * the registered name).
 *
 * On Linux, ``<super>`` is also accepted by the backend as an alias
 * for the Meta key — we emit ``"win"`` for parity with the legacy
 * modifier vocabulary and let the backend normalize as needed.
 *
 * @param isMac Whether the current platform is macOS.
 * @returns A record mapping Browser ``e.code`` values for modifier
 *   keys to their pynput-style lowercase modifier names.
 */
export function getModifierCodeMap(isMac: boolean): Record<string, string> {
	return {
		ControlLeft: "ctrl",
		ControlRight: "ctrl",
		ShiftLeft: "shift",
		ShiftRight: "shift",
		AltLeft: "alt",
		AltRight: "alt",
		MetaLeft: isMac ? "cmd" : "win",
		MetaRight: isMac ? "cmd" : "win",
	};
}

/**
 * Deprecated alias for {@link getModifierCodeMap} evaluated at module
 * load time with the host's detected platform.
 *
 * CR-058: kept for backwards-compat with any caller that imports
 * ``MODIFIER_CODE_TO_PYNPUT`` directly. New code should call
 * ``getModifierCodeMap(isMac)`` (or ``getModifierCodeMap(IS_MAC)``)
 * so the map is always computed against the current platform —
 * module-level constants are evaluated once at import time and become
 * stale if the platform changes (rare, but possible in Electron with
 * UA spoofing or headless mode).
 *
 * @deprecated Use {@link getModifierCodeMap} instead.
 */
export const MODIFIER_CODE_TO_PYNPUT: Record<string, string> =
	getModifierCodeMap(IS_MAC);

/**
 * Single-key presets — only keys that are safe to use alone as a
 * dictation trigger.
 *
 * FIX-HOTKEY-AND-NOTIFICATION: the dropdown was reduced to only the
 * keys that are safe to use as a bare modifier/single-key trigger.
 * Removed:
 * - Win (Windows only): pressing the Win key alone opens the Start
 *   menu — not a usable dictation key.
 * - Shift: users hold Shift for capitalization while typing, so a
 *   bare-Shift trigger would fire constantly while the user is just
 *   typing uppercase letters. Not a usable dictation key.
 * - Cmd (macOS): same problem as Win on Windows — Cmd alone is a
 *   system-reserved gesture (Spotlight on newer macOS, etc.).
 *
 * Removed (not universally present, kept here only as documentation):
 * - PrintScreen (Apple keyboards don't have it; some 60% boards lack it)
 * - ScrollLock (most laptops and all Apple keyboards lack it)
 * - Pause (most laptops and all Apple keyboards lack it)
 * - Insert (60% keyboards and Apple keyboards lack it as a dedicated key)
 * - Home / PageUp / PageDown (60% keyboards lack these)
 * - F1..F12 (60% keyboards require Fn+F-key combos; not single-press)
 *
 * Kept (safe single-key options):
 * - Caps Lock (every full-size and laptop keyboard; OS-level toggle
 *   suppression is handled by the hotkey backend so it doesn't
 *   accidentally enable caps lock mode)
 * - Alt (every keyboard; modifier-only release detection)
 * - Ctrl (every keyboard; modifier-only release detection)
 * - Fn (macOS only — firmware-only on Windows/Linux)
 *
 * FIX-HOTKEY-ARCHITECTURE: F1–F12 entries were removed from the
 * dropdown entirely. They're not universally present on laptop
 * keyboards (which require an Fn+F-key combo) and the native hotkey
 * architecture treats Caps Lock as the recommended default. Function
 * keys are still available via the custom capture button for users
 * who have a keyboard with dedicated function keys.
 *
 * ISSUE-8: this used to be a module-level constant computed once at
 * import time. It's now a getter so the platform is re-detected on
 * every call. The list is small (<5 entries) and the platform check
 * is a single ``navigator.userAgent`` regex, so calling this on every
 * render is cheap and avoids the staleness problem when the initial
 * platform detection was wrong (Electron UA spoofing, headless mode).
 */
export function getSingleKeyPresets(): { value: string; label: string }[] {
	// Re-detect platform on every call so the Fn option appears iff
	// the CURRENT navigator.userAgent looks like macOS, not whatever
	// was detected at module load time.
	const isMac = detectPlatform() === "darwin";
	return [
		// Safe single-key options only.
		// Caps Lock: label is intentionally bare — no "recommended"
		// or "requires OS remap" qualifier. The hotkey backend
		// transparently handles the OS-level toggle suppression.
		{ value: "caps_lock", label: "Caps Lock" },
		{ value: "alt", label: "Alt" },
		{ value: "ctrl", label: "Ctrl" },
		// Fn is firmware-only on Windows/Linux (apps can't see it), so
		// only offer it on macOS where the native backend can hook it.
		...(isMac ? [{ value: "fn", label: "Fn / Globe 🌐 (macOS only)" }] : []),
	];
}

/**
 * Combo presets — multi-key hotkey combinations (e.g. Ctrl+Shift+V,
 * Ctrl+Alt+V) used for re-paste and other shortcut settings.
 *
 * ISSUE-8: this is a getter so the platform is re-detected on every
 * call, avoiding staleness from module-level platform detection.
 */
export function getComboPresets(): { value: string; label: string }[] {
	const platform = detectPlatform();
	const isMac = platform === "darwin";
	const isLinux = platform === "linux";
	return [
		{ value: "<ctrl>+<shift>+v", label: "Ctrl+Shift+V" },
		{ value: "<ctrl>+<alt>+v", label: "Ctrl+Alt+V" },
		{ value: "<ctrl>+<space>", label: "Ctrl+Space" },
		...(isMac
			? [
					{
						value: "<cmd>+<shift>+v",
						label: "Cmd+Shift+V (macOS)",
					},
				]
			: []),
		// Win+Space is intentionally NOT offered on Windows: it is reserved
		// by the OS for the input-language switcher and binding it as a
		// dictation/paste shortcut would silently break language switching.
		// Users can still pick any combo via the custom capture button if
		// they really want to override it.
		...(isLinux ? [{ value: "<super>+<space>", label: "Super+Space" }] : []),
	].filter((preset) => !isReserved(preset.value, platform));
}

/**
 * Format a pynput-format hotkey (e.g. "<ctrl>+<alt>+v") for display
 * in the UI (e.g. "Ctrl+Alt+V" on Windows/Linux, "⌃⌥V" on macOS).
 *
 * Returns "None" if the hotkey is empty/falsy.
 *
 * PVT-FIX-002: on macOS, the four primary modifiers are rendered as
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
	if (!hotkey) return "None";
	// macOS glyph table for the four primary modifiers. Applied only
	// when the detected platform is darwin. Keys not in this map fall
	// through to the text-label path below.
	const MAC_MODIFIER_GLYPHS: Record<string, string> = {
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
	const displayMap: Record<string, string> = {
		ctrl: "Ctrl",
		ctrl_l: "Ctrl",
		ctrl_r: "Ctrl",
		shift: "Shift",
		shift_l: "Shift",
		shift_r: "Shift",
		alt: "Alt",
		alt_l: "Alt",
		alt_r: "Alt",
		alt_gr: "AltGr",
		cmd: "Cmd",
		cmd_l: "Cmd",
		cmd_r: "Cmd",
		win: "Win",
		super: "Super",
		fn: "Fn",
		globe: "\u{1F310}",
		space: "Space",
		enter: "Enter",
		tab: "Tab",
		esc: "Esc",
		caps_lock: "Caps Lock",
		num_lock: "Num Lock",
		scroll_lock: "Scroll Lock",
		print_screen: "Print Screen",
		pause: "Pause",
		insert: "Insert",
		delete: "Delete",
		home: "Home",
		end: "End",
		page_up: "Page Up",
		page_down: "Page Down",
		up: "\u2191",
		down: "\u2193",
		left: "\u2190",
		right: "\u2192",
	};
	const parts = hotkey
		.split("+")
		.map((part) => part.replace(/[<>]/g, "").trim());
	// PVT-FIX-002: re-detect platform on every call so a stale
	// module-level detection (e.g. from Electron UA spoofing or
	// headless mode) doesn't produce the wrong glyphs.
	const isMac = detectPlatform() === "darwin";
	const formattedParts = parts.map((key) => {
		if (isMac && MAC_MODIFIER_GLYPHS[key]) return MAC_MODIFIER_GLYPHS[key];
		if (displayMap[key]) return displayMap[key];
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
 * Validate a hotkey for the UI, with an additional mode parameter
 * for single-key vs. combo constraints.
 *
 * For single mode: must be exactly one key (no modifiers together).
 * For combo mode: delegates to the shared validateHotkey.
 *
 * Returns null on success, or an error message string on failure.
 */
export function validateHotkey(
	hotkey: string,
	mode: "single" | "combo",
): string | null {
	// HOTKEY-UNIFY-002: delegate to the shared validation system.
	// The shared validateHotkey handles:
	//  - empty / no-keys check
	//  - reserved-shortcut check (OS-specific)
	//  - structural check (combo must end with non-modifier)
	//
	// We add mode-specific checks (single key constraint, Fn-on-macOS-only)
	// on top, since those are UI-mode concerns the shared validator
	// doesn't know about.
	if (!hotkey?.trim()) {
		return "Hotkey is empty";
	}

	// Delegate reserved + structural checks to the shared validator.
	const platform = detectPlatform();
	const sharedResult = validateHotkeyShared(hotkey, platform);
	if (!sharedResult.valid) {
		return sharedResult.reason ?? "Invalid hotkey";
	}

	const parts = hotkey
		.split("+")
		.map((p) => p.replace(/[<>]/g, "").trim())
		.filter(Boolean);
	if (parts.length === 0) {
		return "Hotkey has no keys";
	}
	// NATIVE-001: allow single modifiers (alt, ctrl, shift, fn, cmd, win)
	// as the dictation key. The native backends support modifier-only
	// release detection.
	if (mode === "single") {
		if (parts.length > 1) {
			return "Dictation key must be a single key (no modifiers). Use the re-paste key for combos.";
		}
		// Reject Fn on non-macOS platforms
		if (!IS_MAC && (parts[0] === "fn" || parts[0] === "globe")) {
			return "Fn key is only supported on macOS. On Windows/Linux, Fn isn't visible to apps.";
		}
		// Accept any single key (including modifiers and caps_lock)
		return null;
	}
	// Combo mode: HOTKEY-MULTIKEY-001 — pure-modifier combos (e.g.
	// ``<ctrl>+<shift>``, ``<ctrl>+<alt>``) are now ALLOWED. The structural
	// "must end with non-modifier" rule only applies to MIXED combos
	// (modifiers + non-modifiers). The shared validator already enforces
	// this; the redundant check below is kept only for mixed combos as
	// a defense-in-depth guard.
	const lastKey = parts[parts.length - 1];
	const hasNonModifier = parts.some(
		(p) => !MODIFIER_KEYS.includes(p as (typeof MODIFIER_KEYS)[number]),
	);
	if (
		hasNonModifier &&
		MODIFIER_KEYS.includes(lastKey as (typeof MODIFIER_KEYS)[number])
	) {
		return "Combo must end with a non-modifier key (e.g. Ctrl+Alt+V, not Ctrl+Alt+V+Shift)";
	}
	// Reject Fn in combos on non-macOS
	if (!IS_MAC && parts.some((p) => p === "fn" || p === "globe")) {
		return "Fn key is only supported on macOS.";
	}
	return null;
}
