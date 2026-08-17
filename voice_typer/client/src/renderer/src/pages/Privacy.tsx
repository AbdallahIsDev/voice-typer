// Privacy page — how Voice Typer handles audio and data.
//
// IA split: the privacy disclosure previously lived inside the About
// page (which is now genuinely About: product identity, version,
// platforms). Privacy is a first-class destination in the sidebar so
// users don't have to discover it behind an "About" label.
//
// Content is the SAME factual disclosure the About page carried (kept
// intact per the audit rule "do not remove meaningful privacy
// disclosures"), with two presentation fixes:
//   - topic titles no longer end in sentence punctuation (a trailing
//     "." read like a status dot)
//   - the voice-biometrics copy is calmer and more factual
//
// The only dynamic value is the config directory (from the backend's
// get_status — the same authoritative source the Settings diagnostics
// section and Analytics data path use), interpolated into the Local
// data description. On a failed fetch the row falls back to a neutral
// "—" instead of a permanent "Loading…".

import PageHeading from "@/components/common/PageHeading";
import { usePython } from "@/hooks/usePython";
import { t, useT } from "@/i18n/i18n";
import {
    CloudIcon,
    DatabaseIcon,
    Layers01Icon,
    Mic02Icon,
    Shield01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useRef, useState } from "react";

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

export default function PrivacyPage() {
	const { call } = usePython();
	// callRef mirror (Home/About pattern): the mount probe effect reads
	// `callRef.current` so its deps stay identity-free — a test mock
	// handing out a fresh `call` per render must not re-fire the probe.
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);
	// Re-render on locale switch so all t() calls re-resolve.
	useT();
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
				title={t("about.privacyTitle")}
				description={t("about.privacyDescription")}
			/>

			{/* The disclosure — five topic rows with thin dividers (the
			    section card's divide-y supplies them). Icons render
			    directly (no chip), in the standard muted icon tone. */}
			<div className="divide-y divide-border/10 rounded-xl border border-border/10 bg-(--bg-subtle)">
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
							    readable; the rows can stay full width. */}
							<p className="mt-1 max-w-prose">
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
