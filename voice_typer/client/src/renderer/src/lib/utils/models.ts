/**
 * Model-page helpers and shared types (ARCH-20 extraction).
 *
 * PVT-031 / PVT-003: previously this module existed but was unused —
 * `pages/Models.tsx` kept an inline duplicate of every type, constant,
 * and helper. After the spaghetti split, `Models.tsx` and its child
 * panels import these symbols from here so there is a single source of
 * truth (testable in isolation, no React/IPC coupling).
 */

import { t } from "@/i18n/i18n";
import { formatVram as _formatVram } from "@/lib/format";
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
	/**
	 * PVT-003 fix #7: model requires extra system dependencies that
	 * can be installed via a dedicated action (e.g. Parakeet's torch
	 * dependency). When true and `depsOk === false`, the card renders
	 * a "Download Deps" button next to (or instead of) the Select
	 * button. Replaces the old `model.name === "parakeet"` magic
	 * string check in `selectModel`.
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
}

export interface ModelFamily {
	id: string;
	name: string;
	description: string | null;
	variants: ModelInfo[];
}

// ── Static model catalog (renderer-side seed) ─────────────────────────
//
// The renderer keeps a static seed list so the Models page can render
// skeleton cards immediately on first paint (before the backend's
// `get_model_status` / `get_model_catalog` IPCs resolve). The backend's
// `get_model_status` updates `downloaded` / `depsOk` at runtime; the
// `get_model_catalog` IPC overlays rich metadata (`display_name`,
// `required_vram_mb`, `multilingual`, etc.) on top.
//
// Keep this list in sync with the backend's `MODEL_REGISTRY`
// (`voice_typer/server/model_registry.py`). Adding a new model requires
// an entry in BOTH places.
export const INITIAL_MODELS: ModelInfo[] = [
	{
		name: "tiny.en",
		size: "~75MB",
		speed: "Fastest",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "small.en",
		size: "~466MB",
		speed: "Fast",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "medium.en",
		size: "~1.5GB",
		speed: "Slow",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "large-v3-turbo",
		size: "~809MB",
		speed: "Fast",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "distil-large-v3",
		size: "~1.5GB",
		speed: "Fast",
		backend: "distil-whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "distil-medium.en",
		size: "~780MB",
		speed: "Fast",
		backend: "distil-whisper",
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
		depsOk: true,
		isActive: false,
		alwaysAvailable: true,
	},
	{
		name: "parakeet",
		size: "~2.5GB",
		speed: "Fast",
		backend: "parakeet",
		downloaded: false,
		// Parakeet requires `torch` (probed by the backend's
		// `_check_parakeet_deps()`). When this is false the Select
		// button is blocked and a "Download Deps" button is offered.
		depsOk: false,
		isActive: false,
		depsInstallable: true,
	},
];

// ── Cloud ASR providers ──────────────────────────────────────────────
//
// Static list of cloud ASR providers surfaced in the Cloud tab. The
// backend doesn't currently expose a "list_providers" IPC; this seed is
// authoritative for the renderer.
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

// VRAM formatting: re-exported from the shared ``lib/format.ts`` so
// call sites that import from ``@/lib/utils/models`` (LocalModelsPanel,
// the Models spaghetti split, etc.) keep working. PVT-091: previously
// this was a duplicate of the inline copy in ``pages/Models.tsx`` with
// hardcoded ``"MB"`` / ``"GB"`` suffixes and ``toFixed(1)`` rounding —
// now both call sites route through the locale-aware ``Intl.NumberFormat``
// implementation in ``lib/format.ts``.
export function formatVram(mb: number): string {
	return _formatVram(mb);
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
 *
 * PVT-091: the default ``fallback`` is now ``t("models.errors.unknown")``
 * (``"Unknown error"`` in en, translated for every locale) instead of
 * the hardcoded English string — so a backend failure surfacing in a
 * non-English UI no longer leaks English into the snackbar. Callers
 * that explicitly pass a custom ``fallback`` (e.g. for context-specific
 * messages like ``t("models.snack.downloadFailedName", { name })``)
 * still override the default.
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

// ── Active-model resolution ────────────────────────────────────────────

/**
 * Returns true if the given model entry matches the active backend/model
 * pair from the config. Whisper is keyed by both `asr_backend === "whisper"`
 * AND `model_size === m.name`; other backends (qwen, parakeet) are keyed
 * by `asr_backend === m.backend` alone (their `model_size` is the
 * backend name itself).
 */
export function isModelActive(
	m: ModelInfo,
	activeBackend: string,
	activeModel: string,
): boolean {
	if (m.backend === "whisper") {
		return activeBackend === "whisper" && m.name === activeModel;
	}
	return activeBackend === m.backend;
}

/**
 * Map a config's active backend/model over a models list, returning a
 * new array with `isActive` set on the matching entry (and cleared on
 * every other entry). Pure function — used by `useModelLifecycle` to
 * reconcile model state after `get_config` / `config_changed` events.
 */
export function applyActiveState(
	models: ModelInfo[],
	cfg: VoiceTyperConfig | null,
): ModelInfo[] {
	if (!cfg) return models;
	const activeBackend = cfg.asr_backend ?? "whisper";
	const activeModel = cfg.model_size ?? "small.en";
	return models.map((m) => ({
		...m,
		isActive: isModelActive(m, activeBackend, activeModel),
	}));
}

/**
 * Returns the family ID that contains the currently active model,
 * or null if no model is active or no family match is found.
 *
 * PVT-031 fix #2: previously this function inlined a duplicate copy
 * of `INITIAL_MODELS` (the `candidates` array) — a 30-line verbatim
 * duplicate that drifted whenever a new model was added. Now it
 * imports the single source of truth from `INITIAL_MODELS`.
 */
export function getActiveFamilyId(cfg: VoiceTyperConfig | null): string | null {
	if (!cfg) return null;
	const activeBackend = cfg.asr_backend ?? "whisper";
	const activeModel = cfg.model_size ?? "small.en";
	for (const m of INITIAL_MODELS) {
		if (!isModelActive(m, activeBackend, activeModel)) continue;
		if (m.backend === "whisper" || m.backend === "distil-whisper")
			return "whisper";
		if (m.backend === "qwen") return "qwen";
		if (m.backend === "parakeet") return "parakeet";
	}
	return null;
}

/**
 * PVT-031 fix #2 helper: returns the family ID for a model entry,
 * mirroring the membership test used by `getActiveFamilyId`. Used by
 * the sync-guard effect that auto-expands the active family accordion.
 */
export function familyIdForBackend(backend: string): string | null {
	if (backend === "whisper" || backend === "distil-whisper") return "whisper";
	if (backend === "qwen") return "qwen";
	if (backend === "parakeet") return "parakeet";
	return null;
}

// ── Disk-space pre-flight (PVT-033) ───────────────────────────────────
//
// Shape returned by the backend's optional `get_disk_info` IPC. The
// backend doesn't currently expose this — the renderer probes once on
// mount and silently skips the pre-flight check when the IPC is
// unavailable. This keeps the UX opt-in without breaking on backends
// that predate the disk-info IPC.
export interface DiskInfo {
	/** Bytes free on the volume that holds the models directory. */
	free_bytes: number;
	/** Total bytes on the same volume. */
	total_bytes: number;
	/** Absolute path of the models directory (for "Open models folder"). */
	models_dir?: string;
}

/**
 * PVT-033: returns true when there is not enough free disk space to
 * download a model of the given size (in MB). A 10% safety margin is
 * applied so the OS doesn't run completely dry mid-download.
 */
export function hasInsufficientDiskSpace(
	disk: DiskInfo | null,
	modelSizeMb: number,
): boolean {
	if (!disk) return false;
	const requiredBytes = modelSizeMb * 1024 * 1024 * 1.1;
	return disk.free_bytes < requiredBytes;
}
