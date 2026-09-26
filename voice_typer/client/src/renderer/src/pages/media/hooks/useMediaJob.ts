// useMediaJob, the Media page's job lifecycle: start (with the unified
// consent gate + retry), progress/complete/error push subscriptions,
// cancel, and mount-time hydration via `media_transcribe_status` so a
// job survives page navigation (ADR-0023).

import { useCallback, useEffect, useRef, useState } from "react";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import type { TranslationKey } from "@/i18n/translation-keys";
import { CONSENT_REQUIRED_CODE, MEDIA_URL_CONSENT_FIELD } from "@/lib/consent";
import { consentBodyKey, openConsentGate } from "@/lib/consentGate";
import { isNoModelError, mediaErrorKey } from "../lib/mediaErrorCopy";

export type MediaJobPhase =
	| "idle"
	| "loading_model"
	| "downloading"
	| "transcribing"
	| "done"
	| "error";

export interface MediaJobState {
	phase: MediaJobPhase;
	progress: number;
	etaSeconds: number | null;
	durationSeconds: number | null;
	jobId: string | null;
	rowId: number | null;
	chars: number | null;
	partial: boolean;
	errorKey: TranslationKey | null;
	errorCode: string | null;
}

const IDLE_STATE: MediaJobState = {
	phase: "idle",
	progress: 0,
	etaSeconds: null,
	durationSeconds: null,
	jobId: null,
	rowId: null,
	chars: null,
	partial: false,
	errorKey: null,
	errorCode: null,
};

interface StatusSnapshot {
	job: {
		job_id: string;
		status: string;
		progress?: number;
	} | null;
}

export interface UseMediaJobResult {
	job: MediaJobState;
	/** True while the start IPC round-trip is in flight (resolve phase). */
	starting: boolean;
	/** True from start-ack until completion/error (drives Cancel). */
	running: boolean;
	start: (source: string, useSubtitles: boolean) => Promise<void>;
	cancel: () => Promise<void>;
	reset: () => void;
}

const RUNNING_PHASES: ReadonlySet<MediaJobPhase> = new Set([
	"loading_model",
	"downloading",
	"transcribing",
]);

export function useMediaJob(): UseMediaJobResult {
	const { call } = usePython();
	const callRef = useLatestRef(call);
	const [job, setJob] = useState<MediaJobState>(IDLE_STATE);
	const [starting, setStarting] = useState(false);
	// Latest `start` identity for the consent gate's `onAllow` retry:
	// the gate stores the callback long after this render, so it must
	// re-invoke the CURRENT function, not a stale closure.
	const startRef = useRef<
		(source: string, useSubtitles: boolean) => Promise<void>
	>(async () => {});

	const start = useCallback(
		async (source: string, useSubtitles: boolean) => {
			setStarting(true);
			try {
				await call("media_transcribe_start", {
					source,
					use_subtitles: useSubtitles,
				});
				setJob({
					...IDLE_STATE,
					phase: "loading_model",
				});
			} catch (err) {
				const e = err as { code?: string; consent_field?: string };
				if (e.code === CONSENT_REQUIRED_CODE) {
					// C-MIC-3 just-in-time gate: grant + retry the SAME action.
					const field = e.consent_field ?? MEDIA_URL_CONSENT_FIELD;
					openConsentGate({
						consentField: field,
						bodyKey: consentBodyKey(field),
						onAllow: () => void startRef.current(source, useSubtitles),
					});
					return;
				}
				setJob({
					...IDLE_STATE,
					phase: "error",
					errorCode: typeof e.code === "string" ? e.code : null,
					errorKey: isNoModelError(e.code)
						? "media.errorNoModel"
						: mediaErrorKey(e.code),
				});
			} finally {
				setStarting(false);
			}
		},
		[call],
	);
	startRef.current = start;

	const cancel = useCallback(async () => {
		try {
			await call("media_transcribe_cancel");
		} catch (err) {
			console.warn("[renderer:media] cancel failed:", err);
		}
	}, [call]);

	const reset = useCallback(() => setJob(IDLE_STATE), []);

	// Progress pushes (phase + fraction + measured ETA + total duration).
	usePythonEvent("media_transcribe_progress", (data) => {
		if (!data) return;
		setJob((prev) =>
			prev.phase === "done" || prev.phase === "error"
				? prev
				: {
						...prev,
						phase: data.phase,
						progress:
							typeof data.progress === "number" ? data.progress : prev.progress,
						etaSeconds: data.eta_seconds ?? null,
						durationSeconds: data.duration_seconds ?? prev.durationSeconds,
						jobId: data.job_id,
					},
		);
	});

	// Completion (also fired for cancelled jobs carrying a partial).
	usePythonEvent("media_transcribe_complete", (data) => {
		if (!data) return;
		setJob((prev) =>
			prev.phase === "done"
				? prev
				: {
						...prev,
						phase: "done",
						progress: 1,
						jobId: data.job_id,
						rowId: data.row_id,
						chars: data.chars,
						partial: data.partial,
						etaSeconds: null,
						errorKey: null,
						errorCode: null,
					},
		);
	});

	// Failure pushed from the backend job thread (raw message is
	// diagnostics only; the copy comes from the code map).
	usePythonEvent("media_transcribe_error", (data) => {
		if (!data) return;
		setJob((prev) => ({
			...prev,
			phase: "error",
			jobId: data.job_id,
			etaSeconds: null,
			errorCode: data.code,
			errorKey: isNoModelError(data.code)
				? "media.errorNoModel"
				: mediaErrorKey(data.code),
		}));
	});

	// Hydrate a running job after navigation back to the page. One-shot
	// mount read through the callRef mirror (NOT a `[call]` dep: the
	// OOM-loop class — see `useLatestRef` + the dep-guard test).
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	useEffect(() => {
		let cancelled = false;
		void callRef
			.current<StatusSnapshot>("media_transcribe_status")
			.then((result) => {
				if (cancelled) return;
				const snapshot = result?.job;
				if (snapshot && snapshot.status === "running") {
					setJob((prev) =>
						prev.phase === "idle"
							? {
									...IDLE_STATE,
									phase: "transcribing",
									progress:
										typeof snapshot.progress === "number"
											? snapshot.progress
											: 0,
									jobId: snapshot.job_id,
								}
							: prev,
					);
				}
			})
			.catch(() => {
				/* hydration is best-effort */
			});
		return () => {
			cancelled = true;
		};
	}, []);

	return {
		job,
		starting,
		running: RUNNING_PHASES.has(job.phase),
		start,
		cancel,
		reset,
	};
}
