// useForceCancel — the "Force cancel" state machine for a stuck
// dictation transcription, extracted from Home.tsx so the page file
// stays a thin composition root. Behaviour is preserved
// statement-for-statement — this machine is consent/privacy-sensitive
// surface wiring (the reveal gate and every reset path must stay
// exactly as they were).
//
// Owns:
//
//   - `applyStatusChange(data)` — the `status_change` handler body.
//     Entering "transcribing" stamps `transcribeStartedAt` (first
//     stamp wins — `prev ?? Date.now()`) and hides the affordance;
//     every other status resets both. Home.tsx keeps the subscription
//     (single `usePythonEvent("status_change", …)` registration) and
//     delegates here.
//   - the reveal delay: `showForceCancel` flips true after
//     `FORCE_CANCEL_DELAY_MS` inside "transcribing" so the affordance
//     only appears for genuinely stuck transcriptions.
//   - the belt-and-suspenders sync with the store's `recordingState`:
//     in case the page mounts mid-transcription (the push event that
//     started it fired before Home subscribed), the same stamp/reset
//     transitions are derived from the store snapshot.
//   - `handleForceCancel` — the affordance's click handler:
//     `force_cancel_transcription` IPC with success/failure toasts.

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import type { PythonCall } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { useAppStore } from "@/stores/appStore";
import type { StatusChangeEvent } from "@/types/ipc";
import { FORCE_CANCEL_DELAY_MS } from "../lib/constants";

/** Payload shape of the `status_change` push event's `data`. */
type StatusChangeData = StatusChangeEvent["data"];

/**
 * Own the force-cancel availability state machine + the cancel action.
 * Call once at the top level of Home; the store subscription for the
 * `status_change` event itself stays in the page root and forwards
 * payloads to {@link useForceCancel.return.applyStatusChange}.
 *
 * @param call the Python bridge `call` function (from `usePython()`).
 */
export function useForceCancel(call: PythonCall) {
	const [transcribeStartedAt, setTranscribeStartedAt] = useState<number | null>(
		null,
	);
	const [showForceCancel, setShowForceCancel] = useState(false);
	// The store snapshot mirrors the push-event stream (hydrate atomically
	// in useConnection); subscribing here keeps the belt-and-suspenders
	// sync effect self-contained.
	const recordingState = useAppStore((s) => s.recordingState);

	// status_change listener body — tracks entry into "transcribing" so
	// we can show "Force cancel" after FORCE_CANCEL_DELAY_MS. The hotkey
	// is NOT re-fetched here (it belongs to the `config_changed`
	// handler): the `status_change` event fires on every recording →
	// transcribing → idle transition, so a per-event `get_config`
	// round-trip would be wasted work.
	const applyStatusChange = useCallback((data?: StatusChangeData) => {
		const status = typeof data?.status === "string" ? data.status : "";
		if (status === "transcribing") {
			setTranscribeStartedAt((prev) => prev ?? Date.now());
			setShowForceCancel(false);
		} else {
			setTranscribeStartedAt(null);
			setShowForceCancel(false);
		}
	}, []);

	// surface "Force cancel" after FORCE_CANCEL_DELAY_MS in "transcribing".
	useEffect(() => {
		if (transcribeStartedAt === null) return;
		const timeout = setTimeout(
			() => setShowForceCancel(true),
			FORCE_CANCEL_DELAY_MS,
		);
		return () => clearTimeout(timeout);
	}, [transcribeStartedAt]);

	// Belt-and-suspenders: sync transcribe tracking with the recordingState
	// prop in case the page mounts mid-transcription.
	useEffect(() => {
		if (recordingState === "transcribing") {
			setTranscribeStartedAt((prev) => prev ?? Date.now());
		} else {
			setTranscribeStartedAt(null);
			setShowForceCancel(false);
		}
	}, [recordingState]);

	const handleForceCancel = useCallback(async () => {
		try {
			await call("force_cancel_transcription");
			toast.success(t("home.forceCancel"));
		} catch (err) {
			console.error("[renderer:Home] Force cancel failed:", err);
			toast.error(t("home.forceCancelFailed"));
		}
	}, [call]);

	return { showForceCancel, handleForceCancel, applyStatusChange };
}
