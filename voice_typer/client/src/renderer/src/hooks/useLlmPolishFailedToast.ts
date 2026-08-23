// useLlmPolishFailedToast — surfaces backend ``llm_polish_failed``
// push events as one actionable in-app toast.
//
// The optional LLM-polish step post-processes a transcription with the
// configured LLM provider (grammar / punctuation). When that step
// raises (provider down, bad key, network error, consent revoked
// mid-flight) the dictation pipeline swallows the exception and still
// delivers the RAW transcription (`dictation_pipeline.py`) — so without
// this toast the feature fails SILENTLY: the user just sees unpolished
// text with no hint that AI cleanup was skipped or why.
//
// Cooldown: the backend can emit one event per transcription while
// polish is broken. A 5-minute wall-clock cooldown (store-backed, HMR
// safe) keeps the reminder at most ~once per 5 minutes; the fixed
// sonner ``id`` replaces an in-flight toast instead of stacking.

import { toast } from "sonner";
import { usePythonEvent } from "@/hooks/usePython";
import { useDegradationToastStore } from "@/stores/degradationToastStore";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/**
 * Renderer-side cooldown for the polish-failure toast. The failure is
 * non-fatal (raw text still delivered), so re-nagging on every
 * dictation would be noise; five minutes balances "told promptly"
 * against spam while the user goes off to fix their provider settings.
 */
const LLM_POLISH_TOAST_COOLDOWN_MS = 300_000;

/**
 * Subscribe to ``llm_polish_failed`` push events and render the
 * "delivered raw" toast. Call once at the top level of a component.
 *
 * @param t i18n translate function (from useT).
 */
export function useLlmPolishFailedToast(t: TFn): void {
	usePythonEvent("llm_polish_failed", (): (() => void) | undefined => {
		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.llmPolishFailedAt;
		if (last !== null && now - last < LLM_POLISH_TOAST_COOLDOWN_MS) {
			return undefined;
		}
		store.setLlmPolishFailedAt(now);
		store.setLastAnyToastShownAt(now);

		toast.warning(t("degradation.llmPolishFailed"), {
			id: "llm-polish-failed",
			description: t("degradation.llmPolishFailedHint"),
			duration: 8000,
		});
		return undefined;
	});
}
