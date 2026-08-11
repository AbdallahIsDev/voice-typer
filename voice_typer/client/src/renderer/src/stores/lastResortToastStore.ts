/**
 * Zustand store for the per-backend ``asr_last_resort_unloaded`` toast
 * cooldown timestamps.
 *
 * Previously these lived in a module-level ``Map`` inside
 * ``hooks/useLastResortUnloadedToast.ts``. Module-level mutable state in
 * the hook file is reset whenever Vite HMR hot-reloads that module (any
 * edit to the hook while the app is running), which would drop the
 * cooldown and immediately re-toast a backend the user was just pointed
 * at the Models page for. A Zustand store defined at module scope in a
 * SEPARATE file survives HMR of the hook module — the store module is
 * not reloaded when the hook changes — so the 15-minute per-backend
 * cooldown is preserved across hook hot reloads.
 *
 * HMR scope caveat: the store survives edits to the HOOK module, not
 * edits to THIS store module (a full reload or a hot-reload of this
 * file itself re-runs it and clears the cooldown). That matches the
 * previous Map behavior for the reload case and only protects the
 * common "editing the hook" workflow.
 *
 * Scope is deliberately small (one timestamp record per backend) —
 * mirrors the ``appStore`` convention of tiny, focused stores. The
 * cooldown arithmetic stays in the hook; this store owns the state.
 *
 * Usage (event handlers, NOT render — never subscribe a component to
 * this store; nothing renders from it):
 *   import { useLastResortToastStore } from "@/stores/lastResortToastStore";
 *   const last = useLastResortToastStore.getState().lastToastedAt[backend];
 *   useLastResortToastStore.getState().setLastToastedAt(backend, now);
 */

import { create } from "zustand";

interface LastResortToastState {
	/**
	 * Per-backend wall-clock timestamps (``Date.now()``) of the last
	 * toast shown for that backend. Absent key = never toasted.
	 */
	lastToastedAt: Record<string, number>;
	/**
	 * Wall-clock timestamp of the last ``asr_last_resort_unloaded`` toast
	 * shown for ANY backend (``null`` = none yet). Drives the short
	 * renderer-side dedupe window: rapid genuine transitions collapse to
	 * one visible notification instead of stacking a toast per backend.
	 * This is intentionally GLOBAL (not per-backend) — the per-backend
	 * 15-min cooldown in ``lastToastedAt`` handles "same backend spamming";
	 * this handles "several backends breaking within seconds".
	 */
	lastToastShownAt: number | null;
	/** Record that ``backend`` toasted at ``timestamp``. */
	setLastToastedAt: (backend: string, timestamp: number) => void;
	/** Record that a last-resort toast (any backend) showed at ``timestamp``. */
	setLastToastShownAt: (timestamp: number) => void;
	/**
	 * Clear all cooldown/dedupe state (test seam — see the hook's
	 * ``_resetLastResortToastCooldownForTest``).
	 */
	resetLastToastedAt: () => void;
}

export const useLastResortToastStore = create<LastResortToastState>((set) => ({
	lastToastedAt: {},
	lastToastShownAt: null,
	setLastToastedAt: (backend, timestamp) =>
		set((state) => ({
			lastToastedAt: {
				...state.lastToastedAt,
				[backend]: timestamp,
			},
		})),
	setLastToastShownAt: (timestamp) => set({ lastToastShownAt: timestamp }),
	resetLastToastedAt: () => set({ lastToastedAt: {}, lastToastShownAt: null }),
}));
