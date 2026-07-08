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
import { RangeSlider } from "@/components/RangeSlider";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { usePython } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
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

	// UX-028: section-level visibility check for the Audio Enhancement section.
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
	const handleHighPassToggle = (checked: boolean) =>
		updateConfig({ noise_filter_highpass: checked });
	const handleCutoffChange = (v: number) =>
		updateConfigDebounced("noise_filter_highpass_cutoff_hz", v);
	const handleNoiseMethodChange = (v: string) =>
		updateConfig({
			noise_suppression_method:
				v as VoiceTyperConfig["noise_suppression_method"],
		});
	const handleGateToggle = (checked: boolean) =>
		updateConfig({ noise_filter_gate: checked });
	const handleGateOpenChange = (v: number) =>
		updateConfigDebounced("noise_filter_gate_open_threshold_db", v);
	const handleGateCloseChange = (v: number) =>
		updateConfigDebounced("noise_filter_gate_close_threshold_db", v);
	const handleEqToggle = (checked: boolean) =>
		updateConfig({ noise_filter_eq: checked });
	const handleEqLowChange = (v: number) =>
		updateConfigDebounced("noise_filter_eq_low_db", v);
	const handleEqMidChange = (v: number) =>
		updateConfigDebounced("noise_filter_eq_mid_db", v);
	const handleEqHighChange = (v: number) =>
		updateConfigDebounced("noise_filter_eq_high_db", v);
	const handleCompressorToggle = (checked: boolean) =>
		updateConfig({ noise_filter_compressor: checked });
	const handleCompressorThresholdChange = (v: number) =>
		updateConfigDebounced("noise_filter_compressor_threshold_db", v);
	const handleCompressorRatioChange = (v: number) =>
		updateConfigDebounced("noise_filter_compressor_ratio", v);
	const handleLimiterToggle = (checked: boolean) =>
		updateConfig({ noise_filter_limiter: checked });
	const handleLimiterCeilingChange = (v: number) =>
		updateConfigDebounced("noise_filter_limiter_ceiling_db", v);
	const handleNotchToggle = (checked: boolean) =>
		updateConfig({ noise_filter_notch: checked });

	return (
		<SettingsSection
			title={audioSectionTitle}
			description={t("settings.audioEnhancement.description")}
		>
			<div className="animate-fade-in space-y-0 divide-y divide-border">
				{/* ── Volume Backend status ── */}
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

				{/* ── Auto Duck Volume ── */}
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
					/>
				</SettingRow>

				{/* ── ADR 0007: Audio Preset ── */}
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
							aria-label={t("settings.audioEnhancement.microphoneQualityAria")}
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

				{/* ── ADR 0007: Custom filter controls (only when preset === 'custom') ── */}
				{config.audio_preset === "custom" && (
					<>
						<SettingRow
							label={highPassFilterLabel}
							info={t("settings.audioEnhancement.highPassFilterInfo")}
						>
							<Switch
								checked={config.noise_filter_highpass ?? true}
								onCheckedChange={handleHighPassToggle}
								aria-label={t("settings.audioEnhancement.highPassFilterAria")}
							/>
						</SettingRow>
						{(config.noise_filter_highpass ?? true) && (
							<SettingRow
								label={highPassCutoffLabel}
								info={t("settings.audioEnhancement.highPassCutoffInfo")}
							>
								<RangeSlider
									value={config.noise_filter_highpass_cutoff_hz ?? 80}
									min={20}
									max={500}
									step={10}
									onChange={handleCutoffChange}
									ariaLabel={t("settings.audioEnhancement.highPassCutoffAria")}
									suffix="Hz"
								/>
							</SettingRow>
						)}
						<SettingRow
							label={noiseSuppressionLabel}
							info={t("settings.audioEnhancement.noiseSuppressionInfo")}
						>
							<Select
								value={config.noise_suppression_method ?? "rnnoise"}
								onValueChange={handleNoiseMethodChange}
							>
								<SelectTrigger
									className="w-40"
									aria-label={t(
										"settings.audioEnhancement.noiseSuppressionAria",
									)}
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="rnnoise">RNNoise</SelectItem>
									<SelectItem value="deepfilternet">DeepFilterNet</SelectItem>
									<SelectItem value="speex">Speex</SelectItem>
									<SelectItem value="none">
										{t("settings.audioEnhancement.noneOption")}
									</SelectItem>
								</SelectContent>
							</Select>
						</SettingRow>
						<SettingRow
							label={noiseGateLabel}
							info={t("settings.audioEnhancement.noiseGateInfo")}
						>
							<Switch
								checked={config.noise_filter_gate ?? true}
								onCheckedChange={handleGateToggle}
								aria-label={t("settings.audioEnhancement.noiseGateAria")}
							/>
						</SettingRow>
						{(config.noise_filter_gate ?? true) && (
							<>
								<SettingRow
									label={gateOpenThresholdLabel}
									info={t("settings.audioEnhancement.gateOpenThresholdInfo")}
								>
									<RangeSlider
										value={config.noise_filter_gate_open_threshold_db ?? -26}
										min={-96}
										max={0}
										step={1}
										onChange={handleGateOpenChange}
										ariaLabel={t(
											"settings.audioEnhancement.gateOpenThresholdAria",
										)}
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label={gateCloseThresholdLabel}
									info={t("settings.audioEnhancement.gateCloseThresholdInfo")}
								>
									<RangeSlider
										value={config.noise_filter_gate_close_threshold_db ?? -32}
										min={-96}
										max={0}
										step={1}
										onChange={handleGateCloseChange}
										ariaLabel={t(
											"settings.audioEnhancement.gateCloseThresholdAria",
										)}
										suffix="dB"
									/>
								</SettingRow>
							</>
						)}
						<SettingRow
							label={equalizerLabel}
							info={t("settings.audioEnhancement.equalizerInfo")}
						>
							<Switch
								checked={config.noise_filter_eq ?? true}
								onCheckedChange={handleEqToggle}
								aria-label={t("settings.audioEnhancement.equalizerAria")}
							/>
						</SettingRow>
						{(config.noise_filter_eq ?? true) && (
							<>
								<SettingRow
									label={eqLowLabel}
									info={t("settings.audioEnhancement.eqLowInfo")}
								>
									<RangeSlider
										value={config.noise_filter_eq_low_db ?? -3}
										min={-20}
										max={20}
										step={1}
										onChange={handleEqLowChange}
										ariaLabel={t("settings.audioEnhancement.eqLowAria")}
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label={eqMidLabel}
									info={t("settings.audioEnhancement.eqMidInfo")}
								>
									<RangeSlider
										value={config.noise_filter_eq_mid_db ?? 3}
										min={-20}
										max={20}
										step={1}
										onChange={handleEqMidChange}
										ariaLabel={t("settings.audioEnhancement.eqMidAria")}
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label={eqHighLabel}
									info={t("settings.audioEnhancement.eqHighInfo")}
								>
									<RangeSlider
										value={config.noise_filter_eq_high_db ?? 2}
										min={-20}
										max={20}
										step={1}
										onChange={handleEqHighChange}
										ariaLabel={t("settings.audioEnhancement.eqHighAria")}
										suffix="dB"
									/>
								</SettingRow>
							</>
						)}
						<SettingRow
							label={compressorLabel}
							info={t("settings.audioEnhancement.compressorInfo")}
						>
							<Switch
								checked={config.noise_filter_compressor ?? true}
								onCheckedChange={handleCompressorToggle}
								aria-label={t("settings.audioEnhancement.compressorAria")}
							/>
						</SettingRow>
						{(config.noise_filter_compressor ?? true) && (
							<>
								<SettingRow
									label={compressorThresholdLabel}
									info={t("settings.audioEnhancement.compressorThresholdInfo")}
								>
									<RangeSlider
										value={config.noise_filter_compressor_threshold_db ?? -18}
										min={-60}
										max={0}
										step={1}
										onChange={handleCompressorThresholdChange}
										ariaLabel={t(
											"settings.audioEnhancement.compressorThresholdAria",
										)}
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label={compressorRatioLabel}
									info={t("settings.audioEnhancement.compressorRatioInfo")}
								>
									<RangeSlider
										value={config.noise_filter_compressor_ratio ?? 3}
										min={1}
										max={32}
										step={0.5}
										onChange={handleCompressorRatioChange}
										ariaLabel={t(
											"settings.audioEnhancement.compressorRatioAria",
										)}
										suffix=":1"
									/>
								</SettingRow>
							</>
						)}
						<SettingRow
							label={limiterLabel}
							info={t("settings.audioEnhancement.limiterInfo")}
						>
							<Switch
								checked={config.noise_filter_limiter ?? true}
								onCheckedChange={handleLimiterToggle}
								aria-label={t("settings.audioEnhancement.limiterAria")}
							/>
						</SettingRow>
						{(config.noise_filter_limiter ?? true) && (
							<SettingRow
								label={limiterCeilingLabel}
								info={t("settings.audioEnhancement.limiterCeilingInfo")}
							>
								<RangeSlider
									value={config.noise_filter_limiter_ceiling_db ?? -6}
									min={-60}
									max={0}
									step={1}
									onChange={handleLimiterCeilingChange}
									ariaLabel={t("settings.audioEnhancement.limiterCeilingAria")}
									suffix="dB"
								/>
							</SettingRow>
						)}
						<SettingRow
							label={notchFilterLabel}
							info={t("settings.audioEnhancement.notchFilterInfo")}
						>
							<Switch
								checked={config.noise_filter_notch ?? false}
								onCheckedChange={handleNotchToggle}
								aria-label={t("settings.audioEnhancement.notchFilterAria")}
							/>
						</SettingRow>
					</>
				)}
			</div>
		</SettingsSection>
	);
});
