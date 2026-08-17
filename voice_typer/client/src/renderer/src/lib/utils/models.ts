/**
 * Model-page helpers and shared types ( extraction).
 *
 *  / : previously this module existed but was unused —
 * `pages/Models.tsx` kept an inline duplicate of every type, constant,
 * and helper. After the spaghetti split, `Models.tsx` and its child
 * panels import these symbols from here so there is a single source of
 * truth (testable in isolation, no React/IPC coupling).
 */

import { t } from "@/i18n/i18n";
import { formatVram as _formatVram } from "@/lib/format";
import { MODEL_DEFAULT } from "@/pages/onboarding/lib/constants";
import type { VoiceTyperConfig } from "@/types/config";
import type { ModelStatusMap } from "@/types/ipc";

// ── Shared model types ────────────────────────────────────────────────

export interface ModelInfo {
	name: string;
	size: string;
	speed: string;
	backend: string;
	downloaded: boolean;
	depsOk: boolean;
	isActive: boolean;
	// NOTE: a former `alwaysAvailable?: boolean` flag was REMOVED.
	// It claimed Qwen "auto-downloads on first use" and hid the
	// Download button + bypassed the select guard for a model that
	// was never installed — a lie (the backend registry declares Qwen
	// `network_behavior="local-only"`, NOT auto-fetched; the engine
	// requires `qwen_model_path` or an HF cache dir). "Installed" is
	// determined solely by the backend's `get_model_status`
	// (`downloaded`). Do NOT re-add an always-available concept without
	// a backend `network_behavior` value that actually auto-downloads.
	/**
	 *  fix #7: model requires extra system dependencies that
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
		name: "tiny",
		size: "~75MB",
		speed: "Fastest",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "large-v3",
		size: "~3GB",
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
		name: "qwen",
		size: "Variable",
		speed: "Fast",
		backend: "qwen",
		// Qwen is local-only per the backend registry — it is NOT
		// auto-fetched. `downloaded` is set by `get_model_status`
		// (true when `qwen_model_path` points at an existing dir or the
		// HF cache holds the repo). Until then the card shows
		// "Download" (which surfaces the "set qwen_model_path" hint)
		// and selecting it is blocked by the not-downloaded guard.
		downloaded: false,
		// Qwen requires the optional `qwen_asr` pip package (probed by
		// the backend's `_check_qwen_deps()` → `deps_ok`). With
		// `depsInstallable: true`, a qwen_asr-missing install shows the
		// "Download Deps" button and `useModelSelection` blocks the
		// Select action — mirroring Parakeet's gating. `depsOk` is
		// reconciled at runtime by `get_model_status` (seeded `true` so
		// first paint shows "Download", not a false "Dependencies
		// required" flash, for users who DO have qwen_asr installed).
		depsOk: true,
		isActive: false,
		depsInstallable: true,
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

// I18N-FIX: model size "Variable" is the sentinel used by the Qwen entry.
// Translate it for display; pass through literal sizes like ~466MB.
export function formatModelSize(size: string): string {
	return size === "Variable" ? t("models.variable") : size;
}

const SPEED_I18N_MAP: Record<string, string> = {
	Fastest: "models.speed.fastest",
	Fast: "models.speed.fast",
	Slow: "models.speed.slow",
	Variable: "models.speed.variable",
};

export function formatModelSpeed(speed: string): string {
	const key = SPEED_I18N_MAP[speed];
	return key ? t(key) : speed;
}

// MDL-14: model display names are translated via the i18n catalog
// (`models.displayNames.{name}`). The previous `getModelDisplayName(name)`
// helper was DELETED because it had ZERO
// callers in `src/` or `tests/` AND ZERO inline `t(\`models.displayNames.${...}\`)`
// call sites — the helper was orphaned by an earlier refactor that moved
// display-name resolution out of the renderer entirely (the backend's
// `ModelMetadata.display_name` field is now the canonical source, surfaced
// via the `get_model_catalog` IPC). Do NOT re-add this helper without
// also wiring up at least one caller.
//
// Decision: DELETE. Alternative considered: search the
// renderer for inline `t(\`models.displayNames.${...}\`)` patterns and
// route them through the helper. Search returned ZERO matches in
// `voice_typer/client/src`, so there is nothing to wire — the helper
// was genuinely dead. Verified via:
//   rg 'models\.displayNames' voice_typer/client/src
//   rg 'getModelDisplayName' voice_typer  (only the definition matched)
//
// VRAM formatting: re-exported from the shared ``lib/format.ts`` so
// call sites that import from ``@/lib/utils/models`` (LocalModelsPanel,
//the Models spaghetti split, etc.) keep working. : previously
// this was a duplicate of the inline copy in ``pages/Models.tsx`` with
// hardcoded ``"MB"`` / ``"GB"`` suffixes and ``toFixed(1)`` rounding —
// now both call sites route through the locale-aware ``Intl.NumberFormat``
// implementation in ``lib/format.ts``.
export function formatVram(mb: number): string {
	return _formatVram(mb);
}

/**
 * : format an unknown caught value as a user-friendly string.
 *
 * Catch blocks frequently do `showSnack(`Failed: ${err}`)` which
 * stringifies the error via `String(err)`.  For plain `Error`
 * objects this produces `"Error: <message>"` (acceptable), but for
 * non-Error values it produces `"[object Object]"` (cryptic) or
 * `"undefined"` (useless).  This helper extracts a useful message
 * from any thrown value so the snackbar text is always actionable.
 *
 * : the default ``fallback`` is now ``t("models.errors.unknown")``
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
// Groups models by their backend family (Whisper, Qwen, NVIDIA — the
// Parakeet family is branded under the NVIDIA logo) so they render
// inside family cards with shared headers.
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
			// The family is branded under NVIDIA (the model underneath
			// is Parakeet-v3-TDT — its display_name comes from the
			// backend catalog).
			name: "Nvidia",
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
	// An empty active model is the genuine "no model selected" state
	// (the backend's `NO_MODEL_SIZE` sentinel) — NOTHING is active,
	// including backend-keyed models (qwen / parakeet) whose active
	// check below ignores `model_size`.
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
 * every other entry). Pure function — used by `useModelLifecycle` to
 * reconcile model state after `get_config` / `config_changed` events.
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
 *
 *  fix #2: previously this function inlined a duplicate copy
 * of `INITIAL_MODELS` (the `candidates` array) — a 30-line verbatim
 * duplicate that drifted whenever a new model was added. Now it
 * imports the single source of truth from `INITIAL_MODELS`.
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

// The previous "family ID for backend" helper that lived here
// was DELETED. It had ZERO importers in `voice_typer/client/src`
// (verified via `rg` on the function name — only the definition
// matched). Its own docstring claimed "Used by the sync-guard effect
// that auto-expands the active family accordion", but `rg 'sync-guard'`
// on `voice_typer/client/src` returned ZERO matches — the sync-guard
// effect was either never landed or was refactored to inline the
// logic. The function was genuine dead code (not a public API, no test
// coverage, no external importer). Do NOT re-add without also wiring
// up the sync-guard effect OR a test that exercises it.
//
// (Note: the literal function name is intentionally NOT spelled out
// in this comment so that `rg '<function-name>' voice_typer/client/src`
// returns ZERO matches — verifying the function is truly gone. The
// previous docstring + 6-line function body have been excised.)

// ── Active-model resolution from the backend's install truth ────────
//
// SINGLE source of truth for "is a model currently installed and active,
// and on which device" — shared by the Analytics page (Current Setup
// cards), the About page (Diagnostics table), and any future surface.
// "Installed" is determined SOLELY by the backend's `get_model_status`
// (`downloaded: true` for the configured `model_size`); the config's
// `model_size` / `device` defaults ("tiny" / "cuda") are NOT install
// state and must never be surfaced as a live selection when the model's
// weights aren't on disk. Both consumers call this function (never
// inline their own check) so the two pages can't drift apart again.

export interface ResolvedActiveModel {
	/** The installed, active model registry name, or null when the
	 *  configured model's weights aren't on disk (or none configured). */
	model: string | null;
	/** The device the active model runs on (from config), or null. */
	device: string | null;
}

/**
 * Resolve the genuinely-active model from the configured model size +
 * the backend's `get_model_status` install truth.
 *
 * Returns ``{ model: null, device: null }`` when the configured model
 * is empty or its weights are not downloaded — callers must render
 * "Not selected" / "Unknown" in that state, never the config defaults.
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

//Disk-space pre-flight () ───────────────────────────────────
//
//the `DiskInfo` interface previously declared here was a
// DUPLICATE of the one in `types/ipc.ts` with a different shape (added
// `total_bytes: number` and made `models_dir?: string` optional). The
// two had drifted and were a maintenance hazard. The unified type now
// lives in `types/ipc.ts` (the IPC contract), with `total_bytes?` and
// `models_dir?` both optional so richer backend responses still
// type-check. This module imports + re-exports it so existing imports
// (`import { type DiskInfo } from "@/lib/utils/models"`) continue to
// work unchanged.
import type { DiskInfo } from "@/types/ipc";

export type { DiskInfo };

/**
 * : returns true when there is not enough free disk space to
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
