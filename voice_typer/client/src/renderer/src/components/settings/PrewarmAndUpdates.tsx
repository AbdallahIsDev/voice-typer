// Prewarm cache status + offline update notice.
//
// History: an earlier revision of this component hosted an in-app
// "Check for Updates" button that fired a `fetch()` against the
// GitHub releases API (a remote HTTPS endpoint). The "Check for
// Updates" button was removed because the offline-by-default UX
// was preferred; if a future iteration wants to add it back
// (user-initiated GitHub API check), C-DATA-1 permits it under
// the auto-update category — see docs/auto-update-feature.md.
// The Updates section now shows the installed version plus a
// static message directing the user to open the GitHub releases
// page in their browser.
//
// The prewarm cache status surface is unaffected: it queries the
// Python sidecar over the local IPC bridge (in-process, no network).

import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useRef, useState } from "react";
import { ReadonlyRow } from "@/components/common/ReadonlyRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
// Reuse the byte/relative-time formatters already exported by About
// (kept exported there for unit-test coverage) instead of duplicating them.
import { formatBytes, formatRelativeTime } from "@/pages/About";
import pkg from "../../../../../package.json";
import type { IsVisibleFn } from "./types";

const APP_VERSION = pkg.version as string;

// Static anchor URL for the "View Changelog" button. This is NOT a
// renderer-initiated network call — it is an `<a href>` element the
// user explicitly clicks, which Electron routes to the system browser
// (or a new BrowserWindow depending on config). The C-DATA-1 rule
// forbids automated network calls from the production code path; a
// user-clicked external link is the user's browser making the call,
// not Voice Typer.
const RELEASES_URL = "https://github.com/AbdallahIsDev/voice-typer/releases";

// ADR-0009 Issue 3: shape of the ``get_prewarm_status`` IPC response.
// Mirrors the dict returned by voice_typer.server.prewarm.get_prewarm_status().
interface PrewarmStatus {
	last_run: string | null;
	elapsed_s: number | null;
	cache_ratio: number;
	cache_label: "hot" | "partial" | "cold" | "unknown";
	cached_bytes: number;
	total_bytes: number;
	prewarm_running: boolean;
}

// ADR-0009 Issue 3: badge for the prewarm cache status
// (Hot/Partial/Cold/Unknown). Color-coded to match the StatusDot visual
// rhythm: green=hot, amber=partial, red=cold, gray=unknown.
function CacheStatusBadge({ label }: { label: PrewarmStatus["cache_label"] }) {
	const colorClass =
		label === "hot"
			? "bg-success"
			: label === "partial"
				? "bg-warning"
				: label === "cold"
					? "bg-destructive"
					: "bg-muted-foreground/40";
	const textKey =
		label === "hot"
			? "about.cacheHot"
			: label === "partial"
				? "about.cachePartial"
				: label === "cold"
					? "about.cacheCold"
					: "about.cacheUnknown";
	return (
		<span className="inline-flex items-center gap-1.5 text-(--text-primary)">
			<span className={`size-1.5 rounded-full ${colorClass}`} />
			{t(textKey)}
		</span>
	);
}

// Status rows render via the shared `ReadonlyRow` primitive (default
// `value-emphasized` variant: muted label + prominent value) — see
// `@/components/common/ReadonlyRow` for the rationale and the contrast
// with `SettingRow` (which emphasises the LABEL for editable controls).

export interface PrewarmAndUpdatesProps {
	/** Search-filter predicate. Optional — defaults to "always visible"
	 *  so the component can render standalone (in tests, etc.). */
	isVisible?: IsVisibleFn;
}

const ALWAYS_VISIBLE: IsVisibleFn = () => true;

/** Translated row + section + action-button labels rendered by this
 *  component. Exported so the Settings page's search auto-switch
 *  can include them in the privacy tab's label set — without
 *  this, typing "prewarm", "cache", "version", "update", etc. wouldn't
 *  route to the privacy tab because `getTabLabels()` only knows the
 *  two section titles (`about.cacheTitle`, `about.updatesTitle`).
 *
 *  Called inside `handleSearchChange` so the labels reflect the current
 *  locale at the moment the user types. Keep in sync with the labels
 *  passed to `isVisible(...)` and the button text below. */
export function getPrewarmAndUpdatesLabels(): string[] {
	return [
		t("about.cacheTitle"),
		t("about.cacheDescription"),
		t("about.prewarmStatus"),
		t("about.lastRun"),
		t("about.cacheHealth"),
		t("about.prewarmElapsed"),
		t("about.refreshCacheStatus"),
		t("about.runPrewarmNow"),
		t("about.viewPrewarmLog"),
		t("about.updatesTitle"),
		t("about.updatesDescription"),
		t("about.installedVersion"),
		t("about.offlineUpdatesMessage"),
		t("about.viewChangelog"),
	];
}

export default function PrewarmAndUpdates({
	isVisible = ALWAYS_VISIBLE,
}: PrewarmAndUpdatesProps = {}) {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	// ADR-0009 Issue 3: prewarm cache status. null = not fetched yet.
	const [prewarmStatus, setPrewarmStatus] = useState<PrewarmStatus | null>(
		null,
	);
	const [prewarmLoading, setPrewarmLoading] = useState(false);
	// "Run Prewarm Now" button state. runPrewarmLoading is true while the
	// run_prewarm IPC is in flight; once spawned, prewarmRunning (from
	// get_prewarm_status) takes over as the progress indicator.
	const [runPrewarmLoading, setRunPrewarmLoading] = useState(false);

	const fetchPrewarmStatus = async () => {
		setPrewarmLoading(true);
		try {
			const status = await call<PrewarmStatus>("get_prewarm_status");
			setPrewarmStatus(status);
		} catch (e) {
			// Best-effort: leave the previous status (or null) in place.
			// The card renders an "Unknown" placeholder when null.
			console.warn(
				"[renderer:PrewarmAndUpdates] get_prewarm_status failed:",
				e,
			);
		} finally {
			setPrewarmLoading(false);
		}
	};

	// Trigger a manual prewarm run. Spawns a detached subprocess
	// (pythonw -m voice_typer.server.prewarm --force). After spawning,
	// polls get_prewarm_status every 2s until prewarm_running flips to
	// False, then refreshes the card and shows a completion toast.
	// The poll is cancellable via prewarmPollCancelledRef so the
	// loop stops calling setPrewarmStatus / showSnack / IPC after the
	// component unmounts.
	const prewarmPollCancelledRef = useRef(false);
	useEffect(() => {
		// Initialize ref on mount; flip to true on unmount.
		prewarmPollCancelledRef.current = false;
		return () => {
			prewarmPollCancelledRef.current = true;
		};
	}, []);
	const handleRunPrewarm = async () => {
		// Guard: don't re-warm if already hot (button should be disabled,
		// but defend in depth).
		if (prewarmStatus?.cache_label === "hot") {
			showSnack(t("about.prewarmAlreadyHot"), "info");
			return;
		}
		setRunPrewarmLoading(true);
		try {
			const result = await call<{ started: boolean }>("run_prewarm");
			if (result?.started) {
				showSnack(t("about.prewarmStarting"), "info");
				// Poll get_prewarm_status every 2s until prewarm_running
				// flips to False. The subprocess takes ~20-50s on a warm
				// disk, ~50s+ on a cold one.
				const pollDeadline = Date.now() + 120_000; // 2 min cap
				const poll = async () => {
					while (Date.now() < pollDeadline) {
						if (prewarmPollCancelledRef.current) return;
						await new Promise((r) => setTimeout(r, 2000));
						if (prewarmPollCancelledRef.current) return;
						try {
							const status = await call<PrewarmStatus>("get_prewarm_status");
							if (prewarmPollCancelledRef.current) return;
							setPrewarmStatus(status);
							if (!status.prewarm_running) {
								// Prewarm finished — show completion toast
								// based on the new cache label.
								showSnack(t("about.prewarmComplete"), "success");
								return;
							}
						} catch {
							// Backend went away — stop polling.
							return;
						}
					}
					// Timed out — silent (the subprocess may still be
					// running; the user can Refresh manually).
				};
				poll(); // fire-and-forget; don't block the UI
			}
		} catch (err) {
			showSnack(
				t("about.prewarmFailed") +
					(err instanceof Error ? `: ${err.message}` : ""),
				"error",
			);
		} finally {
			setRunPrewarmLoading(false);
		}
	};

	// Open the prewarm log file in the OS default text editor. Calls the
	// open_prewarm_log IPC handler which uses os.startfile (Windows), open
	// (macOS), or xdg-open (Linux). Shows a toast if the log file doesn't
	// exist or can't be opened.
	const handleViewPrewarmLog = async () => {
		try {
			const result = await call<{
				opened: boolean;
				path?: string;
				reason?: string;
			}>("open_prewarm_log");
			if (result?.opened) {
				showSnack(t("about.prewarmLogOpened"), "success");
			} else if (result?.reason === "not_found") {
				showSnack(t("about.prewarmLogNotFound"), "info");
			} else {
				showSnack(t("about.prewarmLogOpenFailed"), "error");
			}
		} catch (err) {
			showSnack(
				t("about.prewarmLogOpenFailed") +
					(err instanceof Error ? `: ${err.message}` : ""),
				"error",
			);
		}
	};

	// The "Check for Updates" button was removed because the
	// offline-by-default UX was preferred; if a future iteration
	// wants to add it back (user-initiated GitHub API check),
	// C-DATA-1 permits it under the auto-update category — see
	// docs/auto-update-feature.md. The Updates section now shows
	// the installed version plus a static message directing the
	// user to open the GitHub releases page in their browser.

	// On mount: fetch prewarm status only. No network call is ever
	// fired from this component (the prewarm status call is a local
	// IPC bridge to the Python sidecar, not a network call).
	useEffect(() => {
		let cancelled = false;
		const load = async () => {
			try {
				const ps = await call<PrewarmStatus>("get_prewarm_status");
				if (!cancelled) setPrewarmStatus(ps);
			} catch (e) {
				// leave prewarmStatus as null; card renders "Unknown"
				console.warn(
					"[renderer:PrewarmAndUpdates] initial get_prewarm_status failed:",
					e,
				);
			}
		};
		load();
		return () => {
			cancelled = true;
		};
	}, [call]);

	return (
		<>
			{/* ── Cache Status (ADR-0009 Issue 3) ─────────────────────── */}
			{/* section-level hide-when-empty check — when no row
				matches the active search query, hide the whole section
				(including its action buttons) so the tab doesn't show a
				lonely header above an empty body. */}
			{[
				t("about.prewarmStatus"),
				t("about.lastRun"),
				t("about.cacheHealth"),
				t("about.prewarmElapsed"),
			].some((l) => isVisible(l, undefined, t("about.cacheTitle"))) && (
				<SettingsSection
					title={t("about.cacheTitle")}
					description={t("about.cacheDescription")}
				>
					{isVisible(
						t("about.prewarmStatus"),
						undefined,
						t("about.cacheTitle"),
					) && (
						<ReadonlyRow
							label={t("about.prewarmStatus")}
							value={
								prewarmStatus?.prewarm_running ? (
									<span className="inline-flex items-center gap-1.5 text-(--text-primary)">
										<span className="size-1.5 animate-pulse rounded-full bg-info" />
										{t("about.cacheRunning")}
									</span>
								) : prewarmStatus ? (
									<CacheStatusBadge label={prewarmStatus.cache_label} />
								) : (
									<span className="text-(--text-muted)">
										{t("about.checking")}
									</span>
								)
							}
						/>
					)}
					{isVisible(t("about.lastRun"), undefined, t("about.cacheTitle")) && (
						<ReadonlyRow
							label={t("about.lastRun")}
							value={
								prewarmStatus?.last_run
									? formatRelativeTime(prewarmStatus.last_run)
									: prewarmStatus
										? t("about.neverRun")
										: t("about.checking")
							}
						/>
					)}
					{isVisible(
						t("about.cacheHealth"),
						undefined,
						t("about.cacheTitle"),
					) && (
						<ReadonlyRow
							label={t("about.cacheHealth")}
							value={
								prewarmStatus && prewarmStatus.total_bytes > 0
									? `${Math.round(prewarmStatus.cache_ratio * 100)}% (${formatBytes(
											prewarmStatus.cached_bytes,
										)} / ${formatBytes(prewarmStatus.total_bytes)})`
									: prewarmStatus
										? `${Math.round(prewarmStatus.cache_ratio * 100)}%`
										: t("about.checking")
							}
						/>
					)}
					{isVisible(
						t("about.prewarmElapsed"),
						undefined,
						t("about.cacheTitle"),
					) && (
						<ReadonlyRow
							label={t("about.prewarmElapsed")}
							value={
								prewarmStatus?.elapsed_s !== null &&
								prewarmStatus?.elapsed_s !== undefined
									? `${prewarmStatus.elapsed_s.toFixed(1)}s`
									: prewarmStatus
										? t("about.unknown")
										: t("about.checking")
							}
						/>
					)}
					<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border">
						<Button
							variant="outline"
							size="sm"
							onClick={fetchPrewarmStatus}
							disabled={prewarmLoading}
						>
							{prewarmLoading
								? t("about.checking")
								: t("about.refreshCacheStatus")}
						</Button>
						{/* "Run Prewarm Now" button. Disabled when cache is Hot
						(no point re-warming), when prewarm is already
						running, or while the run_prewarm IPC is in flight. */}
						<Button
							variant="default"
							size="sm"
							onClick={handleRunPrewarm}
							disabled={
								prewarmStatus?.cache_label === "hot" ||
								prewarmStatus?.prewarm_running === true ||
								runPrewarmLoading
							}
						>
							{prewarmStatus?.prewarm_running === true || runPrewarmLoading
								? t("about.cacheRunning")
								: t("about.runPrewarmNow")}
						</Button>
						{/* "View prewarm log" button. Opens the prewarm log
						file in the OS default text editor. */}
						<Button variant="ghost" size="sm" onClick={handleViewPrewarmLog}>
							{t("about.viewPrewarmLog")}
						</Button>
					</div>
				</SettingsSection>
			)}

			{/* ── Updates (offline notice) ──────────────────────────── */}
			{/* The "Check for Updates" button was removed because the
				offline-by-default UX was preferred; if a future iteration
				wants to add it back (user-initiated GitHub API check),
				C-DATA-1 permits it under the auto-update category — see
				docs/auto-update-feature.md. The section now shows the
				installed version plus a static offline message + a
				user-clicked external link to the GitHub releases page. */}
			{[
				t("about.installedVersion"),
				t("about.offlineUpdatesMessage"),
				t("about.viewChangelog"),
			].some((l) => isVisible(l, undefined, t("about.updatesTitle"))) && (
				<SettingsSection
					title={t("about.updatesTitle")}
					description={t("about.updatesDescription")}
				>
					{isVisible(
						t("about.installedVersion"),
						undefined,
						t("about.updatesTitle"),
					) && (
						<ReadonlyRow
							label={t("about.installedVersion")}
							value={t("about.versionValue", { version: APP_VERSION })}
						/>
					)}
					<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border">
						{/* The "Check for Updates" button was removed because
						the offline-by-default UX was preferred; if a future
						iteration wants to add it back (user-initiated GitHub
						API check), C-DATA-1 permits it under the auto-update
						category — see docs/auto-update-feature.md. A static
						offline notice now directs the user to open the
						GitHub releases page in their own browser. */}
						<p className="text-sm text-(--text-muted) mr-auto">
							{t("about.offlineUpdatesMessage")}
						</p>
						{/* "View Changelog" — an `<a href>` link the user
						clicks to open the GitHub releases page in their
						browser. This is NOT a renderer network call: it's
						an anchor the user explicitly activates, routed by
						Electron to the system browser (or a new
						BrowserWindow). C-DATA-1 forbids automated network
						calls; user-clicked external links are the user's
						browser making the call, not Voice Typer. */}
						<Button asChild variant="ghost" size="sm">
							<a href={RELEASES_URL} target="_blank" rel="noreferrer noopener">
								<HugeiconsIcon
									icon={RefreshIcon}
									strokeWidth={2}
									className="h-4 w-4"
								/>
								{t("about.viewChangelog")}
							</a>
						</Button>
					</div>
				</SettingsSection>
			)}
		</>
	);
}
