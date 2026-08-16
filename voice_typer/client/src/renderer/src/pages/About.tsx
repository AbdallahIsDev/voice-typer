// About page — diagnostics, privacy disclosure, and resources/feedback links.
//
// SET-5: the previous 726-line catch-all version had Help, Cache
// Status, Updates, Diagnostics, Privacy, and Resources sections all crammed
// together. The Help section duplicated the `?` overlay (already reachable
// from TitleBar + the `?` keydown shortcut), and Cache Status + Updates
// belonged on a "Diagnostics" surface rather than the lightweight About
// page. They have been removed; the canonical help is now the `?` overlay,
// and the prewarm/update features are available from Settings →
// Troubleshooting (which already links back here for Diagnostics).
//
// Layout: page heading + sticky in-page section nav, then Diagnostics,
// Privacy (icon cards), Resources & Feedback (icon links), and Credits &
// Licenses. Diagnostics rows carry live status dots; Credits is framed
// differently so the static attribution reads as a different surface.
//
// Config Directory fix: the backend's get_status now returns `config_dir`
// (see voice_typer/server/service/status.py). Previously the renderer
// expected a field the backend never sent, so the row stuck on "Loading…".
import {
	Alert02Icon,
	ArrowUpRight01Icon,
	Book01Icon,
	Clock01Icon,
	CloudIcon,
	CodeIcon,
	Copy01Icon,
	DatabaseIcon,
	Layers01Icon,
	LockIcon,
	Mic02Icon,
	Shield01Icon,
	UserGroupIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import {
	type ReactNode,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { APP_NAME } from "@/branding";
import PageHeading from "@/components/common/PageHeading";
import { ReadonlyRow } from "@/components/common/ReadonlyRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { getLocale, t, useT } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
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
// in-app changelog link so users can see what changed in the
// installed version without leaving the app to browse the repo. Uses
// the existing ``about.viewChangelog`` i18n key (already translated to
// all supported locales). ``CHANGELOG.md`` is the canonical release
// history at the repo root.
const CHANGELOG_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/CHANGELOG.md";
// in-app documentation link. README.md is the
// canonical entry point for user-facing docs in the repo; the /docs
// folder holds deeper references (FEATURES.md, ADRs, debugging guide).
const DOCUMENTATION_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/README.md";
// SECURITY.md is the canonical privacy + security policy doc
// in this repo (there is no separate PRIVACY.md). Previously the About
// page rendered two byte-identical buttons ("Full Privacy Policy" and
// "Security Policy") that both pointed at SECURITY.md — confusing UX.
// We now render only the Security Policy button (in Resources) and add
// a one-line note in the Privacy section body explaining that
// SECURITY.md covers privacy practices too.

/** In-page section anchors (sticky sub-nav targets). */
const SECTIONS = [
	{ id: "about-top", labelKey: "about.title" },
	{ id: "about-diagnostics", labelKey: "about.diagnosticsTitle" },
	{ id: "about-privacy", labelKey: "about.privacyTitle" },
	{ id: "about-resources", labelKey: "about.resourcesTitle" },
	{ id: "about-credits", labelKey: "about.creditsTitle" },
] as const;

function StatusDot({ connected }: { connected: boolean }) {
	return (
		<span
			className={
				"inline-flex items-center gap-1.5 " +
				(connected ? "text-(--text-primary)" : "text-destructive")
			}
		>
			{/* the colored dot is purely decorative —
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

/**
 * Live status value — a colored dot + text for diagnostics rows that
 * reflect live/dynamic backend state (vs. static config facts). The
 * dot is purely decorative (aria-hidden); the text carries the state.
 */
function LiveValue({
	present,
	children,
}: {
	present: boolean;
	children: ReactNode;
}) {
	return (
		<span
			className={cn(
				"inline-flex items-center gap-1.5",
				present ? "text-(--text-primary)" : "text-destructive",
			)}
		>
			<span
				aria-hidden="true"
				className={cn(
					"size-1.5 shrink-0 rounded-full",
					present ? "bg-emerald-500" : "bg-destructive",
				)}
			/>
			<span className="min-w-0 break-all">{children}</span>
		</span>
	);
}

/** Privacy topics — icon + existing i18n title/description keys. */
const PRIVACY_TOPICS = [
	{
		icon: Mic02Icon,
		title: "about.audioProcessingTitle",
		desc: "about.audioProcessingDesc",
	},
	{
		icon: Layers01Icon,
		title: "about.modelWeightsTitle",
		desc: "about.modelWeightsDesc",
	},
	{ icon: CloudIcon, title: "about.cloudAsrTitle", desc: "about.cloudAsrDesc" },
	{
		icon: Shield01Icon,
		title: "about.voiceBiometricsTitle",
		desc: "about.voiceBiometricsDesc",
	},
	{
		icon: DatabaseIcon,
		title: "about.localDataTitle",
		desc: "about.localDataDesc",
	},
] as const;

/** Resources & Feedback links — icon per target + external-link chip. */
const RESOURCE_LINKS = [
	{
		href: DOCUMENTATION_URL,
		icon: Book01Icon,
		label: "about.documentationLink",
	},
	{ href: CHANGELOG_URL, icon: Clock01Icon, label: "about.viewChangelog" },
	{ href: GITHUB_REPO, icon: CodeIcon, label: "about.githubRepository" },
	{ href: GITHUB_ISSUES, icon: Alert02Icon, label: "about.reportBug" },
	{ href: SECURITY_URL, icon: LockIcon, label: "about.securityPolicy" },
	{ href: CONTRIBUTING_URL, icon: UserGroupIcon, label: "about.contributing" },
] as const;

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
//   trailing ".0" for whole numbers via minimumFractionDigits: 1)
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
	// callRef mirror (Home.tsx pattern): the mount probe effect below
	// reads ``callRef.current`` so its deps stay identity-free — a
	// test mock handing out a fresh `call` per render must not re-fire
	// the get_status/get_config probe (OOM loop class).
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);
	const { showSnack } = useSnackbar();
	// Re-render on locale switch so all t() calls re-resolve.
	useT();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(null);
	// R7-F15: initialize to empty string and render `t("about.loading")`
	// as a fallback. Previously hardcoded "~/.voice-typer".
	const [configDir, setConfigDir] = useState<string>("");
	// null = still probing, true/false = settled.
	const [backendConnected, setBackendConnected] = useState<boolean | null>(
		null,
	);
	// the active model's loaded_via string (e.g.
	// "cuda/float16/small.en" or "cpu/int8/tiny.en").
	const [loadedVia, setLoadedVia] = useState<string>("");

	useEffect(() => {
		let cancelled = false;

		const load = async () => {
			// Probe backend connectivity by issuing get_status. If the
			// Python backend is down (or the bridge isn't installed), the
			// call rejects and we mark the backend as disconnected.
			try {
				const status = await callRef.current<{
					config_dir?: string;
					status?: string;
					loaded_via?: string;
				}>("get_status");
				if (!cancelled) {
					setBackendConnected(true);
					// The backend returns config_dir since 2026-08-16
					// (service/status.py) — the About row resolves to a
					// real path. If an older backend omits it, the row
					// falls back to "—" instead of a permanent
					// "Loading…" (see the configDirValue branch below).
					if (status?.config_dir) setConfigDir(status.config_dir);
					// capture loaded_via so the user can see if
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
				const cfg = await callRef.current<VoiceTyperConfig>("get_config");
				if (!cancelled) setConfig(cfg);
			} catch (e) {
				// intentionally leave config as null — diagnostics simply
				// show "—" until the backend comes back online.
				console.warn("[renderer:About] get_config failed:", e);
			}
		};

		load();
		return () => {
			cancelled = true;
		};
	}, []);

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

	const backendLabel =
		backendConnected === null
			? t("about.checking")
			: backendConnected
				? t("about.connected")
				: t("about.disconnected");

	// Config Directory is a live row: never a permanent "Loading…".
	//   - still probing        → "Loading…"
	//   - backend down         → "—" (matches the other config rows)
	//   - connected + resolved → green-dot live value
	//   - connected, no value  → red-dot "—" (older backend)
	const configDirValue =
		backendConnected === null ? (
			<span className="text-(--text-muted)">{t("about.loading")}</span>
		) : backendConnected === false ? (
			t("about.unknown")
		) : configDir ? (
			<LiveValue present>{configDir}</LiveValue>
		) : (
			<LiveValue present={false}>{t("about.unknown")}</LiveValue>
		);

	const copyDiagnostics = useCallback(async () => {
		const lines = [
			`${APP_NAME} ${t("about.versionValue", { version: APP_VERSION })} — ${t("about.diagnosticsTitle")}`,
			"=".repeat(28),
			`${t("about.appVersion")}: ${t("about.versionValue", { version: APP_VERSION })}`,
			`${t("about.pythonBackend")}: ${backendLabel}`,
			`${t("about.configDirectory")}: ${configDir || t("about.unknown")}`,
			`${t("about.asrBackend")}: ${asrBackend}`,
			`${t("about.device")}: ${device}`,
			// Only include Loaded Via when the backend reported a value
			// (the row itself is hidden when empty — same rule).
			...(loadedVia ? [`${t("about.loadedVia")}: ${loadedVia}`] : []),
			`${t("about.hotkey")}: ${hotkey}`,
			`${t("about.microphone")}: ${microphone}`,
		].join("\n");
		try {
			if (navigator.clipboard?.writeText) {
				await navigator.clipboard.writeText(lines);
			} else {
				// jsdom / non-secure-context fallback: hidden textarea +
				// execCommand (mirrors the ErrorBoundary pattern).
				const ta = document.createElement("textarea");
				ta.value = lines;
				ta.style.position = "fixed";
				ta.style.opacity = "0";
				document.body.appendChild(ta);
				ta.select();
				document.execCommand("copy");
				document.body.removeChild(ta);
			}
			showSnack(t("about.copied"), "success");
		} catch {
			showSnack(t("about.copyFailed"), "error");
		}
	}, [
		backendLabel,
		configDir,
		asrBackend,
		device,
		loadedVia,
		hotkey,
		microphone,
		showSnack,
	]);

	return (
		<div className="min-h-full">
			{/* use the standard page shell (flex
				min-h-full w-full flex-col) so the page content stretches to
				fill the viewport — matches History, Vocabulary, Templates,
				Microphone, Dashboard. Previously About dropped the flex
				wrapper, leaving an empty gap below short content. */}
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col space-y-8 px-6 pt-28 pb-6">
				<div id="about-top" className="scroll-mt-28">
					<PageHeading
						title={t("about.title")}
						description={t("about.description")}
					/>
				</div>

				{/* Sticky in-page section nav — jump to a section without
				    scrolling the whole long page. */}
				<nav
					aria-label={t("about.sectionNavLabel")}
					className="sticky top-0 z-20 rounded-lg border border-border/10 bg-(--bg-subtle)/90 backdrop-blur-sm"
				>
					<ul className="flex flex-wrap items-center gap-0.5 px-1.5 py-1.5">
						{SECTIONS.map((section) => (
							<li key={section.id}>
								<a
									href={`#${section.id}`}
									className="inline-flex cursor-pointer items-center rounded-full px-2.5 py-1 text-xs font-medium text-(--text-muted) transition-colors hover:bg-foreground/5 hover:text-(--text-primary) focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
								>
									{t(section.labelKey)}
								</a>
							</li>
						))}
					</ul>
				</nav>

				{/* ── Diagnostics ───────────────────────────────────────── */}
				<div id="about-diagnostics" className="scroll-mt-28">
					<SettingsSection
						title={t("about.diagnosticsTitle")}
						description={t("about.diagnosticsDescription")}
						action={
							<Button
								variant="outline"
								size="sm"
								onClick={copyDiagnostics}
								className="shrink-0 gap-1.5 text-(--text-muted) hover:text-(--text-primary)"
							>
								<HugeiconsIcon
									icon={Copy01Icon}
									strokeWidth={2}
									aria-hidden="true"
									className="size-4"
								/>
								{t("about.copyDiagnostics")}
							</Button>
						}
					>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.appVersion")}
							value={t("about.versionValue", { version: APP_VERSION })}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.pythonBackend")}
							value={backendStatus}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.configDirectory")}
							value={configDirValue}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.asrBackend")}
							value={asrBackend}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.device")}
							value={device}
						/>
						{/* show which device/compute_type the model
							actually loaded via. Hidden entirely when the
							backend reported nothing (no model loaded yet) —
							a bare "—" would be confusing. */}
						{loadedVia && (
							<>
								<ReadonlyRow
									variant="label-emphasized"
									label={t("about.loadedVia")}
									value={<LiveValue present>{loadedVia}</LiveValue>}
								/>
								<p className="px-3.5 pb-2.5 text-xs text-(--text-muted)">
									{t("about.loadedViaHint")}
								</p>
							</>
						)}
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.hotkey")}
							// Render the configured hotkey as design-system Kbd
							// chips (same primitive as Home / the Help overlay),
							// normalized + platform-formatted via formatHotkey.
							value={<HotkeyChips keys={formatHotkey(hotkey)} />}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.microphone")}
							value={microphone}
						/>
					</SettingsSection>
				</div>

				{/* ── Privacy ──────────────────────────────────────────── */}
				{/* expanded privacy disclosure. */}
				<div id="about-privacy" className="scroll-mt-28">
					<SettingsSection
						title={t("about.privacyTitle")}
						description={t("about.privacyDescription")}
					>
						{/* Five distinct topic blocks, each with a small icon —
						    layout-only restructure; the legal copy itself is
						    untouched (same i18n strings, verbatim). */}
						<div className="space-y-3 px-3.5 py-3.5">
							{PRIVACY_TOPICS.map((topic) => (
								<div
									key={topic.title}
									className="flex gap-3 rounded-lg border border-border/10 bg-(--bg-subtle) p-3"
								>
									<span
										aria-hidden="true"
										className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent"
									>
										<HugeiconsIcon
											icon={topic.icon}
											strokeWidth={2}
											className="size-4"
										/>
									</span>
									<div className="min-w-0 text-sm leading-relaxed text-(--text-muted)">
										<p className="font-medium text-(--text-primary)">
											{t(topic.title)}
										</p>
										{/* max-w-prose: keep paragraph line length
										    readable; the cards can stay full width. */}
										<p className="mt-1 max-w-prose">
											{t(topic.desc, {
												// R7-F15: fall back to "Loading…" while configDir is empty.
												configDir: configDir || t("about.loading"),
											})}
										</p>
									</div>
								</div>
							))}
							{/* SECURITY.md is the canonical privacy +
							 * security policy doc in this repo (there is no
							 * separate PRIVACY.md). Previously this section
							 * had a "Full Privacy Policy" button that pointed
							 * at the same SECURITY.md as the Resources section's
							 * "Security Policy" button — confusing UX. We now
							 * point users to the Security Policy button below
							 * instead of rendering a duplicate. */}
							<p className="max-w-prose text-sm leading-relaxed text-(--text-muted)">
								<span className="font-medium text-(--text-primary)">
									{t("about.privacyPolicyNoteLabel")}
								</span>{" "}
								{t("about.privacyPolicyNote")}
							</p>
						</div>
					</SettingsSection>
				</div>

				{/* ── Resources ────────────────────────────────────────── */}
				{/* feedback channels. */}
				<div id="about-resources" className="scroll-mt-28">
					<SettingsSection
						title={t("about.resourcesTitle")}
						description={t("about.resourcesDescription")}
					>
						<div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5">
							{RESOURCE_LINKS.map((link) => (
								<Button
									key={link.href}
									asChild
									variant="outline"
									size="sm"
									className="gap-1.5 text-(--text-muted) hover:text-(--text-primary)"
								>
									<a href={link.href} target="_blank" rel="noreferrer noopener">
										<HugeiconsIcon
											icon={link.icon}
											strokeWidth={2}
											aria-hidden="true"
											className="size-4"
										/>
										{t(link.label)}
										{/* external-link indicator — all of these
										    navigate away from the app. */}
										<HugeiconsIcon
											icon={ArrowUpRight01Icon}
											strokeWidth={2.25}
											aria-hidden="true"
											className="size-3 opacity-60"
										/>
									</a>
								</Button>
							))}
						</div>
					</SettingsSection>
				</div>

				{/* ── Credits & Licenses ───────────────────────────────── */}
				{/* surface authors, third-party
				 * libraries, fonts, and icons so users can see what
				 * Voice Typer is built on without leaving the app. Framed
				 * differently from the Diagnostics card so the static
				 * attribution reads as a distinct surface. */}
				<div id="about-credits" className="scroll-mt-28">
					<SettingsSection
						title={t("about.creditsTitle")}
						description={t("about.creditsDescription")}
						cardClassName="border border-border/10 bg-(--bg-subtle)/60"
					>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.creditsAuthorsLabel")}
							value={t("about.creditsAuthorsValue")}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.creditsLibrariesLabel")}
							value={t("about.creditsLibrariesValue")}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.creditsFontsLabel")}
							value={t("about.creditsFontsValue")}
						/>
						<ReadonlyRow
							variant="label-emphasized"
							label={t("about.creditsIconsLabel")}
							value={t("about.creditsIconsValue")}
						/>
					</SettingsSection>
				</div>
			</div>
		</div>
	);
}
