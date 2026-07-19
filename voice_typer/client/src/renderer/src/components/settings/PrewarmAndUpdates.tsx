// Prewarm cache status + in-app update check.
//
// UX-20 / SET-5 slimming of About.tsx removed the Cache Status and Updates
// sections from the About page but documented them as "relocated to
// Settings → Troubleshooting". They had in fact been dropped from the UI
// entirely (orphaned i18n + a dangling `get_prewarm_status` IPC command).
// This component restores that functionality into the Troubleshooting area
// of Settings, wiring the prewarm diagnostics and the GitHub release check
// back up.
//
// Behavior mirrors the original About implementation: prewarm cache status
// is fetched on mount and refreshable; "Run Prewarm Now" triggers a manual
// warm and polls until it finishes; "View prewarm log" opens the log file;
// and "Check for Updates" compares the installed version against the latest
// GitHub release using the semver-aware `compareSemver`.

import { Download01Icon, RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { compareSemver } from "@/lib/semver";
// Reuse the byte/relative-time formatters already exported by About
// (kept exported there for unit-test coverage) instead of duplicating them.
import { formatBytes, formatRelativeTime } from "@/pages/About";
import pkg from "../../../../../package.json";

const APP_VERSION = pkg.version as string;

// NEW-UX-023: GitHub releases feed for "new version available" checks.
const RELEASES_URL = "https://github.com/AbdallahIsDev/voice-typer/releases";
const LATEST_RELEASE_API =
	"https://api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest";

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
			? "bg-emerald-500"
			: label === "partial"
				? "bg-amber-500"
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

// Small label/value row matching the visual rhythm of SettingsSection.
function Row({ label, value }: { label: string; value: ReactNode }) {
	return (
		<div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
			<span className="text-sm text-(--text-muted)">{label}</span>
			<span className="text-sm font-medium text-(--text-primary)">{value}</span>
		</div>
	);
}

export default function PrewarmAndUpdates() {
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

	// NEW-UX-023: latest release from GitHub (null = not checked yet).
	const [latestVersion, setLatestVersion] = useState<string | null>(null);
	const [checkingUpdate, setCheckingUpdate] = useState(false);

	const fetchPrewarmStatus = async () => {
		setPrewarmLoading(true);
		try {
			const status = await call<PrewarmStatus>("get_prewarm_status");
			setPrewarmStatus(status);
		} catch {
			// Best-effort: leave the previous status (or null) in place.
			// The card renders an "Unknown" placeholder when null.
		} finally {
			setPrewarmLoading(false);
		}
	};

	// Trigger a manual prewarm run. Spawns a detached subprocess
	// (pythonw -m voice_typer.server.prewarm --force). After spawning,
	// polls get_prewarm_status every 2s until prewarm_running flips to
	// False, then refreshes the card and shows a completion toast.
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
						await new Promise((r) => setTimeout(r, 2000));
						try {
							const status = await call<PrewarmStatus>("get_prewarm_status");
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

	// NEW-UX-023: check GitHub releases for a newer version. Runs once on
	// mount, non-blocking. We don't auto-open any UI — just surface a
	// "newer version available" link in the Updates section.
	const checkForUpdate = useCallback(async () => {
		try {
			const resp = await fetch(LATEST_RELEASE_API, {
				headers: { Accept: "application/vnd.github+json" },
			});
			if (!resp.ok) return;
			const data = (await resp.json()) as { tag_name?: string };
			if (!data.tag_name) return;
			// Strip leading 'v' from tag name ("v1.2.3" → "1.2.3").
			const remote = data.tag_name.replace(/^v/, "");
			setLatestVersion(remote);
		} catch {
			// Network failure / rate limit — silently skip. The user can
			// manually click "Check for Updates" to retry.
		}
	}, []);

	const handleManualCheck = async () => {
		setCheckingUpdate(true);
		try {
			const resp = await fetch(LATEST_RELEASE_API, {
				headers: { Accept: "application/vnd.github+json" },
			});
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			const data = (await resp.json()) as { tag_name?: string };
			if (!data.tag_name) throw new Error("No tag_name in response");
			const remote = data.tag_name.replace(/^v/, "");
			setLatestVersion(remote);
			// Use a proper semver comparison instead of lexicographic
			// string comparison (which broke for "1.10.0" vs "1.9.0").
			if (compareSemver(remote, APP_VERSION) === 0) {
				showSnack(
					t("about.onLatestVersion", { version: APP_VERSION }),
					"success",
				);
			} else if (compareSemver(remote, APP_VERSION) > 0) {
				showSnack(t("about.newVersionAvailable", { version: remote }), "info");
			} else {
				showSnack(
					t("about.installedNewer", {
						installed: APP_VERSION,
						latest: remote,
					}),
					"info",
				);
			}
		} catch (err) {
			showSnack(
				t("about.updateCheckFailed", {
					error: err instanceof Error ? err.message : "unknown error",
				}),
				"error",
			);
		} finally {
			setCheckingUpdate(false);
		}
	};

	useEffect(() => {
		let cancelled = false;
		const load = async () => {
			try {
				const ps = await call<PrewarmStatus>("get_prewarm_status");
				if (!cancelled) setPrewarmStatus(ps);
			} catch {
				// leave prewarmStatus as null; card renders "Unknown"
			}
			if (!cancelled) await checkForUpdate();
		};
		load();
		return () => {
			cancelled = true;
		};
	}, [call, checkForUpdate]);

	return (
		<>
			{/* ── Cache Status (ADR-0009 Issue 3) ─────────────────────── */}
			<SettingsSection
				title={t("about.cacheTitle")}
				description={t("about.cacheDescription")}
			>
				<Row
					label={t("about.prewarmStatus")}
					value={
						prewarmStatus?.prewarm_running ? (
							<span className="inline-flex items-center gap-1.5 text-(--text-primary)">
								<span className="size-1.5 animate-pulse rounded-full bg-sky-500" />
								{t("about.cacheRunning")}
							</span>
						) : prewarmStatus ? (
							<CacheStatusBadge label={prewarmStatus.cache_label} />
						) : (
							<span className="text-(--text-muted)">{t("about.checking")}</span>
						)
					}
				/>
				<Row
					label={t("about.lastRun")}
					value={
						prewarmStatus?.last_run
							? formatRelativeTime(prewarmStatus.last_run)
							: prewarmStatus
								? t("about.neverRun")
								: t("about.checking")
					}
				/>
				<Row
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
				<Row
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

			{/* ── Updates (NEW-UX-023) ─────────────────────────────────── */}
			<SettingsSection
				title={t("about.updatesTitle")}
				description={t("about.updatesDescription")}
			>
				<Row
					label={t("about.installedVersion")}
					value={t("about.versionValue", { version: APP_VERSION })}
				/>
				<Row
					label={t("about.latestRelease")}
					value={
						latestVersion === null
							? t("about.checking")
							: compareSemver(latestVersion, APP_VERSION) > 0
								? t("about.updateAvailable", { version: latestVersion })
								: t("about.versionValue", { version: latestVersion })
					}
				/>
				<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border">
					<Button
						variant="outline"
						size="sm"
						onClick={handleManualCheck}
						disabled={checkingUpdate}
					>
						{checkingUpdate ? t("about.checking") : t("about.checkForUpdates")}
					</Button>
					{latestVersion !== null &&
						compareSemver(latestVersion, APP_VERSION) > 0 && (
							<Button asChild variant="default" size="sm">
								<a
									href={RELEASES_URL}
									target="_blank"
									rel="noreferrer noopener"
								>
									<HugeiconsIcon
										icon={Download01Icon}
										strokeWidth={2}
										className="h-4 w-4"
									/>
									{t("about.downloadVersion", { version: latestVersion })}
								</a>
							</Button>
						)}
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
		</>
	);
}
