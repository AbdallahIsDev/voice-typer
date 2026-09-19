import { formatHotkeyLabel } from "./hotkey-utils";

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
