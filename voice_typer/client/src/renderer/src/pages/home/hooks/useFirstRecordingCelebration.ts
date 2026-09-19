import { useCallback } from "react";
import type { PythonCall } from "@/hooks/usePython";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";
import { FIRST_RECORD_CELEBRATED_KEY } from "../lib/constants";

export function useFirstRecordingCelebration(call: PythonCall) {
	const { showSnack } = useSnackbar();
	return useCallback(async () => {
		let alreadyCelebrated = false;
		try {
			alreadyCelebrated =
				localStorage.getItem(FIRST_RECORD_CELEBRATED_KEY) === "1";
		} catch {
			// localStorage unavailable, treat as not-celebrated
			// (proceed with the celebration check below).
			alreadyCelebrated = false;
		}
		if (alreadyCelebrated) return;
		try {
			const history = await call<HistoryRecord[]>("get_history", { limit: 1 });
			if (Array.isArray(history) && history.length === 1) {
				showSnack(t("home.firstDictationTitle"), "success", {
					description: t("home.firstDictationDesc"),
					duration: SNACKBAR_DEFAULT_DURATION_MS.warning,
				});
				try {
					localStorage.setItem(FIRST_RECORD_CELEBRATED_KEY, "1");
				} catch (e) {
					// localStorage unavailable, non-fatal.
					console.warn(
						"[renderer:Home] setItem first-record-celebrated failed:",
						e,
					);
				}
			}
		} catch (e) {
			// Non-critical, skip celebration if history fetch fails.
			console.warn("[renderer:Home] first-recording get_history failed:", e);
		}
	}, [call, showSnack]);
}
