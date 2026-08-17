// About page — product identity.
//
// IA split: this page is now genuinely ABOUT the product: what Voice
// Typer is, what it does, its local/offline + optional-cloud
// capabilities, the installed version, and supported platforms.
//
// What moved out (and where it lives now):
//   - Privacy disclosure  → Privacy page (sidebar destination)
//   - Diagnostics table   → Settings → Troubleshooting (support area)
//   - Resources & Feedback → Settings → Troubleshooting (support area)
//
// The page is deliberately static — it renders no backend state, so it
// needs no fetches and works even when the backend is down. The only
// dynamic value is the installed version, read directly from
// package.json at build time (VERSION-SOURCE-FIX) so it never drifts
// from the canonical source of truth on a release bump.

import { APP_NAME } from "@/branding";
import PageHeading from "@/components/common/PageHeading";
import { ReadonlyRow } from "@/components/common/ReadonlyRow";
import { Logo } from "@/components/layout/Logo";
import { t, useT } from "@/i18n/i18n";
import { CloudIcon, Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import pkg from "../../../../package.json";

// App version — read directly from package.json (see VERSION-SOURCE-FIX
// comment at the top of the file) so this never drifts from the
// canonical source of truth on a release bump.
const APP_VERSION = pkg.version as string;

export default function AboutPage() {
	// Re-render on locale switch so all t() calls re-resolve.
	useT();

	return (
		<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col space-y-6 px-16 pt-28 pb-6">
			<PageHeading
				title={t("about.title")}
				description={t("about.description")}
			/>

			{/* Product identity card — compact, native-app About block.
			    No marketing copy, no hero section: identity, capability
			    split, version, platforms. */}
			<div className="rounded-xl border border-border/10 bg-(--bg-subtle)">
				{/* Identity row: logo + name + capability summary. */}
				<div className="flex items-center gap-3 px-5 pt-5">
					<Logo size={40} className="shrink-0" />
					<div className="min-w-0">
						<h2 className="text-lg font-semibold tracking-tight text-(--text-primary)">
							{APP_NAME}
						</h2>
						<p className="truncate text-xs text-(--text-muted)">
							{t("about.productTagline")}
						</p>
					</div>
				</div>

				<p className="max-w-prose px-5 pt-4 text-sm leading-relaxed text-(--text-muted)">
					{t("about.productDesc")}
				</p>

				{/* Local vs Cloud — the capability split made visually
				    obvious: two side-by-side blocks with their own icon
				    + title + one-line description. */}
				<div className="grid gap-3 px-5 pt-5 sm:grid-cols-2">
					<div className="rounded-lg border border-border/10 bg-(--bg) p-3.5">
						<div className="flex items-center gap-2">
							<HugeiconsIcon
								icon={Mic02Icon}
								strokeWidth={1.75}
								aria-hidden="true"
								className="size-4 shrink-0 text-(--text-muted)"
							/>
							<p className="text-sm font-medium text-(--text-primary)">
								{t("about.localTitle")}
							</p>
						</div>
						<p className="mt-1.5 text-xs leading-relaxed text-(--text-muted)">
							{t("about.localDesc")}
						</p>
					</div>
					<div className="rounded-lg border border-border/10 bg-(--bg) p-3.5">
						<div className="flex items-center gap-2">
							<HugeiconsIcon
								icon={CloudIcon}
								strokeWidth={1.75}
								aria-hidden="true"
								className="size-4 shrink-0 text-(--text-muted)"
							/>
							<p className="text-sm font-medium text-(--text-primary)">
								{t("about.cloudTitle")}
							</p>
						</div>
						<p className="mt-1.5 text-xs leading-relaxed text-(--text-muted)">
							{t("about.cloudDesc")}
						</p>
					</div>
				</div>

				{/* Meta rows: version + platforms. */}
				<div className="mt-5 border-t border-border/10 py-1.5">
					<ReadonlyRow
						variant="label-emphasized"
						label={t("about.version")}
						value={t("about.versionValue", { version: APP_VERSION })}
					/>
					<ReadonlyRow
						variant="label-emphasized"
						label={t("about.platforms")}
						value={t("about.platformsValue")}
					/>
				</div>
			</div>
		</div>
	);
}
