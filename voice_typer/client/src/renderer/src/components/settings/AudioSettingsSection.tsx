// AudioSettingsSection, Audio Enhancement section of the Settings page.
// "Audio Enhancement" SettingsSection: Microphone Quality (enable
// Switch + audio preset Select), Voice activity filtering, Volume
// Backend status, Auto Duck Volume, Duck Level, the custom filter
// chain (High-Pass, Noise Suppression, Noise Gate, Equalizer,
// Compressor, Limiter, Notch Filter), and a "Test microphone" row
// linking to the Microphone page. Behaviour is identical to the
// status fetch (now done via this section's own `usePython` call so the
// parent doesn't need to know about it).

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { AudioFilterChain } from "@/components/audio/AudioFilterChain";
import { RangeSlider } from "@/components/common/RangeSlider";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useLatestRef } from "@/hooks/useLatestRef";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython } from "@/hooks/usePython";
import { useT } from "@/i18n/i18n";
import {
	AUDIO_PRESET_OPTIONS,
	type AudioPreset,
} from "@/lib/utils/audioPresets";
import type { LausuConfig } from "@/types/config";
import { SettingsSkeleton } from "./SettingsSkeleton";
import type { SettingsSectionSharedProps } from "./types";

export const AudioSettingsSection = memo(function AudioSettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
	isVisible,
}: SettingsSectionSharedProps) {
	const { call } = usePython();
	// The same `audio_preset` (and the entire custom filter chain) is
	// also editable on the Microphone page via its preset accordion
	// (PresetAccordionSelector), both surfaces draw their option
	// values/labels from the shared `lib/utils/audioPresets.ts` registry.
	// The Microphone page additionally offers a test-record A/B workflow
	// (record a sample, swap preset, re-record, compare) that this
	// Settings surface does not. The "Test microphone" row at the
	// bottom of the card links there so the user can reach it without
	// discovering the duplicate surface by accident.
	const { navigate } = useNavigation();

	// Stable callback for the cross-link, moved above the
	// early-return so hooks are always called in the same order.
	const handleGoToMicrophone = useCallback(() => {
		navigate("microphone");
	}, [navigate]);

	// Volume backend status, fetched from the Python backend so the UI
	// can show "Volume Backend: pycaw (WASAPI)" / "CoreAudio" / "disabled"
	// and disable the Per-Session Duck toggle on platforms that don't
	// support it (macOS, Linux).  See architecture doc §7.9.
	const [volumeBackend, setVolumeBackend] = useState<{
		available: boolean;
		name: string;
		supports_per_session: boolean;
		is_windows: boolean;
	} | null>(null);

	// callRef mirror (Home.tsx pattern): the mount effect below must not
	// depend on the `call` identity, stable in production, but a test
	// mock handing out a fresh `call` per render would re-fire the load
	// effect every render (get_volume_backend_status → setState →
	// re-render → new call → loop → worker OOM). The mirror keeps the
	// ref fresh; the effect deps stay identity-free.
	const callRef = useLatestRef(call);

	// Best-effort: if the call fails we leave `volumeBackend` as null and
	// the toggle stays enabled-but-server-validated (the Python side also
	// gates on `supports_per_session`).
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const loadVolumeBackend = useCallback(async () => {
		try {
			const result = await callRef.current<{
				available: boolean;
				name: string;
				supports_per_session: boolean;
				is_windows: boolean;
			}>("get_volume_backend_status");
			setVolumeBackend(result);
		} catch (err) {
			console.warn(
				"[renderer:AudioSettingsSection] Failed to load volume backend status:",
				err,
			);
		}
	}, []);

	useEffect(() => {
		loadVolumeBackend();
	}, [loadVolumeBackend]);

	// Remembers the last non-"off" preset so the Microphone Quality
	// enable-Switch can restore it. Synced from the live config (which
	// the Microphone page can also change), so flipping the Switch
	// back on never resets the user to "auto" against their choice.
	const lastNonOffPresetRef = useRef<AudioPreset>("auto");
	useEffect(() => {
		if (config?.audio_preset && config.audio_preset !== "off") {
			lastNonOffPresetRef.current = config.audio_preset;
		}
	}, [config?.audio_preset]);

	const t = useT();

	if (!config) return <SettingsSkeleton rows={3} />;

	// IMPL-C: resolve the translated search-visible labels once per render so
	// the section-level isVisible check and the rendered SettingRow labels
	// share the same strings.
	const volumeBackendLabel = t("settings.audioEnhancement.volumeBackend");
	const volumeBackendInfoSearch = t(
		"settings.audioEnhancement.volumeBackendInfoSearch",
	);
	const vadFilterLabel = t("settings.audioEnhancement.vadFilter");
	const vadFilterInfoSearch = t(
		"settings.audioEnhancement.vadFilterInfoSearch",
	);
	const autoDuckVolumeLabel = t("settings.audioEnhancement.autoDuckVolume");
	const autoDuckVolumeInfoSearch = t(
		"settings.audioEnhancement.autoDuckVolumeInfoSearch",
	);
	const duckLevelLabel = t("settings.audioEnhancement.duckLevel");
	const duckLevelInfoSearch = t(
		"settings.audioEnhancement.duckLevelInfoSearch",
	);
	const microphoneQualityLabel = t(
		"settings.audioEnhancement.microphoneQuality",
	);
	const microphoneQualityInfoSearch = t(
		"settings.audioEnhancement.microphoneQualityInfoSearch",
	);
	const qualityPresetLabel = t("settings.audioEnhancement.qualityPreset");
	const testMicrophoneLabel = t("settings.audioEnhancement.testMicrophone");
	const testMicrophoneInfo = t("settings.audioEnhancement.testMicrophoneInfo");
	const highPassFilterLabel = t("settings.audioEnhancement.highPassFilter");
	const highPassFilterInfoSearch = t(
		"settings.audioEnhancement.highPassFilterInfoSearch",
	);
	const highPassCutoffLabel = t("settings.audioEnhancement.highPassCutoff");
	const highPassCutoffInfoSearch = t(
		"settings.audioEnhancement.highPassCutoffInfoSearch",
	);
	const noiseSuppressionLabel = t("settings.audioEnhancement.noiseSuppression");
	const noiseSuppressionInfoSearch = t(
		"settings.audioEnhancement.noiseSuppressionInfoSearch",
	);
	const noiseGateLabel = t("settings.audioEnhancement.noiseGate");
	const noiseGateInfoSearch = t(
		"settings.audioEnhancement.noiseGateInfoSearch",
	);
	const gateOpenThresholdLabel = t(
		"settings.audioEnhancement.gateOpenThreshold",
	);
	const gateOpenThresholdInfoSearch = t(
		"settings.audioEnhancement.gateOpenThresholdInfoSearch",
	);
	const gateCloseThresholdLabel = t(
		"settings.audioEnhancement.gateCloseThreshold",
	);
	const gateCloseThresholdInfoSearch = t(
		"settings.audioEnhancement.gateCloseThresholdInfoSearch",
	);
	const equalizerLabel = t("settings.audioEnhancement.equalizer");
	const equalizerInfoSearch = t(
		"settings.audioEnhancement.equalizerInfoSearch",
	);
	const eqLowLabel = t("settings.audioEnhancement.eqLow");
	const eqLowInfoSearch = t("settings.audioEnhancement.eqLowInfoSearch");
	const eqMidLabel = t("settings.audioEnhancement.eqMid");
	const eqMidInfoSearch = t("settings.audioEnhancement.eqMidInfoSearch");
	const eqHighLabel = t("settings.audioEnhancement.eqHigh");
	const eqHighInfoSearch = t("settings.audioEnhancement.eqHighInfoSearch");
	const compressorLabel = t("settings.audioEnhancement.compressor");
	const compressorInfoSearch = t(
		"settings.audioEnhancement.compressorInfoSearch",
	);
	const compressorThresholdLabel = t(
		"settings.audioEnhancement.compressorThreshold",
	);
	const compressorThresholdInfoSearch = t(
		"settings.audioEnhancement.compressorThresholdInfoSearch",
	);
	const compressorRatioLabel = t("settings.audioEnhancement.compressorRatio");
	const compressorRatioInfoSearch = t(
		"settings.audioEnhancement.compressorRatioInfoSearch",
	);
	const limiterLabel = t("settings.audioEnhancement.limiter");
	const limiterInfoSearch = t("settings.audioEnhancement.limiterInfoSearch");
	const limiterCeilingLabel = t("settings.audioEnhancement.limiterCeiling");
	const limiterCeilingInfoSearch = t(
		"settings.audioEnhancement.limiterCeilingInfoSearch",
	);
	const notchFilterLabel = t("settings.audioEnhancement.notchFilter");
	const notchFilterInfoSearch = t(
		"settings.audioEnhancement.notchFilterInfoSearch",
	);

	//section-level visibility check for the Audio Enhancement section.
	const audioSectionTitle = t("settings.audioEnhancement.title");
	const sectionItems = [
		{ label: microphoneQualityLabel, info: microphoneQualityInfoSearch },
		{ label: qualityPresetLabel, info: microphoneQualityInfoSearch },
		{ label: vadFilterLabel, info: vadFilterInfoSearch },
		{ label: volumeBackendLabel, info: volumeBackendInfoSearch },
		{ label: autoDuckVolumeLabel, info: autoDuckVolumeInfoSearch },
		{ label: duckLevelLabel, info: duckLevelInfoSearch },
		{ label: highPassFilterLabel, info: highPassFilterInfoSearch },
		{ label: highPassCutoffLabel, info: highPassCutoffInfoSearch },
		{ label: noiseSuppressionLabel, info: noiseSuppressionInfoSearch },
		{ label: noiseGateLabel, info: noiseGateInfoSearch },
		{ label: gateOpenThresholdLabel, info: gateOpenThresholdInfoSearch },
		{ label: gateCloseThresholdLabel, info: gateCloseThresholdInfoSearch },
		{ label: equalizerLabel, info: equalizerInfoSearch },
		{ label: eqLowLabel, info: eqLowInfoSearch },
		{ label: eqMidLabel, info: eqMidInfoSearch },
		{ label: eqHighLabel, info: eqHighInfoSearch },
		{ label: compressorLabel, info: compressorInfoSearch },
		{ label: compressorThresholdLabel, info: compressorThresholdInfoSearch },
		{ label: compressorRatioLabel, info: compressorRatioInfoSearch },
		{ label: limiterLabel, info: limiterInfoSearch },
		{ label: limiterCeilingLabel, info: limiterCeilingInfoSearch },
		{ label: notchFilterLabel, info: notchFilterInfoSearch },
		{ label: testMicrophoneLabel, info: testMicrophoneInfo },
	];
	if (
		!sectionItems.some((item) =>
			isVisible(item.label, item.info, audioSectionTitle),
		)
	) {
		return null;
	}

	// ── Inline handler extraction ─────────────────────────────────
	const handleVadFilterChange = (checked: boolean) =>
		updateConfig({ vad_filter_enabled: checked });
	const handleAutoDuckChange = (checked: boolean) =>
		updateConfig({ volume_duck_enabled: checked });
	const handleDuckLevelChange = (v: number) =>
		updateConfigDebounced("volume_duck_level", v);
	const handleAudioPresetChange = (v: string) =>
		updateConfig({ audio_preset: v as LausuConfig["audio_preset"] });
	// Microphone Quality enable-Switch: "off" lives ONLY behind this
	// Switch, never in the preset Select below. Turning off stashes the
	// current preset in `lastNonOffPresetRef`; turning on restores it so
	// the user's choice (studio / noisy_room / custom…) survives the
	// round-trip instead of resetting to "auto".
	const handleQualityEnabledChange = (checked: boolean) => {
		if (checked) {
			updateConfig({ audio_preset: lastNonOffPresetRef.current });
			return;
		}
		const current = config.audio_preset ?? "auto";
		if (current !== "off") {
			lastNonOffPresetRef.current = current;
		}
		updateConfig({ audio_preset: "off" });
	};
	// "Test microphone" row button label, routed through the i18n
	// layer so it renders in the user's selected UI locale. The key
	// lives under `settings.audioEnhancement.goToMicrophone` in the
	// locale JSON files.
	const goToMicrophoneLabel = t("settings.audioEnhancement.goToMicrophone");

	// The preset currently driving the filter chain ("off" disables
	// it, see the enable-Switch above).
	const activePreset = config.audio_preset ?? "auto";
	const qualityEnabled = activePreset !== "off";
	// Select options exclude "off": disabling is the Switch's job, so
	// the dropdown only offers real presets. While disabled, the Select
	// displays the stashed preset (what enabling will restore).
	const selectablePresets = AUDIO_PRESET_OPTIONS.filter(
		(option) => option.value !== "off",
	);

	return (
		<SettingsSection
			title={audioSectionTitle}
			description={t("settings.audioEnhancement.description")}
		>
			{/*per-row visibility filtering so a search query
                                only highlights the rows whose label/info matches —
                                previously the section-level check showed the entire
                                section (including all rows) when ANY row matched,
                                which defeated the purpose of in-section search. */}
			<div className="animate-fade-in flex flex-col gap-0 divide-y divide-border/5">
				{/* ── ADR 0007: Microphone Quality master Switch (first row) ──
                                    Enabling reveals the preset picker row below;
                                    "off" lives ONLY behind this Switch, never in
                                    the preset dropdown. */}
				{isVisible(
					microphoneQualityLabel,
					microphoneQualityInfoSearch,
					audioSectionTitle,
				) && (
					<SettingRow
						label={microphoneQualityLabel}
						info={t("settings.audioEnhancement.microphoneQualityInfo")}
					>
						<Switch
							checked={qualityEnabled}
							onCheckedChange={handleQualityEnabledChange}
							aria-label={t(
								"settings.audioEnhancement.microphoneQualityEnableAria",
							)}
							data-testid="microphone-quality-switch"
						/>
					</SettingRow>
				)}

				{/* ── ADR 0007: Quality preset picker (revealed while enabled) ── */}
				{qualityEnabled &&
					isVisible(
						qualityPresetLabel,
						microphoneQualityInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={qualityPresetLabel}
							info={t("settings.audioEnhancement.microphoneQualityInfoSearch")}
						>
							<Select
								value={activePreset}
								onValueChange={handleAudioPresetChange}
							>
								<SelectTrigger
									className="w-48"
									aria-label={t(
										"settings.audioEnhancement.microphoneQualityAria",
									)}
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{/* Options come from the shared preset
                                                                        registry (lib/utils/audioPresets.ts), the
                                                                        SAME source the Microphone page's accordion
                                                                        consumes, so the two surfaces can never
                                                                        drift in values or labels. Labels resolve
                                                                        through this component's reactive `t`
                                                                        (useT) so a locale switch re-renders them.
                                                                        "off" is intentionally excluded: disabling
                                                                        is the enable-Switch's job. */}
									{selectablePresets.map((option) => (
										<SelectItem key={option.value} value={option.value}>
											{t(option.labelKey)}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</SettingRow>
					)}

				{/* ── Voice activity filtering ── */}
				{isVisible(vadFilterLabel, vadFilterInfoSearch, audioSectionTitle) && (
					<SettingRow
						label={vadFilterLabel}
						info={t("settings.audioEnhancement.vadFilterInfo")}
					>
						<Switch
							checked={config.vad_filter_enabled ?? true}
							onCheckedChange={handleVadFilterChange}
							aria-label={t("settings.audioEnhancement.vadFilterAria")}
							data-testid="vad-filter-switch"
						/>
					</SettingRow>
				)}

				{/* ── Volume Backend status ── */}
				{isVisible(
					volumeBackendLabel,
					volumeBackendInfoSearch,
					audioSectionTitle,
				) && (
					<SettingRow
						label={volumeBackendLabel}
						info={t("settings.audioEnhancement.volumeBackendInfo")}
					>
						<span className="text-sm text-muted-foreground tabular-nums">
							{volumeBackend
								? volumeBackend.available
									? volumeBackend.name
									: t("settings.audioEnhancement.unavailableSuffix", {
											name: volumeBackend.name,
										})
								: t("settings.audioEnhancement.detecting")}
						</span>
					</SettingRow>
				)}

				{/* ── Auto Duck Volume ── */}
				{isVisible(
					autoDuckVolumeLabel,
					autoDuckVolumeInfoSearch,
					audioSectionTitle,
				) && (
					<SettingRow
						label={autoDuckVolumeLabel}
						info={t("settings.audioEnhancement.autoDuckVolumeInfo")}
					>
						<Switch
							checked={config.volume_duck_enabled ?? true}
							onCheckedChange={handleAutoDuckChange}
							aria-label={t("settings.audioEnhancement.autoDuckVolumeAria")}
						/>
					</SettingRow>
				)}
				{isVisible(duckLevelLabel, duckLevelInfoSearch, audioSectionTitle) && (
					<SettingRow
						label={duckLevelLabel}
						info={t("settings.audioEnhancement.duckLevelInfo")}
					>
						<RangeSlider
							value={config.volume_duck_level ?? 0.2}
							min={0}
							max={0.5}
							step={0.05}
							onChange={handleDuckLevelChange}
							ariaLabel={t("settings.audioEnhancement.duckLevelAria")}
							suffix="%"
							// Disable the Duck Level slider when Auto Duck
							// Volume is off, adjusting the duck level has no effect
							// when ducking is disabled, and a stale value persisted
							// here would silently apply if the user later re-enables
							// ducking.
							disabled={!config.volume_duck_enabled}
						/>
					</SettingRow>
				)}

				{/* ── ADR 0007: Custom filter controls (only when preset === 'custom') ── */}
				{/*F-1: filter chain extracted to shared <AudioFilterChain />.
                                        : the filter chain rows themselves are search-filtered
                                        inside <AudioFilterChain> via its own isVisible checks (it
                                        receives the same `isVisible` prop through `sectionProps`).
                                        Only render the chain wrapper when at least one of its
                                        parent rows (the preset selector) is visible OR the user is
                                        actively searching for a filter name, see AudioFilterChain
                                        implementation. Keep this conditional on preset==="custom" so
                                        the chain never appears for a non-custom preset. */}
				{config.audio_preset === "custom" && (
					<AudioFilterChain config={config} onConfigChange={updateConfig} />
				)}

				{/* ── Test microphone (cross-link row to the Microphone page) ── */}
				{isVisible(
					testMicrophoneLabel,
					testMicrophoneInfo,
					audioSectionTitle,
				) && (
					<SettingRow label={testMicrophoneLabel} info={testMicrophoneInfo}>
						<Button
							type="button"
							variant="outline"
							size="sm"
							onClick={handleGoToMicrophone}
							aria-label={goToMicrophoneLabel}
						>
							{goToMicrophoneLabel}
						</Button>
					</SettingRow>
				)}
			</div>
		</SettingsSection>
	);
});
