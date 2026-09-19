/**
 * Model-page helpers and shared types ( extraction).
 *
 */

import { t } from "@/i18n/i18n";
import {
	formatVram as _formatVram,
	formatWer as _formatWer,
} from "@/lib/format";
import type { VoiceTyperConfig } from "@/types/config";
import type { ModelStatusMap } from "@/types/ipc";

// but `lib/` must never import from `pages/` (inverted layering; this
// The renderer default must match the backend's canonical default
export const MODEL_DEFAULT = "";

export interface ModelInfo {
	name: string;
	size: string;
	speed: string;
	backend: string;
	downloaded: boolean;
	depsOk: boolean;
	isActive: boolean;
	// (`downloaded`). Do NOT re-add an always-available concept without
	/**
	 * Model requires extra system dependencies that
	 * can be installed via a dedicated action (e.g. Parakeet's torch
	 */
	depsInstallable?: boolean;
}

export interface ModelMetadata {
	name: string;
	display_name?: string;
	download_size_mb: number;
	required_vram_mb: number;
	backend: string;
	multilingual: boolean;
	supported_languages: string[] | null; // null = all languages
	description: string;
	repo_id: string;
	is_distilled: boolean;
	speed_rating: string; // "fast" | "medium" | "slow"
	accuracy_rating: string; // "low" | "medium" | "high"
	/**
	 * Published WER (%) on LibriSpeech test-clean (lower is better),
	 * sourced from each model's official model card / evaluation.
	 */
	wer?: number | null;
}

export interface ModelFamily {
	id: string;
	name: string;
	description: string | null;
	variants: ModelInfo[];
}

export const INITIAL_MODELS: ModelInfo[] = [
	{
		name: "tiny",
		size: "75 MB",
		speed: "Fastest",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "large-v3",
		size: "3 GB",
		speed: "Slow",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "large-v3-turbo",
		size: "809 MB",
		speed: "Fast",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "qwen",
		size: "Variable",
		speed: "Fast",
		backend: "qwen",
		downloaded: false,
		// Qwen requires the optional `qwen_asr` pip package (probed by
		depsOk: true,
		isActive: false,
		depsInstallable: true,
	},
	{
		name: "parakeet",
		size: "2.5 GB",
		speed: "Fast",
		backend: "parakeet",
		downloaded: false,
		depsOk: false,
		isActive: false,
		depsInstallable: true,
	},
];

export interface CloudProvider {
	key: "openai" | "groq" | "deepgram";
	url: string;
	model: string;
}

export const CLOUD_PROVIDERS: readonly CloudProvider[] = [
	{
		key: "openai",
		url: "https://api.openai.com/v1/audio/transcriptions",
		model: "whisper-1",
	},
	{
		key: "groq",
		url: "https://api.groq.com/openai/v1/audio/transcriptions",
		model: "whisper-large-v3",
	},
	{
		key: "deepgram",
		url: "https://api.deepgram.com/v1/listen",
		model: "nova-2",
	},
] as const;

export function getProviderLabel(providerKey: string): string {
	switch (providerKey) {
		case "openai":
			return t("models.providers.openai.label");
		case "groq":
			return t("models.providers.groq.label");
		case "deepgram":
			return t("models.providers.deepgram.label");
		default:
			return providerKey;
	}
}

const MODEL_SIZE_NORMALIZER = /^(?:~|≈)?\s*(\d+(?:[.,]\d+)?)\s*([A-Za-z]+)$/;
export function formatModelSize(size: string): string {
	const trimmed = size.trim();
	if (trimmed === "Variable") return t("models.variable");
	const match = MODEL_SIZE_NORMALIZER.exec(trimmed);
	if (!match) return trimmed;
	// optional, hence the explicit guard before formatting).
	const [, number, unit] = match;
	if (number === undefined || unit === undefined) return trimmed;
	return `${number} ${unit.toUpperCase()}`;
}

const SPEED_I18N_MAP: Record<string, string> = {
	Fastest: "models.speed.fastest",
	Fast: "models.speed.fast",
	Medium: "models.speed.medium",
	Slow: "models.speed.slow",
	Variable: "models.speed.variable",
};

/**
 * Capitalize the first letter of a word ("fast" → "Fast"). Defensive:
 * backend catalog entries may omit the field (ERR-1: qwen catalog-only
 */
function capitalizeFirst(value: string | null | undefined): string {
	if (typeof value !== "string") return "";
	return value.length > 0
		? value.charAt(0).toUpperCase() + value.slice(1)
		: value;
}

/**
 * Format a speed rating for display. Accepts both the legacy
 * capitalized source values ("Fast", "Fastest") and the backend
 */
export function formatModelSpeed(speed: string | null | undefined): string {
	if (typeof speed !== "string" || speed.length === 0) return "";
	const key = SPEED_I18N_MAP[speed] ?? SPEED_I18N_MAP[capitalizeFirst(speed)];
	return key ? t(key) : capitalizeFirst(speed);
}

// `voice_typer/client/src`, so there is nothing to wire, the helper
export function formatVram(mb: number): string {
	return _formatVram(mb);
}

/**
 * Re-export of the locale-aware WER percent formatter (see lib/format.ts).
 */
export function formatWer(wer: number): string {
	return _formatWer(wer);
}

/**
 * : format an unknown caught value as a user-friendly string.
 *
 */
export function formatErrorMessage(
	err: unknown,
	fallback: string = t("models.errors.unknown"),
): string {
	if (err instanceof Error) {
		return err.message || fallback;
	}
	if (typeof err === "string") {
		return err || fallback;
	}
	if (err && typeof err === "object") {
		const obj = err as { _error?: unknown; message?: unknown; error?: unknown };
		if (typeof obj._error === "string" && obj._error) return obj._error;
		if (typeof obj.message === "string" && obj.message) return obj.message;
		if (typeof obj.error === "string" && obj.error) return obj.error;
	}
	return fallback;
}

export function formatModelDisplayName(rawId: string): string {
	return rawId
		.split("-")
		.filter((part) => part.length > 0)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");
}

/**
 * Resolve the user-facing display name for a model variant row.
 *
 */
export function getModelVariantDisplayName(
	model: ModelInfo,
	meta?: ModelMetadata | null,
): string {
	if (meta?.display_name) return meta.display_name;
	const formatted = formatModelDisplayName(model.name);
	if (model.backend === "whisper" || model.backend === "distil-whisper") {
		return `Whisper ${formatted}`;
	}
	return formatted;
}

export function groupModelsByFamily(models: ModelInfo[]): ModelFamily[] {
	const whisper = models.filter(
		(m) => m.backend === "whisper" || m.backend === "distil-whisper",
	);
	const qwen = models.filter((m) => m.backend === "qwen");
	const parakeet = models.filter((m) => m.backend === "parakeet");
	const families: ModelFamily[] = [];
	if (whisper.length > 0) {
		families.push({
			id: "whisper",
			name: "OpenAI",
			description: null,
			variants: whisper,
		});
	}
	if (qwen.length > 0) {
		families.push({
			id: "qwen",
			name: "Qwen",
			description: null,
			variants: qwen,
		});
	}
	if (parakeet.length > 0) {
		families.push({
			id: "parakeet",
			name: "Nvidia",
			description: null,
			variants: parakeet,
		});
	}
	return families;
}

/**
 * Returns true if the given model entry matches the active backend/model
 * pair from the config. Whisper is keyed by both `asr_backend === "whisper"`
 */
export function isModelActive(
	m: ModelInfo,
	activeBackend: string,
	activeModel: string,
): boolean {
	if (!activeModel) {
		return false;
	}
	if (m.backend === "whisper") {
		return activeBackend === "whisper" && m.name === activeModel;
	}
	return activeBackend === m.backend;
}

/**
 * Map a config's active backend/model over a models list, returning a
 * new array with `isActive` set on the matching entry (and cleared on
 */
export function applyActiveState(
	models: ModelInfo[],
	cfg: VoiceTyperConfig | null,
): ModelInfo[] {
	if (!cfg) return models;
	const activeBackend = cfg.asr_backend ?? "whisper";
	const activeModel = cfg.model_size ?? MODEL_DEFAULT;
	return models.map((m) => ({
		...m,
		isActive: isModelActive(m, activeBackend, activeModel),
	}));
}

/**
 * Returns the family ID that contains the currently active model,
 * or null if no model is active or no family match is found.
 */
export function getActiveFamilyId(cfg: VoiceTyperConfig | null): string | null {
	if (!cfg) return null;
	const activeBackend = cfg.asr_backend ?? "whisper";
	const activeModel = cfg.model_size ?? MODEL_DEFAULT;
	for (const m of INITIAL_MODELS) {
		if (!isModelActive(m, activeBackend, activeModel)) continue;
		if (m.backend === "whisper" || m.backend === "distil-whisper")
			return "whisper";
		if (m.backend === "qwen") return "qwen";
		if (m.backend === "parakeet") return "parakeet";
	}
	return null;
}

// matched). Its own docstring claimed "Used by the sync-guard effect
// that auto-expands the active family accordion", but `rg 'sync-guard'`
// up the sync-guard effect OR a test that exercises it.

// state and must never be surfaced as a live selection when the model's

export interface ResolvedActiveModel {
	/**
	 * The installed, active model registry name, or null when the
	 * configured model's weights aren't on disk (or none configured).
	 */
	model: string | null;
	/**
	 * The device the active model runs on (from config), or null.
	 */
	device: string | null;
}

/**
 * Resolve the genuinely-active model from the configured model size +
 * the backend's `get_model_status` install truth.
 */
export function resolveActiveModel(
	configuredModel: string,
	modelStatusMap: ModelStatusMap,
	configuredDevice: string | null | undefined,
): ResolvedActiveModel {
	const installed =
		configuredModel !== "" &&
		modelStatusMap[configuredModel]?.downloaded === true;
	return installed
		? { model: configuredModel, device: configuredDevice ?? null }
		: { model: null, device: null };
}

// `total_bytes: number` and made `models_dir?: string` optional). The
// `models_dir?` both optional so richer backend responses still
import type { DiskInfo } from "@/types/ipc";

export type { DiskInfo };

/**
 * : returns true when there is not enough free disk space to
 * download a model of the given size (in MB). A 10% safety margin is
 */
export function hasInsufficientDiskSpace(
	disk: DiskInfo | null,
	modelSizeMb: number,
): boolean {
	if (!disk) return false;
	const requiredBytes = modelSizeMb * 1024 * 1024 * 1.1;
	return disk.free_bytes < requiredBytes;
}

export function requiresHuggingFaceConsent(model: ModelInfo): boolean {
	return (
		model.backend === "whisper" ||
		model.backend === "distil-whisper" ||
		model.backend === "parakeet"
	);
}
