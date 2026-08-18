/**
 * Canonical location for hotkey key-code tables, platform detection,
 * and preset lists.
 *
 * Extracted from the former ``hotkey-utils.ts`` monolith:
 *  - Browser ``e.code`` → pynput-style lowercase key-name table
 *  - Modifier key list + per-platform modifier-code map factory
 *  - Platform detection constants (IS_MAC / IS_WIN / IS_LINUX)
 *  - Single-key and combo preset lists (display labels come from
 *    ``hotkey-format.ts`` via ``formatHotkey``)
 *
 * Re-exports the shared validation API (``detectPlatform`` /
 * ``isReserved`` / ``normalizeHotkey`` / ``RESERVED_SHORTCUTS``) from
 * ``hotkey-validation.ts`` so existing imports from ``hotkey-utils``
 * keep resolving via the backward-compat shim — they're the platform
 * detection entry points the preset getters use.
 *
 * The matching backend mirror lives in
 * ``voice_typer/server/config_validators.py`` (per-platform reserved
 * shortcuts) and ``voice_typer/server/native_hotkeys.py``
 * (``_normalize_key_name``). All three sides share a common key-name
 * vocabulary — see the comment above ``KEY_CODE_TO_PYNPUT``.
 */

import { t } from "@/i18n/i18n";
import { formatHotkey } from "./hotkey-format";
import { detectPlatform, isReserved } from "./hotkey-validation";

// Re-export the shared validation API so callers can use either
// ``hotkey-utils`` (re-export shim) or the canonical module —
// they're equivalent.
export {
	detectPlatform,
	isReserved,
	normalizeHotkey,
	RESERVED_SHORTCUTS,
} from "./hotkey-validation";

// ────────────────────────────────────────────────────────────────────
// Platform detection
// ────────────────────────────────────────────────────────────────────

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

// ────────────────────────────────────────────────────────────────────
// Key-code table (Browser e.code → pynput name)
// ────────────────────────────────────────────────────────────────────

export const KEY_CODE_TO_PYNPUT: Record<string, string> = {
	// this table maps Browser key codes
	// (e.code) to pynput-style lowercase names. It is ONE OF THREE
	// independent key-name tables that share a common vocabulary:
	//
	//   Frontend:  KEY_CODE_TO_PYNPUT (hotkey-keymap.ts) — e.code → pynput
	//   Backend:   _VK_MAP (hotkeys.py) — pynput name → Win32 VK code
	//   Native:    _normalize_key_name (native_hotkeys.py) — pynput name →
	//              wire-protocol name (CapsLock, Space, MediaNext, etc.)
	//
	// All three must agree on the set of names ("f1", "space",
	// "caps_lock", "page_up", etc.). _normalize_key_name is the
	// canonical name-to-name transformer — if you add a name here,
	// add it there too so the native backends can recognize it.
	//
	// letters and digits were missing from
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
 * : ``MetaLeft`` / ``MetaRight`` previously always mapped to
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

// ────────────────────────────────────────────────────────────────────
// Preset lists
// ────────────────────────────────────────────────────────────────────

/**
 * Single-key presets — only keys that are safe to use alone as a
 * dictation trigger.
 *
 * the dropdown was reduced to only the
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
 * F1–F12 entries were removed from the
 * dropdown entirely. They're not universally present on laptop
 * keyboards (which require an Fn+F-key combo) and the native hotkey
 * architecture treats Caps Lock as the recommended default. Function
 * keys are still available via the custom capture button for users
 * who have a keyboard with dedicated function keys.
 *
 * this used to be a module-level constant computed once at
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
		{ value: "caps_lock", label: t("hotkeyKeys.capsLock") },
		{ value: "alt", label: t("hotkeyKeys.alt") },
		{ value: "ctrl", label: t("hotkeyKeys.ctrl") },
		// Fn is firmware-only on Windows/Linux (apps can't see it), so
		// only offer it on macOS where the native backend can hook it.
		...(isMac ? [{ value: "fn", label: t("hotkeyKeys.fnGlobeMacos") }] : []),
	];
}

/**
 * Combo presets — multi-key hotkey combinations (e.g. Ctrl+Shift+V,
 * Ctrl+Alt+V) used for re-paste and other shortcut settings.
 *
 * this is a getter so the platform is re-detected on every
 * call, avoiding staleness from module-level platform detection.
 */
export function getComboPresets(): { value: string; label: string }[] {
	const platform = detectPlatform();
	const isMac = platform === "darwin";
	const isLinux = platform === "linux";
	return [
		{ value: "<ctrl>+<shift>+v", label: formatHotkey("<ctrl>+<shift>+v") },
		{ value: "<ctrl>+<alt>+v", label: formatHotkey("<ctrl>+<alt>+v") },
		{ value: "<ctrl>+<space>", label: formatHotkey("<ctrl>+<space>") },
		...(isMac
			? [
					{
						value: "<cmd>+<shift>+v",
						label: formatHotkey("<cmd>+<shift>+v"),
					},
				]
			: []),
		// Win+Space is intentionally NOT offered on Windows: it is reserved
		// by the OS for the input-language switcher and binding it as a
		// dictation/paste shortcut would silently break language switching.
		// Users can still pick any combo via the custom capture button if
		// they really want to override it.
		...(isLinux
			? [{ value: "<super>+<space>", label: formatHotkey("<super>+<space>") }]
			: []),
	].filter((preset) => !isReserved(preset.value, platform));
}
