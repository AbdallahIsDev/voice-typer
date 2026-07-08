import { type ReactNode, useEffect, useState } from "react";
import { toast } from "sonner";
import { SettingsSection } from "@/components/SettingsSection";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";

// App version. The package.json is two directories above the
// renderer src tree, and the alias `@/../package.json` does not
// resolve cleanly under every TS config — fall back to a hardcoded
// constant matching package.json#version.
const APP_VERSION = "1.0.0";

const GITHUB_REPO = "https://github.com/AbdallahIsDev/voice-typer";
const GITHUB_ISSUES = "https://github.com/AbdallahIsDev/voice-typer/issues";
const SECURITY_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/SECURITY.md";
const CONTRIBUTING_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/CONTRIBUTING.md";
// NEW-PRIV-004 / NEW-UX-021: in-app documentation links.
const PRIVACY_POLICY_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/SECURITY.md";
const README_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/README.md";
const CHANGELOG_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/CHANGELOG.md";
// NEW-UX-023: GitHub releases feed for "new version available" checks.
const RELEASES_URL = "https://github.com/AbdallahIsDev/voice-typer/releases";
const LATEST_RELEASE_API =
	"https://api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest";

// Small label/value row that matches the visual rhythm of SettingRow
// but doesn't carry the input-association machinery (we're read-only).
function Row({ label, value }: { label: string; value: ReactNode }) {
	return (
		<div className="flex items-center justify-between gap-6 px-3.5 py-2.5">
			<span className="text-sm font-medium text-(--text-primary)">{label}</span>
			<span className="shrink-0 text-right text-sm text-(--text-muted)">
				{value}
			</span>
		</div>
	);
}

function StatusDot({ connected }: { connected: boolean }) {
	return (
		<span
			className={
				"inline-flex items-center gap-1.5 " +
				(connected ? "text-(--text-primary)" : "text-destructive")
			}
		>
			<span
				className={
					"size-1.5 rounded-full " +
					(connected ? "bg-emerald-500" : "bg-destructive")
				}
			/>
			{connected ? t("about.connected") : t("about.disconnected")}
		</span>
	);
}

export default function AboutPage() {
	const { call } = usePython();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(null);
	const [configDir, setConfigDir] = useState<string>("~/.voice-typer");
	// null = still probing, true/false = settled.
	const [backendConnected, setBackendConnected] = useState<boolean | null>(
		null,
	);
	// NEW-UX-038: the active model's loaded_via string (e.g.
	// "cuda/float16/small.en" or "cpu/int8/tiny.en").
	const [loadedVia, setLoadedVia] = useState<string>("");
	// NEW-UX-023: latest release from GitHub (null = not checked yet).
	const [latestVersion, setLatestVersion] = useState<string | null>(null);
	const [checkingUpdate, setCheckingUpdate] = useState(false);

	useEffect(() => {
		let cancelled = false;

		const load = async () => {
			// Probe backend connectivity by issuing get_status. If the
			// Python backend is down (or the bridge isn't installed), the
			// call rejects and we mark the backend as disconnected.
			try {
				const status = await call<{
					config_dir?: string;
					status?: string;
					loaded_via?: string;
				}>("get_status");
				if (!cancelled) {
					setBackendConnected(true);
					if (status?.config_dir) setConfigDir(status.config_dir);
					// NEW-UX-038: capture loaded_via so the user can see if
					// their GPU is actually being used or if the model fell
					// back to CPU.
					if (status?.loaded_via) setLoadedVia(status.loaded_via);
				}
			} catch {
				if (!cancelled) setBackendConnected(false);
			}

			// Best-effort config fetch. Will also fail if the backend is
			// down — the UI falls back to "—" placeholders in that case.
			try {
				const cfg = await call<VoiceTyperConfig>("get_config");
				if (!cancelled) setConfig(cfg);
			} catch {
				// intentionally leave config as null — diagnostics simply
				// show "—" until the backend comes back online.
			}
		};

		load();
		return () => {
			cancelled = true;
		};
	}, [call]);

	// NEW-UX-023: check GitHub releases for a newer version.  Runs
	// once on mount, non-blocking.  We don't auto-open any UI — just
	// surface a "newer version available" link in the About page.
	useEffect(() => {
		let cancelled = false;
		const checkForUpdate = async () => {
			try {
				const resp = await fetch(LATEST_RELEASE_API, {
					headers: { Accept: "application/vnd.github+json" },
				});
				if (!resp.ok) return;
				const data = (await resp.json()) as { tag_name?: string };
				if (cancelled || !data.tag_name) return;
				// Strip leading 'v' from tag name ("v1.2.3" → "1.2.3").
				const remote = data.tag_name.replace(/^v/, "");
				setLatestVersion(remote);
			} catch {
				// Network failure / rate limit — silently skip.  The user
				// can manually click "Check for updates" to retry.
			}
		};
		checkForUpdate();
		return () => {
			cancelled = true;
		};
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
			if (remote === APP_VERSION) {
				toast.success(t("about.onLatestVersion", { version: APP_VERSION }));
			} else if (remote > APP_VERSION) {
				toast.info(t("about.newVersionAvailable", { version: remote }));
			} else {
				toast.info(
					t("about.installedNewer", {
						installed: APP_VERSION,
						latest: remote,
					}),
				);
			}
		} catch (err) {
			toast.error(
				t("about.updateCheckFailed", {
					error: err instanceof Error ? err.message : "unknown error",
				}),
			);
		} finally {
			setCheckingUpdate(false);
		}
	};

	const asrBackend = config
		? `${config.asr_backend} (${config.model_size})`
		: t("about.unknown");
	const device = config?.device ?? t("about.unknown");
	const hotkey = config?.hotkey ?? t("about.unknown");
	const microphone = config?.microphone ?? t("microphone.systemDefault");

	const backendStatus =
		backendConnected === null ? (
			<span className="text-(--text-muted)">{t("about.checking")}</span>
		) : (
			<StatusDot connected={backendConnected} />
		);

	return (
		<div className="min-h-full">
			<div className="mx-auto max-w-2xl space-y-8 px-6 pt-28 pb-6">
				{/* Header */}
				<div className="space-y-1 pb-5">
					<h1 className="font-sans text-2xl font-semibold tracking-tight text-(--text-primary)">
						{t("about.title")}
					</h1>
					<p className="text-sm text-(--text-muted)">
						{t("about.description")}
					</p>
				</div>

				{/* ── Diagnostics ───────────────────────────────────────── */}
				<SettingsSection
					title={t("about.diagnosticsTitle")}
					description={t("about.diagnosticsDescription")}
				>
					<Row
						label={t("about.appVersion")}
						value={t("about.versionValue", { version: APP_VERSION })}
					/>
					<Row label={t("about.pythonBackend")} value={backendStatus} />
					<Row label={t("about.configDirectory")} value={configDir} />
					<Row label={t("about.asrBackend")} value={asrBackend} />
					<Row label={t("about.device")} value={device} />
					{/* NEW-UX-038: show which device/compute_type the model
              actually loaded via. */}
					<Row
						label={t("about.loadedVia")}
						value={loadedVia || t("about.unknown")}
					/>
					<Row label={t("about.hotkey")} value={hotkey} />
					<Row label={t("about.microphone")} value={microphone} />
				</SettingsSection>

				{/* ── Privacy ──────────────────────────────────────────── */}
				{/* NEW-PRIV-004 / NEW-PRIV-009: expanded privacy disclosure. */}
				<SettingsSection
					title={t("about.privacyTitle")}
					description={t("about.privacyDescription")}
				>
					<div className="px-3.5 py-3.5 text-sm leading-relaxed text-(--text-muted) space-y-3">
						<p>
							<span className="font-medium text-(--text-primary)">
								{t("about.audioProcessingTitle")}
							</span>{" "}
							{t("about.audioProcessingDesc")}
						</p>
						<p>
							<span className="font-medium text-(--text-primary)">
								{t("about.modelWeightsTitle")}
							</span>{" "}
							{t("about.modelWeightsDesc")}
						</p>
						<p>
							<span className="font-medium text-(--text-primary)">
								{t("about.cloudAsrTitle")}
							</span>{" "}
							{t("about.cloudAsrDesc")}
						</p>
						<p>
							<span className="font-medium text-(--text-primary)">
								{t("about.voiceBiometricsTitle")}
							</span>{" "}
							{t("about.voiceBiometricsDesc")}
						</p>
						<p>
							<span className="font-medium text-(--text-primary)">
								{t("about.localDataTitle")}
							</span>{" "}
							{t("about.localDataDesc", { configDir })}
						</p>
					</div>
					<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border">
						<Button asChild variant="outline" size="sm">
							<a
								href={PRIVACY_POLICY_URL}
								target="_blank"
								rel="noreferrer noopener"
							>
								{t("about.fullPrivacyPolicy")}
							</a>
						</Button>
					</div>
				</SettingsSection>

				{/* ── Updates ──────────────────────────────────────────── */}
				{/* NEW-UX-023: in-app "new version available" check. */}
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
								: latestVersion > APP_VERSION
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
							{checkingUpdate
								? t("about.checking")
								: t("about.checkForUpdates")}
						</Button>
						{latestVersion !== null && latestVersion > APP_VERSION && (
							<Button asChild variant="default" size="sm">
								<a
									href={RELEASES_URL}
									target="_blank"
									rel="noreferrer noopener"
								>
									{t("about.downloadVersion", { version: latestVersion })}
								</a>
							</Button>
						)}
						<Button asChild variant="ghost" size="sm">
							<a href={CHANGELOG_URL} target="_blank" rel="noreferrer noopener">
								{t("about.viewChangelog")}
							</a>
						</Button>
					</div>
				</SettingsSection>

				{/* ── Help ─────────────────────────────────────────────── */}
				{/* NEW-UX-021 / NEW-UX-040: expanded help section. */}
				<SettingsSection
					title={t("about.helpTitle")}
					description={t("about.helpDescription")}
				>
					<Row
						label={t("about.startStopDictation")}
						value={t("about.startStopDictationValue")}
					/>
					<Row
						label={t("about.cancelRecording")}
						value={t("about.cancelRecordingValue")}
					/>
					<Row
						label={t("about.repasteTranscription")}
						value={t("about.repasteTranscriptionValue")}
					/>
					<Row
						label={t("about.toggleSidebar")}
						value={t("about.toggleSidebarValue")}
					/>
					<Row
						label={t("about.navigateFields")}
						value={t("about.navigateFieldsValue")}
					/>
					<Row
						label={t("about.toggleSwitches")}
						value={t("about.toggleSwitchesValue")}
					/>
					<Row
						label={t("about.closeDialogs")}
						value={t("about.closeDialogsValue")}
					/>
					<Row
						label={t("about.openDropdowns")}
						value={t("about.openDropdownsValue")}
					/>
					<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border">
						<Button asChild variant="outline" size="sm">
							<a href={README_URL} target="_blank" rel="noreferrer noopener">
								{t("about.documentation")}
							</a>
						</Button>
						<Button asChild variant="outline" size="sm">
							<a href={CHANGELOG_URL} target="_blank" rel="noreferrer noopener">
								{t("about.changelog")}
							</a>
						</Button>
					</div>
				</SettingsSection>

				{/* ── Resources ────────────────────────────────────────── */}
				{/* NEW-UX-022: feedback channels. */}
				<SettingsSection
					title={t("about.resourcesTitle")}
					description={t("about.resourcesDescription")}
				>
					<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5">
						<Button asChild variant="outline" size="sm">
							<a href={GITHUB_REPO} target="_blank" rel="noreferrer noopener">
								{t("about.githubRepository")}
							</a>
						</Button>
						<Button asChild variant="outline" size="sm">
							<a href={GITHUB_ISSUES} target="_blank" rel="noreferrer noopener">
								{t("about.reportBug")}
							</a>
						</Button>
						<Button asChild variant="outline" size="sm">
							<a href={SECURITY_URL} target="_blank" rel="noreferrer noopener">
								{t("about.securityPolicy")}
							</a>
						</Button>
						<Button asChild variant="outline" size="sm">
							<a
								href={CONTRIBUTING_URL}
								target="_blank"
								rel="noreferrer noopener"
							>
								{t("about.contributing")}
							</a>
						</Button>
					</div>
				</SettingsSection>
			</div>
		</div>
	);
}
