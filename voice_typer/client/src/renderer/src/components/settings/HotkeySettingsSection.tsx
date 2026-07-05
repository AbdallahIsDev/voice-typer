// HotkeySettingsSection — Hotkey + Recording sections of the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders two
// SettingsSection blocks: "Hotkey" (Dictation Key via HotkeyPicker) and
// "Recording" (Recording Mode, Auto-Stop, ESC to Cancel, Auto-Paste,
// Sound Feedback, Re-Paste Key, Silence Warning, Max Duration, Dead-Air
// Timeout). Behaviour is identical to the previous monolithic
// implementation; both sections are always rendered (no search-filter
// hide-when-empty wrapper, matching the original).

import { memo, useCallback } from "react";
import { HotkeyPicker } from "@/components/HotkeyPicker";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import { NumberInput } from "@/components/ui/number-input";
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
import { setSoundFeedbackEnabled } from "@/lib/sound-manager";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

const AUTO_STOP_OPTIONS = [
	{ value: 60, label: "1 minute" },
	{ value: 120, label: "2 minutes" },
	{ value: 180, label: "3 minutes" },
	{ value: 300, label: "5 minutes" },
];

const RECORDING_MODE_OPTIONS: SegmentedControlOption<
	"toggle" | "push_to_talk"
>[] = [
	{ value: "toggle", label: "Tap to Record" },
	{ value: "push_to_talk", label: "Push to Talk" },
];

export const HotkeySettingsSection = memo(function HotkeySettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
	isVisible,
}: SettingsSectionSharedProps) {
	// ESC-FIX-002-LEGACY: useCallback calls MUST be before any early return
	// per React's Rules of Hooks. These only depend on props (updateConfig)
	// which are always available, so they are safe to call unconditionally.
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

	if (!config) return <SettingsSkeleton rows={3} />;

	// FIX (Task ID 6 / Settings Search): capture each section's title in
	// a local constant so the SAME value feeds both the
	// ``<SettingsSection title="…">`` prop AND the ``isVisible(...)``
	// predicate's new third argument. The section-level visibility
	// check below hides the whole section when the search query doesn't
	// match the section title OR any of its rows.
	const hotkeyTitle = "Hotkey";
	const recordingTitle = "Recording";

	const hotkeyVisible = isVisible(
		"Dictation Key",
		"The keyboard key used to start and stop recording.",
		hotkeyTitle,
	);

	const recordingItems = [
		{
			label: "Recording Mode",
			info: "Tap to Record: press the key once to start and again to stop. Push to Talk: hold the key while speaking.",
		},
		{
			label: "Auto-Stop",
			info: "Automatically stop recording after this many seconds of silence.",
		},
		{
			label: "ESC to Cancel",
			info: "Press Escape to cancel an active recording.",
		},
		{
			label: "Auto-Paste",
			info: "Automatically paste transcribed text into the currently focused field.",
		},
		{
			label: "Sound Feedback",
			info: "Play a short audio cue when recording starts and stops.",
		},
		{
			label: "Re-Paste Key",
			info: "Keyboard shortcut to re-paste the last transcription.",
		},
		{
			label: "Silence Warning",
			info: "Seconds of silence before showing a warning to help catch microphone issues.",
		},
		{
			label: "Max Duration",
			info: "Maximum recording length. Set to 0 for automatic (varies by device).",
		},
		{
			label: "Dead-Air Timeout",
			info: "Seconds of silence after speech is detected before auto-stopping.",
		},
	];
	const recordingVisible = recordingItems.some((item) =>
		isVisible(item.label, item.info, recordingTitle),
	);

	return (
		<>
			{/* ── SECTION: Hotkey ───────────────────────────────────── */}
			{hotkeyVisible && (
				<SettingsSection
					title={hotkeyTitle}
					description="Key to start and stop dictation."
				>
					<SettingRow
						label="Dictation Key"
						info="The keyboard key used to start and stop recording. Click the button to record a new key, or pick from the preset list. Supports F1-F19, Caps Lock, Print Screen, and more."
					>
						<HotkeyPicker
							value={config.hotkey}
							onChange={handleDictationChange}
							mode="single"
							aria-label="Dictation key"
							onCaptureStart={handleDictationCaptureStart}
							onCaptureEnd={handleDictationCaptureEnd}
						/>
					</SettingRow>
				</SettingsSection>
			)}

			{/* ── SECTION: Recording ─────────────────────────────────── */}
			{recordingVisible && (
				<SettingsSection
					title={recordingTitle}
					description="Behavior, shortcuts, and silence handling."
				>
					{/* ── Dropdowns ──────────────────────────────────────── */}
					<SettingRow
						label="Recording Mode"
						info="Toggle: press the key once to start and again to stop. Push-to-talk: hold the key while speaking."
					>
						<SegmentedControl
							options={RECORDING_MODE_OPTIONS}
							value={config.recording_mode ?? "toggle"}
							onChange={(v) =>
								updateConfig({ recording_mode: v as "toggle" | "push_to_talk" })
							}
							ariaLabel="Recording Mode"
						/>
					</SettingRow>

					<SettingRow
						label="Auto-Stop"
						info="Automatically stop recording after this many seconds of silence."
					>
						<Select
							value={String(config.silence_auto_stop_seconds ?? 60)}
							onValueChange={(v) =>
								updateConfig({ silence_auto_stop_seconds: Number(v) })
							}
						>
							<SelectTrigger className="w-36" aria-label="Auto-Stop">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{AUTO_STOP_OPTIONS.map((opt) => (
									<SelectItem key={opt.value} value={String(opt.value)}>
										{opt.label}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>

					{/* ── Switches ───────────────────────────────────────── */}
					<SettingRow
						label="ESC to Cancel"
						info="Press Escape to cancel an active recording."
					>
						<Switch
							checked={config.esc_cancel_enabled ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ esc_cancel_enabled: checked })
							}
							aria-label="ESC to Cancel"
						/>
					</SettingRow>

					<SettingRow
						label="Auto-Paste"
						info="Automatically paste transcribed text into the currently focused field."
					>
						<Switch
							checked={config.paste_on_stop}
							onCheckedChange={(checked) =>
								updateConfig({ paste_on_stop: checked })
							}
							aria-label="Auto-Paste"
						/>
					</SettingRow>

					{/* NEW-UX-029: Audio cue on record start/stop for accessibility
                                        and confirmation.  Especially useful for blind users who
                                        can't see the visual indicator change. */}
					<SettingRow
						label="Sound Feedback"
						info="Play a short audio cue when recording starts and stops. Useful for accessibility and confirmation."
					>
						<Switch
							checked={config.sound_feedback_enabled ?? true}
							onCheckedChange={(checked) => {
								updateConfig({ sound_feedback_enabled: checked });
								// SOUND-FIX-REWRITE: use the centralized
								// setSoundFeedbackEnabled helper from
								// @/lib/sound-manager. This keeps the
								// localStorage flag in sync with config
								// and is the same path used by App.tsx's
								// config-load sync.
								setSoundFeedbackEnabled(checked);
							}}
							aria-label="Sound Feedback"
						/>
					</SettingRow>

					{/* ── Inputs ─────────────────────────────────────────── */}
					<SettingRow
						label="Re-Paste Key"
						info="Keyboard shortcut to re-paste the last transcription. Click the button to record a new combo, or pick from the preset list."
					>
						<HotkeyPicker
							value={config.repaste_hotkey ?? "<ctrl>+<alt>+v"}
							onChange={handleRepasteChange}
							mode="combo"
							aria-label="Re-paste key"
							onCaptureStart={handleRepasteCaptureStart}
							onCaptureEnd={handleRepasteCaptureEnd}
						/>
					</SettingRow>

					<SettingRow
						label="Silence Warning"
						info="Seconds of silence before showing a warning to help catch microphone issues."
					>
						<div className="flex items-center gap-2">
							<NumberInput
								min={3}
								max={30}
								step={1}
								value={String(config.silence_warning_seconds)}
								onChange={(e) =>
									updateConfigDebounced(
										"silence_warning_seconds",
										Number(e.target.value),
									)
								}
								className="w-20 text-center"
								aria-label="Silence Warning Seconds"
							/>
							<span className="text-sm text-(--text-muted)">sec</span>
						</div>
					</SettingRow>

					<SettingRow
						label="Max Duration"
						info="Maximum recording length. Set to 0 for automatic (varies by device)."
					>
						<div className="flex items-center gap-2">
							<NumberInput
								min={0}
								max={7200}
								step={1}
								value={String(config.max_recording_seconds)}
								onChange={(e) =>
									updateConfigDebounced(
										"max_recording_seconds",
										Number(e.target.value),
									)
								}
								className="w-20 text-center"
								aria-label="Max Recording Duration"
							/>
							<span className="text-sm text-(--text-muted)">sec</span>
						</div>
					</SettingRow>

					{/* AUDIO-DEAD: dead-air timeout — auto-stop after silence follows speech */}
					<SettingRow
						label="Dead-Air Timeout"
						info="Seconds of silence after speech is detected before auto-stopping. 0 = disabled (never auto-stop on silence)."
					>
						<div className="flex items-center gap-2">
							<NumberInput
								min={0}
								max={600}
								step={5}
								value={String(config.dead_air_timeout ?? 30)}
								onChange={(e) =>
									updateConfigDebounced(
										"dead_air_timeout",
										Number(e.target.value),
									)
								}
								className="w-20 text-center"
								aria-label="Dead-Air Timeout Seconds"
							/>
							<span className="text-sm text-(--text-muted)">sec</span>
						</div>
					</SettingRow>
				</SettingsSection>
			)}
		</>
	);
});
