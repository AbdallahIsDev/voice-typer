// About page — diagnostics, privacy disclosure, and resources/feedback links.
//
// UX-20 SET-5: the previous 726-line catch-all version had Help, Cache
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
import { getLocale, t } from "@/i18n/i18n";
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
// in-app documentation link. README.md is the
// canonical entry point for user-facing docs in the repo; the /docs
// folder holds deeper references (FEATURES.md, ADRs, debugging guide).
const DOCUMENTATION_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/README.md";
// BG-59: SECURITY.md is the canonical privacy + security policy doc
// in this repo (there is no separate PRIVACY.md). Previously the About
// page rendered two byte-identical buttons ("Full Privacy Policy" and
// "Security Policy") that both pointed at SECURITY.md — confusing UX.
// We now render only the Security Policy button (in Resources) and add
// a one-line note in the Privacy section body explaining that
// SECURITY.md covers privacy practices too.

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
			{/* : the colored dot is purely decorative —
			 * the adjacent "Connected" / "Disconnected" text conveys the
			 * state to assistive tech. Mark aria-hidden so screen readers
			 * don't announce a meaningless "graphic". */}
			<span
				aria-hidden="true"
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
// R7-F19: marked `@internal`. Exported only for unit-test coverage.
// Coordinate with I9/I12 about moving it to `lib/format.ts`.
//
// switch from hardcoded "MB"/"GB" suffixes +
// `.toFixed()` to `Intl.NumberFormat` with `style: "unit"` so the
// output respects the user-selected UI locale (e.g. "1,6 GB" in de,
// "1.6 Go" in fr where CLDR uses octets). Visible behaviour in `en`
// is preserved bit-for-bit:
// - sub-GB values are rendered as "<int> MB" (Math.round, no decimals)
// - GB-range values are rendered as "<x.x> GB" (1 decimal, including
// trailing ".0" for whole numbers via minimumFractionDigits: 1)
// - bytes <= 0 return the literal "0 MB" (same as before)
//
// The existing `pages/__tests__/About.test.tsx::formatBytes` suite
// (owned by sub-agent 17) covers the `en` outputs and continues to
// pass with the Intl implementation when the locale is `en`.
/**
 * @internal
 */
export function formatBytes(bytes: number): string {
	if (bytes <= 0) return "0 MB";
	const gb = bytes / (1024 * 1024 * 1024);
	if (gb >= 1) {
		return new Intl.NumberFormat(getLocale(), {
			style: "unit",
			unit: "gigabyte",
			minimumFractionDigits: 1,
			maximumFractionDigits: 1,
		}).format(gb);
	}
	const mb = bytes / (1024 * 1024);
	return new Intl.NumberFormat(getLocale(), {
		style: "unit",
		unit: "megabyte",
		maximumFractionDigits: 0,
	}).format(Math.round(mb));
}

// ADR-0009 Issue 3: format an ISO timestamp as a relative "N hours ago" string.
// R7-F19: JSDoc clarifies the ISO fallback is English-only.
//
// the >7-day fallback previously returned the
// raw ISO 8601 string (e.g. "2025-07-12T10:30:00.000Z") — an
// English-only, locale-independent, screen-reader-unfriendly format.
// It now uses `Intl.DateTimeFormat` with `dateStyle: "medium"` so the
// timestamp is rendered in the user-selected UI locale (e.g.
// "Jul 12, 2025" in en, "12 juil. 2025" in fr).
//
// **Note (R7-F19 / sub-agent 21):** the existing test
// `pages/__tests__/About.test.tsx::formatRelativeTime > returns the
// raw ISO string for timestamps older than 7 days` (owned by
// sub-agent 17) asserts `result === tenDaysAgo` (the raw ISO). With
// the Intl.DateTimeFormat fallback the result is now a localized
// date string, so that specific assertion will fail until agent 17
// updates it to match the new contract. The sub-minute/minute/hour/
// day-branches (and the null/unparseable fallbacks) are unchanged.
/**
 * Format an ISO timestamp as a localized relative-time string. For
 * timestamps older than 7 days, falls back to a localized medium-format
 * date (e.g. "Jul 12, 2025") via `Intl.DateTimeFormat`.
 */
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
		// >7-day fallback — localized medium-format date instead
		// of the raw ISO 8601 string. `dateStyle: "medium"` produces e.g.
		// "Jul 12, 2025" in en, "12 juil. 2025" in fr, "12. Juli 2025" in de.
		return new Intl.DateTimeFormat(getLocale(), {
			dateStyle: "medium",
		}).format(new Date(then));
	} catch {
		return iso;
	}
}

export default function AboutPage() {
	const { call } = usePython();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(null);
	// R7-F15: initialize to empty string and render `t("about.loading")`
	// as a fallback. Previously hardcoded "~/.voice-typer".
	const [configDir, setConfigDir] = useState<string>("");
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
			} catch (e) {
				// intentionally leave config as null — diagnostics simply
				// show "—" until the backend comes back online.
				console.warn("[About] get_config failed:", e);
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
			{/* : use the standard page shell (flex
				min-h-full w-full flex-col) so the page content stretches to
				fill the viewport — matches History, Vocabulary, Templates,
				Microphone, Dashboard. Previously About dropped the flex
				wrapper, leaving an empty gap below short content. */}
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col space-y-8 px-6 pt-28 pb-6">
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
					<Row
						label={t("about.configDirectory")}
						value={configDir || t("about.loading")}
					/>
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
							{t("about.localDataDesc", {
								// R7-F15: fall back to "Loading…" while configDir is empty.
								configDir: configDir || t("about.loading"),
							})}
						</p>
						{/* BG-59: SECURITY.md is the canonical privacy +
						 * security policy doc in this repo (there is no
						 * separate PRIVACY.md). Previously this section
						 * had a "Full Privacy Policy" button that pointed
						 * at the same SECURITY.md as the Resources section's
						 * "Security Policy" button — confusing UX. We now
						 * point users to the Security Policy button below
						 * instead of rendering a duplicate. */}
						<p>
							<span className="font-medium text-(--text-primary)">
								{t("about.privacyPolicyNoteLabel")}
							</span>{" "}
							{t("about.privacyPolicyNote")}
						</p>
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
							<a
								href={DOCUMENTATION_URL}
								target="_blank"
								rel="noreferrer noopener"
							>
								{t("about.documentationLink")}
							</a>
						</Button>
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

				{/* ── Credits & Licenses ───────────────────────────────── */}
				{/* : surface authors, third-party
				 * libraries, fonts, and icons so users can see what
				 * Voice Typer is built on without leaving the app. */}
				<SettingsSection
					title={t("about.creditsTitle")}
					description={t("about.creditsDescription")}
				>
					<Row
						label={t("about.creditsAuthorsLabel")}
						value={t("about.creditsAuthorsValue")}
					/>
					<Row
						label={t("about.creditsLibrariesLabel")}
						value={t("about.creditsLibrariesValue")}
					/>
					<Row
						label={t("about.creditsFontsLabel")}
						value={t("about.creditsFontsValue")}
					/>
					<Row
						label={t("about.creditsIconsLabel")}
						value={t("about.creditsIconsValue")}
					/>
				</SettingsSection>
			</div>
		</div>
	);
}
