// AudioSettingsSection — Audio Enhancement section of the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders the
// "Audio Enhancement" SettingsSection: Volume Backend status, Auto Duck
// Volume, Duck Level, Microphone Quality (audio preset), and the custom
// filter chain (High-Pass, Noise Suppression, Noise Gate, Equalizer,
// Compressor, Limiter, Notch Filter). Behaviour is identical to the
// previous monolithic implementation, including the `volumeBackend`
// status fetch (now done via this section's own `usePython` call so the
// parent doesn't need to know about it).

import { memo, useCallback, useEffect, useState } from "react";
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
import { useNavigation } from "@/hooks/useNavigation";
import { usePython } from "@/hooks/usePython";
import { useT } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
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
	// also editable on the Microphone page via `AudioPresetSelector`.
	// The Microphone page additionally offers a test-record A/B workflow
	// (record a sample, swap preset, re-record, compare) that this
	// Settings surface does not. The cross-link banner below surfaces
	// that to the user so they don't have to discover the duplicate
	// surface by accident.
	const { navigate } = useNavigation();

	// Stable callback for the cross-link — moved above the
	// early-return so hooks are always called in the same order.
	const handleGoToMicrophone = useCallback(() => {
		navigate("microphone");
	}, [navigate]);

	// Volume backend status — fetched from the Python backend so the UI
	// can show "Volume Backend: pycaw (WASAPI)" / "CoreAudio" / "disabled"
	// and disable the Per-Session Duck toggle on platforms that don't
	// support it (macOS, Linux).  See architecture doc §7.9.
	const [volumeBackend, setVolumeBackend] = useState<{
		available: boolean;
		name: string;
		supports_per_session: boolean;
		is_windows: boolean;
	} | null>(null);

	// Best-effort: if the call fails we leave `volumeBackend` as null and
	// the toggle stays enabled-but-server-validated (the Python side also
	// gates on `supports_per_session`).
	const loadVolumeBackend = useCallback(async () => {
		try {
			const result = await call<{
				available: boolean;
				name: string;
				supports_per_session: boolean;
				is_windows: boolean;
			}>("get_volume_backend_status");
			setVolumeBackend(result);
		} catch (err) {
			console.warn("Failed to load volume backend status:", err);
		}
	}, [call]);

	useEffect(() => {
		loadVolumeBackend();
	}, [loadVolumeBackend]);

	const t = useT();

	if (!config) return <SettingsSkeleton rows={3} />;

	// IMPL-C: resolve the translated search-visible labels once per render so
	// the section-level isVisible check and the rendered SettingRow labels
	// share the same strings.
	const volumeBackendLabel = t("settings.audioEnhancement.volumeBackend");
	const volumeBackendInfoSearch = t(
		"settings.audioEnhancement.volumeBackendInfoSearch",
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
		{ label: volumeBackendLabel, info: volumeBackendInfoSearch },
		{ label: autoDuckVolumeLabel, info: autoDuckVolumeInfoSearch },
		{ label: duckLevelLabel, info: duckLevelInfoSearch },
		{ label: microphoneQualityLabel, info: microphoneQualityInfoSearch },
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
	];
	if (
		!sectionItems.some((item) =>
			isVisible(item.label, item.info, audioSectionTitle),
		)
	) {
		return null;
	}

	// ── Inline handler extraction ─────────────────────────────────
	const handleAutoDuckChange = (checked: boolean) =>
		updateConfig({ volume_duck_enabled: checked });
	const handleDuckLevelChange = (v: number) =>
		updateConfigDebounced("volume_duck_level", v);
	const handleAudioPresetChange = (v: string) =>
		updateConfig({ audio_preset: v as VoiceTyperConfig["audio_preset"] });
	// Cross-link banner text + button label are routed through the i18n
	// layer so they render in the user's selected UI locale. The keys live
	// under `settings.audioEnhancement.crossLinkBanner` /
	// `settings.audioEnhancement.goToMicrophone` in the locale JSON files.
	const crossLinkBannerText = t("settings.audioEnhancement.crossLinkBanner");
	const goToMicrophoneLabel = t("settings.audioEnhancement.goToMicrophone");

	return (
		<>
			{/* Cross-link banner: the same audio preset + filter chain is
                                also editable on the Microphone page (with the additional
                                test-record A/B workflow that this Settings surface lacks).
                                Surfacing this here prevents the user from assuming the two
                                surfaces control different things just because they look
                                different. */}
			<div
				role="note"
				className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-(--bg-subtle) px-4 py-3 text-sm text-(--text-primary)"
			>
				<p className="flex-1 min-w-0">{crossLinkBannerText}</p>
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={handleGoToMicrophone}
					aria-label={goToMicrophoneLabel}
				>
					{goToMicrophoneLabel}
				</Button>
			</div>
			<SettingsSection
				title={audioSectionTitle}
				description={t("settings.audioEnhancement.description")}
			>
				{/*per-row visibility filtering so a search query
                                only highlights the rows whose label/info matches —
                                previously the section-level check showed the entire
                                section (including all rows) when ANY row matched,
                                which defeated the purpose of in-section search. */}
				<div className="animate-fade-in space-y-0 divide-y divide-border">
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
							<span className="text-sm text-(--text-muted) tabular-nums">
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
					{isVisible(
						duckLevelLabel,
						duckLevelInfoSearch,
						audioSectionTitle,
					) && (
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
								// Fix #10: disable the Duck Level slider when Auto Duck
								// Volume is off — adjusting the duck level has no effect
								// when ducking is disabled, and a stale value persisted
								// here would silently apply if the user later re-enables
								// ducking.
								disabled={!config.volume_duck_enabled}
							/>
						</SettingRow>
					)}

					{/* ── ADR 0007: Audio Preset ── */}
					{isVisible(
						microphoneQualityLabel,
						microphoneQualityInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={microphoneQualityLabel}
							info={t("settings.audioEnhancement.microphoneQualityInfo")}
						>
							<Select
								value={config.audio_preset ?? "auto"}
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
									<SelectItem value="auto">
										{t("settings.audioEnhancement.presetAuto")}
									</SelectItem>
									<SelectItem value="studio">
										{t("settings.audioEnhancement.presetStudio")}
									</SelectItem>
									<SelectItem value="noisy_room">
										{t("settings.audioEnhancement.presetNoisyRoom")}
									</SelectItem>
									<SelectItem value="off">
										{t("settings.audioEnhancement.presetOff")}
									</SelectItem>
									<SelectItem value="custom">
										{t("settings.audioEnhancement.presetCustom")}
									</SelectItem>
								</SelectContent>
							</Select>
						</SettingRow>
					)}

					{/* ── ADR 0007: Custom filter controls (only when preset === 'custom') ── */}
					{/*F-1: filter chain extracted to shared <AudioFilterChain />.
                                        : the filter chain rows themselves are search-filtered
                                        inside <AudioFilterChain> via its own isVisible checks (it
                                        receives the same `isVisible` prop through `sectionProps`).
                                        Only render the chain wrapper when at least one of its
                                        parent rows (the preset selector) is visible OR the user is
                                        actively searching for a filter name — see AudioFilterChain
                                        implementation. Keep this conditional on preset==="custom" so
                                        the chain never appears for a non-custom preset. */}
					{config.audio_preset === "custom" && (
						<AudioFilterChain config={config} onConfigChange={updateConfig} />
					)}
				</div>
			</SettingsSection>
		</>
	);
});
