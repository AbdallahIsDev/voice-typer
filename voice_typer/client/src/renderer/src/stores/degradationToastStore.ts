// Zustand store for the degradation-toast cooldown timestamps
// (``llm_polish_failed`` / ``asr_backend_disabled`` / the backend-load,
// mic-permission, tray-fallback, cloud-fallback and paste-deferred
// toasts).
// Same pattern + rationale as ``lastResortToastStore.ts``: cooldown
// timestamps live at module scope in a SEPARATE file so Vite HMR of the
// hook modules does NOT reset them, editing a toast hook while the app
// runs must not clear the cooldown and immediately re-nag the user.
// Scope is deliberately small (a few timestamps); the cooldown
// arithmetic stays in the hooks, this store owns the state. Event
// handlers read/write via ``useDegradationToastStore.getState()`` —
// never subscribe a component to it; nothing renders from it.

import { create } from "zustand";

interface DegradationToastState {
	llmPolishFailedAt: number | null;
	textEnhancementFailedAt: number | null;
	asrBackendDisabledAt: Record<string, number>;
	asrBackendLoadFailedAt: number | null;
	micPermissionRevokedAt: number | null;
	trayFallbackShownAt: number | null;
	cloudFallbackUsedAt: number | null;
	pasteDeferredAt: number | null;
	lastAnyToastShownAt: number | null;
	setLlmPolishFailedAt: (timestamp: number) => void;
	setTextEnhancementFailedAt: (timestamp: number) => void;
	setAsrBackendDisabledAt: (backend: string, timestamp: number) => void;
	setAsrBackendLoadFailedAt: (timestamp: number) => void;
	setMicPermissionRevokedAt: (timestamp: number) => void;
	setTrayFallbackShownAt: (timestamp: number) => void;
	setCloudFallbackUsedAt: (timestamp: number) => void;
	setPasteDeferredAt: (timestamp: number) => void;
	setLastAnyToastShownAt: (timestamp: number) => void;
	/** Test seam, reset every field. */
	resetForTest: () => void;
}

export const useDegradationToastStore = create<DegradationToastState>(
	(set) => ({
		llmPolishFailedAt: null,
		textEnhancementFailedAt: null,
		asrBackendDisabledAt: {},
		asrBackendLoadFailedAt: null,
		micPermissionRevokedAt: null,
		trayFallbackShownAt: null,
		cloudFallbackUsedAt: null,
		pasteDeferredAt: null,
		lastAnyToastShownAt: null,
		setLlmPolishFailedAt: (timestamp) => set({ llmPolishFailedAt: timestamp }),
		setTextEnhancementFailedAt: (timestamp) =>
			set({ textEnhancementFailedAt: timestamp }),
		setAsrBackendDisabledAt: (backend, timestamp) =>
			set((state) => ({
				asrBackendDisabledAt: {
					...state.asrBackendDisabledAt,
					[backend]: timestamp,
				},
			})),
		setAsrBackendLoadFailedAt: (timestamp) =>
			set({ asrBackendLoadFailedAt: timestamp }),
		setMicPermissionRevokedAt: (timestamp) =>
			set({ micPermissionRevokedAt: timestamp }),
		setTrayFallbackShownAt: (timestamp) =>
			set({ trayFallbackShownAt: timestamp }),
		setCloudFallbackUsedAt: (timestamp) =>
			set({ cloudFallbackUsedAt: timestamp }),
		setPasteDeferredAt: (timestamp) => set({ pasteDeferredAt: timestamp }),
		setLastAnyToastShownAt: (timestamp) =>
			set({ lastAnyToastShownAt: timestamp }),
		resetForTest: () =>
			set({
				llmPolishFailedAt: null,
				textEnhancementFailedAt: null,
				asrBackendDisabledAt: {},
				asrBackendLoadFailedAt: null,
				micPermissionRevokedAt: null,
				trayFallbackShownAt: null,
				cloudFallbackUsedAt: null,
				pasteDeferredAt: null,
				lastAnyToastShownAt: null,
			}),
	}),
);
