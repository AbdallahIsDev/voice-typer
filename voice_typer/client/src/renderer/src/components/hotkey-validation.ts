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
 *
 * HOTKEY-VALIDATION-002 (Task 2.2.5): the prior fix over-corrected by
 * adding letters/digits to KEY_CODE_TO_PYNPUT without adding a
 * validation rule to reject single letters/digits as standalone
 * hotkeys. This module now mirrors ALL backend rules: universal
 * reserved (Alt+Tab/F4/Esc/Space), per-platform reserved (Win+*,
 * Cmd+letter, etc.), Alt+Shift (Windows language switching),
 * Ctrl+<common-letter> (Copy/Paste/Undo/etc.), Shift+<letter>
 * (capitalization interference), and the new single-letter/digit
 * rejection rule.
 */

/**
 * Reserved OS shortcuts that should never be assignable.
 *
 * HOTKEY-SHARED-001: loaded from the canonical JSON file at
 * ``voice_typer/server/hotkey_reserved.json``. A copy lives at
 * ``voice_typer/client/src/renderer/src/data/hotkey_reserved.json``
 * and is imported with a project-relative path so the import doesn't
 * depend on a Vite alias that resolves outside the renderer root
 * (which can crash Vite's dev server during HMR on locale switch).
 * The sync test ``test_hotkey_reserved_sync.py`` verifies the copy
 * matches the server original.
 */

import hotkeyReserved from "../data/hotkey_reserved.json";

export const UNIVERSAL_RESERVED_SHORTCUTS: readonly string[] =
	hotkeyReserved.universal_reserved;
export const RESERVED_SHORTCUTS: Record<string, string[]> =
	hotkeyReserved.per_platform_reserved as Record<string, string[]>;
export const BLOCKED_CTRL_LETTERS: readonly string[] =
	hotkeyReserved.blocked_ctrl_letters;
export const MODIFIER_KEYS_SHARED: readonly string[] = hotkeyReserved.modifiers;

const _MODIFIER_KEYS_SET = new Set(MODIFIER_KEYS_SHARED);

/**
 * Check if a part is a known modifier key.
 */
function _isModifier(p: string): boolean {
	return _MODIFIER_KEYS_SET.has(p);
}

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
	// HOTKEY-FIX-001 (Round 0): normalize both sides via normalizeHotkey()
	// (which strips angle brackets and lowercases) before comparing. The
	// RESERVED_SHORTCUTS table stores entries with brackets on both parts
	// (e.g. ``"<win>+<e>"``), but callers pass hotkeys using the pynput
	// convention with brackets only on the modifier (e.g. ``"<win>+e"``).
	// The previous raw-lowercase comparison only matched when the caller
	// used the exact same bracket convention as the table — so Win+E,
	// Cmd+Space, Super+L, etc. were all silently accepted as valid
	// despite being OS-reserved. Normalizing both sides makes the
	// comparison bracket-agnostic.
	const normalized = normalizeHotkey(hotkey);
	return reserved.some((r) => normalizeHotkey(r) === normalized);
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
 *  2. Reserved (universal): Alt+Tab, Alt+F4, Alt+Esc, Alt+Space,
 *     Enter, Ctrl+Enter, Shift+Enter are blocked on every platform
 *     (window management or typing interference).
 *  3. Reserved (per-platform): OS-reserved shortcuts (Win+E, Cmd+Space,
 *     Super+L, etc.) are rejected so the user can't silently break
 *     system shortcuts.
 *  4. Single letter/digit rejection: a standalone ``<a>``, ``<1>``, etc.
 *     is rejected because it would interfere with normal typing.
 *     HOTKEY-VALIDATION-002 (Task 2.2.5): the prior fix added letters
 *     and digits to KEY_CODE_TO_PYNPUT (so Alt+Q works) but forgot to
 *     add a rule preventing them from being assigned as standalone
 *     hotkeys — silently accepting ``<a>`` and triggering dictation
 *     every time the user typed 'a'.
 *  5. Structural: a single part can be any NON-LETTER, NON-DIGIT key
 *     (including a modifier alone — ``<shift>``, ``<alt>``, etc., are
 *     valid single-key triggers via modifier-only release detection;
 *     ``<caps_lock>``, ``<f2>``, ``<tab>``, etc. are also valid). A
 *     combo (2+ parts) must NOT end with a modifier.
 *  6. Win+anything / Super+anything block (Windows, Linux): system
 *     shell shortcuts.
 *  7. Cmd+<letter> block (macOS): heavily used by the system and apps.
 *  8. Alt+Shift block (Windows): language switching.
 *  9. Ctrl+<common-letter> block (Copy/Paste/Undo/Save/etc.): only
 *     applies to PURE Ctrl+<letter> (no other modifier).
 * 10. Shift+<letter> block: interferes with text capitalization. Only
 *     applies to PURE Shift+<letter> (no other modifier).
 *
 * IMPORTANT (Problem 2.2 — partial-assign bug): this function never
 * returns a "partial" result. If a combo is invalid, the WHOLE combo
 * is rejected; the caller must keep the previous shortcut unchanged.
 * The ``partial`` field on ValidationResult is typed as ``never`` to
 * make this contract enforceable at the type level.
 *
 * HOTKEY-VALIDATION-002 (Task 2.2.5): the prior frontend validator
 * was missing rules 2, 6, 7, 8, 9, 10 — only rules 1, 3, 5 were
 * enforced. This allowed the frontend to accept combos the backend
 * would reject (e.g. ``<ctrl>+<c>``, ``<alt>+<tab>``) and combos the
 * user could never use (``<a>``, ``<1>``). The rules now mirror the
 * backend ``_validate_hotkey`` in config_validators.py exactly.
 */
export function validateHotkey(
	hotkey: string,
	platform: string,
): ValidationResult {
	// 1. Non-empty
	if (!hotkey?.trim()) {
		return { valid: false, reason: "Hotkey is empty" };
	}

	// Parse parts once — used by rules 3-10.
	const parts = hotkey
		.split("+")
		.map((p) => p.replace(/[<>]/g, "").trim().toLowerCase())
		.filter(Boolean);
	if (parts.length === 0) {
		return { valid: false, reason: "Hotkey has no keys" };
	}

	// 2. Universal reserved (Alt+Tab/F4/Esc/Space) — every platform.
	const normalized = parts.map((p) => `<${p}>`).join("+");
	if (
		UNIVERSAL_RESERVED_SHORTCUTS.some(
			(r) => normalizeHotkey(r) === normalizeHotkey(normalized),
		)
	) {
		return {
			valid: false,
			reason:
				"Reserved — conflicts with operating system or common app shortcuts",
		};
	}

	// 3. Per-platform reserved.
	if (isReserved(hotkey, platform)) {
		return { valid: false, reason: "Reserved by operating system" };
	}

	// Helper: classify parts.
	const isModifier = (p: string): boolean => _isModifier(p);
	const nonMods = parts.filter((p) => !isModifier(p));

	// 4. Single letter/digit rejection (HOTKEY-VALIDATION-002).
	//    A standalone <a>, <1>, etc. would trigger on every keypress
	//    of that character during normal typing.
	if (parts.length === 1) {
		const sole = parts[0];
		if (/^[a-z0-9]$/.test(sole)) {
			return {
				valid: false,
				reason: `Single letters and digits can't be used as hotkeys — '${sole}' would interfere with typing`,
			};
		}
	}

	// 5. Structural: a combo that includes a NON-MODIFIER must NOT end with
	//    a modifier (e.g. ``Ctrl+Alt+V`` is fine, ``Ctrl+V+Alt`` is not).
	//    HOTKEY-MULTIKEY-001 (Task 1.3): pure-modifier combos (e.g.
	//    ``Ctrl+Shift``, ``Ctrl+Alt``) are now ALLOWED — they're valid
	//    modifier-only release triggers in the native backends. The
	//    previous blanket rule "combo must not end with a modifier"
	//    incorrectly rejected these, causing a frontend/backend mismatch
	//    (the backend ``_validate_hotkey`` in config_validators.py has
	//    never had this rule). Now we only reject combos that mix
	//    modifiers AND non-modifiers but end with a modifier.
	if (parts.length >= 2 && nonMods.length > 0) {
		const lastKey = parts[parts.length - 1];
		if (isModifier(lastKey)) {
			return {
				valid: false,
				reason:
					"Combo must end with a non-modifier key (e.g. Ctrl+Alt+V, not Ctrl+Alt+V+Shift)",
			};
		}
	}

	// 6. Win+anything block (Windows only).
	// HOTKEY-VALIDATION-002 (Task 2.2.5): the prior code blanket-blocked
	// Super+anything on Linux too, which incorrectly rejected
	// <super>+<space> (a combo most Linux DEs allow reassigning). The
	// blanket block now applies only on Windows (where the Win key is
	// heavily reserved by the OS shell). On Linux, Super combos are
	// checked against the per-platform reserved list (Super+L, Super+D,
	// Super+Tab) — all other Super combos are allowed.
	const hasWin = parts.some((p) => p === "win" || p === "super");
	if (hasWin && platform === "win32") {
		return {
			valid: false,
			reason: "Windows key combinations are reserved by the OS shell",
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
					reason: `Cmd+${nm.toUpperCase()} is reserved by macOS / common apps`,
				};
			}
		}
	}

	// 8. Alt+Shift block (Windows language switching).
	if (platform === "win32") {
		const hasAlt = parts.some((p) => p.startsWith("alt"));
		const hasShift = parts.some((p) => p.startsWith("shift"));
		if (hasAlt && hasShift && nonMods.length === 0) {
			return {
				valid: false,
				reason: "Alt+Shift is reserved by Windows for language switching",
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
						reason: `Ctrl+${nm.toUpperCase()} is a reserved application shortcut`,
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
						reason: `Shift+${nm.toUpperCase()} interferes with text capitalization or symbol input`,
					};
				}
			}
		}
	}

	return { valid: true };
}
