import type { LausuConfig } from "@/types/config";
import type { RecordingState } from "@/types/ipc";
import { create } from "zustand";

export type ConnectionStatus =
	| "connected"
	| "disconnected"
	| "connecting"
	| "restarting"
	| "reconnecting";

export function isRecoveringStatus(status: string): boolean {
	return status === "restarting" || status === "reconnecting";
}

interface AppState {
	// ── Connection ──────────────────────────────────────────────
	/** Current connection status to the Python backend. */
	connectionStatus: ConnectionStatus;
	setConnectionStatus: (status: ConnectionStatus) => void;

	// ── Recording ───────────────────────────────────────────────
	/** Current recording state pushed from the backend. */
	recordingState: RecordingState;
	setRecordingState: (state: RecordingState) => void;

	lastError: string | null;
	setLastError: (error: string | null) => void;

	// ── Config (cached snapshot) ────────────────────────────────
	/** Cached config snapshot, updated on get_config and config_changed. */
	config: Partial<LausuConfig> | null;
	setConfig: (config: Partial<LausuConfig> | null) => void;
	/** Merge partial config updates (e.g. from config_changed events). */
	mergeConfig: (updates: Partial<LausuConfig>) => void;
}

export const useAppStore = create<AppState>((set) => ({
	// Connection
	connectionStatus: "connecting",
	setConnectionStatus: (status) =>
		set((state) => {
			// When the backend reconnects, clear any
			// stale ``lastError`` so the UI doesn't keep showing an
			// error banner after a successful reconnection. The
			// leaving ``lastError`` intact, so a transient IPC
			// error followed by a successful ``get_config``
			// retry left the user staring at the stale error
			// message even though the app was working again.
			// We only emit a state change when there's actually
			// an error to clear, to avoid spurious state-emission
			// that would trigger extra renders in subscribers.
			if (status === "connected" && state.lastError !== null) {
				return {
					connectionStatus: status,
					lastError: null,
				};
			}
			return { connectionStatus: status };
		}),

	// Recording
	recordingState: "idle",
	setRecordingState: (state) => set({ recordingState: state }),
	lastError: null,
	setLastError: (error) => set({ lastError: error }),

	// Config
	config: null,
	setConfig: (config) => set({ config }),
	mergeConfig: (updates) =>
		set((state) => ({
			config: state.config ? { ...state.config, ...updates } : updates,
		})),
}));
