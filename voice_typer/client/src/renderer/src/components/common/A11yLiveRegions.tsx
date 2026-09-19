import { useT } from "@/i18n/i18n";
import type { ConnectionStatus } from "@/stores/appStore";
import { isRecoveringStatus } from "@/stores/appStore";
import type { Page, RecordingState } from "@/types/ipc";

interface A11yLiveRegionsProps {
	recordingState: RecordingState;
	/** Active route, coarse transcribing/loading announcements are
	 *  suppressed on the Home page (it owns its own specific live
	 *  region for those transitions). */
	currentPage: Page;
	connectionStatus: ConnectionStatus;
	/** Ref holding the previous connection status, mirrored by
	 *  `useConnectionToasts`, read during render so the recovery
	 *  region announces only real recoveries (not the initial
	 *  connecting → connected transition). */
	prevConnectionRef: React.RefObject<ConnectionStatus>;
}

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
				{isRecoveringStatus(connectionStatus) ? t("app.restartingBackend") : ""}
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
