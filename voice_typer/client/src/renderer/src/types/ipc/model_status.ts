// types/ipc/model_status.ts
//
// TASK-24 supplementary IPC contracts for the Models page:
// `get_model_status` response shape + `get_disk_info` response shape.
//
// Split out from the original monolithic `types/ipc.ts` (DT-31 / DT-FIX-7).
// No behaviour change vs. the original file — pure structural refactor.

/**
 * Per-model entry in the `get_model_status` IPC response.
 *
 * The backend's `voice_typer/server/service.py::_compute_model_status`
 * returns `dict[str, { downloaded: bool, deps_ok: bool }]`. Renderers
 * previously inlined that shape at every `call<...>("get_model_status")`
 * call site — see `hooks/useModelLifecycle.ts` for the duplicated
 * `Record<string, { downloaded: boolean; deps_ok: boolean }>` annotation.
 *
 * TASK-24-FIX-6 adds the `hash_verified` discriminator so the Models
 * page can surface a warning badge when a downloaded model's hash doesn't
 * match the registry's expected hash (e.g. partial download left on disk
 * after a crash, or a third-party import that bypassed the HuggingFace
 * cache). The backend currently omits this field (defaults to `"unknown"`
 * for backwards compat); when `voice_typer/server/model_registry.py`
 * starts populating it, the renderer will already have the type.
 */
export interface ModelStatusEntry {
	downloaded: boolean;
	deps_ok: boolean;
	/**
	 * Hash-verification result for the on-disk model files.
	 *
	 * - `"verified"` — the downloaded files' hash matches the
	 *   registry's expected hash.
	 * - `"mismatch"` — the files exist but the hash doesn't match
	 *   (corrupt download, third-party import, or a partial file).
	 *   The Models page should show a "Re-download" affordance.
	 * - `"unknown"` — the backend hasn't computed a hash yet
	 *   (legacy backend that predates the field, or a model that
	 *   doesn't have a registry hash). The Models page should NOT
	 *   show a verification badge in this state.
	 *
	 * Optional for backwards compatibility with backends that
	 * predate TASK-24-FIX-6 — absence is treated as `"unknown"`.
	 */
	hash_verified?: "verified" | "mismatch" | "unknown";
}

/**
 * Convenience alias: the full `get_model_status` response is a map
 * keyed by the model's registry name.
 *
 * The renderer's `hooks/useModelLifecycle.ts` now uses this
 * alias instead of the prior inline `Record<string, { downloaded:
 * boolean; deps_ok: boolean }>` annotation — the inline form was
 * replaced by `call<ModelStatusMap>("get_model_status")` at both
 * call sites (the `refreshModelStatus` helper and the parallelized
 * `loadConfig` Promise.allSettled block).
 */
export type ModelStatusMap = Record<string, ModelStatusEntry>;

/**
 * TASK-24-FIX-5: disk-space info for the Models-page pre-flight check
 * (PVT-033). Returned by the optional `get_disk_info` IPC.
 *
 * The shape deliberately matches the *minimal* contract documented in the
 * fix brief: free bytes on the volume that hosts the models directory,
 * plus the absolute path of the models directory itself (so the renderer
 * can show "X GB free in /home/…/.voice-typer/huggingface/hub" and offer
 * an "Open models folder" button).
 *
 * NOTE: `lib/utils/models.ts` declares a richer `DiskInfo` interface
 * (with an additional `total_bytes: number` field and `models_dir?`
 * optional). That interface is owned by sub-agent 6 and is NOT modified
 * here — this file declares the IPC-level contract per the fix brief.
 * The two shapes are intentionally compatible: the richer object
 * satisfies this interface (the extra `total_bytes` field is allowed by
 * TypeScript's structural typing, and `models_dir` is required here but
 * optional there — callers that consume `lib/utils/models.ts`'s
 * `DiskInfo` should normalise to a non-null `models_dir` before treating
 * the value as this type).
 */
export interface DiskInfo {
	/** Bytes free on the volume that holds the models directory. */
	free_bytes: number;
	/** Total capacity bytes of the volume that holds the models directory. */
	total_bytes?: number;
	/** Absolute path of the models directory. */
	models_dir: string;
}
