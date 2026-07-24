import type { Ref } from "react";
import { t } from "@/i18n/i18n";
import { HEADING_CLASS } from "../lib/constants";
import type { MicrophoneOption } from "../lib/types";

export interface DoneStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	selectedHotkey: string;
	selectedModel: string;
	selectedMic: string;
	microphones: MicrophoneOption[];
}

export function DoneStep({
	headingRef,
	selectedHotkey,
	selectedModel,
	selectedMic,
	microphones,
}: DoneStepProps) {
	// PVT-005: use existing `summaryHotkey`/`summaryMic`/`summaryModel`.
	// The old `doneHotkey`/`doneMic`/`doneModel` keys never existed in any
	// locale, so the Done step rendered raw key strings instead of labels.
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.completeTitle")}
			</h2>
			{/* BG-14: surface the model-download warning so first-run
			    users aren't surprised by a ~466 MB / ~1.5 GB download
			    on a metered connection. The `completeDescription` key
			    already exists in every locale; this is the only
			    consumer. */}
			<p className="mb-4 text-sm text-(--text-secondary)">
				{t("onboarding.completeDescription", {
					hotkey: selectedHotkey.replace(/[<>]/g, "").toUpperCase(),
				})}
			</p>
			<div className="mb-6 space-y-2 text-sm text-(--text-secondary)">
				<p>
					{t("onboarding.summaryHotkey")}{" "}
					<strong>{selectedHotkey.replace(/[<>]/g, "").toUpperCase()}</strong>
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
