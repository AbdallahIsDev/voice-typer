import type { Ref } from "react";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";
import { formatVram } from "@/lib/format";
import { formatModelSpeed } from "@/lib/utils/models";
import { HEADING_CLASS } from "../lib/constants";
import type { ModelOption } from "../lib/types";

export interface ModelStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	modelOptions: ModelOption[];
	selectedModel: string;
	setSelectedModel: (v: string) => void;
}

/**
 * BG-100: derive the language-coverage badge key for a model option.
 *
 * `languages` follows the same convention as
 * `ModelMetadata.supported_languages` in `lib/utils/models.ts`:
 *   - `undefined` → field not sent by backend → no badge (caller skips)
 *   - `null`      → all languages (multilingual)
 *   - `[]`        → treat as "no explicit list" → multilingual fallback
 *   - `['en']` (length 1, only English) → English-only badge
 *   - any other non-empty array → multilingual badge
 */
function languageBadgeKey(
	languages: string[] | null | undefined,
): string | null {
	if (languages === undefined) return null;
	if (languages === null) return "onboarding.multilingualBadge";
	if (languages.length === 0) return "onboarding.multilingualBadge";
	const onlyEnglish = languages.every((l) => l.toLowerCase() === "en");
	return onlyEnglish
		? "onboarding.englishOnlyBadge"
		: "onboarding.multilingualBadge";
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
					{modelOptions.map((m) => {
						const langKey = languageBadgeKey(m.languages);
						return (
							<SelectItem
								key={m.name}
								value={m.name}
								textValue={`${m.description} — ${m.size} (${formatModelSpeed(m.speed)})`}
							>
								<span className="flex flex-wrap items-center gap-1.5">
									<span>
										{m.description} — {m.size} ({formatModelSpeed(m.speed)})
									</span>
									{/* BG-100: per-option badge row showing VRAM
									    requirement and language coverage. Both
									    badges are optional — older backends don't
									    return these fields. */}
									{m.vram_gb != null && (
										<span className="rounded-full bg-bg-subtle px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-(--text-muted)">
											{t("onboarding.vramBadge", {
												vram: formatVram(m.vram_gb * 1024),
											})}
										</span>
									)}
									{langKey != null && (
										<span className="rounded-full bg-bg-subtle px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-(--text-muted)">
											{t(langKey)}
										</span>
									)}
								</span>
							</SelectItem>
						);
					})}
				</SelectContent>
			</Select>
		</>
	);
}

export default ModelStep;
