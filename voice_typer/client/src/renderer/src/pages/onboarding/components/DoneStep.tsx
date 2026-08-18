import type { Ref } from "react";
import { formatHotkey } from "@/components/hotkey/hotkey-format";
import { t } from "@/i18n/i18n";
import type { BackendChoice } from "../hooks/useOnboardingWizard";
import { HEADING_CLASS } from "../lib/constants";
import type { MicrophoneOption } from "../lib/types";

export interface DoneStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	selectedHotkey: string;
	selectedModel: string;
	selectedMic: string;
	microphones: MicrophoneOption[];
	// Local-vs-cloud choice made on the Model step (shown in the summary
	// so the user sees what they opted into — nothing is downloaded
	// automatically).
	selectedBackend: BackendChoice;
}

function backendLabel(backend: BackendChoice): string {
	return backend === "cloud"
		? t("onboarding.backendCloudLabel")
		: t("onboarding.backendLocalLabel");
}

export function DoneStep({
	headingRef,
	selectedHotkey,
	selectedModel,
	selectedMic,
	microphones,
	selectedBackend,
}: DoneStepProps) {
	//use existing `summaryHotkey`/`summaryMic`/`summaryModel`.
	// The old `doneHotkey`/`doneMic`/`doneModel` keys never existed in any
	// locale, so the Done step rendered raw key strings instead of labels.
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.completeTitle")}
			</h2>
			{/* The `completeDescription` key already exists in every
			    locale; this is the only consumer. It no longer promises a
			    background model download — the user already chose local
			    (explicit download) or cloud on the Model step. */}
			<p className="mb-4 text-sm text-(--text-secondary)">
				{t("onboarding.completeDescription", {
					hotkey: formatHotkey(selectedHotkey),
				})}
			</p>
			<div className="mb-6 space-y-2 text-sm text-(--text-secondary)">
				<p>
					{t("onboarding.summaryBackend")}{" "}
					<strong>{backendLabel(selectedBackend)}</strong>
				</p>
				<p>
					{t("onboarding.summaryHotkey")}{" "}
					<strong>{formatHotkey(selectedHotkey)}</strong>
				</p>
				<p>
					{t("onboarding.summaryModel")} <strong>{selectedModel}</strong>
				</p>
				{selectedMic && (
					<p>
						{t("onboarding.summaryMic")}{" "}
						<strong>
							{microphones.find((m) => m.id === selectedMic)?.name ??
								selectedMic}
						</strong>
					</p>
				)}
			</div>
		</>
	);
}

export default DoneStep;
