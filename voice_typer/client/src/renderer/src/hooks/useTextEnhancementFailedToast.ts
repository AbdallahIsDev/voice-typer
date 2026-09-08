// useTextEnhancementFailedToast — surfaces backend
// ``text_enhancement_failed`` push events as one actionable in-app
// toast.
//
// The rule-based AI-enhancement step (Step 7b) post-processes a
// transcription with local grammar/style rules. When that step raises
// (a broken rule set, an unexpected input) the dictation pipeline
// swallows the exception and still delivers the RAW transcription
// (`dictation_pipeline/enhancement_steps.py`) — so without this toast
// the failure is invisible: the user just sees un-enhanced text with
// no hint that AI cleanup was skipped.
//
// Distinct from ``useLlmPolishFailedToast``: this hook listens for the
// RULE-BASED enhancer's event, not the LLM-polish path. The backend
// publishes the two events under different names so the LLM
// toast never fires for a local rule failure and vice versa.
//
// Cooldown: the backend can emit one event per transcription while
// enhancement is broken. A 5-minute wall-clock cooldown (store-backed,
// HMR safe) keeps the reminder at most ~once per 5 minutes; the fixed
// sonner ``id`` replaces an in-flight toast instead of stacking.

import { usePythonEvent } from "@/hooks/usePython";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/**
 * Renderer-side cooldown for the enhancement-failure toast. The failure
 * is non-fatal (raw text still delivered), so re-nagging on every
 * dictation would be noise; five minutes balances "told promptly"
 * against spam.
 */
const TEXT_ENHANCEMENT_TOAST_COOLDOWN_MS = 300_000;

/**
 * Subscribe to ``text_enhancement_failed`` push events and render the
 * "delivered raw" toast. Call once at the top level of a component.
 *
 * @param t i18n translate function (from useT).
 */
export function useTextEnhancementFailedToast(t: TFn): void {
	const { showSnack } = useSnackbar();
	usePythonEvent("text_enhancement_failed", (): (() => void) | undefined => {
		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.textEnhancementFailedAt;
		if (last !== null && now - last < TEXT_ENHANCEMENT_TOAST_COOLDOWN_MS) {
			return undefined;
		}
		store.setTextEnhancementFailedAt(now);
		store.setLastAnyToastShownAt(now);

		showSnack(t("degradation.textEnhancementFailed"), "warning", {
			id: "text-enhancement-failed",
			description: t("degradation.textEnhancementFailedHint"),
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
		});
		return undefined;
	});
}
