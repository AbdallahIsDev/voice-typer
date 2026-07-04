/**
 * Shared hotkey validation system — used by all keyboard shortcut settings.
 *
 * HOTKEY-UNIFY-001: previously validation was duplicated between
 * HotkeyPicker, hotkey-utils.ts, and backend config_validators.py.
 * This module is the single source of truth for:
 * - Blocked/reserved shortcuts (OS-specific)
 * - Modifier-only validation (Shift alone is valid; Shift+Z rejects all)
 * - Duplicate detection
 * - Normalization
 *
 * The matching backend mirror lives in
 * ``voice_typer/server/config_validators.py`` as ``_RESERVED_HOTKEYS``.
 * The two MUST be kept in sync — if you add a shortcut here, add it there.
 */

/**
 * Reserved OS shortcuts that should never be assignable.
 *
 * Platform-specific:
 * - Windows: Win+E (Explorer), Win+V (clipboard history), Win+Space
 *   (input-language switch), Win+D (show desktop), Win+L (lock), etc.
 * - macOS: Cmd+Space (Spotlight), Cmd+Q (quit), Cmd+W (close window),
 *   Cmd+H (hide), Cmd+M (minimize), Cmd+Tab (app switcher),
 *   Cmd+Shift+3/4/5 (screenshots).
 * - Linux: Super+L (lock), Super+D (show desktop), Super+Tab (window
 *   switcher). NOTE: Super+Space is intentionally NOT reserved on
 *   Linux — most desktop environments allow it to be reassigned. See
 *   the existing invariant test "still offers <super>+<space> on Linux".
 *
 * Keys are stored lowercase; isReserved() lowercases both sides before
 * comparing so callers can pass either case.
 */
export const RESERVED_SHORTCUTS: Record<string, string[]> = {
	win32: [
		"<win>+e",
		"<win>+v",
		"<win>+space",
		"<win>+d",
		"<win>+l",
		"<win>+tab",
		"<win>+r",
		"<win>+i",
		"<win>+p",
		"<win>+m",
	],
	darwin: [
		"<cmd>+space",
		"<cmd>+q",
		"<cmd>+w",
		"<cmd>+h",
		"<cmd>+m",
		"<cmd>+tab",
		"<cmd>+shift+3",
		"<cmd>+shift+4",
		"<cmd>+shift+5",
	],
	linux: [
		"<super>+l",
		"<super>+d",
		"<super>+tab",
		// NOTE: <super>+<space> is intentionally NOT reserved on Linux
		// — most desktop environments allow it to be reassigned. The
		// existing invariant test "still offers <super>+<space> on Linux
		// (Linux does not reserve it)" documents this. Do NOT add it
		// here without updating that test.
	],
};

/**
 * Modifier keys that may appear as a prefix part of a combo, or alone
 * as a single-key trigger (modifier-only release detection in the
 * native backends). Mirrors MODIFIER_KEYS in hotkey-utils.ts so this
 * module stays self-contained (no circular import).
 */
export const MODIFIER_KEYS_SHARED = [
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
	"super",
	"fn",
	"globe",
] as const;

export interface ValidationResult {
	valid: boolean;
	/** Human-readable reason when valid is false. */
	reason?: string;
	/**
	 * NEVER set by validateHotkey. Documented here only so callers can
	 * assert it's absent — the partial-assign bug (Problem 2.2) hinged
	 * on a hypothetical "partial" return value that never existed, but
	 * we explicitly test `not.toHaveProperty("partial")` to lock the
	 * contract: validateHotkey either accepts the whole combo or
	 * rejects the whole combo, never returns a fragment.
	 */
	partial?: never;
}

/**
 * Detect the current platform from navigator.userAgent.
 *
 * Returns one of the keys of RESERVED_SHORTCUTS ("win32" | "darwin" |
 * "linux") or "unknown" if the platform can't be identified. Callers
 * that already have a platform string can pass it directly to
 * isReserved / validateHotkey.
 */
export function detectPlatform(): string {
	if (typeof navigator === "undefined") return "unknown";
	const ua = navigator.userAgent.toLowerCase();
	if (ua.includes("mac")) return "darwin";
	if (ua.includes("win")) return "win32";
	if (ua.includes("linux")) return "linux";
	return "unknown";
}

/**
 * Check if a hotkey is reserved by the OS for the given platform.
 *
 * Comparison is case-insensitive — both sides are lowercased before
 * comparing. The hotkey string is compared as-is (with angle brackets,
 * e.g. ``"<win>+e"``); callers should NOT pre-normalize.
 */
export function isReserved(hotkey: string, platform: string): boolean {
	if (!hotkey) return false;
	const reserved = RESERVED_SHORTCUTS[platform] || [];
	const normalized = hotkey.toLowerCase();
	return reserved.some((r) => r.toLowerCase() === normalized);
}

/**
 * Normalize a hotkey string for comparison: trim each part, lowercase,
 * and strip angle brackets. Used internally to compare two hotkey
 * strings for equality (e.g., duplicate detection across fields).
 *
 * Example: ``"<Ctrl>+<V>"`` → ``"ctrl+v"``
 */
export function normalizeHotkey(hotkey: string): string {
	if (!hotkey) return "";
	return hotkey
		.split("+")
		.map((p) => p.replace(/[<>]/g, "").trim().toLowerCase())
		.filter(Boolean)
		.join("+");
}

/**
 * Validate a hotkey combination.
 *
 * Rules (applied in order, short-circuiting on first failure):
 *  1. Non-empty: a blank hotkey is invalid.
 *  2. Reserved: OS-reserved shortcuts (Win+E, Cmd+Space, etc.) are
 *     rejected so the user can't silently break system shortcuts.
 *  3. Structural: a single part can be any key (including a modifier
 *     alone — ``<shift>``, ``<alt>``, etc., are valid single-key
 *     triggers via modifier-only release detection). A combo (2+ parts)
 *     must NOT end with a modifier — ``<shift>+<ctrl>`` is rejected
 *     because the trailing modifier would never fire as a hotkey.
 *
 * IMPORTANT (Problem 2.2 — partial-assign bug): this function never
 * returns a "partial" result. If a combo is invalid, the WHOLE combo
 * is rejected; the caller must keep the previous shortcut unchanged.
 * The ``partial`` field on ValidationResult is typed as ``never`` to
 * make this contract enforceable at the type level.
 */
export function validateHotkey(
	hotkey: string,
	platform: string,
): ValidationResult {
	// 1. Non-empty
	if (!hotkey?.trim()) {
		return { valid: false, reason: "Hotkey is empty" };
	}

	// 2. Reserved by OS
	if (isReserved(hotkey, platform)) {
		return { valid: false, reason: "Reserved by operating system" };
	}

	// 3. Structural: parse parts
	const parts = hotkey
		.split("+")
		.map((p) => p.replace(/[<>]/g, "").trim())
		.filter(Boolean);
	if (parts.length === 0) {
		return { valid: false, reason: "Hotkey has no keys" };
	}

	// Single part: modifier-only (e.g. <shift>, <alt>) or any single
	// key (e.g. <caps_lock>, <f2>) is valid. The native backends
	// support modifier-only release detection.
	if (parts.length === 1) {
		return { valid: true };
	}

	// Combo (2+ parts): the LAST part must NOT be a modifier. A combo
	// that ends with a modifier (e.g. <shift>+<ctrl>) would never fire
	// as a hotkey — pynput/Win32 require a non-modifier terminator.
	// Rejecting the WHOLE combo (rather than returning just the
	// modifier prefix) is the fix for Problem 2.2: previously the
	// keyup handler would assign <shift> alone after the user pressed
	// Shift+Ctrl, silently downgrading the user's intent.
	const lastKey = parts[parts.length - 1];
	if (
		MODIFIER_KEYS_SHARED.includes(
			lastKey as (typeof MODIFIER_KEYS_SHARED)[number],
		)
	) {
		return {
			valid: false,
			reason:
				"Combo must end with a non-modifier key (e.g. Ctrl+Alt+V, not just Ctrl)",
		};
	}

	return { valid: true };
}
