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
				<p className="text-sm text-(--text-muted) text-balance">
					{description || "\u00A0"}
				</p>
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
		<div className="flex flex-col gap-2">
			{children ? (
				// Title + description on the left, action row on the right
				// with clear separation (gap-6) so a 4-button toolbar never
				// runs into the heading text. items-start keeps a tall
				// button group from stretching the heading; flex-wrap lets
				// the action row drop below on narrow windows instead of
				// squeezing the title.
				<div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
					<div className="flex min-w-0 flex-col gap-2">
						<HeadingContent title={title} description={description} />
					</div>
					<div className="flex shrink-0 flex-wrap items-center gap-2">
						{children}
					</div>
				</div>
			) : (
				<HeadingContent title={title} description={description} />
			)}
		</div>
	);
}
