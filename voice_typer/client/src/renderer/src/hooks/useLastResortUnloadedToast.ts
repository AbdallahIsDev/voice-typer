import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import { useLastResortToastStore } from "@/stores/lastResortToastStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

const LAST_RESORT_TOAST_COOLDOWN_MS = 900_000;

const LAST_RESORT_TOAST_DEDUPE_MS = 10_000;

export function _resetLastResortToastCooldownForTest(): void {
	useLastResortToastStore.getState().resetLastToastedAt();
}

export function useLastResortUnloadedToast(
	t: TranslateFn,
	onOpenModels: () => void,
): void {
	const { showSnack } = useSnackbar();
	usePythonEvent(
		"asr_last_resort_unloaded",
		(data): (() => void) | undefined => {
			const payload = (data ?? {}) as { backend?: string };
			const backend =
				typeof payload.backend === "string" ? payload.backend : "unknown";

			const now = Date.now();
			const state = useLastResortToastStore.getState();
			const last = state.lastToastedAt[backend];
			if (last !== undefined && now - last < LAST_RESORT_TOAST_COOLDOWN_MS) {
				// Same backend re-fired inside the cooldown (e.g. the 15s
				// get_status probe reset the registry latch), the user was
				// already pointed at the Models page; don't re-nag.
				return undefined;
			}
			// Global dedupe: a toast for ANY backend was shown within the
			// short dedupe window, rapid genuine transitions (e.g. whisper
			// + qwen breaking within seconds) collapse to ONE visible
			// notification instead of stacking per backend.
			const lastShown = state.lastToastShownAt;
			if (lastShown !== null && now - lastShown < LAST_RESORT_TOAST_DEDUPE_MS) {
				return undefined;
			}
			state.setLastToastedAt(backend, now);
			state.setLastToastShownAt(now);

			showSnack(t("models.lastResortUnloaded"), "warning", {
				// Per-backend stable id: a cooldown-boundary re-fire for the
				// SAME backend REPLACES its in-flight toast instead of stacking;
				// a DIFFERENT backend gets its own toast (two broken backends
				// each surface once rather than silently overwriting each other).
				id: `asr-last-resort-unloaded:${backend}`,
				description: t("models.lastResortUnloadedHint"),
				duration: SNACKBAR_DEFAULT_DURATION_MS.error,
				action: {
					label: t("common.openModels"),
					onClick: onOpenModels,
				},
			});
			return undefined;
		},
	);
}
