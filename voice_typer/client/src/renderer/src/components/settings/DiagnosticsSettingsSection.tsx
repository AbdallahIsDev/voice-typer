// DiagnosticsSettingsSection — the live diagnostics table + "Copy
// diagnostics" button.
//
// IA split: the diagnostics that previously lived on the About page
// now live here, in Settings → Troubleshooting (the support-oriented
// destination). The About page is product identity only.
//
// NOTE ON KEY NAMESPACE: the section renders the `about.*` i18n keys
// (about.diagnosticsTitle etc.) — the keys predate the IA split and
// are consumed by this section, the Privacy page, and the About page.
// The namespaces are internal; the user-facing destinations are
// correct.
//
// Behaviour is identical to the previous About-page implementation:
//   - probes get_status / get_config / get_model_status on mount
//   - the Speech recognizer / Device rows derive from the SHARED
//     resolveActiveModel helper (lib/utils/models.ts) — the same
//     source of truth as the Analytics page's Current Setup cards
//   - Config Directory resolves from get_status's config_dir (the
//     backend's authoritative path — never a hardcoded Windows path)
//   - Copy diagnostics formats a labeled block to the clipboard

import { Copy01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, type ReactNode, useCallback, useEffect, useState } from "react";
import { APP_NAME } from "@/branding";
import { ReadonlyRow } from "@/components/common/ReadonlyRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { Button } from "@/components/ui/button";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { getLocale, t, tChoice } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import { formatDevice } from "@/lib/utils/configDisplay";
import { resolveActiveModel } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";
import type { ModelStatusMap } from "@/types/ipc";
// VERSION-SOURCE-FIX: import the version directly from package.json so
// it stays in sync with the single source of truth.
import pkg from "../../../../../package.json";
import type { IsVisibleFn } from "./types";

const APP_VERSION = pkg.version as string;

function StatusDot({ connected }: { connected: boolean }) {
	return (
		<span
			className={
				"inline-flex items-center gap-2 " +
				(connected ? "text-(--text-primary)" : "text-destructive")
			}
		>
			{/* the colored dot is purely decorative — the adjacent
			    "Connected" / "Disconnected" text conveys the state to
			    assistive tech. */}
			<span
				aria-hidden="true"
				className={
					"size-1.5 rounded-full " +
					(connected ? "bg-success" : "bg-destructive")
				}
			/>
			{connected ? t("about.connected") : t("about.disconnected")}
		</span>
	);
}

/**
 * Live status value — a colored dot + text for diagnostics rows that
 * reflect live/dynamic backend state (vs. static config facts).
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
				"inline-flex items-center gap-2",
				present ? "text-(--text-primary)" : "text-destructive",
			)}
		>
			<span
				aria-hidden="true"
				className={cn(
					"size-1.5 shrink-0 rounded-full",
					present ? "bg-success" : "bg-destructive",
				)}
			/>
			<span className="min-w-0 break-all">{children}</span>
		</span>
	);
}

// ADR-0009 Issue 3: format a byte count as a human-readable string.
// Exported for unit-test coverage. Coordinate with moving it to
// `lib/format.ts` (the Dashboard already shares formatBytes there —
// this copy predates that consolidation and is kept exported for the
// existing unit suite).
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
		if (diffMin < 60) return tChoice("about.relativeTime.minutesAgo", diffMin);
		if (diffHr < 24) return tChoice("about.relativeTime.hoursAgo", diffHr);
		if (diffDay < 7) return tChoice("about.relativeTime.daysAgo", diffDay);
		return new Intl.DateTimeFormat(getLocale(), {
			dateStyle: "medium",
		}).format(new Date(then));
	} catch {
		return iso;
	}
}

interface DiagnosticsSettingsSectionProps {
	/** Search-filter predicate — same shape as the page-level helper. */
	isVisible: IsVisibleFn;
}

export const DiagnosticsSettingsSection = memo(
	function DiagnosticsSettingsSection({
		isVisible,
	}: DiagnosticsSettingsSectionProps) {
		const { call } = usePython();
		const { showSnack } = useSnackbar();
		// callRef mirror (Home.tsx pattern): the mount probe effect reads
		// `callRef.current` so its deps stay identity-free — a test mock
		// handing out a fresh `call` per render must not re-fire the probe.
		const callRef = useLatestRef(call);
		const [config, setConfig] = useState<VoiceTyperConfig | null>(null);
		// empty string = still probing / unresolved; renders "—" fallback.
		const [configDir, setConfigDir] = useState<string>("");
		// null = still probing, true/false = settled.
		const [backendConnected, setBackendConnected] = useState<boolean | null>(
			null,
		);
		const [loadedVia, setLoadedVia] = useState("");
		const [modelStatus, setModelStatus] = useState<ModelStatusMap | null>(null);

		// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
		useEffect(() => {
			let cancelled = false;

			const load = async () => {
				try {
					const status = await callRef.current<{
						config_dir?: string;
						status?: string;
						loaded_via?: string;
					}>("get_status");
					if (!cancelled) {
						setBackendConnected(true);
						// The backend returns config_dir since 2026-08-16
						// (service/status.py) — the row resolves to a real
						// path. Older backends fall back to "—".
						if (status?.config_dir) setConfigDir(status.config_dir);
						if (status?.loaded_via) setLoadedVia(status.loaded_via);
					}
				} catch {
					if (!cancelled) setBackendConnected(false);
				}

				try {
					const cfg = await callRef.current<VoiceTyperConfig>("get_config");
					if (!cancelled) setConfig(cfg);
				} catch (e) {
					// intentionally leave config as null — diagnostics
					// simply show "—" until the backend comes back.
					console.warn(
						"[renderer:DiagnosticsSettingsSection] get_config failed:",
						e,
					);
				}

				// Best-effort model-install truth (same IPC the
				// Analytics / Models pages use). Fail-safe: never
				// advertise a model whose weights we can't verify.
				try {
					const ms = await callRef.current<ModelStatusMap>("get_model_status");
					if (!cancelled) setModelStatus(ms ?? {});
				} catch {
					if (!cancelled) setModelStatus({});
				}
			};

			load();
			return () => {
				cancelled = true;
			};
		}, []);

		// SHARED model-install truth (lib/utils/models.ts) — the same
		// function the Analytics page's Current Setup cards use.
		const activeModel = resolveActiveModel(
			config?.model_size ?? "",
			modelStatus ?? {},
			config?.device,
		);
		const asrBackend = !config
			? t("about.unknown")
			: activeModel.model
				? `${config.asr_backend} (${activeModel.model})`
				: t("about.notSelected");
		const device = !config
			? t("about.unknown")
			: activeModel.device
				? formatDevice(activeModel.device)
				: t("about.notSelected");
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
				`${t("about.backend")}: ${backendLabel}`,
				`${t("about.configDirectory")}: ${configDir || t("about.unknown")}`,
				`${t("about.asrBackend")}: ${asrBackend}`,
				`${t("about.device")}: ${device}`,
				...(loadedVia ? [`${t("about.loadedVia")}: ${loadedVia}`] : []),
				`${t("about.hotkey")}: ${hotkey}`,
				`${t("about.microphone")}: ${microphone}`,
			].join("\n");
			try {
				if (navigator.clipboard?.writeText) {
					await navigator.clipboard.writeText(lines);
				} else {
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

		// Section-level hide-when-empty (Settings search filter).
		const title = t("about.diagnosticsTitle");
		const description = t("about.diagnosticsDescription");
		const copyLabel = t("about.copyDiagnostics");
		const sectionVisible =
			isVisible(title, description, title) ||
			[
				copyLabel,
				t("about.appVersion"),
				t("about.backend"),
				t("about.configDirectory"),
				t("about.asrBackend"),
				t("about.device"),
				t("about.hotkey"),
				t("about.microphone"),
			].some((label) => isVisible(label, undefined, title));
		if (!sectionVisible) return null;

		return (
			<SettingsSection
				title={title}
				description={description}
				action={
					<Button
						variant="outline"
						size="sm"
						onClick={copyDiagnostics}
						className="shrink-0 gap-2 text-(--text-muted) hover:text-(--text-primary)"
					>
						<HugeiconsIcon
							icon={Copy01Icon}
							strokeWidth={2}
							aria-hidden="true"
							className="size-4"
						/>
						{copyLabel}
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
					label={t("about.backend")}
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
				{/* show which device/compute_type the model actually
				    loaded via. Hidden entirely when the backend reported
				    nothing (no model loaded yet) — a bare "—" would be
				    confusing. */}
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
					value={<HotkeyChips keys={formatHotkey(hotkey)} />}
				/>
				<ReadonlyRow
					variant="label-emphasized"
					label={t("about.microphone")}
					value={microphone}
				/>
			</SettingsSection>
		);
	},
);
