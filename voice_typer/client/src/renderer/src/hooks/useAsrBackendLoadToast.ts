// useAsrBackendLoadToast — surfaces the ASR backend background-load
// outcome (``asr_backend_ready`` / ``asr_backend_load_failed`` push
// events).
//
// The model/backend load runs on a daemon thread AFTER the ``set_config``
// IPC already acked (its response carries a ``model_loading`` envelope).
// The renderer's Models-page "Using model" success snack fires on that
// ack — so when the background load later FAILS, that success toast is a
// lie the user has no way to notice: the frame was previously dropped at
// the host's event gate and no renderer surface existed.
//
// This hook is the single consumer of both events:
//   - ``asr_backend_load_failed`` → ONE error toast naming the model +
//     the server-provided failure reason, with an "Open Models" action.
//     A fixed sonner id (per model) replaces an in-flight failure toast
//     instead of stacking when the user retries a failing model.
//   - ``asr_backend_ready`` → dismisses any in-flight failure toast from
//     this hook (the ready event is the completion signal the ack's
//     ``model_loading`` envelope promised — the load SUCCEEDED, so the
//     failure surface must clear). No success toast is shown: success is
//     already surfaced by the Models-page snack + the ``status_change``
//     pill, and a second toast would be pure noise.
//
// Cooldown: a short window suppresses re-toast storms from a
// load-retry loop (the backend emits one failure per attempt). The
// timestamp lives in `degradationToastStore` (Zustand, in its own
// module) so Vite HMR of this hook file does not reset it mid-session.

import { usePythonEvent } from "@/hooks/usePython";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { capToastDescription } from "./capToastDescription";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/** Sonner id of the load-failure toast (single replaceable surface). */
const LOAD_FAILED_TOAST_ID = "asr-backend-load-failed";

/** Suppression window for back-to-back load failures (ms). */
const LOAD_FAILED_TOAST_COOLDOWN_MS = 10_000;

/**
 * Subscribe to the backend-load lifecycle push events and surface the
 * failure case. Call once at the top level of a component (App wires
 * it with the i18n `t` function + a navigate callback).
 *
 * @param t i18n translate function (from useT).
 * @param onOpenModels callback that navigates to the Models page (App
 *   wires ``() => navigate("models")``).
 */
export function useAsrBackendLoadToast(t: TFn, onOpenModels: () => void): void {
	const { showSnack, clearSnack } = useSnackbar();

	usePythonEvent("asr_backend_load_failed", (data) => {
		const payload = (data ?? {}) as {
			backend?: unknown;
			model_size?: unknown;
			failure_reason?: unknown;
		};
		const modelSize =
			typeof payload.model_size === "string" && payload.model_size !== ""
				? payload.model_size
				: typeof payload.backend === "string"
					? payload.backend
					: "unknown";
		const failureReason =
			typeof payload.failure_reason === "string"
				? capToastDescription(payload.failure_reason)
				: undefined;

		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.asrBackendLoadFailedAt;
		if (last !== null && now - last < LOAD_FAILED_TOAST_COOLDOWN_MS) {
			return undefined;
		}
		store.setAsrBackendLoadFailedAt(now);
		store.setLastAnyToastShownAt(now);

		showSnack(t("models.snack.loadFailed", { name: modelSize }), "error", {
			id: LOAD_FAILED_TOAST_ID,
			description: failureReason,
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
			action: {
				label: t("common.openModels"),
				onClick: onOpenModels,
			},
		});
		return undefined;
	});

	usePythonEvent("asr_backend_ready", () => {
		// The load succeeded — clear this hook's failure surface. Sonner's
		// dismiss on an id that is not showing is a harmless no-op, so
		// this needs no "is a failure toast visible" bookkeeping.
		clearSnack(LOAD_FAILED_TOAST_ID);
		return undefined;
	});
}
