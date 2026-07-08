// HotkeySettingsSection — Hotkey section of the Settings page.
//
// Renders the "Hotkey" SettingsSection block (Dictation Key via
// HotkeyPicker). The Recording section is handled by the standalone
// RecordingSettingsSection component.

import { memo, useCallback } from "react";
import { HotkeyPicker } from "@/components/HotkeyPicker";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import { t } from "@/i18n/i18n";
import { RecordingSettingsSection } from "./RecordingSettingsSection";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

export const HotkeySettingsSection = memo(function HotkeySettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
	isVisible,
}: SettingsSectionSharedProps) {
	// ESC-FIX-002-LEGACY: useCallback calls MUST be before any early return
	// per React's Rules of Hooks.
	const handleDictationChange = useCallback(
		(h: string) => updateConfig({ hotkey: h }),
		[updateConfig],
	);
	const handleDictationCaptureStart = useCallback(() => {
		void window.python?.call({
			type: "set_esc_cancel_paused",
			data: { paused: true },
		});
	}, []);
	const handleDictationCaptureEnd = useCallback(() => {
		void window.python?.call({
			type: "set_esc_cancel_paused",
			data: { paused: false },
		});
	}, []);

	if (!config) return <SettingsSkeleton rows={3} />;

	const dictationKeyLabel = t("settings.hotkeySection.dictationKey");
	const dictationKeyInfoSearch = t(
		"settings.hotkeySection.dictationKeyInfoSearch",
	);
	const hotkeyTitle = t("settings.hotkeySection.hotkeyTitle");

	const hotkeyVisible = isVisible(
		dictationKeyLabel,
		dictationKeyInfoSearch,
		hotkeyTitle,
	);

	return (
		<>
			{/* ── SECTION: Hotkey ───────────────────────────────────── */}
			{hotkeyVisible && (
				<SettingsSection
					title={hotkeyTitle}
					description={t("settings.hotkeySection.hotkeyDescription")}
				>
					<SettingRow
						label={dictationKeyLabel}
						info={t("settings.hotkeySection.dictationKeyInfo")}
					>
						<HotkeyPicker
							value={config.hotkey}
							onChange={handleDictationChange}
							mode="single"
							aria-label={t("settings.hotkeySection.dictationKeyAria")}
							onCaptureStart={handleDictationCaptureStart}
							onCaptureEnd={handleDictationCaptureEnd}
						/>
					</SettingRow>
				</SettingsSection>
			)}

			{/* ── SECTION: Recording ─────────────────────────────────── */}
			<RecordingSettingsSection
				config={config}
				updateConfig={updateConfig}
				updateConfigDebounced={updateConfigDebounced}
				isVisible={isVisible}
			/>
		</>
	);
});
