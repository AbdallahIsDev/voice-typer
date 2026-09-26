// types/ipc/model_status.ts
//supplementary IPC contracts for the Models page:
// `get_model_status` response shape + `get_disk_info` response shape.

/**
 * Per-model entry in the `get_model_status` IPC response.
 * The backend's `voice_typer/server/service.py::_compute_model_status`
 * returns `dict[str, { downloaded: bool, deps_ok: bool }]`. Renderers
 * call site, see `hooks/useModelLifecycle.ts` for the duplicated
 * `Record<string, { downloaded: boolean; deps_ok: boolean }>` annotation.
 *  adds the `hash_verified` discriminator so the Models
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
	hash_verified?: "verified" | "mismatch" | "unknown";
}

export type ModelStatusMap = Record<string, ModelStatusEntry>;

/**
 * Storage summary attached by the backend's `_handle_get_model_status`
 * under the `_storage` key (namespaced so it never collides with a
 * model name). Lets the Models page show total hub bytes + the shared
 * cache path without an extra IPC round-trip.
 */
export interface ModelStorageSummary {
	/** Total bytes of regular files under the shared hub dir. */
	used_bytes: number;
	/** Absolute path of the shared HuggingFace hub dir. */
	hub_path: string;
	/** Absolute path of the app config (data) dir. */
	config_dir: string;
}

/**
 * Full `get_model_status` response: per-model map plus the optional
 * `_storage` summary (absent on older backends; treat as unknown).
 */
export type ModelStatusResponse = ModelStatusMap & {
	_storage?: ModelStorageSummary;
};

export interface DiskInfo {
	/** Bytes free on the volume that holds the models directory. */
	free_bytes: number;
	/** Total capacity bytes of the volume that holds the models directory. */
	total_bytes?: number;
	/** Absolute path of the models directory. */
	models_dir: string;
}
