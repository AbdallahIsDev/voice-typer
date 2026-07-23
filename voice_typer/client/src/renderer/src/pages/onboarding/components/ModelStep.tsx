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
import type { ModelOption } from "../lib/types";

export interface ModelStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	modelOptions: ModelOption[];
	selectedModel: string;
	setSelectedModel: (v: string) => void;
}

export function ModelStep({
	headingRef,
	modelOptions,
	selectedModel,
	setSelectedModel,
}: ModelStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.modelTitle")}
			</h2>
			<p className="mb-4 text-sm text-(--text-muted)">
				{t("onboarding.modelDescription")}
			</p>
			<Select value={selectedModel} onValueChange={setSelectedModel}>
				<SelectTrigger
					className="w-full"
					aria-label={t("onboarding.modelSelectAria")}
				>
					<SelectValue placeholder={t("onboarding.modelSelectAria")} />
				</SelectTrigger>
				<SelectContent>
					{modelOptions.map((m) => (
						<SelectItem key={m.name} value={m.name}>
							{m.description} — {m.size} ({m.speed})
						</SelectItem>
					))}
				</SelectContent>
			</Select>
		</>
	);
}

export default ModelStep;
