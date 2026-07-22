// RecordingSettingsSection — Recording section of the Settings page.
//
// Renders the "Recording" SettingsSection block with Dictation Key,
// Re-Paste Key, Recording Mode, Auto-Stop, ESC to Cancel, Auto-Paste,
// Sound Feedback, Silence Warning, Max Duration, and Dead-Air Timeout.
// The Dictation Key was moved here from the now-removed standalone
// HotkeySettingsSection since it was the only setting in that section.

import { memo, useCallback, useMemo, useState } from "react";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { HotkeyPicker } from "@/components/hotkey/HotkeyPicker";
import {
	getComboPresets,
	getSingleKeyPresets,
} from "@/components/hotkey/hotkey-utils";
import { NumberInputStepper } from "@/components/ui/number-input-stepper";
import {
	SegmentedControl,
	type SegmentedControlOption,
} from "@/components/ui/segmented-control";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n/i18n";
import { setSoundFeedbackEnabled } from "@/lib/sound-manager";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

// PVT-066 (Sub-agent 14): the dictation-key dropdown presets are now
// derived from ``getSingleKeyPresets()`` — the SAME single source of
// truth used elsewhere in the hotkey system. Previously this file
// re-declared its own inline list which:
//   1. Reintroduced the ``<shift>`` hazard (Shift is held for
//      capitalization while typing — using it as a dictation key
//      would fire dictation on every uppercase letter).
//   2. Did NOT include the macOS-only Fn / Globe key (the inline
//      list was platform-static; the getter re-detects the platform
//      on every call).
//
// The ``.map`` wraps each value in angle brackets (``<value>``) so
// the resulting preset shape matches the combo-mode hotkey format
// the HotkeyPicker expects (e.g. ``<caps_lock>``, ``<ctrl>``).
//
// Computed via ``useMemo`` so the array identity is stable across
// re-renders (HotkeyPicker's presets prop comparison doesn't thrash).
const useDictationKeyPresets = () =>
	useMemo(
		() =>
			getSingleKeyPresets().map((p) => ({
				value: `<${p.value}>`,
				label: p.label,
			})),
		[],
	);

// IMPL-C: option label keys (translated at render time so the labels
// honour the active UI locale).
const STOP_ON_SILENCE_OPTION_KEYS = [
	{ value: 60, labelKey: "settings.hotkeySection.minutes1" },
	{ value: 120, labelKey: "settings.hotkeySection.minutes2" },
	{ value: 180, labelKey: "settings.hotkeySection.minutes3" },
	{ value: 300, labelKey: "settings.hotkeySection.minutes5" },
] as const;

const RECORDING_MODE_OPTION_KEYS: {
	value: "toggle" | "push_to_talk";
	labelKey: string;
}[] = [
	{ value: "push_to_talk", labelKey: "settings.hotkeySection.pushToTalk" },
	{ value: "toggle", labelKey: "settings.hotkeySection.tapToRecord" },
];

export const RecordingSettingsSection = memo(function RecordingSettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
	isVisible,
}: SettingsSectionSharedProps) {
	// PVT-066: dictation-key presets derived from the shared
	// single-key preset list (platform-aware, no <shift> hazard).
	const dictationKeyPresets = useDictationKeyPresets();
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

	const handleRepasteChange = useCallback(
		(h: string) => updateConfig({ repaste_hotkey: h }),
		[updateConfig],
	);
	const handleRepasteCaptureStart = useCallback(() => {
		void window.python?.call({
			type: "set_esc_cancel_paused",
			data: { paused: true },
		});
	}, []);
	const handleRepasteCaptureEnd = useCallback(() => {
		void window.python?.call({
			type: "set_esc_cancel_paused",
			data: { paused: false },
		});
	}, []);

	const handleSilenceWarningChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		updateConfigDebounced("silence_warning_seconds", Number(e.target.value));

	const handleMaxRecordingTimeChange = (
		e: React.ChangeEvent<HTMLInputElement>,
	) =>
		updateConfigDebounced(
			"max_recording_time_seconds",
			Number(e.target.value) * 60,
		);

	const handleRecordingModeChange = (v: string) =>
		updateConfig({ recording_mode: v as "toggle" | "push_to_talk" });

	const handleStopOnSilenceChange = (v: string) =>
		updateConfig({ stop_on_silence_seconds: Number(v) });

	const handleEscCancelChange = (checked: boolean) =>
		updateConfig({ esc_cancel_enabled: checked });

	const handleAutoPasteChange = (checked: boolean) =>
		updateConfig({ paste_on_stop: checked });

	const handleSoundFeedbackChange = (checked: boolean) => {
		updateConfig({ sound_feedback_enabled: checked });
		setSoundFeedbackEnabled(checked);
	};

	// Fix #9: track invalid reasons for the NumberInputStepper inputs
	// so we can surface inline error messages and helper text. The state
	// is set by the NumberInputStepper's `onInvalid` callback (called
	// with "parse" | "range" | null) and read in the JSX below the input.
	const [silenceInvalidReason, setSilenceInvalidReason] = useState<
		"parse" | "range" | null
	>(null);
	const [maxRecordingInvalidReason, setMaxRecordingInvalidReason] = useState<
		"parse" | "range" | null
	>(null);

	const t = useT();

	if (!config) return <SettingsSkeleton rows={3} />;

	// IMPL-C: resolve translated labels/info/aria once per render.
	const dictationKeyLabel = t("settings.hotkeySection.dictationKey");
	const dictationKeyInfoSearch = t(
		"settings.hotkeySection.dictationKeyInfoSearch",
	);
	const recordingModeLabel = t("settings.hotkeySection.recordingMode");
	const recordingModeInfoSearch = t(
		"settings.hotkeySection.recordingModeInfoSearch",
	);
	const stopOnSilenceLabel = t("settings.hotkeySection.stopOnSilence");
	const stopOnSilenceInfoSearch = t(
		"settings.hotkeySection.stopOnSilenceInfoSearch",
	);
	const escToCancelLabel = t("settings.hotkeySection.escToCancel");
	const escToCancelInfoSearch = t("settings.hotkeySection.escToCancelInfo");
	const autoPasteLabel = t("settings.hotkeySection.autoPaste");
	const autoPasteInfoSearch = t("settings.hotkeySection.autoPasteInfo");
	const soundFeedbackLabel = t("settings.hotkeySection.soundFeedback");
	const soundFeedbackInfoSearch = t(
		"settings.hotkeySection.soundFeedbackInfoSearch",
	);
	const repasteKeyLabel = t("settings.hotkeySection.repasteKey");
	const repasteKeyInfoSearch = t("settings.hotkeySection.repasteKeyInfoSearch");
	const silenceWarningLabel = t("settings.hotkeySection.silenceWarning");
	const silenceWarningInfoSearch = t(
		"settings.hotkeySection.silenceWarningInfo",
	);
	const maxRecordingTimeLabel = t("settings.hotkeySection.maxRecordingTime");
	const maxRecordingTimeInfoSearch = t(
		"settings.hotkeySection.maxRecordingTimeInfoSearch",
	);
	const maxRecordingMinutes = Math.round(
		config.max_recording_time_seconds / 60,
	);
	const recordingModeOptions: SegmentedControlOption<
		"toggle" | "push_to_talk"
	>[] = RECORDING_MODE_OPTION_KEYS.map((opt) => ({
		value: opt.value,
		label: t(opt.labelKey),
	}));
	const autoStopOptions = STOP_ON_SILENCE_OPTION_KEYS.map((opt) => ({
		value: opt.value,
		label: t(opt.labelKey),
	}));

	// FIX (Task ID 6 / Settings Search): capture section title in a local
	// constant so the SAME value feeds both the <SettingsSection title="…">
	// prop AND the isVisible(…) predicate's third argument.
	const recordingTitle = t("settings.hotkeySection.recordingTitle");

	const recordingItems = [
		{ label: dictationKeyLabel, info: dictationKeyInfoSearch },
		{ label: repasteKeyLabel, info: repasteKeyInfoSearch },
		{ label: recordingModeLabel, info: recordingModeInfoSearch },
		{ label: stopOnSilenceLabel, info: stopOnSilenceInfoSearch },
		{ label: escToCancelLabel, info: escToCancelInfoSearch },
		{ label: autoPasteLabel, info: autoPasteInfoSearch },
		{ label: soundFeedbackLabel, info: soundFeedbackInfoSearch },
		{ label: silenceWarningLabel, info: silenceWarningInfoSearch },
		{ label: maxRecordingTimeLabel, info: maxRecordingTimeInfoSearch },
	];
	const recordingVisible = recordingItems.some((item) =>
		isVisible(item.label, item.info, recordingTitle),
	);

	return (
		<>
			{/* ── SECTION: Recording ─────────────────────────────────── */}
			{recordingVisible && (
				<SettingsSection
					title={recordingTitle}
					description={t("settings.hotkeySection.recordingDescription")}
				>
					{/* ── Dropdowns ──────────────────────────────────────── */}
					<SettingRow
						label={dictationKeyLabel}
						info={t("settings.hotkeySection.dictationKeyInfo")}
					>
						<HotkeyPicker
							value={config.hotkey}
							onChange={handleDictationChange}
							mode="combo"
							presets={dictationKeyPresets}
							occupiedHotkeys={
								config.repaste_hotkey ? [config.repaste_hotkey] : undefined
							}
							aria-label={t("settings.hotkeySection.dictationKeyAria")}
							onCaptureStart={handleDictationCaptureStart}
							onCaptureEnd={handleDictationCaptureEnd}
						/>
					</SettingRow>

					<SettingRow
						label={recordingModeLabel}
						info={t("settings.hotkeySection.recordingModeInfo")}
					>
						<SegmentedControl
							options={recordingModeOptions}
							value={config.recording_mode ?? "toggle"}
							onChange={handleRecordingModeChange}
							ariaLabel={t("settings.hotkeySection.recordingModeAria")}
						/>
					</SettingRow>

					<SettingRow
						label={stopOnSilenceLabel}
						info={t("settings.hotkeySection.stopOnSilenceInfo")}
					>
						<Select
							value={String(config.stop_on_silence_seconds ?? 60)}
							onValueChange={handleStopOnSilenceChange}
						>
							<SelectTrigger
								className="w-36"
								aria-label={t("settings.hotkeySection.stopOnSilenceAria")}
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{autoStopOptions.map((opt) => (
									<SelectItem key={opt.value} value={String(opt.value)}>
										{opt.label}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>

					<SettingRow
						label={repasteKeyLabel}
						info={t("settings.hotkeySection.repasteKeyInfo")}
					>
						<HotkeyPicker
							value={config.repaste_hotkey ?? "<ctrl>+<alt>+v"}
							onChange={handleRepasteChange}
							mode="combo"
							presets={getComboPresets()}
							occupiedHotkeys={config.hotkey ? [config.hotkey] : undefined}
							aria-label={t("settings.hotkeySection.repasteKeyAria")}
							onCaptureStart={handleRepasteCaptureStart}
							onCaptureEnd={handleRepasteCaptureEnd}
						/>
					</SettingRow>

					{/* ── Switches ───────────────────────────────────────── */}
					<SettingRow
						label={escToCancelLabel}
						info={t("settings.hotkeySection.escToCancelInfo")}
					>
						<Switch
							checked={config.esc_cancel_enabled ?? false}
							onCheckedChange={handleEscCancelChange}
							aria-label={t("settings.hotkeySection.escToCancelAria")}
						/>
					</SettingRow>

					<SettingRow
						label={autoPasteLabel}
						info={t("settings.hotkeySection.autoPasteInfo")}
					>
						<Switch
							checked={config.paste_on_stop}
							onCheckedChange={handleAutoPasteChange}
							aria-label={t("settings.hotkeySection.autoPasteAria")}
						/>
					</SettingRow>

					{/* NEW-UX-029: Audio cue on record start/stop for accessibility
                                        and confirmation.  Especially useful for blind users who
                                        can't see the visual indicator change. */}
					<SettingRow
						label={soundFeedbackLabel}
						info={t("settings.hotkeySection.soundFeedbackInfo")}
					>
						<Switch
							checked={config.sound_feedback_enabled ?? true}
							onCheckedChange={handleSoundFeedbackChange}
							aria-label={t("settings.hotkeySection.soundFeedbackAria")}
						/>
					</SettingRow>

					<SettingRow
						label={silenceWarningLabel}
						info={t("settings.hotkeySection.silenceWarningInfo")}
					>
						<div className="flex flex-col items-end gap-1">
							<div className="flex items-center gap-2">
								<NumberInputStepper
									min={3}
									max={30}
									step={1}
									value={String(config.silence_warning_seconds)}
									onChange={handleSilenceWarningChange}
									// Fix #9: surface parse / range errors via inline
									// message + helper text. The NumberInputStepper
									// still sets aria-invalid internally for SR + visual.
									onInvalid={setSilenceInvalidReason}
									className="w-20 text-center"
									aria-label={t("settings.hotkeySection.silenceWarningAria")}
								/>
								<span className="text-sm text-(--text-muted)">s</span>
							</div>
							{/* Helper text: valid range. Always shown so the user
                                                                knows the bounds before they type. */}
							<span className="text-xs text-(--text-muted)">Range: 3–30 s</span>
							{silenceInvalidReason && (
								<span role="alert" className="text-xs text-destructive">
									{silenceInvalidReason === "parse"
										? "Enter a whole number"
										: "Must be between 3 and 30"}
								</span>
							)}
						</div>
					</SettingRow>

					<SettingRow
						label={maxRecordingTimeLabel}
						info={t("settings.hotkeySection.maxRecordingTimeInfo")}
					>
						<div className="flex flex-col items-end gap-1">
							<div className="flex items-center gap-2">
								<NumberInputStepper
									min={5}
									max={60}
									step={1}
									value={String(maxRecordingMinutes)}
									onChange={handleMaxRecordingTimeChange}
									// Fix #9: surface parse / range errors via inline
									// message + helper text.
									onInvalid={setMaxRecordingInvalidReason}
									className="w-20 text-center"
									aria-label={t("settings.hotkeySection.maxRecordingTimeAria")}
								/>
								<span className="text-sm text-(--text-muted)">min</span>
							</div>
							<span className="text-xs text-(--text-muted)">
								Range: 5–60 min
							</span>
							{maxRecordingInvalidReason && (
								<span role="alert" className="text-xs text-destructive">
									{maxRecordingInvalidReason === "parse"
										? "Enter a whole number"
										: "Must be between 5 and 60"}
								</span>
							)}
						</div>
					</SettingRow>

					{/* RW-0: dead_air_timeout setting REMOVED. It was redundant with
                                            stop_on_silence_seconds — auto-stop already resets on every speech
                                            detection, so "silence after speech" needs no separate control.
                                            Do NOT re-add this setting. */}
				</SettingsSection>
			)}
		</>
	);
});
