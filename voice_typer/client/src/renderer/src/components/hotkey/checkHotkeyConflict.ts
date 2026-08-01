/**
 * Hotkey conflict-detection helper.
 *
 * Extracted from the three duplicated inline blocks in
 * ``HotkeyPicker.tsx`` (single-key capture, full-combo capture, and
 * the preset-dropdown ``onSelect``). Each site did the same thing:
 *
 *   1. Skip the check when the user is re-selecting the current value
 *      (no-op — the value isn't actually changing).
 *   2. If the new hotkey is already in ``occupiedHotkeys`` (a list of
 *      hotkeys owned by sibling pickers), format a human-readable
 *      label via ``formatHotkeyLabel`` and return the localized
 *      ``hotkeyValidation.alreadyInUse`` error string.
 *   3. Otherwise return ``null`` (no conflict).
 *
 * Centralising the logic here means future tweaks to the conflict
 * rule (e.g. allowing overlap when one side is a strict modifier-only
 * combo and the other is a combo that includes a non-modifier) only
 * touch one file.
 *
 * The caller is responsible for what to do with the returned string —
 * typically ``setError(conflict); resetCaptureSession(); return;`` in
 * capture sites, or ``setError(conflict); return;`` in the dropdown
 * ``onSelect`` (where there's no capture session to reset).
 *
 * Screen-reader conflict detection lives here too (see
 * ``checkScreenReaderConflict`` below). Unlike ``checkHotkeyConflict``
 * (which returns a hard-block error string), the SR check is advisory —
 * it tells the caller that the chosen hotkey may break the user's
 * screen reader, but does NOT prevent the assignment. Callers
 * (HotkeyPicker.tsx, RecordingSettingsSection.tsx, HotkeyStep.tsx)
 * surface a localized warning banner and let the user proceed.
 */

import hotkeyReserved from "../../data/hotkey_reserved.json";
import { formatHotkeyLabel } from "./hotkey-utils";

/**
 * @param newHotkey       The hotkey string the user just captured/selected
 *                        (e.g. ``"<ctrl>+<shift>+c>"``).
 * @param currentValue    The picker's current value (skipped by the
 *                        conflict check so re-selecting the same value
 *                        is always allowed).
 * @param occupiedHotkeys Hotkey strings already claimed by sibling
 *                        pickers (e.g. the dictation key picker passes
 *                        the repaste key's value here so the two can't
 *                        collide). ``undefined`` is treated as "no
 *                        occupied list" — the check is skipped.
 * @param t               The i18n ``t`` function (from
 *                        ``@/i18n/i18n``). Used to localize the
 *                        ``hotkeyValidation.alreadyInUse`` message
 *                        with the formatted hotkey label as a param.
 * @returns The localized error string when a conflict is detected,
 *          or ``null`` when the new hotkey is available.
 */
export function checkHotkeyConflict(
	newHotkey: string,
	currentValue: string,
	occupiedHotkeys: string[] | undefined,
	t: (key: string, params?: Record<string, string>) => string,
): string | null {
	if (newHotkey !== currentValue && occupiedHotkeys?.includes(newHotkey)) {
		const hotkeyLabel = formatHotkeyLabel(newHotkey);
		return t("hotkeyValidation.alreadyInUse", { label: hotkeyLabel });
	}
	return null;
}

// ──────────────────────────────────────────────────────────────────────
// Screen-reader conflict detection (advisory, NOT a hard block)
// ──────────────────────────────────────────────────────────────────────

/**
 * Shape of one entry in the ``screen_reader_conflicts`` section of
 * ``hotkey_reserved.json``. Mirrors the JSON structure exactly so the
 * cast below is type-safe.
 */
interface ScreenReaderConflictEntry {
	hotkey: string;
	sr_software: string[];
}

/**
 * Per-platform list of (hotkey, screen-reader software) entries that
 * conflict with default SR modifier keys. Loaded once at module init
 * from the canonical ``hotkey_reserved.json`` (kept in sync with the
 * server copy by ``tests/test_hotkey_reserved_sync.py``).
 *
 * Example entry:
 *   ``{ darwin: [{ hotkey: "<caps_lock>", sr_software: ["VoiceOver"] }] }``
 *
 * An empty array for a platform means "no known SR conflict for the
 * default SR on that platform" — e.g. Linux's Orca uses Insert by
 * default, so Caps Lock is not in the Linux list.
 */
type ScreenReaderConflictsData = Record<string, ScreenReaderConflictEntry[]>;

const SCREEN_READER_CONFLICTS: ScreenReaderConflictsData =
	(
		hotkeyReserved as unknown as {
			screen_reader_conflicts?: ScreenReaderConflictsData;
		}
	).screen_reader_conflicts ?? {};

/**
 * Result of an SR-conflict check.
 *
 * - ``conflict`` — true when the hotkey conflicts with a default SR
 *   modifier key on the user's current platform.
 * - ``platform`` — the detected platform key (``"darwin"`` /
 *   ``"win32"`` / ``"linux"`` / ``"unknown"``). Always returned (even
 *   on no conflict) so callers can branch on platform without
 *   re-detecting.
 * - ``srSoftware`` — when ``conflict`` is true, the product names of
 *   the affected screen readers (e.g. ``["VoiceOver"]``,
 *   ``["Narrator", "NVDA", "JAWS"]``). Empty array on no conflict.
 *   A defensive copy is returned so callers can't mutate the JSON
 *   lookup table.
 */
export interface ScreenReaderConflictResult {
	conflict: boolean;
	platform: string;
	srSoftware: string[];
}

/**
 * Detect the current platform from ``navigator.platform`` (heuristic,
 * offline — per C-DATA-1 in CONSTRAINTS.md, NO network calls).
 *
 * Returns one of ``"darwin"`` | ``"win32"`` | ``"linux"`` matching the
 * keys in ``hotkey_reserved.json``, or ``"unknown"`` if the platform
 * can't be identified.
 *
 * Why ``navigator.platform`` and not ``navigator.userAgent`` (which
 * the existing ``detectPlatform()`` in hotkey-validation.ts uses)?
 * ``navigator.platform`` is the most reliable OS signal in the
 * renderer: it returns ``"MacIntel"`` on macOS, ``"Win32"`` /
 * ``"Win64"`` on Windows, and ``"Linux x86_64"`` / ``"Linux armv7l"``
 * on Linux. ``navigator.userAgent`` is sometimes overridden by
 * Electron for site-compat reasons (e.g. some sites that gate on
 * "Macintosh" in the UA get a spoofed UA), which would silently
 * mis-detect the OS and skip the SR warning for users who need it.
 *
 * ``navigator.platform`` is technically deprecated in the spec, but
 * every current browser (including Chromium/Electron) still exposes
 * it; the deprecation just means new APIs should prefer
 * ``navigator.userAgentData``. Until ``userAgentData`` is available
 * in Electron, ``navigator.platform`` is the right tool for this
 * offline heuristic.
 */
function _detectSrPlatform(): string {
	if (typeof navigator === "undefined") return "unknown";
	// navigator.platform may be undefined in some test environments —
	// treat that the same as "unknown" rather than crashing.
	const p = (navigator.platform ?? "").toLowerCase();
	if (p.startsWith("mac")) return "darwin";
	if (p.startsWith("win")) return "win32";
	if (p.startsWith("linux")) return "linux";
	return "unknown";
}

/**
 * Normalize a hotkey token for case- and bracket-insensitive
 * comparison: strip angle brackets, trim, lowercase.
 *
 * Example: ``"<Caps_Lock>"`` → ``"caps_lock"``.
 */
function _normalizeHotkeyToken(hotkey: string): string {
	return hotkey.replace(/[<>]/g, "").trim().toLowerCase();
}

/**
 * Heuristic, OFFLINE check: returns conflict info when the given
 * hotkey is one that a screen reader uses as its default modifier key
 * on the user's current platform. This is a WARNING, not a hard block
 * — the caller decides whether to surface a banner or refuse the
 * value. ``HotkeyPicker.tsx`` renders a localized warning banner;
 * ``RecordingSettingsSection.tsx`` and ``HotkeyStep.tsx`` will do the
 * same in their respective surfaces.
 *
 * Example: ``<caps_lock>`` on macOS is the default VoiceOver "VO"
 * modifier; on Windows it's the default Narrator modifier and a
 * common NVDA/JAWS alternative. Linux has no equivalent default
 * SR-modifier convention (Orca uses Insert by default; Caps Lock is
 * not reserved), so ``<caps_lock>`` returns no conflict there.
 *
 * Per C-DATA-1 (CONSTRAINTS.md), this function performs NO network
 * calls — it's a pure heuristic based on ``navigator.platform`` + a
 * static lookup table loaded from ``hotkey_reserved.json`` at module
 * init. It does NOT query the OS for installed SR software (which
 * would require a native call + a permission grant + would still be
 * unreliable since SR detection isn't exposed by any cross-platform
 * API). The trade-off is that we can't tell whether the user has
 * actually ENABLED a given SR — we warn conservatively whenever the
 * hotkey matches a known default SR modifier on the user's platform.
 *
 * @param hotkey The hotkey string to check (e.g. ``"<caps_lock>"``).
 *               Case- and bracket-insensitive — both
 *               ``"<caps_lock>"`` and ``"Caps_Lock"`` match.
 * @returns ``{ conflict, platform, srSoftware }``. When ``conflict``
 *          is false, ``srSoftware`` is an empty array. ``platform``
 *          is always returned (even on no conflict) so callers can
 *          branch without re-detecting.
 */
export function checkScreenReaderConflict(
	hotkey: string,
): ScreenReaderConflictResult {
	const platform = _detectSrPlatform();
	if (!hotkey || platform === "unknown") {
		return { conflict: false, platform, srSoftware: [] };
	}
	const entries = SCREEN_READER_CONFLICTS[platform] ?? [];
	const normalized = _normalizeHotkeyToken(hotkey);
	for (const entry of entries) {
		if (_normalizeHotkeyToken(entry.hotkey) === normalized) {
			return {
				conflict: true,
				platform,
				srSoftware: [...entry.sr_software],
			};
		}
	}
	return { conflict: false, platform, srSoftware: [] };
}
