// useLastTranscriptionPreview — the ephemeral "last transcription" card
// state, extracted from Home.tsx so the page file stays a thin
// composition root. Behaviour is preserved statement-for-statement.
//
// Owns the `lastText` / `lastQuality` pair plus its auto-clear timer:
//
//   - `applyTranscriptionFinal(data)` — the text/quality half of the
//     `transcription_final` push handler (Home.tsx keeps the
//     subscription and calls this, then its shared
//     `debouncedRefreshFromEvent`). Empty/whitespace payloads are
//     ignored. Each accepted payload (re)starts the
//     `LAST_TEXT_AUTO_CLEAR_MS` idle timer that wipes the preview so a
//     previous transcription isn't exposed on a shared/locked screen.
//     A stale low-confidence flag can never attach to a NEW
//     transcription because quality is set/cleared everywhere the text
//     is (Whisper batch path only — see `build_quality_summary` in
//     `voice_typer/server/transcription.py`).
//   - the `recording_started` subscription — clears the text + timer
//     (but NOT the quality value, matching the original handler) when
//     a new recording begins.
//   - `handleUndo` / `handleRepaste` / `handleDiscard` — the preview
//     card's action callbacks. Undo/repaste surface a sonner error on
//     IPC failure; Discard clears the EPHEMERAL preview card only —
//     the transcription itself stays in persisted history (the server's
//     transcription_final payload carries no history id, so there is no
//     honest backend command to delete "the last transcription" from
//     here; local clear is the real contract).
//
// The hook also owns the timer's unmount cleanup (previously one branch
// of Home's combined timer-cleanup effect).

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { usePythonEvent } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import type {
	TranscriptionFinalEvent,
	TranscriptionQualitySummary,
} from "@/types/ipc";
import { LAST_TEXT_AUTO_CLEAR_MS } from "../lib/constants";
import type { CallFn } from "./useFirstRecordingCelebration";

/** Payload shape of the `transcription_final` push event's `data`. */
type TranscriptionFinalData = TranscriptionFinalEvent["data"];

/**
 * Own the ephemeral last-transcription preview (text + confidence
 * summary + auto-clear timer) and the card's action callbacks.
 *
 * @param call the Python bridge `call` function (from `usePython()`).
 * @param celebrateFirstRecording the first-run celebration callback
 *   (from `useFirstRecordingCelebration`) — invoked after a non-empty
 *   `transcription_final` payload is accepted, exactly as before.
 */
export function useLastTranscriptionPreview(
	call: CallFn,
	celebrateFirstRecording: () => void,
) {
	const [lastText, setLastText] = useState("");
	const [lastQuality, setLastQuality] = useState<
		TranscriptionQualitySummary | undefined
	>(undefined);
	const lastTextTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	// Clear the text and its pending auto-clear timer (no quality
	// change). Shared by `recording_started` and the undo path — the
	// exact statement sequence those handlers used inline.
	const clearTextAndTimer = useCallback(() => {
		setLastText("");
		if (lastTextTimer.current) {
			clearTimeout(lastTextTimer.current);
			lastTextTimer.current = null;
		}
	}, []);

	// transcription_final: update lastText + auto-clear, celebrate the
	// first ever transcription. The refresh half of the handler
	// (`debouncedRefreshFromEvent`) stays in Home.tsx — the page root
	// invokes it right after this.
	const applyTranscriptionFinal = useCallback(
		(data?: TranscriptionFinalData) => {
			if (typeof data?.text === "string" && data.text.trim()) {
				setLastText(data.text);
				setLastQuality(data.quality ?? undefined);
				if (lastTextTimer.current) clearTimeout(lastTextTimer.current);
				lastTextTimer.current = setTimeout(() => {
					setLastText("");
					setLastQuality(undefined);
				}, LAST_TEXT_AUTO_CLEAR_MS);
				celebrateFirstRecording();
			}
		},
		[celebrateFirstRecording],
	);

	// recording_started: a new dictation began — drop the preview text
	// (quality is intentionally left as-is, matching the original
	// handler; the card is unrenderable without text, and the next
	// accepted transcription_final always refreshes quality).
	usePythonEvent("recording_started", (): (() => void) | undefined => {
		clearTextAndTimer();
		return undefined;
	});

	// Clear the pending auto-clear timer on unmount (the other half of
	// Home's former combined timer-cleanup effect).
	useEffect(() => {
		return () => {
			if (lastTextTimer.current) {
				clearTimeout(lastTextTimer.current);
				lastTextTimer.current = null;
			}
		};
	}, []);

	const handleUndo = useCallback(async () => {
		try {
			await call("undo_last");
		} catch (err) {
			console.error("[renderer:Home] Undo failed:", err);
			toast.error(t("home.undoFailed"));
		}
		clearTextAndTimer();
	}, [call, clearTextAndTimer]);

	const handleRepaste = useCallback(async () => {
		try {
			await call("repaste_last");
		} catch (err) {
			console.error("[renderer:Home] Re-paste failed:", err);
			toast.error(t("home.repasteFailed"));
		}
	}, [call]);

	// Discard: clears the EPHEMERAL preview card only — the transcription
	// itself stays in persisted history (and remains on screen in
	// History). The server's transcription_final payload carries no
	// history id, so there is no honest backend command to delete "the
	// last transcription" from here; local clear is the real contract.
	const handleDiscard = useCallback(() => {
		setLastText("");
		setLastQuality(undefined);
		if (lastTextTimer.current) {
			clearTimeout(lastTextTimer.current);
			lastTextTimer.current = null;
		}
	}, []);

	return {
		lastText,
		lastQuality,
		applyTranscriptionFinal,
		handleUndo,
		handleRepaste,
		handleDiscard,
	};
}
