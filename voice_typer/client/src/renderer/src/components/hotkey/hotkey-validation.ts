/**
 * Reserved OS shortcuts that should never be assignable.
 * loaded from the canonical JSON file at
 * ``voice_typer/server/hotkey_reserved.json``. A copy lives at
 * ``voice_typer/client/src/renderer/src/data/hotkey_reserved.json``
 * and is imported with a project-relative path so the import doesn't
 * depend on a Vite alias that resolves outside the renderer root
 * (which can crash Vite's dev server during HMR on locale switch).
 * The sync test ``test_hotkey_reserved_sync.py`` verifies the copy
 * matches the server original.
 */

import { t } from "@/i18n/i18n";
import hotkeyReserved from "../../data/hotkey_reserved.json";

export const UNIVERSAL_RESERVED_SHORTCUTS: readonly string[] =
	hotkeyReserved.universal_reserved;
export const RESERVED_SHORTCUTS: Record<string, string[]> =
	hotkeyReserved.per_platform_reserved as Record<string, string[]>;
export const BLOCKED_CTRL_LETTERS: readonly string[] =
	hotkeyReserved.blocked_ctrl_letters;
export const MODIFIER_KEYS_SHARED: readonly string[] = hotkeyReserved.modifiers;

const _MODIFIER_KEYS_SET = new Set(MODIFIER_KEYS_SHARED);

function _isModifier(p: string): boolean {
	return _MODIFIER_KEYS_SET.has(p);
}

const _MESSAGE_SPECIAL_LABELS: Readonly<Record<string, string>> = {
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
	globe: "Globe",
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
	up: "Up",
	down: "Down",
	left: "Left",
	right: "Right",
	backspace: "Backspace",
};

function _formatForMessage(hotkey: string): string {
	if (!hotkey) return "";
	return hotkey
		.split("+")
		.map((part) => part.replace(/[<>]/g, "").trim())
		.filter(Boolean)
		.map((part) => {
			const lower = part.toLowerCase();
			if (_MESSAGE_SPECIAL_LABELS[lower]) return _MESSAGE_SPECIAL_LABELS[lower];
			if (/^f\d{1,2}$/.test(lower)) return lower.toUpperCase();
			if (lower.length === 1) return lower.toUpperCase();
			return part.charAt(0).toUpperCase() + part.slice(1);
		})
		.join("+");
}

export interface ValidationResult {
	valid: boolean;
	/** Human-readable reason when valid is false. */
	reason?: string;
	partial?: never;
}

export function detectPlatform(): string {
	if (typeof navigator === "undefined") return "unknown";
	const ua = navigator.userAgent.toLowerCase();
	if (ua.includes("mac")) return "darwin";
	if (ua.includes("win")) return "win32";
	if (ua.includes("linux")) return "linux";
	return "unknown";
}

export function isReserved(hotkey: string, platform: string): boolean {
	if (!hotkey) return false;
	const reserved = RESERVED_SHORTCUTS[platform] || [];
	// normalize both sides via normalizeHotkey()
	// (which strips angle brackets and lowercases) before comparing. The
	// RESERVED_SHORTCUTS table stores entries with brackets on both parts
	// (e.g. ``"<win>+<e>"``), but callers pass hotkeys using the pynput
	// convention with brackets only on the modifier (e.g. ``"<win>+e"``).
	// The previous raw-lowercase comparison only matched when the caller
	// used the exact same bracket convention as the table, so Win+E,
	// Cmd+Space, Super+L, etc. were all silently accepted as valid
	// despite being OS-reserved. Normalizing both sides makes the
	// comparison bracket-agnostic.
	const normalized = normalizeHotkey(hotkey);
	return reserved.some((r) => normalizeHotkey(r) === normalized);
}

export function normalizeHotkey(hotkey: string): string {
	if (!hotkey) return "";
	return hotkey
		.split("+")
		.map((p) => p.replace(/[<>]/g, "").trim().toLowerCase())
		.filter(Boolean)
		.join("+");
}

export function validateHotkey(
	hotkey: string,
	platform: string,
): ValidationResult {
	// 1. Non-empty
	if (!hotkey?.trim()) {
		return { valid: false, reason: t("hotkeyValidation.empty") };
	}

	// Parse parts once, used by rules 3-10.
	const parts = hotkey
		.split("+")
		.map((p) => p.replace(/[<>]/g, "").trim().toLowerCase())
		.filter(Boolean);
	if (parts.length === 0) {
		return { valid: false, reason: t("hotkeyValidation.noKeys") };
	}

	// 2. Universal reserved (Alt+Tab/F4/Esc/Space), every platform.
	const normalized = parts.map((p) => `<${p}>`).join("+");
	if (
		UNIVERSAL_RESERVED_SHORTCUTS.some(
			(r) => normalizeHotkey(r) === normalizeHotkey(normalized),
		)
	) {
		//include the conflicting combo in the message
		// so the user sees exactly which shortcut is reserved.
		const label = _formatForMessage(normalized);
		return {
			valid: false,
			reason: t("hotkeyValidation.reservedUniversal", { label }),
		};
	}

	// 3. Per-platform reserved.
	if (isReserved(hotkey, platform)) {
		//include the conflicting combo in the message.
		const label = _formatForMessage(hotkey);
		return {
			valid: false,
			reason: t("hotkeyValidation.reservedOs", { label }),
		};
	}

	// Helper: classify parts.
	const isModifier = (p: string): boolean => _isModifier(p);
	const nonMods = parts.filter((p) => !isModifier(p));

	// 4. Single letter/digit rejection.
	//    A standalone <a>, <1>, etc. would trigger on every keypress
	//    of that character during normal typing.
	if (parts.length === 1) {
		// noUncheckedIndexedAccess: parts[0] is `string | undefined`;
		// the length===1 guard proves it exists.
		const sole = parts[0];
		if (sole !== undefined && /^[a-z0-9]$/.test(sole)) {
			return {
				valid: false,
				reason: t("hotkeyValidation.singleLetterDigit", { key: sole }),
			};
		}
	}

	// 5. Structural: a combo that includes a NON-MODIFIER must NOT end with
	//    a modifier (e.g. ``Ctrl+Alt+V`` is fine, ``Ctrl+V+Alt`` is not).
	//    pure-modifier combos (e.g.
	//    ``Ctrl+Shift``, ``Ctrl+Alt``) are now ALLOWED, they're valid
	//    modifier-only release triggers in the native backends. The
	//    previous blanket rule "combo must not end with a modifier"
	//    incorrectly rejected these, causing a frontend/backend mismatch
	//    (the backend ``_validate_hotkey`` in config_validators.py has
	//    never had this rule). Now we only reject combos that mix
	//    modifiers AND non-modifiers but end with a modifier.
	if (parts.length >= 2 && nonMods.length > 0) {
		// noUncheckedIndexedAccess: parts[parts.length-1] is
		// `string | undefined`; the length>=2 guard proves it exists.
		const lastKey = parts[parts.length - 1];
		if (lastKey !== undefined && isModifier(lastKey)) {
			//include the conflicting combo.
			const label = _formatForMessage(hotkey);
			return {
				valid: false,
				reason: t("hotkeyValidation.mustEndWithNonModifier", { label }),
			};
		}
	}

	// 6. Win+anything block (Windows only).
	// Super+anything on Linux too, which incorrectly rejected
	// <super>+<space> (a combo most Linux DEs allow reassigning). The
	// blanket block now applies only on Windows (where the Win key is
	// heavily reserved by the OS shell). On Linux, Super combos are
	// checked against the per-platform reserved list (Super+L, Super+D,
	// Super+Tab), all other Super combos are allowed.
	const hasWin = parts.some((p) => p === "win" || p === "super");
	if (hasWin && platform === "win32") {
		//include the conflicting combo.
		const label = _formatForMessage(hotkey);
		return {
			valid: false,
			reason: t("hotkeyValidation.reservedWindows", { label }),
		};
	}

	// 7. Cmd+<letter> block (macOS).
	const hasCmd = parts.some(
		(p) => p === "cmd" || p === "cmd_l" || p === "cmd_r",
	);
	if (hasCmd && platform === "darwin" && parts.length > 1) {
		for (const nm of nonMods) {
			if (nm.length === 1 && /^[a-z]$/.test(nm)) {
				return {
					valid: false,
					reason: t("hotkeyValidation.reservedMacosCmd", {
						key: nm.toUpperCase(),
					}),
				};
			}
		}
	}

	// 8. Alt+Shift block (Windows language switching).
	if (platform === "win32") {
		const hasAlt = parts.some((p) => p.startsWith("alt"));
		const hasShift = parts.some((p) => p.startsWith("shift"));
		if (hasAlt && hasShift && nonMods.length === 0) {
			//include the conflicting combo.
			const label = _formatForMessage(hotkey);
			return {
				valid: false,
				reason: t("hotkeyValidation.reservedWindowsAltShift", { label }),
			};
		}
	}

	// 9. Ctrl+<common-letter> block (PURE Ctrl+<letter> only).
	const hasCtrl = parts.some((p) => p.startsWith("ctrl"));
	if (hasCtrl) {
		const modifiersNonCtrl = parts.filter(
			(p) => isModifier(p) && !p.startsWith("ctrl"),
		);
		if (modifiersNonCtrl.length === 0) {
			for (const nm of nonMods) {
				if (BLOCKED_CTRL_LETTERS.includes(nm)) {
					return {
						valid: false,
						reason: t("hotkeyValidation.reservedAppCtrl", {
							key: nm.toUpperCase(),
						}),
					};
				}
			}
		}
	}

	// 10. Shift+<letter> block (PURE Shift+<letter> only).
	const hasShiftAny = parts.some((p) => p.startsWith("shift"));
	if (hasShiftAny) {
		const modifiersNonShift = parts.filter(
			(p) => isModifier(p) && !p.startsWith("shift"),
		);
		if (modifiersNonShift.length === 0) {
			for (const nm of nonMods) {
				if (nm.length === 1 && /^[a-z0-9]$/.test(nm)) {
					return {
						valid: false,
						reason: t("hotkeyValidation.shiftLetterInterferes", {
							key: nm.toUpperCase(),
						}),
					};
				}
			}
		}
	}

	return { valid: true };
}
