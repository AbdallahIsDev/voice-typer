import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { useAppStore } from "@/stores/appStore";
import type { LausuConfig } from "@/types/config";
import { useCallback, useEffect, useRef, useState } from "react";

let _cachedConfig: LausuConfig | null = null;

/**
 * /4: extract a human-readable warning string from a `set_config`
 * response envelope. Returns `null` when the response is a plain
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
	config: LausuConfig | null;
	saving: boolean;
	pending: boolean;
	/**
	 * /5: per-flush error message string (null when no
	 * error).  Surfaces the backend's specific validator text
	 */
	error: string | null;
	/**
	 * Set when the initial `get_config` fetch fails. The Settings
	 * page renders a load-failure EmptyState with a Retry action
	 */
	loadError: string | null;
	/**
	 * : true while debounced writes are queued OR a flush
	 * is in flight.  Consumers (Settings.tsx) can use this to
	 */
	hasPendingOrSaving: boolean;
	updateConfig: (updates: Partial<LausuConfig>) => Promise<void>;
	updateConfigDebounced: (
		key: keyof LausuConfig,
		value: unknown,
		delayMs?: number,
	) => void;
	loadConfig: () => Promise<void>;
	/**
	 * Merge an externally-pushed config update (e.g. the
	 * `config_changed` Python event) into local state AND the diff
	 */
	mergeExternalConfig: (data: Partial<LausuConfig>) => void;
	/**
	 * Flush any pending (debounced or microtask-queued) writes
	 * immediately. Exposed so the Settings page can flush on
	 */
	flushPendingUpdates: () => Promise<void>;
}

export function useSettingsConfig(): UseSettingsConfigResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const [config, setConfig] = useState<LausuConfig | null>(_cachedConfig);
	const [saving, setSaving] = useState(false);
	const [pending, setPending] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [loadError, setLoadError] = useState<string | null>(null);

	const lastSavedConfigRef = useRef<LausuConfig | null>(_cachedConfig);
	const pendingUpdatesRef = useRef<Partial<LausuConfig>>({});
	const flushScheduledRef = useRef(false);
	const flushPromiseResolversRef = useRef<Array<() => void>>([]);
	const flushPendingUpdatesRef = useRef<() => Promise<void>>(async () => {});
	const configRef = useRef<LausuConfig | null>(_cachedConfig);
	useEffect(() => {
		configRef.current = config;
	}, [config]);

	// callRef mirror (Home.tsx pattern): `loadConfig` must keep a STABLE
	const callRef = useLatestRef(call);

	// read this ref, so callers don't need to wire up their own
	const cancelledRef = useRef(false);
	useEffect(() => {
		cancelledRef.current = false;
		return () => {
			cancelledRef.current = true;
		};
	}, []);

	const loadConfig = useCallback(
		async (isCancelled: () => boolean = () => cancelledRef.current) => {
			try {
				const result = await callRef.current<LausuConfig>("get_config");
				if (isCancelled()) return;
				setLoadError(null);
				_cachedConfig = result;
				lastSavedConfigRef.current = result;
				setConfig(result);
			} catch (err) {
				if (!isCancelled()) {
					console.error(
						"[renderer:useSettingsConfig] Failed to load config:",
						err,
					);
					setLoadError(err instanceof Error ? err.message : String(err));
				}
			}
		},
		[callRef],
	);

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
			const result = (await call("set_config", diff)) as unknown;
			const warningMessage = _extractSaveWarning(result);
			if (warningMessage !== null) {
				setError(warningMessage);
				showSnack(warningMessage, "warning");
			} else {
				setError(null);
			}
			lastSavedConfigRef.current = {
				...lastSaved,
				...(diff as Partial<LausuConfig>),
			};
		} catch (err) {
			const message =
				err instanceof Error && err.message ? err.message : "unknown error";
			console.error(
				"[renderer:useSettingsConfig] Failed to update config:",
				err,
			);
			//do NOT call loadConfig() here.  The
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
		async (updates: Partial<LausuConfig>) => {
			const currentConfig = configRef.current;
			if (!currentConfig) return;
			setSaving(true);
			const newConfig = { ...currentConfig, ...updates };
			_cachedConfig = newConfig;
			setConfig(newConfig);
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
		[], // stable identity, reads from refs
	);

	const debouncedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>(
		{},
	);
	const pendingDebouncedValuesRef = useRef<Partial<LausuConfig>>({});
	const updateConfigDebounced = useCallback(
		(key: keyof LausuConfig, value: unknown, delayMs = 500) => {
			const currentConfig = configRef.current;
			if (currentConfig) {
				const newConfig = { ...currentConfig, [key]: value };
				_cachedConfig = newConfig;
				setConfig(newConfig);
			}
			if (debouncedTimers.current[key as string]) {
				clearTimeout(debouncedTimers.current[key as string]);
			}
			(pendingDebouncedValuesRef.current as Record<string, unknown>)[
				key as string
			] = value;
			setPending(true);
			debouncedTimers.current[key as string] = setTimeout(() => {
				void updateConfig({ [key]: value } as Partial<LausuConfig>);
				delete debouncedTimers.current[key as string];
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

	useEffect(() => {
		const flushPendingDebounced = () => {
			const pendingDebounced = pendingDebouncedValuesRef.current;
			const hasPendingDebounced = Object.keys(pendingDebounced).length > 0;
			const hasPendingFlush = Object.keys(pendingUpdatesRef.current).length > 0;
			if (!hasPendingDebounced && !hasPendingFlush) return;
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
		};
	}, []);

	// updater (updaters must be pure: StrictMode double-invokes
	const mergeExternalConfig = useCallback((data: Partial<LausuConfig>) => {
		const prev = configRef.current;
		if (prev) {
			const merged = { ...prev, ...data } as LausuConfig;
			configRef.current = merged;
			setConfig(merged);
			_cachedConfig = merged;
		}
		if (lastSavedConfigRef.current) {
			lastSavedConfigRef.current = {
				...lastSavedConfigRef.current,
				...data,
			} as LausuConfig;
		}
	}, []);

	return {
		config,
		saving,
		pending,
		error,
		loadError,
		hasPendingOrSaving: pending || saving,
		updateConfig,
		updateConfigDebounced,
		loadConfig,
		mergeExternalConfig,
		flushPendingUpdates,
	};
}
