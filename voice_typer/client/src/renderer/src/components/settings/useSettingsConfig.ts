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

import { useCallback, useEffect, useRef, useState } from "react";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";

// Module-level cache — persists across page navigations so settings
// render instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null;

export interface UseSettingsConfigResult {
	config: VoiceTyperConfig | null;
	saving: boolean;
	pending: boolean;
	saved: boolean;
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
}

export function useSettingsConfig(): UseSettingsConfigResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [saving, setSaving] = useState(false);
	const [pending, setPending] = useState(false);
	const [saved, setSaved] = useState(false);
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
			await call("set_config", diff);
			lastSavedConfigRef.current = {
				...lastSaved,
				...(diff as Partial<VoiceTyperConfig>),
			};
			showSnack(t("settings.savedToast"), "success");
			if (savedTimeoutRef.current) clearTimeout(savedTimeoutRef.current);
			setSaved(true);
			savedTimeoutRef.current = setTimeout(() => {
				setSaved(false);
				savedTimeoutRef.current = null;
			}, 2000);
		} catch (err) {
			console.error("Failed to update config:", err);
			await loadConfig();
			showSnack(t("settings.saveFailedToast"), "error");
		} finally {
			setSaving(false);
			resolveAll();
		}
	}, [call, loadConfig, showSnack]);

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
			// PVT-028 Fix #8: mark the save as pending so the indicator
			// shows "Pending…" while the debounce timer is running.
			setPending(true);
			debouncedTimers.current[key as string] = setTimeout(() => {
				void updateConfig({ [key]: value } as Partial<VoiceTyperConfig>);
				delete debouncedTimers.current[key as string];
				if (Object.keys(debouncedTimers.current).length === 0) {
					setPending(false);
				}
			}, delayMs);
		},
		[updateConfig],
	);

	// Cleanup pending debounced timers + flush pending writes on unmount.
	useEffect(() => {
		return () => {
			if (Object.keys(pendingUpdatesRef.current).length > 0) {
				void flushPendingUpdatesRef.current();
			}
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
		updateConfig,
		updateConfigDebounced,
		loadConfig,
		mergeExternalConfig,
	};
}
