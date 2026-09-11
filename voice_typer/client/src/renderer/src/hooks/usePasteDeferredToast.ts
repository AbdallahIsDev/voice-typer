// usePasteDeferredToast, surfaces the ``paste_deferred`` push event
// (auto-paste skipped, transcription safe on the clipboard).
//
// After a dictation the pipeline writes the text to the clipboard and
// synthesizes a paste keystroke into the focused app. Two real-world
// conditions make that keystroke impossible: the target app activated
// macOS Secure Input (password field, the OS blocks synthesized
// keystrokes by design) or an IME text composition was in progress.
// In both cases the TRANSCRIPTION IS NOT LOST, it sits on the
// clipboard, but the user sees no text appear where they expect it,
// and without this toast they have no way to know their words are one
// manual paste away.
//
// This hook is the single consumer of the event. It shows ONE warning
// toast stating the actual state (auto-paste skipped, text on the
// clipboard) with a reason-specific hint naming WHY the keystroke was
// dropped (Secure Input vs IME composition) so the user knows pasting
// manually is safe and expected to work.
//
// Cooldown: the secure-input emitter already dedupes to once per
// session server-side, but the IME-composition path can defer one
// paste per dictation while a composition stays open, a 10s window
// collapses consecutive deferrals into one visible notice.

import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Sonner id (single replaceable surface). */
const PASTE_DEFERRED_TOAST_ID = "paste-deferred";

/** Suppression window for back-to-back deferrals (ms). */
const PASTE_DEFERRED_TOAST_COOLDOWN_MS = 10_000;

/** Reason → hint key: each emitter reason gets copy that names the
 *  actual blocker (the generic key covers any future reason value). */
const PASTE_DEFERRED_HINT_KEYS: Record<string, string> = {
	secure_input: "degradation.pasteDeferredHintSecureInput",
	ime_composition: "degradation.pasteDeferredHintIme",
};

/**
 * Subscribe to ``paste_deferred`` push events and show the
 * clipboard notice. Call once at the top level of a component (App
 * wires it with the i18n `t` function).
 *
 * @param t i18n translate function (from useT).
 */
export function usePasteDeferredToast(t: TranslateFn): void {
	const { showSnack } = useSnackbar();
	usePythonEvent("paste_deferred", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as { reason?: unknown };
		const reason = typeof payload.reason === "string" ? payload.reason : "";
		const hintKey =
			PASTE_DEFERRED_HINT_KEYS[reason] ?? "degradation.pasteDeferredHint";

		const now = Date.now();
		const store = useDegradationToastStore.getState();
		const last = store.pasteDeferredAt;
		if (last !== null && now - last < PASTE_DEFERRED_TOAST_COOLDOWN_MS) {
			return undefined;
		}
		store.setPasteDeferredAt(now);
		store.setLastAnyToastShownAt(now);

		showSnack(t("degradation.pasteDeferred"), "warning", {
			id: PASTE_DEFERRED_TOAST_ID,
			description: t(hintKey),
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
		});
		return undefined;
	});
}
