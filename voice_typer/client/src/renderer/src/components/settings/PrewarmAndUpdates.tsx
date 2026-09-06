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
//
// (RESTORED 2026-08-14): the Cache Status card + `get_prewarm_status`
// / `open_prewarm_log` IPC calls were restored verbatim from commit
// 5a319872 — the card is a user-facing product feature, not prewarm
// machinery (plan §6.3 addendum). The "Run Prewarm Now" button was
// ALSO restored the same day (§6.3 addendum second half), wired to the
// re-implemented `run_prewarm` IPC: the Python handler no longer
// spawns the deleted standalone-prewarm subprocess — it re-runs the
// worker's warm phase in-process (warm_imports_for_worker on a daemon
// thread) and refreshes the status file, so the button re-warms the OS
// standby cache on demand. Two things were NOT restored, in lockstep
// with the Python side:
//   * the `prewarm_running` field — it tracked that subprocess via
//     the deleted process-tracker machinery; the restored status
//     response carries `enabled` instead. The button's "running"
//     state is tracked locally (`runPrewarmLoading`) + via a short
//     poll of `last_run` after starting.
//   * the 2-minute poll loop — the restored in-process warm pass is
//     fast (seconds, not the 20-50 s subprocess), so the button
//     re-fetches status once after starting instead of long-polling.

import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useState } from "react";
import { ReadonlyRow } from "@/components/common/ReadonlyRow";
import { SettingsSection } from "@/components/common/SettingsSection";
// Reuse the byte/relative-time formatters exported by the diagnostics
// section (they moved there with the diagnostics table in the IA split)
// instead of duplicating them.
import {
	formatBytes,
	formatRelativeTime,
} from "@/components/settings/DiagnosticsSettingsSection";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import pkg from "../../../../../package.json";
import type { IsVisibleFn } from "./types";
import { useLatestRef } from "@/hooks/useLatestRef";

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
// Mirrors the dict returned by
// voice_typer.server.prewarm.status.get_prewarm_status(). RESTORED
// 2026-08-14: matches the restored response — `enabled` (fast_startup
// config toggle) + worker warm-run timing; `prewarm_running` was
// dropped with the process-tracker machinery (see header comment).
interface PrewarmStatus {
	enabled: boolean;
	last_run: string | null;
	elapsed_s: number | null;
	cache_ratio: number;
	cache_label: "hot" | "partial" | "cold" | "unknown";
	cached_bytes: number;
	total_bytes: number;
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
		<span className="inline-flex items-center gap-2 text-(--text-primary)">
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
		t("about.runPrewarmNow"),
		t("about.refreshCacheStatus"),
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

	// Ref mirror of `call` so the mount-load effect keeps `[]` deps.
	// Test mocks may return a fresh `call` per render — depending on it
	// would re-fire the load (get_prewarm_status → setPrewarmStatus →
	// re-render → new call → loop). Same pattern as useVocabulary.ts.
	const callRef = useLatestRef(call);
	const { showSnack } = useSnackbar();

	// ADR-0009 Issue 3: prewarm cache status. null = not fetched yet.
	const [prewarmStatus, setPrewarmStatus] = useState<PrewarmStatus | null>(
		null,
	);
	const [prewarmLoading, setPrewarmLoading] = useState(false);
	// "Run Prewarm Now" button state. runPrewarmLoading is true while
	// the run_prewarm IPC is in flight (RESTORED 2026-08-14 §6.3
	// addendum 2nd half).
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

	// Open the prewarm log file in the OS default text editor. Calls the
	// open_prewarm_log IPC handler which uses os.startfile (Windows), open
	// (macOS), or xdg-open (Linux). Shows a toast if the log file doesn't
	// exist or can't be opened. (RESTORED 2026-08-14 — the handler now
	// opens the worker log, which carries the [PREWARM] lines.)
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

	// "Run Prewarm Now": call the re-implemented run_prewarm IPC
	// (RESTORED 2026-08-14 §6.3 addendum 2nd half). The handler
	// re-runs the worker's warm phase in-process on a daemon thread
	// (no subprocess spawn), so this returns quickly; the status file
	// is refreshed by the handler's background warm pass, and we
	// re-fetch once after a short delay so the card shows the new
	// last_run/elapsed_s.
	const handleRunPrewarm = async () => {
		setRunPrewarmLoading(true);
		try {
			const result = await call<{ started: boolean }>("run_prewarm");
			if (result?.started) {
				showSnack(t("about.prewarmStarting"), "info");
				// The in-process warm pass is fast (seconds); wait once
				// then refresh so the card reflects the fresh run.
				await new Promise((r) => setTimeout(r, 1500));
				await fetchPrewarmStatus();
			}
		} catch (err) {
			showSnack(
				t("about.prewarmLogOpenFailed") +
					(err instanceof Error ? `: ${err.message}` : ""),
				"error",
			);
		} finally {
			setRunPrewarmLoading(false);
		}
	};

	// On mount: fetch prewarm status only. No network call is ever
	// fired from this component (the prewarm status call is a local
	// IPC bridge to the Python sidecar, not a network call).
	useEffect(() => {
		let cancelled = false;
		const load = async () => {
			try {
				const ps = await callRef.current<PrewarmStatus>("get_prewarm_status");
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
	}, []);

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
								prewarmStatus ? (
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
					<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border/5">
						{/* "Run Prewarm Now" button (RESTORED 2026-08-14 §6.3
						addendum 2nd half). Disabled while the run_prewarm IPC
						is in flight; the in-process warm pass is fast, so no
						long-running state. */}
						<Button
							variant="default"
							size="sm"
							onClick={handleRunPrewarm}
							disabled={runPrewarmLoading}
						>
							{runPrewarmLoading
								? t("about.cacheRunning")
								: t("about.runPrewarmNow")}
						</Button>
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
						{/* "View prewarm log" button. Opens the worker log
						(the prewarm record) in the OS default text editor. */}
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
					<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border/5">
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
