// About page — product identity.
//
// IA split: this page is now genuinely ABOUT the product: what Voice
// Typer is, what it does, its local/offline + optional-cloud
// capabilities, the installed version, and supported platforms.
//
// What moved out (and where it lives now):
//   - Privacy disclosure  → Privacy page (sidebar destination)
//   - Diagnostics table   → Settings → Troubleshooting (support area)
//   - Resources & Feedback → Settings → Troubleshooting (support area)
//
// The page stays static AT LOAD — the only dynamic values are the
// installed version, read directly from package.json at build time
// (VERSION-SOURCE-FIX) so it never drifts from the canonical source of
// truth on a release bump, and an USER-INITIATED runtime-pack update
// check (click "Check for Updates"; no fetch happens on mount), so the
// page still renders fine when the backend is down.

import { CloudIcon, Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRef, useState } from "react";
import { APP_NAME } from "@/branding";
import PageHeading from "@/components/common/PageHeading";
import { ReadonlyRow } from "@/components/common/ReadonlyRow";
import { Logo } from "@/components/layout/Logo";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { t, useT } from "@/i18n/i18n";
import { consentBodyKey, openConsentGate } from "@/lib/consentGate";
import pkg from "../../../../package.json";

// App version — read directly from package.json (see VERSION-SOURCE-FIX
// comment at the top of the file) so this never drifts from the
// canonical source of truth on a release bump.
const APP_VERSION = pkg.version as string;

/**
 * Shape of the `check_offline_pack_update` IPC response.
 *
 * Mirrors `UpdateCheckResult` in
 * `voice_typer/server/service/update_check.py` (`total=False`, so every
 * field is optional here too) — the handler
 * (`_handle_check_offline_pack_update` in `server/ipc/lifecycle.py`)
 * returns it as a plain dict over the bridge.
 */
interface PackUpdateCheckResult {
	success?: boolean;
	local_version?: string | null;
	remote_version?: string | null;
	update_available?: boolean;
	download_triggered?: boolean;
	consent_required?: boolean;
	error?: string;
	reason?: string;
}

/** Lifecycle of the user-initiated pack update check. */
type PackUpdatePhase = "idle" | "checking" | "done";

export default function AboutPage() {
	// Re-render on locale switch so all t() calls re-resolve.
	useT();

	const { call } = usePython();
	// Ref mirror of `call` (PrewarmAndUpdates pattern) so the click
	// handler never goes stale across renders.
	const callRef = useRef(call);
	callRef.current = call;

	// Runtime-pack update check — user-initiated only. No fetch on
	// mount: the check hits the GitHub Releases manifest (C-DATA-1
	// category-2 allowed update check) and may trigger a consent-gated
	// background download, so it must never fire implicitly.
	const [packPhase, setPackPhase] = useState<PackUpdatePhase>("idle");
	const [packResult, setPackResult] = useState<PackUpdateCheckResult | null>(
		null,
	);

	const runPackUpdateCheck = async () => {
		setPackPhase("checking");
		try {
			const result = (await callRef.current(
				"check_offline_pack_update",
				{},
			)) as PackUpdateCheckResult;
			setPackResult(result);
			// Point-of-use consent gate: the backend found an update but
			// refused to start the download because
			// `offline_pack_consent` is off. Ask via the SHARED consent
			// dialog right now — Allow persists the consent and re-runs
			// the check (which then triggers the download); Cancel
			// leaves the pack untouched. No persistent "enable in
			// Settings" nag — the modal only opens at the moment of the
			// blocked attempt, and only while the consent is missing.
			if (result?.consent_required) {
				openConsentGate({
					consentField: "offline_pack_consent",
					bodyKey: consentBodyKey("offline_pack_consent"),
					onAllow: () => void runPackUpdateCheck(),
				});
			}
		} catch (err) {
			setPackResult({
				success: false,
				error: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setPackPhase("done");
		}
	};

	const handleCheckPackUpdate = () => {
		void runPackUpdateCheck();
	};

	// Resolve the row value + optional detail line from the current
	// phase + last response. Computed INSIDE the component body so the
	// strings follow the active locale (B-REVIEW-3 pattern).
	let packStatusText: string;
	let packDetail: string | null = null;
	if (packPhase === "idle") {
		packStatusText = t("about.runtimePackNotChecked");
	} else if (packPhase === "checking") {
		packStatusText = t("about.checking");
	} else if (packResult?.consent_required) {
		// Update found but the download was refused by the consent gate
		// (server returns success=false + consent_required=true). The
		// shared consent dialog was opened by `runPackUpdateCheck` —
		// this row stays informational ("update available"), never a
		// persistent "go enable consent" instruction.
		packStatusText =
			packResult.remote_version != null
				? t("about.updateAvailable", { version: packResult.remote_version })
				: t("about.runtimePackFailed");
	} else if (packResult && packResult.success === true) {
		if (packResult.update_available) {
			packStatusText =
				packResult.remote_version != null
					? t("about.updateAvailable", { version: packResult.remote_version })
					: t("about.runtimePackFailed");
			if (packResult.download_triggered) {
				packDetail = t("about.runtimePackDownloadStarted");
			}
		} else if (packResult.local_version != null) {
			packStatusText = t("about.runtimePackUpToDate", {
				version: packResult.local_version,
			});
		} else {
			packStatusText = t("about.unknown");
		}
	} else {
		packStatusText = t("about.runtimePackFailed");
		const message = packResult?.error;
		if (typeof message === "string" && message.length > 0) {
			packDetail = message;
		}
	}

	return (
		<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col space-y-6 px-16 pt-28 pb-6">
			<PageHeading
				title={t("about.title")}
				description={t("about.description")}
			/>

			{/* Product identity card — compact, native-app About block.
			    No marketing copy, no hero section: identity, capability
			    split, version, platforms. */}
			<div className="rounded-xl border border-border/10 bg-(--bg-subtle)">
				{/* Identity row: logo + name + capability summary. */}
				<div className="flex items-center gap-3 px-5 pt-5">
					<Logo size={40} className="shrink-0" />
					<div className="min-w-0">
						<h2 className="text-lg font-semibold tracking-tight text-(--text-primary)">
							{APP_NAME}
						</h2>
						<p className="truncate text-xs text-(--text-muted)">
							{t("about.productTagline")}
						</p>
					</div>
				</div>

				<p className="max-w-prose px-5 pt-4 text-sm leading-relaxed text-(--text-muted)">
					{t("about.productDesc")}
				</p>

				{/* Local vs Cloud — the capability split made visually
				    obvious: two side-by-side blocks with their own icon
				    + title + one-line description. */}
				<div className="grid gap-3 px-5 pt-5 sm:grid-cols-2">
					<div className="rounded-lg border border-border/10 bg-(--bg) p-3.5">
						<div className="flex items-center gap-2">
							<HugeiconsIcon
								icon={Mic02Icon}
								strokeWidth={1.75}
								aria-hidden="true"
								className="size-4 shrink-0 text-(--text-muted)"
							/>
							<p className="text-sm font-medium text-(--text-primary)">
								{t("about.localTitle")}
							</p>
						</div>
						<p className="mt-1.5 text-xs leading-relaxed text-(--text-muted)">
							{t("about.localDesc")}
						</p>
					</div>
					<div className="rounded-lg border border-border/10 bg-(--bg) p-3.5">
						<div className="flex items-center gap-2">
							<HugeiconsIcon
								icon={CloudIcon}
								strokeWidth={1.75}
								aria-hidden="true"
								className="size-4 shrink-0 text-(--text-muted)"
							/>
							<p className="text-sm font-medium text-(--text-primary)">
								{t("about.cloudTitle")}
							</p>
						</div>
						<p className="mt-1.5 text-xs leading-relaxed text-(--text-muted)">
							{t("about.cloudDesc")}
						</p>
					</div>
				</div>

				{/* Meta rows: version + platforms. */}
				<div className="mt-5 border-t border-border/10 py-1.5">
					<ReadonlyRow
						variant="label-emphasized"
						label={t("about.version")}
						value={t("about.versionValue", { version: APP_VERSION })}
					/>
					<ReadonlyRow
						variant="label-emphasized"
						label={t("about.platforms")}
						value={t("about.platformsValue")}
					/>
					{/* Runtime pack — installed pack status + a
					    user-initiated update check against the release
					    manifest (C-DATA-1 category-2 allowed update check;
					    fires ONLY on button click, never on mount). */}
					<ReadonlyRow
						variant="label-emphasized"
						label={t("about.runtimePack")}
						value={packStatusText}
					/>
					<div className="flex flex-wrap items-center gap-3 px-3.5 pb-2.5 pt-1">
						<Button
							variant="outline"
							size="sm"
							onClick={handleCheckPackUpdate}
							disabled={packPhase === "checking"}
						>
							{packPhase === "checking"
								? t("about.checking")
								: t("about.checkForUpdates")}
						</Button>
						{packDetail && (
							<p className="min-w-0 flex-1 text-xs leading-snug text-(--text-muted)">
								{packDetail}
							</p>
						)}
					</div>
				</div>
			</div>
		</div>
	);
}
