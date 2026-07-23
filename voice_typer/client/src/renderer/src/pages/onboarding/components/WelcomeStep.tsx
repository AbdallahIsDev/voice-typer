import type { Ref } from "react";
import { t } from "@/i18n/i18n";

export interface WelcomeStepProps {
	headingRef: Ref<HTMLHeadingElement>;
}

export function WelcomeStep({ headingRef }: WelcomeStepProps) {
	return (
		<>
			<h1
				ref={headingRef}
				tabIndex={-1}
				className="mb-3 text-2xl font-bold text-(--text-primary) outline-none"
			>
				{t("onboarding.welcomeTitle")}
			</h1>
			<p className="mb-6 text-sm text-(--text-muted)">
				{t("onboarding.welcomeDescription")}
			</p>
			{/* Fix 12: render all 5 step items (was only 3). */}
			<ul className="mb-6 space-y-2 text-sm text-(--text-secondary)">
				{[1, 2, 3, 4, 5].map((n) => (
					<li key={n} className="flex items-center gap-2">
						<span className="text-accent">{n}.</span>{" "}
						{t(`onboarding.step${n}Item`)}
					</li>
				))}
			</ul>
		</>
	);
}

export default WelcomeStep;
