// useAsrBackendDisabledToast — surfaces backend
// ``asr_backend_disabled`` push events as one actionable in-app toast.
//
// When an ASR backend (e.g. whisper on CUDA) fails to load repeatedly
// the registry auto-disables it and falls back to a different engine
// (`asr_registry.py`). Transcription keeps working, but possibly slower
// or with a different model than the user chose — a change they'd
// otherwise never notice. The toast names what was disabled and points
// to the Models page where a replacement can be picked.
//
// Sibling hook: ``useLastResortUnloadedToast`` handles the TERMINAL
// case (no backend left at all). This hook covers the recoverable
// fallback case.
//
// Rate limiting mirrors ``useLastResortUnloadedToast``:
//   1. Per-backend 15-min cooldown — the disable is latched server-side
//      but re-config / retries can re-emit; don't re-nag.
//   2. Short global dedupe window — several backends breaking within
//      seconds collapse to ONE visible notification.
// A per-backend sonner ``id`` replaces an in-flight toast for the same
// backend instead of stacking.

import { usePythonEvent } from "@/hooks/usePython";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/**
 * Per-backend cooldown — mirrors the last-resort toast so both ASR
 * degradation surfaces nag at the same rate.
 */
const ASR_DISABLED_TOAST_COOLDOWN_MS = 900_000;

/**
 * Global dedupe window across ALL degradation toasts (slightly longer
 * than the 8s toast duration).
 */
const DEGRADATION_TOAST_DEDUPE_MS = 10_000;

/**
 * Subscribe to ``asr_backend_disabled`` push events and render the
 * fallback pointer toast. Call once at the top level of a component.
 *
 * @param t i18n translate function (from useT).
 * @param onOpenModels callback that navigates to the Models page (App
 *   wires ``() => navigate("models")``).
 */
export function useAsrBackendDisabledToast(
	t: TFn,
	onOpenModels: () => void,
): void {
	const { showSnack } = useSnackbar();
	usePythonEvent("asr_backend_disabled", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as { backend?: unknown };
		const backend =
			typeof payload.backend === "string" ? payload.backend : "unknown";

		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.asrBackendDisabledAt[backend];
		if (last !== undefined && now - last < ASR_DISABLED_TOAST_COOLDOWN_MS) {
			return undefined;
		}
		const lastShown = store.lastAnyToastShownAt;
		if (lastShown !== null && now - lastShown < DEGRADATION_TOAST_DEDUPE_MS) {
			return undefined;
		}
		store.setAsrBackendDisabledAt(backend, now);
		store.setLastAnyToastShownAt(now);

		showSnack(t("degradation.asrBackendDisabled", { backend }), "warning", {
			id: `asr-backend-disabled:${backend}`,
			description: t("degradation.asrBackendDisabledHint"),
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
			action: {
				label: t("common.openModels"),
				onClick: onOpenModels,
			},
		});
		return undefined;
	});
}
