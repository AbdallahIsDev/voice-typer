/**
 * Zustand store for cross-cutting app state.
 *
 * BACKLOG-004: Connection status, recording state, and config were split
 * across useConnection (hook) and useTheme (hook) with prop drilling
 * through App.tsx. This store provides a single source of truth for the
 * genuinely cross-cutting slices so any component can subscribe without
 * prop drilling.
 *
 * Scope is deliberately small — only connection, recording, and config.
 * Theme, navigation, and per-component local state stay in their existing
 * hooks (useTheme, useNavigation) because they're already clean and don't
 * prop-drill. This is an incremental improvement, not a full rewrite.
 *
 * Usage:
 *   import { useAppStore } from "@/stores/appStore";
 *   const connectionStatus = useAppStore(s => s.connectionStatus);
 *   const setConnectionStatus = useAppStore(s => s.setConnectionStatus);
 */

import { create } from "zustand";
import type { VoiceTyperConfig } from "@/types/config";
import type { RecordingState } from "@/types/ipc";

export type ConnectionStatus =
	| "connected"
	| "disconnected"
	| "connecting"
	| "restarting";

interface AppState {
	// ── Connection ──────────────────────────────────────────────
	/** Current connection status to the Python backend. */
	connectionStatus: ConnectionStatus;
	setConnectionStatus: (status: ConnectionStatus) => void;

	// ── Recording ───────────────────────────────────────────────
	/** Current recording state pushed from the backend. */
	recordingState: RecordingState;
	setRecordingState: (state: RecordingState) => void;

	/**
	 * Last error message from the backend (null = no error).
	 *
	 * Kept as ``string | null`` (not a structured
	 * ``AppError`` object) because App.tsx and Home.tsx render it
	 * directly as a React text node and pass it as a ``string | null``
	 * prop. Changing the type would break those consumers (which are
	 * owned by other fix agents).
	 *
	 * Auto-cleared on successful reconnection — see
	 * ``setConnectionStatus`` below.
	 */
	lastError: string | null;
	setLastError: (error: string | null) => void;

	// ── Config (cached snapshot) ────────────────────────────────
	/** Cached config snapshot — updated on get_config and config_changed. */
	config: Partial<VoiceTyperConfig> | null;
	setConfig: (config: Partial<VoiceTyperConfig> | null) => void;
	/** Merge partial config updates (e.g. from config_changed events). */
	mergeConfig: (updates: Partial<VoiceTyperConfig>) => void;
}

export const useAppStore = create<AppState>((set) => ({
	// Connection
	connectionStatus: "connecting",
	setConnectionStatus: (status) =>
		set((state) => {
			// When the backend reconnects, clear any
			// stale ``lastError`` so the UI doesn't keep showing an
			// error banner after a successful reconnection. The
			// previous implementation set only ``connectionStatus``,
			// leaving ``lastError`` intact — so a transient IPC
			// error followed by a successful ``get_config``
			// retry left the user staring at the stale error
			// message even though the app was working again.
			//
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
