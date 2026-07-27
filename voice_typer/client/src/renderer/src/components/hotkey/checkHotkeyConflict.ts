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
 */
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
