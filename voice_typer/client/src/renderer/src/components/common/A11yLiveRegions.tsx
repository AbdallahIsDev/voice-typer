import { useT } from "@/i18n/i18n";
import type { ConnectionStatus } from "@/stores/appStore";
import type { Page, RecordingState } from "@/types/ipc";

interface A11yLiveRegionsProps {
	recordingState: RecordingState;
	/** Active route — coarse transcribing/loading announcements are
	 *  suppressed on the Home page (it owns its own specific live
	 *  region for those transitions). */
	currentPage: Page;
	connectionStatus: ConnectionStatus;
	/** Ref holding the previous connection status, mirrored by
	 *  `useConnectionToasts` — read during render so the recovery
	 *  region announces only real recoveries (not the initial
	 *  connecting → connected transition). */
	prevConnectionRef: React.RefObject<ConnectionStatus>;
}

/**
 * The app shell's three screen-reader live regions.
 *
 * Split into THREE regions with distinct politeness streams:
 *
 *   1. Polite + recording-state stream. Previously a single
 *      aria-atomic region concatenated the recording and connection
 *      streams, so any change in either re-announced the ENTIRE
 *      combined text — meaning a brief `connectionStatus` flicker
 *      caused "Recording started." to be re-announced even though
 *      recording state hadn't changed. Isolating the streams means
 *      each only re-announces when ITS OWN content changes.
 *   2. Assertive + connection-ERROR stream (disconnected,
 *      restarting) — these interrupt the user since they indicate a
 *      problem requiring attention. Split from the recovery region so
 *      the recovery announcement stays polite (non-interrupting) and
 *      doesn't yank the user out of what they were doing.
 *   3. Polite + connection-RECOVERY stream (re-connected after an
 *      outage) — non-interrupting so the user hears it but isn't
 *      pulled out of what they were doing.
 *
 * Reuses existing i18n keys only (`app.lostConnection`,
 * `app.restartingBackend`, `about.connected`) — no new translation
 * keys are required.
 */
export function A11yLiveRegions({
	recordingState,
	currentPage,
	connectionStatus,
	prevConnectionRef,
}: A11yLiveRegionsProps) {
	const t = useT();
	return (
		<>
			<div aria-live="polite" aria-atomic="true" className="sr-only">
				{recordingState === "recording" ? t("a11y.recordingStarted") : ""}
				{/* Coarse transcribing/loading announcements are suppressed
				    on the Home page: Home's dynamic status line (its single
				    specific live region) already announces "Transcribing…
				    please wait" / "Downloading model…", so this coarse
				    "Transcribing audio." / "Loading model…" would
				    double-announce the same transition across the two live
				    regions. On every OTHER page the coarse announcement is
				    the only one (Home isn't mounted), so keep it there. */}
				{recordingState === "transcribing" && currentPage !== "home"
					? t("a11y.transcribingAudio")
					: ""}
				{recordingState === "idle" ? t("a11y.ready") : ""}
				{recordingState === "error" ? t("a11y.errorOccurred") : ""}
				{recordingState === "loading" && currentPage !== "home"
					? t("a11y.loadingModel")
					: ""}
				{recordingState === "cancelling" ? t("a11y.cancelling") : ""}
			</div>
			<div aria-live="assertive" aria-atomic="true" className="sr-only">
				{connectionStatus === "disconnected" ? t("app.lostConnection") : ""}
				{connectionStatus === "restarting" ? t("app.restartingBackend") : ""}
			</div>
			<div aria-live="polite" aria-atomic="true" className="sr-only">
				{connectionStatus === "connected" &&
				prevConnectionRef.current !== "connected" &&
				prevConnectionRef.current !== "connecting"
					? t("about.connected")
					: ""}
			</div>
		</>
	);
}
