// useCloudFallbackToast, surfaces the ``cloud_fallback_used`` push
// event (cloud ASR degradation).
//
// When the configured cloud transcription provider raises (network
// outage, expired key, provider 5xx) the engine falls back to the LOCAL
// speech engine for that transcription, the dictation itself still
// succeeds, so without a visible signal the degradation is silent: the
// user keeps getting text (at local-engine accuracy, without the cloud
// they chose) and has no way to notice their provider is down until
// they open the log.
//
// This hook is the single consumer of the event. It shows ONE warning
// toast naming the actual state (cloud failed → local engine took
// over) with the provider named in the hint, mirroring the
// ``llm_polish_failed`` surface: the output was still delivered, so
// the notice must inform, not alarm.
//
// Cooldown: the backend emits one event per transcription while the
// provider is down, so a 5-minute store-backed window (same window as
// ``llm_polish_failed``, both fire per transcription during an
// outage) keeps the reminder at most ~once per 5 minutes instead of
// once per dictation.

import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Sonner id (single replaceable surface). */
const CLOUD_FALLBACK_TOAST_ID = "cloud-fallback-used";

/** Suppression window while the cloud provider is down (ms), matches
 * the polish-failure window (both fire per transcription). */
const CLOUD_FALLBACK_TOAST_COOLDOWN_MS = 300_000;

/**
 * Subscribe to ``cloud_fallback_used`` push events and show the
 * local-engine-fallback notice. Call once at the top level of a
 * component (App wires it with the i18n `t` function).
 *
 * @param t i18n translate function (from useT).
 */
export function useCloudFallbackToast(t: TranslateFn): void {
	const { showSnack } = useSnackbar();
	usePythonEvent("cloud_fallback_used", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as { provider?: unknown };
		const provider =
			typeof payload.provider === "string" && payload.provider !== ""
				? payload.provider
				: "unknown";

		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.cloudFallbackUsedAt;
		if (last !== null && now - last < CLOUD_FALLBACK_TOAST_COOLDOWN_MS) {
			return undefined;
		}
		store.setCloudFallbackUsedAt(now);
		store.setLastAnyToastShownAt(now);

		showSnack(t("degradation.cloudFallbackUsed"), "warning", {
			id: CLOUD_FALLBACK_TOAST_ID,
			description: t("degradation.cloudFallbackUsedHint", { provider }),
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
		});
		return undefined;
	});
}
