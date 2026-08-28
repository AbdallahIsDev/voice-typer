import { useCallback } from "react";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";
import { FIRST_RECORD_CELEBRATED_KEY } from "../lib/constants";

/**
 * The shape of the `call` function provided by `usePython()`. Declared
 * locally so the hook can be unit-tested without pulling in the full
 * `usePython` dependency graph.
 */
export type CallFn = <T = unknown>(
	type: string,
	data?: Record<string, unknown>,
) => Promise<T>;

/**
 * Previously used `get_today_stats` and checked `count === 1`
 * — this triggered on the first dictation of ANY day, not the user's
 * lifetime first. We now check `get_history({limit: 1})` and celebrate
 * only when the user has exactly one historical record (this just-added
 * one). The flag is persisted to localStorage so we never celebrate twice.
 *
 *  / : extracted from Home.tsx so the page file stays a
 * thin composition root. Behaviour is preserved byte-for-byte.
 *
 * The previous catch block exited the ENTIRE callback via `return`,
 * which suppressed the first-recording celebration in environments where
 * localStorage throws (Safari private mode, strict CSP, sandboxed
 * iframe). The comment said "treat as not-celebrated" (i.e. proceed as
 * if the flag is unset) but the code did the OPPOSITE. Now we proceed —
 * if the read fails, we just skip the "already celebrated" short-circuit
 * and let the celebration run (the write path below is already wrapped
 * in its own try/catch).
 */

export function useFirstRecordingCelebration(call: CallFn) {
	const { showSnack } = useSnackbar();
	return useCallback(async () => {
		let alreadyCelebrated = false;
		try {
			alreadyCelebrated =
				localStorage.getItem(FIRST_RECORD_CELEBRATED_KEY) === "1";
		} catch {
			// localStorage unavailable — treat as not-celebrated
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
					// localStorage unavailable — non-fatal.
					console.warn(
						"[renderer:Home] setItem first-record-celebrated failed:",
						e,
					);
				}
			}
		} catch (e) {
			// Non-critical — skip celebration if history fetch fails.
			console.warn("[renderer:Home] first-recording get_history failed:", e);
		}
	}, [call, showSnack]);
}
