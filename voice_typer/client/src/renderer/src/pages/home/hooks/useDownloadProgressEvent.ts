// useDownloadProgressEvent — HuggingFace model download progress for
// the Home progressbar, extracted from Home.tsx so the page file stays
// a thin composition root. Behaviour is preserved
// statement-for-statement.
//
// Subscribes to `download_progress` events emitted while a HuggingFace
// model download is in flight. Percentages outside 0-100 are ignored
// (defensive against malformed payloads). The percentage resets to
// null whenever the recording state leaves "loading" so a stale bar
// can never linger after the download finishes or is cancelled.

import { useEffect, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import type { RecordingState } from "@/types/ipc";

/**
 * Subscribe to `download_progress` and return the current percentage
 * (0-100), or `null` when no download bar should render. Call once at
 * the top level of Home.
 *
 * @param recordingState the store's recording state — the bar resets
 *   whenever it leaves "loading".
 */
export function useDownloadProgressEvent(
	recordingState: RecordingState,
): number | null {
	const [downloadPct, setDownloadPct] = useState<number | null>(null);

	usePythonEvent("download_progress", (data): (() => void) | undefined => {
		const pct = data?.percent;
		if (typeof pct === "number" && pct >= 0 && pct <= 100) {
			setDownloadPct(pct);
		}
		return undefined;
	});

	useEffect(() => {
		if (recordingState !== "loading") setDownloadPct(null);
	}, [recordingState]);

	return downloadPct;
}
