// settingsTabLabels — the source of truth for "which labels appear on
//which Settings tab?" Used by the search auto-switch () so the
// user's query picks the tab whose labels best match — label-based,
// not hint-based.
//
// Hints (settings.searchHints.*) remain in i18n for completeness
//( — translated into zh/de/fr/hi/ru) but are NOT used by the
// auto-switch logic; labels are sufficient because every section title
// and row label is already translated and reflects the user's locale.

import { t } from "@/i18n/i18n";

export type SettingsTab = "appearance" | "general" | "aiAudio" | "privacy";

/** Returns the translated labels that appear on each tab. Called inside
 *  `handleSearchChange` so the labels reflect the current locale at
 *  the moment the user types. */
export function getTabLabels(): Record<SettingsTab, string[]> {
	return {
		appearance: [
			t("settings.tabs.appearance"),
			t("settings.appearance.title"),
			t("settings.appearance.colorScheme"),
			t("settings.appearance.themePreset"),
			t("settings.appearance.customTheme"),
			t("settings.appearance.textSize"),
		],
		general: [
			t("settings.tabs.general"),
			t("settings.general"),
			t("settings.overlay"),
			t("settings.hotkeySection.recordingTitle"),
			t("settings.launchAtLogin"),
			t("settings.fastStartup"),
			t("settings.appLanguage"),
			t("settings.notifications"),
			t("settings.trayClick"),
			t("settings.bubbleBehaviorLabel"),
			t("settings.bubblePositionLabel"),
			t("settings.showOnAppStartup"),
			t("settings.dragToMove"),
			t("settings.bubbleMicButton"),
			t("settings.hotkeySection.dictationKey"),
			t("settings.hotkeySection.repasteKey"),
			t("settings.hotkeySection.recordingMode"),
			t("settings.hotkeySection.stopOnSilence"),
			t("settings.hotkeySection.escToCancel"),
			t("settings.hotkeySection.autoPaste"),
			t("settings.hotkeySection.soundFeedback"),
			t("settings.hotkeySection.silenceWarning"),
			t("settings.hotkeySection.maxRecordingTime"),
		],
		aiAudio: [
			t("settings.tabs.aiAudio"),
			t("models.title"),
			t("settings.audioEnhancement.title"),
			t("settings.audioEnhancement.volumeBackend"),
			t("settings.audioEnhancement.autoDuckVolume"),
			t("settings.audioEnhancement.duckLevel"),
			t("settings.audioEnhancement.microphoneQuality"),
			t("settings.audioEnhancement.highPassFilter"),
			t("settings.audioEnhancement.noiseSuppression"),
			t("settings.audioEnhancement.noiseGate"),
			t("settings.audioEnhancement.equalizer"),
			t("settings.audioEnhancement.compressor"),
			t("settings.audioEnhancement.limiter"),
			t("settings.audioEnhancement.notchFilter"),
			t("settings.postProcessing"),
			t("settings.transcriptionLanguage"),
			t("settings.autoPunctuation"),
			t("settings.textCleanupLabel"),
			t("settings.textSnippets"),
			t("settings.vocabulary"),
			t("settings.llmPolishing"),
			t("settings.aiEnhancement.title"),
			t("settings.vocabAutomation.title"),
		],
		privacy: [
			t("settings.tabs.privacy"),
			t("settings.privacy.privacyTitle"),
			t("settings.privacy.audioRecoveryTitle"),
			t("settings.privacy.crashRecovery"),
			t("settings.privacy.huggingFaceDownloadsLabel"),
			t("settings.privacy.voiceBiometricLabel"),
			t("settings.privacy.openaiCloudAsrLabel"),
			t("settings.privacy.groqCloudAsrLabel"),
			t("settings.privacy.deepgramCloudAsrLabel"),
			t("settings.privacy.llmTextPolishingLabel"),
			t("settings.privacy.exportAllDataLabel"),
			t("settings.troubleshooting.title"),
			t("settings.troubleshooting.openLogFolder"),
			t("settings.troubleshooting.diagnostics"),
			t("settings.troubleshooting.helpFaq"),
			t("settings.troubleshooting.reportBug"),
			t("settings.troubleshooting.reRunWizard"),
			t("settings.troubleshooting.resetToDefaults"),
			t("settings.troubleshooting.resetAccessibility"),
			t("settings.troubleshooting.resetLinux"),
			t("about.cacheTitle"),
			t("about.updatesTitle"),
		],
	};
}
