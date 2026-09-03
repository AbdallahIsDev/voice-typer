// RecordingSettingsSection — Recording section of the Settings page.
//
// Renders the "Recording" SettingsSection block with Dictation Key,
// Re-Paste Key, Recording Mode, Auto-Stop, ESC to Cancel, Auto-Paste,
// Sound Feedback, Silence Warning, Max Duration, and Dead-Air Timeout.
// The Dictation Key was moved here from the now-removed standalone
// HotkeySettingsSection since it was the only setting in that section.

import { PlayIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { RangeSlider } from "@/components/common/RangeSlider";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { HotkeyPicker } from "@/components/hotkey/HotkeyPicker";
import {
	getComboPresets,
	getSingleKeyPresets,
} from "@/components/hotkey/hotkey-utils";
import { Button } from "@/components/ui/button";
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
import {
	playSoundCue,
	setSoundFeedbackEnabled,
	setSoundVolume,
} from "@/lib/sound-manager";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

//(Sub-agent 14): the dictation-key dropdown presets are now
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
// The dictation-key ``HotkeyPicker`` is wired with ``mode="single"``
// so the capture validator rejects multi-key combos (matching the
// single-key-only promise of the dropdown). In single mode the
// HotkeyPicker strips angle brackets from the stored value before
// matching it against the preset list (see HotkeyPicker.tsx), so the
// preset values must be the RAW key names (``"caps_lock"``, ...) —
// NOT wrapped in ``<...>``. The HotkeyPicker re-adds the brackets on
// selection (``newValue = `<${opt.value}>` ``) so the stored config
// value keeps the canonical pynput format.
//
// Computed via ``useMemo`` so the array identity is stable across
// re-renders (HotkeyPicker's presets prop comparison doesn't thrash).
const useDictationKeyPresets = () =>
	useMemo(
		() =>
			getSingleKeyPresets().map((p) => ({
				value: p.value,
				label: p.label,
			})),
		[],
	);

// The repaste-key ``HotkeyPicker`` is wired with ``mode="combo"`` and
// its presets come from ``getComboPresets()``. ``getComboPresets()``
// re-detects the platform on every call (so the macOS Cmd+Shift+V
// option appears iff the current navigator.userAgent looks like
// macOS), so it CANNOT be hoisted to module scope. Memoizing it here
// keeps the array identity stable across renders (the inline call
// pattern was re-creating the array on every render, thrashing the
// HotkeyPicker's presets prop comparison and causing unnecessary
// re-renders of the dropdown).
const useRepasteKeyPresets = () => useMemo(() => getComboPresets(), []);

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
	//dictation-key presets derived from the shared
	// single-key preset list (platform-aware, no <shift> hazard).
	const dictationKeyPresets = useDictationKeyPresets();
	// Memoized repaste-key presets — getComboPresets() re-detects the
	// platform on every call, so it can't be hoisted to module scope,
	// but it can be memoized per-mount so the array identity stays
	// stable across renders.
	const repasteKeyPresets = useRepasteKeyPresets();
	//ESC-: useCallback calls MUST be before any early return
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

	const handleUnsafePasteChange = (checked: boolean) =>
		updateConfig({ unsafe_paste_on_unknown_focus: checked });

	const handleWarnElevatedPasteChange = (checked: boolean) =>
		updateConfig({ warn_elevated_paste: checked });

	const handleWarnPasswordPasteChange = (checked: boolean) =>
		updateConfig({ warn_password_paste: checked });

	const handleSoundFeedbackChange = (checked: boolean) => {
		updateConfig({ sound_feedback_enabled: checked });
		setSoundFeedbackEnabled(checked);
	};

	// Volume slider → debounced config write + immediate manager sync so
	// a Test Sound click right after a drag previews the new level even
	// before the debounce timer fires. The config value stays the
	// canonical 0.0..1.0 float (mirrors the server allowlist); the SLIDER
	// works in percent units (0-100, step 5) so the readout / thumb
	// aria-valuetext / endpoints render as "65%" instead of raw decimals.
	const handleSoundVolumeChange = (v: number) => {
		const clamped = Math.min(1, Math.max(0, v / 100));
		setSoundVolume(clamped);
		updateConfigDebounced("sound_volume", clamped);
	};

	// Preview one existing cue at the configured volume. The manager
	// gates on the enabled flag internally, so a disabled toggle means
	// the preview is a no-op — matching the cues' real behavior.
	const handleTestSound = () => {
		playSoundCue("complete");
	};

	// Keep the manager's volume mirror in sync with config pushes
	// (config_changed events, reset-to-defaults, initial load) that
	// didn't flow through the slider handler.
	useEffect(() => {
		if (typeof config?.sound_volume === "number") {
			setSoundVolume(config.sound_volume);
		}
	}, [config?.sound_volume]);

	// track invalid reasons for the NumberInputStepper inputs
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
	// New rows reuse ONE info string for both the search predicate and
	// the tooltip (no separate InfoSearch keys) — the predicate accepts
	// the same string.
	const soundVolumeLabel = t("settings.hotkeySection.soundVolume");
	const soundVolumeInfoSearch = t("settings.hotkeySection.soundVolumeInfo");
	const testSoundLabel = t("settings.hotkeySection.testSound");
	const unsafePasteLabel = t("settings.hotkeySection.unsafePaste");
	const unsafePasteInfoSearch = t(
		"settings.hotkeySection.unsafePasteInfoSearch",
	);
	const warnElevatedLabel = t("settings.hotkeySection.warnElevatedPaste");
	const warnElevatedInfoSearch = t(
		"settings.hotkeySection.warnElevatedPasteInfoSearch",
	);
	const warnPasswordLabel = t("settings.hotkeySection.warnPasswordPaste");
	const warnPasswordInfoSearch = t(
		"settings.hotkeySection.warnPasswordPasteInfoSearch",
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
		{ label: unsafePasteLabel, info: unsafePasteInfoSearch },
		{ label: warnElevatedLabel, info: warnElevatedInfoSearch },
		{ label: warnPasswordLabel, info: warnPasswordInfoSearch },
		{ label: soundFeedbackLabel, info: soundFeedbackInfoSearch },
		{ label: soundVolumeLabel, info: soundVolumeInfoSearch },
		{ label: testSoundLabel, info: soundVolumeInfoSearch },
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
							mode="single"
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
						//use the corrected tooltip text that
						// names the visible SegmentedControl labels
						// ("Tap to Record" / "Push to Talk") instead of
						// the legacy "Toggle" wording that didn't match.
						info={t("settings.hotkeySection.recordingModeInfoSearch")}
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
							presets={repasteKeyPresets}
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

					{/*Paste-safety rows (previously config.json-only
                                            fields, now user-tunable). unsafe_paste_on_unknown_focus is
                                            an escape hatch (paste into unidentified windows); the two
                                            warn_* toggles gate the confirmation dialogs for elevated
                                            (admin) windows and password fields. */}
					<SettingRow
						label={unsafePasteLabel}
						info={t("settings.hotkeySection.unsafePasteInfoSearch")}
					>
						<Switch
							checked={config.unsafe_paste_on_unknown_focus ?? false}
							onCheckedChange={handleUnsafePasteChange}
							aria-label={t("settings.hotkeySection.unsafePasteAria")}
							data-testid="unsafe-paste-switch"
						/>
					</SettingRow>

					<SettingRow
						label={warnElevatedLabel}
						info={t("settings.hotkeySection.warnElevatedPasteInfoSearch")}
					>
						<Switch
							checked={config.warn_elevated_paste ?? true}
							onCheckedChange={handleWarnElevatedPasteChange}
							aria-label={t("settings.hotkeySection.warnElevatedPasteAria")}
							data-testid="warn-elevated-paste-switch"
						/>
					</SettingRow>

					<SettingRow
						label={warnPasswordLabel}
						info={t("settings.hotkeySection.warnPasswordPasteInfoSearch")}
					>
						<Switch
							checked={config.warn_password_paste ?? true}
							onCheckedChange={handleWarnPasswordPasteChange}
							aria-label={t("settings.hotkeySection.warnPasswordPasteAria")}
							data-testid="warn-password-paste-switch"
						/>
					</SettingRow>

					{/*Audio cue on record start/stop for accessibility
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

					{/*Volume multiplier for the cues above + a one-click
                                            preview. Both rows are disabled while sound feedback is off —
                                            the cues (and the preview) are no-ops then anyway. */}
					<SettingRow
						label={soundVolumeLabel}
						info={t("settings.hotkeySection.soundVolumeInfoSearch")}
					>
						<RangeSlider
							value={Math.round((config.sound_volume ?? 1) * 100)}
							min={0}
							max={100}
							step={5}
							onChange={handleSoundVolumeChange}
							ariaLabel={t("settings.hotkeySection.soundVolumeAria")}
							disabled={!(config.sound_feedback_enabled ?? true)}
							suffix="%"
						/>
					</SettingRow>

					<SettingRow
						label={testSoundLabel}
						info={t("settings.hotkeySection.soundVolumeInfoSearch")}
					>
						<Button
							variant="outline"
							size="sm"
							className="gap-2"
							onClick={handleTestSound}
							disabled={!(config.sound_feedback_enabled ?? true)}
							aria-label={t("settings.hotkeySection.testSoundAria")}
							data-testid="test-sound-button"
						>
							<HugeiconsIcon
								icon={PlayIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{testSoundLabel}
						</Button>
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
									// surface parse / range errors via inline
									// message + helper text. The NumberInputStepper
									// still sets aria-invalid internally for SR + visual.
									onInvalid={setSilenceInvalidReason}
									className="w-20 text-center"
									aria-label={t("settings.hotkeySection.silenceWarningAria")}
								/>
								<span className="text-sm text-(--text-muted)">
									{t("settings.hotkeySection.secondsSuffix")}
								</span>
							</div>
							{/* Helper text: valid range. Always shown so the user
                                                                knows the bounds before they type. */}
							{/*i18n the range hint + inline parse/range errors
                                                                (previously hardcoded English literals). */}
							<span className="text-xs text-(--text-muted)">
								{t("settings.hotkeySection.rangeHintSeconds", {
									min: "3",
									max: "30",
								})}
							</span>
							{silenceInvalidReason && (
								<span role="alert" className="text-xs text-destructive">
									{silenceInvalidReason === "parse"
										? t("settings.hotkeySection.parseError")
										: t("settings.hotkeySection.rangeErrorSeconds", {
												min: "3",
												max: "30",
											})}
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
									// surface parse / range errors via inline
									// message + helper text.
									onInvalid={setMaxRecordingInvalidReason}
									className="w-20 text-center"
									aria-label={t("settings.hotkeySection.maxRecordingTimeAria")}
								/>
								<span className="text-sm text-(--text-muted)">
									{t("settings.hotkeySection.minutesSuffix")}
								</span>
							</div>
							{/*i18n the range hint + inline parse/range errors
                                                                (previously hardcoded English literals). */}
							<span className="text-xs text-(--text-muted)">
								{t("settings.hotkeySection.rangeHintMinutes", {
									min: "5",
									max: "60",
								})}
							</span>
							{maxRecordingInvalidReason && (
								<span role="alert" className="text-xs text-destructive">
									{maxRecordingInvalidReason === "parse"
										? t("settings.hotkeySection.parseError")
										: t("settings.hotkeySection.rangeErrorMinutes", {
												min: "5",
												max: "60",
											})}
								</span>
							)}
						</div>
					</SettingRow>

					{/*dead_air_timeout setting REMOVED. It was redundant with
                                            stop_on_silence_seconds — auto-stop already resets on every speech
                                            detection, so "silence after speech" needs no separate control.
                                            Do NOT re-add this setting. */}
				</SettingsSection>
			)}
		</>
	);
});
