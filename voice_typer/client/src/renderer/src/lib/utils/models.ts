/**
 * Model-page helpers and shared types (ARCH-20 extraction).
 *
 * These were originally defined inline in `pages/Models.tsx`. They are
 * pure functions (no React state, no IPC) plus the local model types,
 * so they live here as a small, testable module. `ModelsPage` imports
 * them and stays a thin composition root.
 */

import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";

// ── Shared model types ────────────────────────────────────────────────

export interface ModelInfo {
	name: string;
	size: string;
	speed: string;
	backend: string;
	downloaded: boolean;
	depsOk: boolean;
	isActive: boolean;
	// UX-010: replaces the magic string `!model.alwaysAvailable` check.
	// Qwen doesn't need a separate download step (it auto-downloads
	// from HuggingFace on first use), so the "Download" button is
	// hidden for it.
	alwaysAvailable?: boolean;
}

export interface ModelMetadata {
	name: string;
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
}

export interface ModelFamily {
	id: string;
	name: string;
	description: string | null;
	variants: ModelInfo[];
}

// ── Provider / display formatting ──────────────────────────────────────

// I18N-FIX: provider labels are translated at call time (not baked into a
// module-level constant) so locale changes take effect on the next render.
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

// I18N-FIX: model size "Variable" is a sentinel for qwen / always-available
// models.  Translate it for display; pass through literal sizes like ~466MB.
export function formatModelSize(size: string): string {
	return size === "Variable" ? t("models.variable") : size;
}

// MDL-14: model display names are translated via the i18n catalog
// (`models.displayNames.{name}`). `t()` returns the raw key when the
// key is not found, so we fall back to the model's internal name in
// that case (e.g. for newly added models that haven't been registered
// in the translations yet).
export function getModelDisplayName(name: string): string {
	const key = `models.displayNames.${name}`;
	const translated = t(key);
	return translated === key ? name : translated;
}

// VRAM formatting: show in GB when >= 1024 MB, otherwise in MB.
export function formatVram(mb: number): string {
	if (mb >= 1024) {
		return `${(mb / 1024).toFixed(1)}GB`;
	}
	return `${mb}MB`;
}

/**
 * UX-ERR-001: format an unknown caught value as a user-friendly string.
 *
 * Catch blocks frequently do `showSnack(`Failed: ${err}`)` which
 * stringifies the error via `String(err)`.  For plain `Error`
 * objects this produces `"Error: <message>"` (acceptable), but for
 * non-Error values it produces `"[object Object]"` (cryptic) or
 * `"undefined"` (useless).  This helper extracts a useful message
 * from any thrown value so the snackbar text is always actionable.
 */
export function formatErrorMessage(
	err: unknown,
	fallback = "Unknown error",
): string {
	if (err instanceof Error) {
		return err.message || fallback;
	}
	if (typeof err === "string") {
		return err || fallback;
	}
	if (err && typeof err === "object") {
		// IPC responses shape errors as { _error: "..." } or
		// { message: "..." }; prefer those when present.
		const obj = err as { _error?: unknown; message?: unknown; error?: unknown };
		if (typeof obj._error === "string" && obj._error) return obj._error;
		if (typeof obj.message === "string" && obj.message) return obj.message;
		if (typeof obj.error === "string" && obj.error) return obj.error;
	}
	return fallback;
}

// ── Model family grouping ──────────────────────────────────────────────
// Groups models by their backend family (Whisper, Qwen, Parakeet) so they
// render inside family cards with shared headers.
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
			name: "Whisper",
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
			name: "Parakeet",
			description: null,
			variants: parakeet,
		});
	}
	return families;
}

/**
 * Returns the family ID that contains the currently active model,
 * or null if no model is active or no family match is found.
 */
export function getActiveFamilyId(cfg: VoiceTyperConfig | null): string | null {
	if (!cfg) return null;
	const activeBackend = cfg.asr_backend ?? "whisper";
	const activeModel = cfg.model_size ?? "small.en";
	// INITIAL_MODELS-equivalent membership test: replicate the active-model
	// resolution used by ModelsPage's initial state. We don't need the full
	// catalog here — just the backend/name of the possibly-active model.
	const candidates: ModelInfo[] = [
		{
			name: "tiny.en",
			size: "",
			speed: "",
			backend: "whisper",
			downloaded: true,
			depsOk: true,
			isActive: false,
		},
		{
			name: "small.en",
			size: "",
			speed: "",
			backend: "whisper",
			downloaded: true,
			depsOk: true,
			isActive: false,
		},
		{
			name: "medium.en",
			size: "",
			speed: "",
			backend: "whisper",
			downloaded: true,
			depsOk: true,
			isActive: false,
		},
		{
			name: "large-v3-turbo",
			size: "",
			speed: "",
			backend: "whisper",
			downloaded: true,
			depsOk: true,
			isActive: false,
		},
		{
			name: "distil-large-v3",
			size: "",
			speed: "",
			backend: "distil-whisper",
			downloaded: true,
			depsOk: true,
			isActive: false,
		},
		{
			name: "distil-medium.en",
			size: "",
			speed: "",
			backend: "distil-whisper",
			downloaded: true,
			depsOk: true,
			isActive: false,
		},
		{
			name: "qwen",
			size: "",
			speed: "",
			backend: "qwen",
			downloaded: true,
			depsOk: true,
			isActive: false,
			alwaysAvailable: true,
		},
		{
			name: "parakeet",
			size: "",
			speed: "",
			backend: "parakeet",
			downloaded: true,
			depsOk: true,
			isActive: false,
		},
	];
	for (const m of candidates) {
		let isActive = false;
		if (m.backend === "whisper") {
			isActive = activeBackend === "whisper" && m.name === activeModel;
		} else {
			isActive = activeBackend === m.backend;
		}
		if (isActive) {
			if (m.backend === "whisper" || m.backend === "distil-whisper")
				return "whisper";
			if (m.backend === "qwen") return "qwen";
			if (m.backend === "parakeet") return "parakeet";
		}
	}
	return null;
}
