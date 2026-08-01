import type { ReactNode } from "react";

interface PageHeadingProps {
	title: string;
	description?: string;
	children?: ReactNode;
}

/**
 * Internal helper that renders the actual `<h1>` title plus the optional
 * description paragraph. Extracted so the two layout branches in
 * {@link PageHeading} (with-action row vs. stacked) share a single
 * render path — previously both branches duplicated the same JSX, which
 * made it easy for them to drift out of sync (e.g. a className tweak
 * applied to one branch but not the other).
 */
function HeadingContent({
	title,
	description,
}: {
	title: string;
	description?: string;
}) {
	return (
		<>
			<h1 className="font-sans text-2xl font-semibold tracking-tight text-(--text-primary)">
				{title}
			</h1>
			{description !== undefined ? (
				<p className="text-sm text-(--text-muted)">{description || "\u00A0"}</p>
			) : null}
		</>
	);
}

export default function PageHeading({
	title,
	description,
	children,
}: PageHeadingProps) {
	return (
		<div className="space-y-1 pb-5">
			{children ? (
				<div className="flex items-center justify-between gap-4">
					<div className="min-w-0 space-y-1">
						<HeadingContent title={title} description={description} />
					</div>
					{children}
				</div>
			) : (
				<HeadingContent title={title} description={description} />
			)}
		</div>
	);
}
