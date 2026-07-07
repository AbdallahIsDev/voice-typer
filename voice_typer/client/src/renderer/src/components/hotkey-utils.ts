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
 * The preset lists (SINGLE_KEY_PRESETS, COMBO_PRESETS) are now
 * filtered through ``isReserved()`` at module load time, so any
 * preset that conflicts with an OS-reserved shortcut (e.g.
 * ``<cmd>+<space>`` on macOS) is automatically excluded from the
 * dropdown.
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
	// HOTKEY-FIX-002 (Round 1): letters and digits were missing from
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
] as const;

export const MODIFIER_CODE_TO_PYNPUT: Record<string, string> = {
	ControlLeft: "ctrl",
	ControlRight: "ctrl",
	ShiftLeft: "shift",
	ShiftRight: "shift",
	AltLeft: "alt",
	AltRight: "alt",
	MetaLeft: "cmd",
	MetaRight: "cmd",
};

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
 */
export const SINGLE_KEY_PRESETS: { value: string; label: string }[] = [
	// Safe single-key options only.
	// Caps Lock: label is intentionally bare — no "recommended"
	// or "requires OS remap" qualifier. The hotkey backend
	// transparently handles the OS-level toggle suppression.
	{ value: "caps_lock", label: "Caps Lock" },
	{ value: "alt", label: "Alt" },
	{ value: "ctrl", label: "Ctrl" },
	// Fn is firmware-only on Windows/Linux (apps can't see it), so
	// only offer it on macOS where the native backend can hook it.
	...(IS_MAC ? [{ value: "fn", label: "Fn / Globe 🌐 (macOS only)" }] : []),
];

/**
 * Combo presets — combos that work on all platforms (with platform-aware
 * modifier naming).
 *
 * HOTKEY-UNIFY-002: presets are filtered through ``isReserved()`` so
 * any combo that conflicts with an OS-reserved shortcut is NOT
 * offered. Previously ``<cmd>+<space>`` (macOS Spotlight) was
 * offered with a warning label — now it's silently excluded, since
 * the user shouldn't be able to pick a combo that will silently
 * break the OS. ``<super>+<space>`` on Linux is NOT reserved (most
 * desktop environments allow reassigning it), so it's still offered.
 */
export const COMBO_PRESETS: { value: string; label: string }[] = [
	{ value: "<ctrl>+<alt>+v", label: "Ctrl+Alt+V (default)" },
	{ value: "<ctrl>+<shift>+v", label: "Ctrl+Shift+V" },
	{ value: "<ctrl>+<alt>+r", label: "Ctrl+Alt+R" },
	{ value: "<ctrl>+<shift>+r", label: "Ctrl+Shift+R" },
	{ value: "<ctrl>+<space>", label: "Ctrl+Space" },
	{ value: "<alt>+<space>", label: "Alt+Space" },
	...(IS_MAC
		? [
				{ value: "<cmd>+<shift>+v", label: "Cmd+Shift+V (macOS)" },
				// HOTKEY-UNIFY-002: <cmd>+<space> (Spotlight) is now
				// excluded — it's reserved by macOS and binding it as
				// a dictation shortcut would silently break Spotlight.
				// Users who really want it can still attempt the custom
				// capture button, where validateHotkey() will reject it
				// with a clear error message.
			]
		: []),
	// Win+Space is intentionally NOT offered on Windows: it is reserved
	// by the OS for the input-language switcher and binding it as a
	// dictation/paste shortcut would silently break language switching.
	// Users can still pick any combo via the custom capture button if
	// they really want to override it.
	...(IS_LINUX ? [{ value: "<super>+<space>", label: "Super+Space" }] : []),
].filter((preset) => !isReserved(preset.value, PLATFORM));

export function formatHotkeyLabel(hotkey: string): string {
	if (!hotkey) return "None";
	return hotkey
		.split("+")
		.map((part) => {
			const key = part.replace(/[<>]/g, "").trim();
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
				globe: "🌐",
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
			if (displayMap[key]) return displayMap[key];
			if (/^f\d{1,2}$/.test(key)) return key.toUpperCase();
			if (key.length === 1) return key.toUpperCase();
			return key.charAt(0).toUpperCase() + key.slice(1);
		})
		.join("+");
}

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
	// Combo mode: last part must be a non-modifier (already checked by
	// the shared validator, but double-check here for clarity).
	const lastKey = parts[parts.length - 1];
	if (MODIFIER_KEYS.includes(lastKey as (typeof MODIFIER_KEYS)[number])) {
		return "Combo must end with a non-modifier key (e.g. Ctrl+Alt+V, not just Ctrl)";
	}
	// Reject Fn in combos on non-macOS
	if (!IS_MAC && parts.some((p) => p === "fn" || p === "globe")) {
		return "Fn key is only supported on macOS.";
	}
	return null;
}
