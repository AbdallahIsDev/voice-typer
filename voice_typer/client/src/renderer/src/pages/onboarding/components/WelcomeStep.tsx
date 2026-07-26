import type { Ref } from "react";
import { t } from "@/i18n/i18n";

// shared heading class. WelcomeStep previously used
// an inline <h1> className that was visually identical to the other
// steps' <h2> className — but as an <h1> it created a duplicate-h1
// WCAG H25 violation (Onboarding.tsx already renders an sr-only <h1>
// for step context). Migrated to <h2> with the same class so the
// visible step heading sits below the page-level sr-only <h1> in the
// heading hierarchy, matching every other step component.
const HEADING_CLASS =
	"mb-3 text-2xl font-bold text-(--text-primary) outline-none";

export interface WelcomeStepProps {
	headingRef: Ref<HTMLHeadingElement>;
}

export function WelcomeStep({ headingRef }: WelcomeStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.welcomeTitle")}
			</h2>
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
