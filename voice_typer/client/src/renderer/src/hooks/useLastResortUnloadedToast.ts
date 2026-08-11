/**
 * useLastResortUnloadedToast — surfaces backend ``asr_last_resort_unloaded``
 * push events as an in-app sonner toast.
 *
 * The backend publishes this event via ``event_bus.publish`` (see
 * ``voice_typer/server/asr/circuit_breaker.py:381``) whenever
 * ``registry.get_active()`` falls through to an unloaded last-resort backend
 * — i.e. transcription would silently return empty. The OS tray
 * notification for the same condition (``model_manager.py``
 * ``_on_last_resort_unloaded``) is gated behind the user's "Show
 * Notifications" toggle AND requires a live host transport, so users who
 * disabled tray notifications get ZERO feedback today. This hook makes the
 * Models-page pointer visible in-app no matter what the OS tray is doing.
 *
 * The toast carries an "Open Models" action (mirrors the host ``notification``
 * event's ``click_path: "/models"``) so the user can jump straight to the
 * Models page and download / repair the model.
 *
 * Rate limiting has TWO layers, mirroring the server:
 *
 * 1. Per-backend 15-min cooldown (mirrors the server's ModelManager
 *    ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS = 900``): the registry's one-shot
 *    latch can be reset by the 15s ``get_status`` probe, so a permanently
 *    unloaded backend would otherwise re-toast every ~15s. A per-backend
 *    wall-clock timestamp (``Date.now()`` — the server equivalent uses
 *    ``time.monotonic()``, but the renderer has no cross-reload monotonic
 *    source and the 15-min window makes a clock-jump edge case negligible)
 *    suppresses repeats within the cooldown window — the user is told
 *    promptly, and a still-broken backend re-toasts at most ~4x/hour. A
 *    per-backend sonner ``id`` additionally REPLACES any in-flight toast
 *    for the same backend instead of stacking.
 *
 * 2. Short GLOBAL dedupe window (``LAST_RESORT_TOAST_DEDUPE_MS``): rapid
 *    GENUINE transitions — e.g. whisper and qwen both breaking within a
 *    few seconds — collapse to ONE visible notification instead of
 *    stacking a toast per backend. This is renderer-side only (the server
 *    gate matches the tray's suppressions, but two genuinely-broken
 *    backends in quick succession would otherwise each toast).
 *
 * Both timestamps live in the ``lastResortToastStore`` Zustand store (NOT
 * a module-level ``Map`` in this file) so Vite HMR / hot-reload of this
 * hook module does NOT reset them — editing the hook while the app is
 * running used to clear the cooldown and immediately re-toast a backend
 * the user was just pointed at the Models page for.
 */

import { toast } from "sonner";
import { usePythonEvent } from "@/hooks/usePython";
import { useLastResortToastStore } from "@/stores/lastResortToastStore";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/**
 * Renderer-side mirror of the server's ModelManager last-resort cooldown
 * (``_LAST_RESORT_NOTIFY_COOLDOWN_SECS = 900.0``). Kept in lockstep so the
 * in-app toast and the tray notification never spam the user at different
 * rates.
 */
const LAST_RESORT_TOAST_COOLDOWN_MS = 900_000;

/**
 * Global renderer-side dedupe window: a second ``asr_last_resort_unloaded``
 * event for ANY backend within this window is suppressed so rapid genuine
 * transitions collapse to one visible toast. Chosen to be slightly longer
 * than the toast's ``duration`` (8s) so a dismiss + immediate re-fire
 * doesn't re-nag within the same notification cycle.
 */
const LAST_RESORT_TOAST_DEDUPE_MS = 10_000;

/**
 * Test seam — clear the per-backend cooldown timestamps (delegates to the
 * Zustand store that now owns them). @internal
 */
export function _resetLastResortToastCooldownForTest(): void {
	useLastResortToastStore.getState().resetLastToastedAt();
}

/**
 * Subscribe to ``asr_last_resort_unloaded`` push events and render the
 * Models-page pointer toast. Call once at the top level of a component;
 * the subscription lives for the component's lifetime.
 *
 * @param t i18n translate function (from useT).
 * @param onOpenModels callback that navigates to the Models page (App wires
 *   ``() => navigate("models")``).
 */
export function useLastResortUnloadedToast(
	t: TFn,
	onOpenModels: () => void,
): void {
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
				// get_status probe reset the registry latch) — the user was
				// already pointed at the Models page; don't re-nag.
				return undefined;
			}
			// Global dedupe: a toast for ANY backend was shown within the
			// short dedupe window — rapid genuine transitions (e.g. whisper
			// + qwen breaking within seconds) collapse to ONE visible
			// notification instead of stacking per backend.
			const lastShown = state.lastToastShownAt;
			if (lastShown !== null && now - lastShown < LAST_RESORT_TOAST_DEDUPE_MS) {
				return undefined;
			}
			state.setLastToastedAt(backend, now);
			state.setLastToastShownAt(now);

			toast.warning(t("models.lastResortUnloaded", { backend }), {
				// Per-backend stable id: a cooldown-boundary re-fire for the
				// SAME backend REPLACES its in-flight toast instead of stacking;
				// a DIFFERENT backend gets its own toast (two broken backends
				// each surface once rather than silently overwriting each other).
				id: `asr-last-resort-unloaded:${backend}`,
				description: t("models.lastResortUnloadedHint"),
				duration: 8000,
				action: {
					label: t("common.openModels"),
					onClick: onOpenModels,
				},
			});
			return undefined;
		},
	);
}
