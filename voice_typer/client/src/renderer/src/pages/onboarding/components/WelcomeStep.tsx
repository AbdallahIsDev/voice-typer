import type { Ref } from "react";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	getLocale,
	getLocaleLabel,
	type Locale,
	SUPPORTED_LOCALES,
	setLocale,
	t,
	useT,
} from "@/i18n/i18n";

const HEADING_CLASS = "text-2xl font-bold text-(--text-primary) outline-none";

const LOCALE_OPTIONS = SUPPORTED_LOCALES.map((locale) => ({
	value: locale,
	label: getLocaleLabel(locale),
}));

export interface WelcomeStepProps {
	headingRef: Ref<HTMLHeadingElement>;
}

export function WelcomeStep({ headingRef }: WelcomeStepProps) {
	useT();
	const currentLocale = getLocale();

	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.welcomeTitle")}
			</h2>
			<p className="text-sm text-(--text-muted)">
				{t("onboarding.welcomeDescription")}
			</p>
			<ul className="flex flex-col gap-2 text-sm text-(--text-secondary)">
				{[1, 2, 3, 4, 5, 6].map((n) => (
					<li key={n} className="flex items-center gap-2">
						<span className="text-accent">{n}.</span>{" "}
						{t(`onboarding.step${n}Item`)}
					</li>
				))}
			</ul>
			<div
				className="flex flex-col gap-2"
				data-testid="onboarding-language-picker"
			>
				<label
					className="text-xs font-medium text-(--text-muted)"
					htmlFor="onboarding-language-select"
				>
					{t("settings.appLanguage")}
				</label>
				<Select
					value={currentLocale}
					onValueChange={(v) => setLocale(v as Locale)}
				>
					<SelectTrigger
						id="onboarding-language-select"
						className="w-full"
						aria-label={t("settings.appLanguage")}
					>
						<SelectValue placeholder={t("settings.appLanguage")} />
					</SelectTrigger>
					<SelectContent>
						{LOCALE_OPTIONS.map((opt) => (
							<SelectItem key={opt.value} value={opt.value}>
								{opt.label}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
			</div>
		</>
	);
}

export default WelcomeStep;
