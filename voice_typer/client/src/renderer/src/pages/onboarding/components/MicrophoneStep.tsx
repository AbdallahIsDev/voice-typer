import type { Ref } from "react";
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
}

export function MicrophoneStep({
	headingRef,
	microphones,
	selectedMic,
	setSelectedMic,
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
				<p className="text-sm text-(--text-muted)">{t("onboarding.noMics")}</p>
			)}
		</>
	);
}

export default MicrophoneStep;
