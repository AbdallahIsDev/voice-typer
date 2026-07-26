// useSettingsConfig — owns the VoiceTyperConfig state, the debounced /
// batched `set_config` IPC writes, and the save-state indicators
// (saving / pending / saved) used by the Settings page's sticky header.
//
// Extracted from src/renderer/src/pages/Settings.tsx (PVT-028) so the
// page component is responsible for layout/UX only — not for the
// intricate batched-write + diff + flush + sync logic.
//
// Behaviour is identical to the previous inline implementation:
//   - `updateConfig(updates)` applies the update to local state
//     immediately, mirrors it into the Zustand appStore synchronously
//     (D1-FIX so App.tsx's route guard sees the new value on the next
//     render), and queues a microtask flush that sends a single
//     diffed `set_config` IPC (PERF-002 batching).
//   - `updateConfigDebounced(key, value, delayMs)` is the same but
//     defers the IPC commit by `delayMs` so rapid keystrokes collapse
//     into one write. Sets `pending=true` while the timer is running
//     so the save indicator can show "Pending…" (PVT-028 Fix #8).
//   - `loadConfig()` re-fetches from the backend.
//   - `flushPendingUpdates()` is exposed (via ref) so the page's
//     unmount cleanup can flush any in-flight writes.
//
// XA-14 fixes (settings save flow):
//   - XA-14-1: debounced text-field saves are flushed on unmount +
//     `beforeunload` (no longer dropped when the user navigates away
//     or quits the app within the 500ms debounce window). Mirrors
//     `useTheme.ts`'s QUIT-FLUSH-FIX pattern.
//   - XA-14-2: backend validator text is surfaced in the error snack
//     (instead of the generic "Failed to save setting" message).
//   - XA-14-3: partial-success `model_errors` envelope is surfaced as
//     a warning (instead of being silently swallowed).
//   - XA-14-4: rejected (unknown) keys are surfaced as a warning.
//   - XA-14-5: `error` state is exposed so the SettingsSaveIndicator
//     can render a "Save failed" state.
//   - XA-14-6: `hasPendingOrSaving` flag is exposed so consumers can
//     guard `onNavigate` calls with a ConfirmDialog.
//   - XA-14-9: a failed save does NOT call `loadConfig()` immediately
//     (the user's attempted value is retained for edit + retry).

import { useCallback, useEffect, useRef, useState } from "react";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";

// Module-level cache — persists across page navigations so settings
// render instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null;

/**
 * XA-14-3/4: extract a human-readable warning string from a `set_config`
 * response envelope. Returns `null` when the response is a plain
 * full-success ack (no `data` field). Returns the failing field name
 * for partial-success envelopes and the first rejected key name for
 * rejected-keys envelopes.
 *
 * The backend (config_handlers.py) returns:
 *   - `{type:"ack"}` on full success (no `data` field).
 *   - `{type:"ack", data:{status:"partial", model_errors:[{code, field,
 *      message}], applied:[...]}}` when `change_model` /
 *      `set_active_backend` raised during the apply step.
 *   - `{type:"ack", data:{accepted:[...], rejected:[...]}}` when some
 *      keys were silently dropped (unknown / not in the allowlist).
 */
function _extractSaveWarning(response: unknown): string | null {
	if (typeof response !== "object" || response === null) return null;
	const envelope = response as { type?: string; data?: unknown };
	const payload = (
		envelope && typeof envelope === "object" && "data" in envelope
			? (envelope as { data?: unknown }).data
			: response
	) as Record<string, unknown> | null;
	if (!payload || typeof payload !== "object") return null;
	// (1) Partial-success envelope.
	if (payload.status === "partial") {
		const modelErrors = payload.model_errors;
		if (Array.isArray(modelErrors) && modelErrors.length > 0) {
			const first = modelErrors[0] as
				| { field?: string; message?: string }
				| undefined;
			if (first && typeof first.field === "string") {
				return `${t("settings.saveFailedToast")}: ${first.field} not applied`;
			}
		}
		return t("settings.saveFailedToast");
	}
	// (2) Rejected-keys envelope.
	const rejected = payload.rejected;
	if (Array.isArray(rejected) && rejected.length > 0) {
		const firstRejected = rejected[0];
		if (typeof firstRejected === "string") {
			return `${t("settings.saveFailedToast")}: ${firstRejected} not recognized`;
		}
	}
	return null;
}

export interface UseSettingsConfigResult {
	config: VoiceTyperConfig | null;
	saving: boolean;
	pending: boolean;
	saved: boolean;
	/** XA-14-2/5: per-flush error message string (null when no
	 *  error).  Surfaces the backend's specific validator text
	 *  (e.g. "field 'history_max_entries' must be in [10, 1000000],
	 *  got 5") instead of the generic "Failed to save setting"
	 *  toast text.  Cleared on the next successful save. */
	error: string | null;
	/** XA-14-6: true while debounced writes are queued OR a flush
	 *  is in flight.  Consumers (Settings.tsx) can use this to
	 *  guard `onNavigate` calls with a ConfirmDialog so the user
	 *  doesn't abandon unsaved changes. */
	hasPendingOrSaving: boolean;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => Promise<void>;
	updateConfigDebounced: (
		key: keyof VoiceTyperConfig,
		value: unknown,
		delayMs?: number,
	) => void;
	loadConfig: () => Promise<void>;
	/** Merge an externally-pushed config update (e.g. the
	 *  `config_changed` Python event) into local state AND the diff
	 *  baseline so the next flush doesn't re-send values the backend
	 *  already has. */
	mergeExternalConfig: (data: Partial<VoiceTyperConfig>) => void;
	/** Flush any pending (debounced or microtask-queued) writes
	 *  immediately. Exposed so the Settings page can flush on
	 *  unmount / `beforeunload`. */
	flushPendingUpdates: () => Promise<void>;
}

export function useSettingsConfig(): UseSettingsConfigResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [saving, setSaving] = useState(false);
	const [pending, setPending] = useState(false);
	const [saved, setSaved] = useState(false);
	// XA-14-2/5: per-flush error string surfaced to the UI so the
	// indicator can render a "Save failed" state with the backend's
	// specific message.  Cleared on the next successful save.
	const [error, setError] = useState<string | null>(null);
	const savedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	// PERF-002: batched writes — accumulate updates in `pendingUpdatesRef`
	// and flush them in a single `set_config` IPC via a microtask.
	const lastSavedConfigRef = useRef<VoiceTyperConfig | null>(_cachedConfig);
	const pendingUpdatesRef = useRef<Partial<VoiceTyperConfig>>({});
	const flushScheduledRef = useRef(false);
	const flushPromiseResolversRef = useRef<Array<() => void>>([]);
	const flushPendingUpdatesRef = useRef<() => Promise<void>>(async () => {});
	// PERF-MEMO-001: ref mirror of `config` so `updateConfig` /
	// `updateConfigDebounced` can have stable identity (empty deps).
	const configRef = useRef<VoiceTyperConfig | null>(_cachedConfig);
	useEffect(() => {
		configRef.current = config;
	}, [config]);

	const loadConfig = useCallback(async () => {
		try {
			const result = await call<VoiceTyperConfig>("get_config");
			_cachedConfig = result;
			lastSavedConfigRef.current = result;
			setConfig(result);
		} catch (err) {
			console.error("Failed to load config:", err);
		}
	}, [call]);

	const flushPendingUpdates = useCallback(async () => {
		const updates = pendingUpdatesRef.current;
		pendingUpdatesRef.current = {};
		const resolvers = flushPromiseResolversRef.current;
		flushPromiseResolversRef.current = [];
		flushScheduledRef.current = false;
		const resolveAll = () => {
			for (const resolve of resolvers) resolve();
		};
		const lastSaved = lastSavedConfigRef.current;
		if (!lastSaved) {
			resolveAll();
			return;
		}
		// Shallow-diff against the last saved snapshot so no-op writes
		// (e.g. a slider dragged back to its original value) are skipped.
		const lastSavedRecord = lastSaved as unknown as Record<string, unknown>;
		const diff: Record<string, unknown> = {};
		for (const [key, value] of Object.entries(updates)) {
			if (!Object.is(lastSavedRecord[key], value)) diff[key] = value;
		}
		if (Object.keys(diff).length === 0) {
			resolveAll();
			return;
		}
		try {
			// XA-14-3/4: capture the response envelope so the
			// partial-success `model_errors` and the
			// unknown-key `rejected` arrays can be surfaced
			// to the user instead of silently swallowed.  The
			// server returns `{type:"ack", data:{...}}` for
			// both full success and partial success; the
			// `data` field is omitted entirely on the
			// all-keys-accepted common case.
			const result = (await call("set_config", diff)) as unknown;
			const warningMessage = _extractSaveWarning(result);
			if (warningMessage !== null) {
				setError(warningMessage);
				showSnack(warningMessage, "warning");
			} else {
				// XA-4-10: drop the redundant success snackbar.
				// The sticky "Saved ✓" indicator (driven by
				// `saved` state below) already confirms the
				// save; firing a transient snackbar too was
				// noisy on every keystroke commit. The
				// error-case toast is still fired in the
				// catch block below.
				setError(null);
			}
			lastSavedConfigRef.current = {
				...lastSaved,
				...(diff as Partial<VoiceTyperConfig>),
			};
			if (savedTimeoutRef.current) clearTimeout(savedTimeoutRef.current);
			setSaved(true);
			savedTimeoutRef.current = setTimeout(() => {
				setSaved(false);
				savedTimeoutRef.current = null;
			}, 2000);
		} catch (err) {
			// XA-14-2: surface the backend's specific
			// validator text (e.g. "field 'history_max_entries'
			// must be in [10, 1000000], got 5") instead of
			// the generic "Failed to save setting" message.
			// The Python error envelope carries the message
			// on `data.message`; usePython re-throws it as a
			// JS Error so `err.message` is populated.
			const message =
				err instanceof Error && err.message ? err.message : "unknown error";
			console.error("Failed to update config:", err);
			// XA-14-9: do NOT call loadConfig() here.  The
			//  local state retains the user's attempted
			//  value so they can edit + retry without
			//  retyping; calling loadConfig() would silently
			//  overwrite the attempted value with the
			//  backend's old value, hiding the failure.
			//  The diff baseline (lastSavedConfigRef) still
			//  points at the backend's last-known value, so
			//  a retry will re-send the same diff.
			const display =
				message === "unknown error"
					? t("settings.saveFailedToast")
					: `${t("settings.saveFailedToast")}: ${message}`;
			showSnack(display, "error");
			setError(display);
		} finally {
			setSaving(false);
			resolveAll();
		}
	}, [call, showSnack]);

	useEffect(() => {
		flushPendingUpdatesRef.current = flushPendingUpdates;
	}, [flushPendingUpdates]);

	const updateConfig = useCallback(
		async (updates: Partial<VoiceTyperConfig>) => {
			const currentConfig = configRef.current;
			if (!currentConfig) return;
			setSaving(true);
			const newConfig = { ...currentConfig, ...updates };
			_cachedConfig = newConfig;
			setConfig(newConfig);
			// D1-FIX: synchronously mirror the update into the Zustand
			// appStore so App.tsx's route guard sees the new value on
			// the next render (the config_changed push event arrives
			// later, asynchronously).
			useAppStore.getState().mergeConfig(updates);
			pendingUpdatesRef.current = {
				...pendingUpdatesRef.current,
				...updates,
			};
			const flushPromise = new Promise<void>((resolve) => {
				flushPromiseResolversRef.current.push(resolve);
			});
			if (!flushScheduledRef.current) {
				flushScheduledRef.current = true;
				queueMicrotask(() => {
					void flushPendingUpdatesRef.current();
				});
			}
			await flushPromise;
		},
		[], // PERF-MEMO-001: stable identity — reads from refs
	);

	// UX-007: debounced update for text inputs that fire on every
	// keystroke. Keeps a local draft in component state; commits via
	// updateConfig after `delayMs` of idle.
	const debouncedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>(
		{},
	);
	// XA-14-1: pending debounced values keyed by field name. The
	// timer callbacks capture `(key, value)` in their closures, but
	// closures are inaccessible from the unmount cleanup — so we
	// mirror the latest pending value here.  On unmount / page
	// unload, we merge this object into `pendingUpdatesRef` and
	// flush, so a user who types into a text field and navigates
	// away within the 500ms debounce window doesn't lose their
	// edit.  Mirrors the QUIT-FLUSH-FIX pattern from `useTheme.ts`.
	const pendingDebouncedValuesRef = useRef<Partial<VoiceTyperConfig>>({});
	const updateConfigDebounced = useCallback(
		(key: keyof VoiceTyperConfig, value: unknown, delayMs = 500) => {
			const currentConfig = configRef.current;
			if (currentConfig) {
				const newConfig = { ...currentConfig, [key]: value };
				_cachedConfig = newConfig;
				setConfig(newConfig);
			}
			if (debouncedTimers.current[key as string]) {
				clearTimeout(debouncedTimers.current[key as string]);
			}
			// XA-14-1: mirror the latest pending value so the
			// unmount/unload flush can recover it without
			// waiting for the timer to fire.
			(pendingDebouncedValuesRef.current as Record<string, unknown>)[
				key as string
			] = value;
			// PVT-028 Fix #8: mark the save as pending so the indicator
			// shows "Pending…" while the debounce timer is running.
			setPending(true);
			debouncedTimers.current[key as string] = setTimeout(() => {
				void updateConfig({ [key]: value } as Partial<VoiceTyperConfig>);
				delete debouncedTimers.current[key as string];
				// XA-14-1: the timer fired and handed the
				// value off to updateConfig (which merged
				// it into pendingUpdatesRef).  Drop it
				// from the pending-debounced mirror so
				// the unmount flush doesn't double-send.
				delete (pendingDebouncedValuesRef.current as Record<string, unknown>)[
					key as string
				];
				if (Object.keys(debouncedTimers.current).length === 0) {
					setPending(false);
				}
			}, delayMs);
		},
		[updateConfig],
	);

	// XA-14-1: cleanup pending debounced timers + flush pending
	// writes on unmount.  Pre-fix, the unmount cleanup cleared
	// debounced timers WITHOUT firing them, dropping any value the
	// user had typed but not yet committed (e.g. an LLM API key
	// typed into a text field, with the user navigating to another
	// page within the 500ms debounce window).  We now merge the
	// pending debounced values into `pendingUpdatesRef` BEFORE
	// clearing the timers, then flush — so the IPC write actually
	// reaches the backend.  Mirrors `useTheme.ts`'s QUIT-FLUSH-FIX.
	//
	// A `beforeunload` listener covers the close-to-tray / window-
	// close / app-quit path (the React unmount cleanup does NOT
	// fire on `beforeunload` — Electron tears down the renderer
	// process directly).  The listener calls the same flush path
	// so a pending edit isn't dropped when the user quits the app
	// mid-debounce.  Fire-and-forget: the IPC layer queues the
	// write before the process exits.
	useEffect(() => {
		const flushPendingDebounced = () => {
			const pendingDebounced = pendingDebouncedValuesRef.current;
			const hasPendingDebounced = Object.keys(pendingDebounced).length > 0;
			const hasPendingFlush = Object.keys(pendingUpdatesRef.current).length > 0;
			if (!hasPendingDebounced && !hasPendingFlush) return;
			// Merge any not-yet-fired debounced values into
			// the flush buffer so a single set_config call
			// carries both the debounced edits and any
			// already-queued microtask writes.
			if (hasPendingDebounced) {
				pendingUpdatesRef.current = {
					...pendingUpdatesRef.current,
					...pendingDebounced,
				};
				pendingDebouncedValuesRef.current = {};
			}
			void flushPendingUpdatesRef.current();
		};
		const onBeforeUnload = () => flushPendingDebounced();
		window.addEventListener("beforeunload", onBeforeUnload);
		return () => {
			window.removeEventListener("beforeunload", onBeforeUnload);
			flushPendingDebounced();
			Object.values(debouncedTimers.current).forEach(clearTimeout);
			if (savedTimeoutRef.current) {
				clearTimeout(savedTimeoutRef.current);
				savedTimeoutRef.current = null;
			}
		};
	}, []);

	// Merge an externally-pushed config update (e.g. the
	// `config_changed` Python event) into local state AND the diff
	// baseline so the next flush doesn't re-send values the backend
	// already has.
	const mergeExternalConfig = useCallback((data: Partial<VoiceTyperConfig>) => {
		setConfig((prev) => {
			if (!prev) return prev;
			const merged = { ...prev, ...data } as VoiceTyperConfig;
			_cachedConfig = merged;
			return merged;
		});
		if (lastSavedConfigRef.current) {
			lastSavedConfigRef.current = {
				...lastSavedConfigRef.current,
				...data,
			} as VoiceTyperConfig;
		}
	}, []);

	return {
		config,
		saving,
		pending,
		saved,
		error,
		hasPendingOrSaving: pending || saving,
		updateConfig,
		updateConfigDebounced,
		loadConfig,
		mergeExternalConfig,
		flushPendingUpdates,
	};
}
