// Zustand store for the renderer-side ``device_lost`` state.
//
// The backend publishes ``device_lost`` when the ACTIVE microphone
// disappears and retries are exhausted (`level_monitor/monitoring.py`
// for the mic-test stream, `mic_lifecycle_hooks.py` for the dictation
// recorder). One App-level subscriber
// (`hooks/useDeviceLostToast.ts`) consumes the event ONCE and:
//   1. raises the global toast, and
//   2. records the loss here — the Microphone page reads this store to
//      pause the live meter + show a recovery banner. A single event →
//      a single subscription feeding both surfaces (no duplicate
//      subscribers, no divergent state).
//
// The store lives at module scope in its OWN file (not inside the hook)
// so Vite HMR of the hook module does not reset it — same rationale as
// `lastResortToastStore.ts`.

import { create } from "zustand";

interface DeviceLostState {
	/**
	 * `source` string from the last ``device_lost`` event, or ``null``
	 * when no device is currently flagged lost. Diagnostics only —
	 * user-facing copy is source-agnostic.
	 */
	lostSource: string | null;
	/**
	 * Wall-clock timestamp of the last device-lost toast shown
	 * (``null`` = none yet). Drives the short global dedupe window so
	 * rapid re-emissions collapse into one visible notification.
	 */
	lastToastShownAt: number | null;
	/** Record a device loss (idempotent — latest wins). */
	markLost: (source: string) => void;
	/** Record that a device-lost toast showed at ``timestamp``. */
	setLastToastShownAt: (timestamp: number) => void;
	/** Clear the lost flag (user hit Retry / monitoring recovered). */
	clearLost: () => void;
	/** Test seam — reset every field. */
	resetForTest: () => void;
}

export const useDeviceLostStore = create<DeviceLostState>((set) => ({
	lostSource: null,
	lastToastShownAt: null,
	markLost: (source) => set({ lostSource: source }),
	setLastToastShownAt: (timestamp) => set({ lastToastShownAt: timestamp }),
	clearLost: () => set({ lostSource: null }),
	resetForTest: () => set({ lostSource: null, lastToastShownAt: null }),
}));
