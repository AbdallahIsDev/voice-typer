import type { Ref } from "react";
import { useMemo, useState } from "react";
import { HelpOverlay } from "@/components/help/HelpOverlay";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import {
	configHotkeyLabels,
	formatHotkey,
	REPASTE_HOTKEY_DEFAULT,
} from "@/components/hotkey/hotkey-format";
import { Button } from "@/components/ui/button";
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
	//
	// The cheat-sheet link opens the SAME shared HelpOverlay component
	// the app's `?` shortcut opens (the punctuation cheat sheet lives
	// inside it). The overlay instance is page-local because the
	// wizard page can't reach App.tsx's instance; the `?`-shortcut's
	// open-dialog guard prevents the two from stacking.
	const [helpOpen, setHelpOpen] = useState(false);
	const helpLabels = useMemo(
		() =>
			configHotkeyLabels({
				hotkey: selectedHotkey || null,
				repaste_hotkey: REPASTE_HOTKEY_DEFAULT,
			}),
		[selectedHotkey],
	);

	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.completeTitle")}
			</h2>
			{/* The `completeDescription` key already exists in every
			    locale; this is the only consumer. It no longer promises a
			    background model download — the user already chose local
			    (explicit download) or cloud on the Model step. */}
			<p className="text-sm text-(--text-secondary)">
				{t("onboarding.completeDescription", {
					hotkey: formatHotkey(selectedHotkey),
				})}
			</p>
			<div className="flex flex-col gap-2 text-sm text-(--text-secondary)">
				<p>
					{t("onboarding.summaryBackend")}{" "}
					<strong>{backendLabel(selectedBackend)}</strong>
				</p>
				<p>
					{t("onboarding.summaryHotkey")}{" "}
					<HotkeyChips keys={formatHotkey(selectedHotkey)} />
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
			{/* Cheat-sheet entry point + `?` shortcut tip, consistent with
			    the summary block's visual language. The link opens the
			    shared HelpOverlay (which contains the punctuation cheat
			    sheet); the tip names the `?` shortcut via HotkeyChips (no
			    "+" text — C-UI-1). */}
			<div
				className="flex flex-wrap items-center gap-x-2 gap-y-2 text-sm text-(--text-secondary)"
				data-testid="onboarding-done-help"
			>
				<Button
					variant="link"
					size="sm"
					className="h-auto p-0"
					onClick={() => setHelpOpen(true)}
					aria-label={t("help.openCheatSheet")}
					data-testid="done-step-cheatsheet-link"
				>
					{t("help.openCheatSheet")}
				</Button>
				<span className="text-(--text-secondary)">
					{t("onboarding.doneHelpHint")}
				</span>
				<HotkeyChips keys="?" />
			</div>
			<HelpOverlay
				open={helpOpen}
				onClose={() => setHelpOpen(false)}
				dictationLabel={helpLabels.dictationLabel}
				repasteLabel={helpLabels.repasteLabel}
			/>
		</>
	);
}

export default DoneStep;
