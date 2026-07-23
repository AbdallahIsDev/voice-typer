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

export interface HotkeyStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	hotkeyPresets: string[];
	selectedHotkey: string;
	setSelectedHotkey: (v: string) => void;
}

export function HotkeyStep({
	headingRef,
	hotkeyPresets,
	selectedHotkey,
	setSelectedHotkey,
}: HotkeyStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.hotkeyTitle")}
			</h2>
			<p className="mb-4 text-sm text-(--text-muted)">
				{t("onboarding.hotkeyDescription")}
			</p>
			<Select value={selectedHotkey} onValueChange={setSelectedHotkey}>
				<SelectTrigger
					className="w-full"
					aria-label={t("onboarding.hotkeySelectAria")}
				>
					<SelectValue placeholder={t("onboarding.hotkeySelectAria")} />
				</SelectTrigger>
				<SelectContent>
					{hotkeyPresets.map((hk) => (
						<SelectItem key={hk} value={hk}>
							{hk.replace(/[<>]/g, "").toUpperCase()}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
		</>
	);
}

export default HotkeyStep;
