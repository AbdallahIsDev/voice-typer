// About & Privacy page — merged product identity + data-handling
// disclosure.
//
// The previously separate About page (product identity) and Privacy
// page (audio/data disclosure) were combined into ONE sidebar
// destination: both surfaces are short, read as a single "what this
// app is + how it treats your data" story, and the merge removes a
// low-traffic second navigation entry.
//
// The page stays static AT LOAD — the only dynamic values are:
//   - the installed version, read directly from package.json at build
//     time (VERSION-SOURCE-FIX) so it never drifts from the canonical
//     source of truth on a release bump;
//   - an USER-INITIATED runtime-pack update check (click "Check for
//     Updates"; no fetch happens on mount), so the identity card still
//     renders fine when the backend is down;
//   - the config directory (from the backend's get_status — the same
//     authoritative source the Settings diagnostics section and
//     Analytics data path use), interpolated into the Local data
//     description. On a failed fetch the row falls back to a neutral
//     "—" instead of a permanent "Loading…".
//
// The privacy disclosure is kept intact per the audit rule "do not
// remove meaningful privacy disclosures", with its earlier
// presentation fixes preserved:
//   - topic titles no longer end in sentence punctuation (a trailing
//     "." read like a status dot)
//   - the voice-biometrics copy is calmer and more factual

import {
	CloudIcon,
	DatabaseIcon,
	Layers01Icon,
	Mic02Icon,
	VoiceIdIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useRef, useState } from "react";
import { APP_NAME } from "@/branding";
import PageHeading from "@/components/common/PageHeading";
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
 * field is optional) — the handler
 * (`_handle_check_offline_pack_update` in `server/ipc/lifecycle.py`)
 * returns it as a plain dict over the bridge. Only the field the page
 * actually consults is listed; the server may send more.
 */
interface PackUpdateCheckResult {
	consent_required?: boolean;
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
		icon: VoiceIdIcon,
		title: "about.voiceBiometricsTitle",
		desc: "about.voiceBiometricsDesc",
	},
	{
		icon: DatabaseIcon,
		title: "about.localDataTitle",
		desc: "about.localDataDesc",
	},
] as const;

export default function AboutAndPrivacyPage() {
	// Re-render on locale switch so all t() calls re-resolve.
	useT();

	const { call } = usePython();
	// Ref mirror of `call` (PrewarmAndUpdates pattern) shared by BOTH
	// dynamic paths on this page — the pack update check click handler
	// and the mount-time configDir probe — so neither goes stale across
	// renders and a test mock handing out a fresh `call` per render can
	// never re-fire the probe.
	const callRef = useRef(call);
	callRef.current = call;

	// Runtime-pack update check — user-initiated only. No fetch on
	// mount: the check hits the GitHub Releases manifest (C-DATA-1
	// category-2 allowed update check) and may trigger a consent-gated
	// background download, so it must never fire implicitly. There is
	// deliberately NO status readout on this page — the button label
	// flips to "Checking…" while in flight, and the shared consent gate
	// opens at the moment the backend refuses the download for missing
	// consent.
	const [packChecking, setPackChecking] = useState(false);

	const runPackUpdateCheck = async () => {
		setPackChecking(true);
		try {
			const result = (await callRef.current(
				"check_offline_pack_update",
				{},
			)) as PackUpdateCheckResult;
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
		} catch {
			// Silent by design — there is no status readout to update;
			// the user can simply click the button again.
		} finally {
			setPackChecking(false);
		}
	};

	const handleCheckPackUpdate = () => {
		void runPackUpdateCheck();
	};

	// The backend's authoritative config directory (get_status) —
	// rendered into the Local data description. Empty until resolved;
	// a failed fetch leaves it empty and the row shows "—".
	const [configDir, setConfigDir] = useState("");

	useEffect(() => {
		let cancelled = false;
		(async () => {
			try {
				const status = await callRef.current<{ config_dir?: string }>(
					"get_status",
				);
				if (!cancelled && status?.config_dir) setConfigDir(status.config_dir);
			} catch {
				// leave configDir empty — the Local data row falls back
				// to a neutral "—" (never a permanent "Loading…").
			}
		})();
		return () => {
			cancelled = true;
		};
	}, []);

	return (
		<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col space-y-6 px-16 pt-28 pb-6">
			<PageHeading
				title={t("aboutAndPrivacy.title")}
				description={t("aboutAndPrivacy.description")}
			/>

			{/* Product identity card — compact, native-app About block.
			    No marketing copy, no hero section: identity, capability
			    split, version + update check. */}
			<div className="rounded-xl border border-border/5 bg-(--bg-subtle)">
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
					<div className="rounded-lg border border-border/5 bg-(--bg) p-3.5">
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
					<div className="rounded-lg border border-border/5 bg-(--bg) p-3.5">
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

				{/* Version + update check — the card's single meta row: the
			    version text with the (smaller) update-check button beside
			    it at a 24px gap (user spec). The check is user-initiated
			    against the release manifest (C-DATA-1 category-2 allowed
			    update check; fires ONLY on button click, never on
			    mount). */}
				<div className="mt-5 flex flex-wrap items-center gap-6 border-t border-border/5 px-5 py-4">
					<span className="text-sm font-medium text-(--text-primary)">
						{t("about.version")}
					</span>
					<span className="text-sm text-(--text-muted)">
						{t("about.versionValue", { version: APP_VERSION })}
					</span>
					<Button
						variant="outline"
						size="xs"
						onClick={handleCheckPackUpdate}
						disabled={packChecking}
					>
						{packChecking ? t("about.checking") : t("about.checkForUpdates")}
					</Button>
				</div>
			</div>

			{/* The privacy disclosure — five topic rows with thin dividers (the
                            section card's divide-y supplies them). Icons render
                            directly (no chip), in the standard muted icon tone. */}
			<div className="divide-y divide-border/5 rounded-xl border border-border/5 bg-(--bg-subtle)">
				{PRIVACY_TOPICS.map((topic) => (
					<div key={topic.title} className="flex gap-3 px-4 py-4">
						<HugeiconsIcon
							icon={topic.icon}
							strokeWidth={1.75}
							aria-hidden="true"
							className="mt-0.5 size-5 shrink-0 text-(--text-muted)"
						/>
						<div className="min-w-0 text-sm leading-relaxed text-(--text-muted)">
							<p className="font-medium text-(--text-primary)">
								{t(topic.title)}
							</p>
							{/* max-w-prose: keep paragraph line length
							    readable; the rows can stay full width.
							    text-balance: even out the final line of
							    the description when it wraps. */}
							<p className="mt-1 max-w-prose text-balance">
								{t(topic.desc, {
									configDir: configDir || t("about.unknown"),
								})}
							</p>
						</div>
					</div>
				))}
			</div>
		</div>
	);
}
