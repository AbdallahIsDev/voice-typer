// About page — diagnostics, privacy disclosure, and resources/feedback links.
//
// UX-20 / SET-5: the previous 726-line catch-all version had Help, Cache
// Status, Updates, Diagnostics, Privacy, and Resources sections all crammed
// together. The Help section duplicated the `?` overlay (already reachable
// from TitleBar + the `?` keydown shortcut), and Cache Status + Updates
// belonged on a "Diagnostics" surface rather than the lightweight About
// page. They have been removed; the canonical help is now the `?` overlay,
// and the prewarm/update features are available from Settings →
// Troubleshooting (which already links back here for Diagnostics).
//
// The remaining three sections (Diagnostics, Privacy, Resources) keep the
// page focused on "what is this app, where does my data go, where do I
// file bugs." ~300 LOC, well under the ~400 LOC ceiling.
import { type ReactNode, useEffect, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";

// VERSION-SOURCE-FIX: import the version directly from package.json so
// it stays in sync with the single source of truth. Previously this
// was a hardcoded constant `const APP_VERSION = "1.0.0"` that silently
// drifted from package.json#version on every release bump. The Vite
// build resolves JSON imports at build time (tsconfig.web.json has
// resolveJsonModule: true), so the bundled output contains the literal
// string — no runtime file read.
import pkg from "../../../../package.json";

// App version. Read directly from package.json (see VERSION-SOURCE-FIX
// comment at the top of the file) so this never drifts from the
// canonical source of truth on a release bump.
const APP_VERSION = pkg.version as string;

const GITHUB_REPO = "https://github.com/AbdallahIsDev/voice-typer";
const GITHUB_ISSUES = "https://github.com/AbdallahIsDev/voice-typer/issues";
const SECURITY_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/SECURITY.md";
const CONTRIBUTING_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/CONTRIBUTING.md";
// NEW-PRIV-004 / NEW-UX-021: in-app documentation links.
const PRIVACY_POLICY_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/SECURITY.md";

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

// ADR-0009 Issue 3: format a byte count as a human-readable string.
// 0 → "0 MB"; 1750000000 → "1.7 GB". Was used by the (now-removed)
// Cache Status card; kept exported because the unit tests in
// About.test.tsx still cover it and future diagnostics surfaces
// (e.g. Settings → Troubleshooting) may want to reuse it.
export function formatBytes(bytes: number): string {
	if (bytes <= 0) return "0 MB";
	const gb = bytes / (1024 * 1024 * 1024);
	if (gb >= 1) return `${gb.toFixed(1)} GB`;
	const mb = bytes / (1024 * 1024);
	return `${Math.round(mb)} MB`;
}

// ADR-0009 Issue 3: format an ISO timestamp as a relative "N hours ago" string.
// Falls back to the raw ISO string for timestamps older than 7 days.
// Kept exported for unit-test coverage (see About.test.tsx).
export function formatRelativeTime(iso: string | null): string {
	if (!iso) return t("about.neverRun");
	try {
		const then = new Date(iso).getTime();
		if (Number.isNaN(then)) return iso;
		const now = Date.now();
		const diffMs = now - then;
		const diffMin = Math.floor(diffMs / 60000);
		const diffHr = Math.floor(diffMin / 60);
		const diffDay = Math.floor(diffHr / 24);
		if (diffMin < 1) return t("about.relativeTime.lessThanMinute");
		if (diffMin < 60)
			return t("about.relativeTime.minutesAgo", { count: String(diffMin) });
		if (diffHr < 24)
			return t("about.relativeTime.hoursAgo", { count: String(diffHr) });
		if (diffDay < 7)
			return t("about.relativeTime.daysAgo", { count: String(diffDay) });
		return iso;
	} catch {
		return iso;
	}
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
				<PageHeading
					title={t("about.title")}
					description={t("about.description")}
				/>

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
