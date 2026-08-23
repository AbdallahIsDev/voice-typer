// Zustand store for the degradation-toast cooldown timestamps
// (``llm_polish_failed`` / ``asr_backend_disabled``).
//
// Same pattern + rationale as ``lastResortToastStore.ts``: cooldown
// timestamps live at module scope in a SEPARATE file so Vite HMR of the
// hook modules does NOT reset them — editing a toast hook while the app
// runs must not clear the cooldown and immediately re-nag the user.
//
// Scope is deliberately small (a few timestamps); the cooldown
// arithmetic stays in the hooks, this store owns the state. Event
// handlers read/write via ``useDegradationToastStore.getState()`` —
// never subscribe a component to it; nothing renders from it.

import { create } from "zustand";

interface DegradationToastState {
	/**
	 * Wall-clock timestamp of the last ``llm_polish_failed`` toast
	 * shown (``null`` = none yet). The backend can emit one event per
	 * transcription while polish is broken — without a renderer-side
	 * cooldown every dictation would re-toast.
	 */
	llmPolishFailedAt: number | null;
	/**
	 * Per-backend wall-clock timestamps of the last
	 * ``asr_backend_disabled`` toast. Absent key = never toasted.
	 */
	asrBackendDisabledAt: Record<string, number>;
	/**
	 * Wall-clock timestamp of the last degradation toast of ANY kind
	 * (``null`` = none yet). Short global dedupe window: several
	 * degradations landing within seconds collapse to one visible
	 * notification instead of stacking.
	 */
	lastAnyToastShownAt: number | null;
	setLlmPolishFailedAt: (timestamp: number) => void;
	setAsrBackendDisabledAt: (backend: string, timestamp: number) => void;
	setLastAnyToastShownAt: (timestamp: number) => void;
	/** Test seam — reset every field. */
	resetForTest: () => void;
}

export const useDegradationToastStore = create<DegradationToastState>(
	(set) => ({
		llmPolishFailedAt: null,
		asrBackendDisabledAt: {},
		lastAnyToastShownAt: null,
		setLlmPolishFailedAt: (timestamp) => set({ llmPolishFailedAt: timestamp }),
		setAsrBackendDisabledAt: (backend, timestamp) =>
			set((state) => ({
				asrBackendDisabledAt: {
					...state.asrBackendDisabledAt,
					[backend]: timestamp,
				},
			})),
		setLastAnyToastShownAt: (timestamp) =>
			set({ lastAnyToastShownAt: timestamp }),
		resetForTest: () =>
			set({
				llmPolishFailedAt: null,
				asrBackendDisabledAt: {},
				lastAnyToastShownAt: null,
			}),
	}),
);
