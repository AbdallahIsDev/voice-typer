// ResourcesSettingsSection — documentation, development, and feedback
// links.
//
// IA split: the resources grid that previously lived on the About page
// now lives here, in Settings → Troubleshooting (the support-oriented
// destination). The About page is product identity only.
//
// NOTE ON KEY NAMESPACE: the section renders the `about.*` i18n keys
// (about.resourcesTitle, about.documentationLink etc.) — the keys
// predate the IA split. The namespaces are internal; the user-facing
// destinations are correct.
//
// The grid is unchanged from the previous About-page implementation:
// 7 links in a 2-column grid (documentation, changelog, GitHub,
// report-bug, request-feature, security), with the last link
// (Contributing) spanning the full row so nothing orphans at half
// width. Each button carries an external-link indicator.
import {
	ArrowUpRight01Icon,
	Book01Icon,
	Bug02Icon,
	BulbIcon,
	Clock01Icon,
	CodeIcon,
	LockIcon,
	UserGroupIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { IsVisibleFn } from "./types";

const GITHUB_REPO = "https://github.com/AbdallahIsDev/voice-typer";
const GITHUB_ISSUES = "https://github.com/AbdallahIsDev/voice-typer/issues";
const SECURITY_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/SECURITY.md";
const CONTRIBUTING_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/CONTRIBUTING.md";
// in-app changelog link so users can see what changed in the
// installed version without leaving the app to browse the repo. Uses
// the existing ``about.viewChangelog`` i18n key (already translated to
// all supported locales). ``CHANGELOG.md`` is the canonical release
// history at the repo root.
const CHANGELOG_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/CHANGELOG.md";
// in-app documentation link. README.md is the canonical entry point for
// user-facing docs in the repo; the /docs folder holds deeper
// references (FEATURES.md, ADRs, debugging guide).
const DOCUMENTATION_URL =
	"https://github.com/AbdallahIsDev/voice-typer/blob/main/README.md";

/** Resources & Feedback links — icon per target + external-link chip. */
const RESOURCE_LINKS = [
	{
		href: DOCUMENTATION_URL,
		icon: Book01Icon,
		label: "about.documentationLink",
	},
	{ href: CHANGELOG_URL, icon: Clock01Icon, label: "about.viewChangelog" },
	{ href: GITHUB_REPO, icon: CodeIcon, label: "about.githubRepository" },
	// Report a Bug / Request a Feature are split into two intent-specific
	// buttons with label-based GitHub issue links (?labels=… works on any
	// repo without depending on template filenames) and matching icons:
	// a bug for reports, a lightbulb for feature ideas.
	{
		href: `${GITHUB_ISSUES}/new?labels=bug`,
		icon: Bug02Icon,
		label: "about.reportBug",
	},
	{
		href: `${GITHUB_ISSUES}/new?labels=enhancement`,
		icon: BulbIcon,
		label: "about.requestFeature",
	},
	{ href: SECURITY_URL, icon: LockIcon, label: "about.securityPolicy" },
	{ href: CONTRIBUTING_URL, icon: UserGroupIcon, label: "about.contributing" },
] as const;

interface ResourcesSettingsSectionProps {
	/** Search-filter predicate — same shape as the page-level helper. */
	isVisible: IsVisibleFn;
}

export const ResourcesSettingsSection = memo(function ResourcesSettingsSection({
	isVisible,
}: ResourcesSettingsSectionProps) {
	const title = t("about.resourcesTitle");
	const description = t("about.resourcesDescription");

	// Section-level hide-when-empty (Settings search filter).
	const sectionVisible =
		isVisible(title, description, title) ||
		RESOURCE_LINKS.some((link) => isVisible(t(link.label), undefined, title));
	if (!sectionVisible) return null;

	return (
		<SettingsSection title={title} description={description}>
			<div className="grid grid-cols-2 gap-2 px-3.5 py-3.5">
				{RESOURCE_LINKS.map((link, index) => (
					<Button
						key={link.href}
						asChild
						variant="outline"
						size="sm"
						className={cn(
							// 7 links after the bug/feature split: 3 tidy
							// rows of 2, plus the last link (Contributing)
							// spanning the full row so nothing orphans at
							// half width.
							index === RESOURCE_LINKS.length - 1 ? "col-span-2" : "",
							"w-full justify-start gap-1.5 text-(--text-muted) hover:text-(--text-primary)",
						)}
					>
						<a href={link.href} target="_blank" rel="noreferrer noopener">
							<HugeiconsIcon
								icon={link.icon}
								strokeWidth={2}
								aria-hidden="true"
								className="size-4 shrink-0"
							/>
							<span className="min-w-0 truncate">{t(link.label)}</span>
							{/* external-link indicator — all of these
								    navigate away from the app. */}
							<HugeiconsIcon
								icon={ArrowUpRight01Icon}
								strokeWidth={2.25}
								aria-hidden="true"
								className="size-3 shrink-0 opacity-60"
							/>
						</a>
					</Button>
				))}
			</div>
		</SettingsSection>
	);
});
