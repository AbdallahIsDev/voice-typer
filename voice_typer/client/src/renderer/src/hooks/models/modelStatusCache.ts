// Shared `get_model_status` snapshot (C-CACHE-5/STALE-ACTIVE).
// The status call stats the real filesystem, and TWO pages fire it on
// overlapping paths: Models `loadConfig` and the Analytics refresh
// (plus sidebar hover prefetches for both). Without sharing, one visit
// cycle stats the disk twice; with it, concurrent readers share one
// flight and revisits inside the TTL skip the stat entirely.
// The TTL stays short on purpose: users can delete model folders
// out-of-band (the STALE-ACTIVE case), so a cached `downloaded: true`
// must age out in seconds, not minutes. App-initiated disk changes
// (download finished, folder imported, model deleted/selected) expire
// or rewrite the snapshot explicitly at their call sites — the TTL
// only covers quiet revisits, never known mutations.

import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
import {
	isSnapshotFresh,
	type PrefetchCall,
	peekValueFlight,
	shareFlight,
	type TimestampedSnapshot,
} from "@/lib/snapshotCache";
import type { ModelStatusResponse } from "@/types/ipc";

export const MODEL_STATUS_SNAPSHOT_KEY = "models.statusSnapshot";
export const MODEL_STATUS_TTL_MS = 30_000;

export interface ModelStatusSnapshot extends TimestampedSnapshot {
	status: ModelStatusResponse;
}

// Shape guard for the write path: an error envelope flattened into the
// cache would read as "no models downloaded" (or poison the seed) on
// every revisit until the TTL ages out. Per-entry check, not just
// "is an object": a stray `{ count: 1 }` from a crossed IPC must not
// land here either.
export function isModelStatusResponse(v: unknown): v is ModelStatusResponse {
	if (!v || typeof v !== "object" || Array.isArray(v)) return false;
	const rec = v as Record<string, unknown>;
	if (rec.type === "error" || "_error" in rec) return false;
	for (const [key, entry] of Object.entries(rec)) {
		if (key === "_storage") continue;
		if (!entry || typeof entry !== "object") return false;
		if (typeof (entry as { downloaded?: unknown }).downloaded !== "boolean")
			return false;
	}
	return true;
}

export function readModelStatusSnapshot(): ModelStatusSnapshot | null {
	const snap = peekIpcCache<ModelStatusSnapshot>(MODEL_STATUS_SNAPSHOT_KEY);
	if (snap && isModelStatusResponse(snap.status)) return snap;
	return null;
}

export function writeModelStatusSnapshot(status: ModelStatusResponse): void {
	writeIpcCache(MODEL_STATUS_SNAPSHOT_KEY, {
		status,
		fetchedAt: Date.now(),
	} satisfies ModelStatusSnapshot);
}

/** True when a fresh snapshot makes a disk stat redundant. */
export function isModelStatusFresh(): boolean {
	return isSnapshotFresh(readModelStatusSnapshot(), MODEL_STATUS_TTL_MS);
}

// Force the next reader to re-stat (disk changed under us: a download
// finished, a folder was imported). Never serves stale flags after a
// known mutation — the TTL only covers quiet revisits.
export function expireModelStatusSnapshot(): void {
	const snap = readModelStatusSnapshot();
	if (snap) writeIpcCache(MODEL_STATUS_SNAPSHOT_KEY, { ...snap, fetchedAt: 0 });
}

// Rewrite one entry after a CONFIRMED delete (backend reported
// success): the cache reflects post-delete truth immediately instead
// of serving `downloaded: true` until the TTL ages out. Never invent
// `downloaded: true` here — only the stat itself may set that.
export function patchCachedModelDownloaded(
	name: string,
	downloaded: boolean,
): void {
	if (downloaded) return;
	const snap = readModelStatusSnapshot();
	if (!snap) return;
	const prev = snap.status[name];
	writeIpcCache(MODEL_STATUS_SNAPSHOT_KEY, {
		status: {
			...snap.status,
			[name]: { downloaded, deps_ok: prev?.deps_ok ?? false },
		},
		fetchedAt: Date.now(),
	} satisfies ModelStatusSnapshot);
}

// Shared fetch: fresh cache short-circuits, concurrent callers share
// one flight. Never rejects (failures resolve stale-or-null so callers
// keep prior state); never writes invalid shapes.
export function fetchSharedModelStatus(
	call: PrefetchCall,
): Promise<ModelStatusResponse | null> {
	const snap = readModelStatusSnapshot();
	if (snap && isSnapshotFresh(snap, MODEL_STATUS_TTL_MS))
		return Promise.resolve(snap.status);
	return shareFlight<ModelStatusResponse | null>(
		MODEL_STATUS_SNAPSHOT_KEY,
		async () => {
			let status: unknown = null;
			try {
				status = await call("get_model_status");
			} catch {
				// Fall through to stale-or-null below.
			}
			if (isModelStatusResponse(status)) {
				writeModelStatusSnapshot(status);
				return status;
			}
			return readModelStatusSnapshot()?.status ?? null;
		},
	);
}

/** In-flight shared fetch, if any. */
export function peekModelStatusFlight(): Promise<ModelStatusResponse | null> | null {
	return peekValueFlight<ModelStatusResponse | null>(MODEL_STATUS_SNAPSHOT_KEY);
}

// Hover/focus prefetch for the Models page: warms the status snapshot
// so the mount's loadConfig skips the disk stat. All guards live in
// fetchSharedModelStatus (C-CACHE-4). Resolves with the in-flight fetch
// when one is running (mount piggybacks it), immediately otherwise.
export function prefetchModelsStatus(
	call: PrefetchCall,
): Promise<ModelStatusResponse | null> {
	return fetchSharedModelStatus(call);
}
