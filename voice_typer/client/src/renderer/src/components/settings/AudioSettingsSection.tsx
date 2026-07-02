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
import type { VoiceTyperConfig } from "@/types/config";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

export const AudioSettingsSection = memo(function AudioSettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
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

	return (
		<SettingsSection
			title="Audio Enhancement"
			description="Volume ducking and noise filtering for cleaner dictation."
		>
			<div className="animate-fade-in space-y-0 divide-y divide-border">
				{/* ── Volume Backend status ── */}
				<SettingRow
					label="Volume Backend"
					info="The active audio control backend. 'disabled' means ducking won't work on this platform — install the platform's optional dependency (pycaw on Windows, pyobjc on macOS)."
				>
					<span className="text-sm text-(--text-muted) tabular-nums">
						{volumeBackend
							? volumeBackend.available
								? volumeBackend.name
								: `${volumeBackend.name} (unavailable)`
							: "Detecting…"}
					</span>
				</SettingRow>

				{/* ── Auto Duck Volume ── */}
				<SettingRow
					label="Auto Duck Volume"
					info="Reduce system volume during dictation to prevent speaker bleed into the mic. Smart Duck is built-in: if no audio is playing, the volume won't change. Cross-platform — works on Windows, macOS, and Linux."
				>
					<Switch
						checked={config.volume_duck_enabled ?? true}
						onCheckedChange={(checked) =>
							updateConfig({ volume_duck_enabled: checked })
						}
						aria-label="Auto Duck Volume"
					/>
				</SettingRow>
				<SettingRow
					label="Duck Level"
					info="How quiet to make system audio. 25% = whisper-quiet, 50% = slight dip."
				>
					<RangeSlider
						value={config.volume_duck_level ?? 0.2}
						min={0}
						max={0.5}
						step={0.05}
						onChange={(v) => updateConfigDebounced("volume_duck_level", v)}
						ariaLabel="Duck Level"
						suffix="%"
					/>
				</SettingRow>

				{/* ── ADR 0007: Audio Preset ── */}
				<SettingRow
					label="Microphone Quality"
					info="Presets configure the entire filter chain for common scenarios. Choose 'Custom' for advanced control of individual filters."
				>
					<Select
						value={config.audio_preset ?? "auto"}
						onValueChange={(v) =>
							updateConfig({
								audio_preset: v as VoiceTyperConfig["audio_preset"],
							})
						}
					>
						<SelectTrigger
							className="w-48"
							aria-label="Microphone Quality Preset"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="auto">Auto (recommended)</SelectItem>
							<SelectItem value="studio">Studio (clean environment)</SelectItem>
							<SelectItem value="noisy_room">
								Noisy Room (keyboard/fan/HVAC)
							</SelectItem>
							<SelectItem value="off">Off (raw audio)</SelectItem>
							<SelectItem value="custom">Custom (advanced)</SelectItem>
						</SelectContent>
					</Select>
				</SettingRow>

				{/* ── ADR 0007: Custom filter controls (only when preset === 'custom') ── */}
				{config.audio_preset === "custom" && (
					<>
						<SettingRow
							label="High-Pass Filter"
							info="Remove low-frequency rumble (HVAC, traffic) below the cutoff frequency."
						>
							<Switch
								checked={config.noise_filter_highpass ?? true}
								onCheckedChange={(checked) =>
									updateConfig({ noise_filter_highpass: checked })
								}
								aria-label="High-Pass Filter"
							/>
						</SettingRow>
						{(config.noise_filter_highpass ?? true) && (
							<SettingRow
								label="High-Pass Cutoff"
								info="Frequencies below this are attenuated. 80Hz removes HVAC rumble. 100–150Hz also removes traffic."
							>
								<RangeSlider
									value={config.noise_filter_highpass_cutoff_hz ?? 80}
									min={20}
									max={500}
									step={10}
									onChange={(v) =>
										updateConfigDebounced("noise_filter_highpass_cutoff_hz", v)
									}
									ariaLabel="High-Pass Cutoff"
									suffix="Hz"
								/>
							</SettingRow>
						)}
						<SettingRow
							label="Noise Suppression"
							info="Neural network denoiser. RNNoise (default, lightweight). DeepFilterNet (premium, better quality, requires torch). Speex (lightest CPU)."
						>
							<Select
								value={config.noise_suppression_method ?? "rnnoise"}
								onValueChange={(v) =>
									updateConfig({
										noise_suppression_method:
											v as VoiceTyperConfig["noise_suppression_method"],
									})
								}
							>
								<SelectTrigger
									className="w-40"
									aria-label="Noise Suppression Method"
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="rnnoise">RNNoise</SelectItem>
									<SelectItem value="deepfilternet">DeepFilterNet</SelectItem>
									<SelectItem value="speex">Speex</SelectItem>
									<SelectItem value="none">None</SelectItem>
								</SelectContent>
							</Select>
						</SettingRow>
						<SettingRow
							label="Noise Gate"
							info="Silence audio below a threshold to remove idle hiss. Uses OBS-style open/close thresholds with attack/hold/release."
						>
							<Switch
								checked={config.noise_filter_gate ?? true}
								onCheckedChange={(checked) =>
									updateConfig({ noise_filter_gate: checked })
								}
								aria-label="Noise Gate"
							/>
						</SettingRow>
						{(config.noise_filter_gate ?? true) && (
							<>
								<SettingRow
									label="Gate Open Threshold"
									info="Level above which the gate opens (passes audio). -26dB is a good default for speech."
								>
									<RangeSlider
										value={config.noise_filter_gate_open_threshold_db ?? -26}
										min={-96}
										max={0}
										step={1}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_gate_open_threshold_db",
												v,
											)
										}
										ariaLabel="Gate Open Threshold"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="Gate Close Threshold"
									info="Level below which the gate closes (attenuates audio). Should be 5-10dB below open threshold."
								>
									<RangeSlider
										value={config.noise_filter_gate_close_threshold_db ?? -32}
										min={-96}
										max={0}
										step={1}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_gate_close_threshold_db",
												v,
											)
										}
										ariaLabel="Gate Close Threshold"
										suffix="dB"
									/>
								</SettingRow>
							</>
						)}
						<SettingRow
							label="Equalizer"
							info="3-band EQ: boost mid (speech intelligibility), cut low (rumble), slight high (presence). OBS-style crossover."
						>
							<Switch
								checked={config.noise_filter_eq ?? true}
								onCheckedChange={(checked) =>
									updateConfig({ noise_filter_eq: checked })
								}
								aria-label="Equalizer"
							/>
						</SettingRow>
						{(config.noise_filter_eq ?? true) && (
							<>
								<SettingRow
									label="EQ — Low (bass)"
									info="Boost/cut below 800Hz. -3dB removes rumble and proximity effect."
								>
									<RangeSlider
										value={config.noise_filter_eq_low_db ?? -3}
										min={-20}
										max={20}
										step={1}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_eq_low_db", v)
										}
										ariaLabel="EQ Low"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="EQ — Mid (speech)"
									info="Boost/cut 800Hz–5kHz (speech intelligibility band). +3dB improves consonant clarity."
								>
									<RangeSlider
										value={config.noise_filter_eq_mid_db ?? 3}
										min={-20}
										max={20}
										step={1}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_eq_mid_db", v)
										}
										ariaLabel="EQ Mid"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="EQ — High (treble)"
									info="Boost/cut above 5kHz. +2dB adds presence and brightness."
								>
									<RangeSlider
										value={config.noise_filter_eq_high_db ?? 2}
										min={-20}
										max={20}
										step={1}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_eq_high_db", v)
										}
										ariaLabel="EQ High"
										suffix="dB"
									/>
								</SettingRow>
							</>
						)}
						<SettingRow
							label="Compressor"
							info="Evens out loud/quiet speech for consistent ASR accuracy. OBS-style peak envelope with threshold/ratio/attack/release."
						>
							<Switch
								checked={config.noise_filter_compressor ?? true}
								onCheckedChange={(checked) =>
									updateConfig({ noise_filter_compressor: checked })
								}
								aria-label="Compressor"
							/>
						</SettingRow>
						{(config.noise_filter_compressor ?? true) && (
							<>
								<SettingRow
									label="Compressor Threshold"
									info="Level above which compression starts. -18dB is a good default for speech."
								>
									<RangeSlider
										value={config.noise_filter_compressor_threshold_db ?? -18}
										min={-60}
										max={0}
										step={1}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_compressor_threshold_db",
												v,
											)
										}
										ariaLabel="Compressor Threshold"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="Compressor Ratio"
									info="How hard to compress. 3:1 is gentle. 10:1 is aggressive (limiter-like)."
								>
									<RangeSlider
										value={config.noise_filter_compressor_ratio ?? 3}
										min={1}
										max={32}
										step={0.5}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_compressor_ratio", v)
										}
										ariaLabel="Compressor Ratio"
										suffix=":1"
									/>
								</SettingRow>
							</>
						)}
						<SettingRow
							label="Limiter"
							info="Brick-wall ceiling to prevent clipping. Catches transient clicks/pops before they reach ASR."
						>
							<Switch
								checked={config.noise_filter_limiter ?? true}
								onCheckedChange={(checked) =>
									updateConfig({ noise_filter_limiter: checked })
								}
								aria-label="Limiter"
							/>
						</SettingRow>
						{(config.noise_filter_limiter ?? true) && (
							<SettingRow
								label="Limiter Ceiling"
								info="Absolute maximum output level. -6dB prevents clipping while allowing headroom."
							>
								<RangeSlider
									value={config.noise_filter_limiter_ceiling_db ?? -6}
									min={-60}
									max={0}
									step={1}
									onChange={(v) =>
										updateConfigDebounced("noise_filter_limiter_ceiling_db", v)
									}
									ariaLabel="Limiter Ceiling"
									suffix="dB"
								/>
							</SettingRow>
						)}
						<SettingRow
							label="Notch Filter (hum)"
							info="Remove 50/60Hz electrical mains hum. Off by default — only enable if you hear a persistent low buzz."
						>
							<Switch
								checked={config.noise_filter_notch ?? false}
								onCheckedChange={(checked) =>
									updateConfig({ noise_filter_notch: checked })
								}
								aria-label="Notch Filter"
							/>
						</SettingRow>
					</>
				)}
			</div>
		</SettingsSection>
	);
});
