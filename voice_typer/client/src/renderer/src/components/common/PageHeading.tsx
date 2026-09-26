import type { ReactNode } from "react";

interface PageHeadingProps {
	title: string;
	description?: string;
	children?: ReactNode;
}

function HeadingContent({
	title,
	description,
}: {
	title: string;
	description?: string;
}) {
	return (
		<>
			<h1 className="font-sans text-2xl font-semibold tracking-tight text-foreground">
				{title}
			</h1>
			{description !== undefined ? (
				<p className="text-sm text-muted-foreground text-balance">
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
