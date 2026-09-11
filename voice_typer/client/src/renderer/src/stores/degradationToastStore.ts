// Zustand store for the degradation-toast cooldown timestamps
// (``llm_polish_failed`` / ``asr_backend_disabled`` / the backend-load,
// mic-permission, tray-fallback, cloud-fallback and paste-deferred
// toasts).
//
// Same pattern + rationale as ``lastResortToastStore.ts``: cooldown
// timestamps live at module scope in a SEPARATE file so Vite HMR of the
// hook modules does NOT reset them, editing a toast hook while the app
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
	 * transcription while polish is broken, without a renderer-side
	 * cooldown every dictation would re-toast.
	 */
	llmPolishFailedAt: number | null;
	/**
	 * Wall-clock timestamp of the last ``text_enhancement_failed``
	 * toast shown (``null`` = none yet). Same cooldown rationale as
	 * ``llmPolishFailedAt``, the rule-based enhancement step can emit
	 * one event per transcription while it is broken.
	 */
	textEnhancementFailedAt: number | null;
	/**
	 * Per-backend wall-clock timestamps of the last
	 * ``asr_backend_disabled`` toast. Absent key = never toasted.
	 */
	asrBackendDisabledAt: Record<string, number>;
	/**
	 * Wall-clock timestamp of the last ``asr_backend_load_failed``
	 * toast shown (``null`` = none yet). The backend emits one failure
	 * per load attempt, a retry loop must not re-toast on every
	 * attempt inside the window.
	 */
	asrBackendLoadFailedAt: number | null;
	/**
	 * Wall-clock timestamp of the last ``microphone_permission_revoked``
	 * toast shown (``null`` = none yet). The device-health checker can
	 * re-fire before the user acts; the window collapses the re-fires.
	 */
	micPermissionRevokedAt: number | null;
	/**
	 * Wall-clock timestamp of the last ``tray_fallback_notification``
	 * banner shown (``null`` = none yet). The tray drain loop publishes
	 * one frame per queued notification, so a backlog must collapse
	 * into one banner.
	 */
	trayFallbackShownAt: number | null;
	/**
	 * Wall-clock timestamp of the last ``cloud_fallback_used`` toast
	 * shown (``null`` = none yet). The backend emits one event per
	 * transcription while the cloud provider is down, without a
	 * cooldown every dictation during an outage would re-toast.
	 */
	cloudFallbackUsedAt: number | null;
	/**
	 * Wall-clock timestamp of the last ``paste_deferred`` toast shown
	 * (``null`` = none yet). The IME-composition path can defer one
	 * paste per dictation while a composition is open, the window
	 * collapses consecutive deferrals.
	 */
	pasteDeferredAt: number | null;
	/**
	 * Wall-clock timestamp of the last degradation toast of ANY kind
	 * (``null`` = none yet). One-way record: hooks WRITE the timestamp
	 * when they show a toast; only the backend-disabled hook READS it
	 * (to stay quiet shortly after any other degradation surfaced).
	 * It does NOT currently collapse cross-event stacking, each hook
	 * enforces only its own per-event cooldown window.
	 */
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
