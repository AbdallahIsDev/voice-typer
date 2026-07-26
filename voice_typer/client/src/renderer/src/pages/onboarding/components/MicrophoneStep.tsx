import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { Ref } from "react";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";
import { HEADING_CLASS } from "../lib/constants";
import type { MicrophoneOption } from "../lib/types";

export interface MicrophoneStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	microphones: MicrophoneOption[];
	selectedMic: string;
	setSelectedMic: (v: string) => void;
	/** Optional refresh callback. When provided, the no-mics
	 * branch shows a Refresh button + hint instead of a dead-end
	 * message. The parent (Onboarding.tsx via useOnboardingWizard)
	 * can wire this to re-fetch the microphone list. */
	onRefreshMics?: () => void;
}

export function MicrophoneStep({
	headingRef,
	microphones,
	selectedMic,
	setSelectedMic,
	onRefreshMics,
}: MicrophoneStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.micTitle")}
			</h2>
			<p className="mb-4 text-sm text-(--text-muted)">
				{t("onboarding.micDescription")}
			</p>
			{microphones.length > 0 ? (
				<Select value={selectedMic} onValueChange={setSelectedMic}>
					<SelectTrigger
						className="w-full"
						aria-label={t("onboarding.micSelectAria")}
					>
						<SelectValue placeholder={t("onboarding.micSelectAria")} />
					</SelectTrigger>
					<SelectContent>
						{microphones.map((mic) => (
							<SelectItem key={mic.id} value={mic.id}>
								{mic.name}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
			) : (
				// the no-mics branch was previously a
				// dead end — just a passive message with no recovery
				// affordance. Now it shows the message PLUS a hint about
				// OS mic permission and (when the parent provides
				// onRefreshMics) a Refresh button so the user can re-scan
				// after plugging in a mic or granting permission.
				<div className="flex flex-col gap-3">
					<p className="text-sm text-(--text-muted)">
						{t("onboarding.noMics")}
					</p>
					<p className="text-xs text-(--text-muted)">
						{t("onboarding.noMicsHint")}
					</p>
					{onRefreshMics && (
						<Button
							type="button"
							variant="outline"
							className="self-start gap-2"
							onClick={onRefreshMics}
						>
							<HugeiconsIcon
								icon={RefreshIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{t("onboarding.refreshMics")}
						</Button>
					)}
				</div>
			)}
		</>
	);
}

export default MicrophoneStep;
