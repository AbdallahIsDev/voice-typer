interface LogoProps {
	size?: number;
	className?: string;
	/**
	 * PROD-14: when the Logo is wrapped inside a parent that already
	 * carries the accessible name (e.g. the <button> wrapper used in
	 * Sidebar's collapsed state), pass `decorative` to demote the
	 * inner SVG to aria-hidden so the parent's aria-label is the
	 * single source of truth and ATs don't announce "Voice Typer"
	 * twice. Defaults to `false` (Logo is self-labeled) to preserve
	 * existing standalone behavior.
	 */
	decorative?: boolean;
}

import { APP_NAME } from "@/branding";

export function Logo({ size = 20, className, decorative = false }: LogoProps) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 128 128"
			fill="none"
			xmlns="http://www.w3.org/2000/svg"
			className={className}
			role={decorative ? undefined : "img"}
			aria-label={decorative ? undefined : APP_NAME}
			aria-hidden={decorative ? true : undefined}
		>
			{decorative ? null : <title>{APP_NAME}</title>}
			<rect
				x="15"
				y="48"
				width="14"
				height="32"
				rx="7"
				className="fill-current text-(--text-primary)"
			/>
			<rect
				x="43"
				y="32"
				width="14"
				height="64"
				rx="7"
				className="fill-current text-(--text-primary)"
			/>
			{/* PROD-14: tint the tallest bar with the brand color
                            (var(--primary)) so the logo carries brand identity
                            instead of inheriting --text-primary uniformly. The
                            other bars remain --text-primary so they "support"
                            the accent bar visually. */}
			<rect
				x="71"
				y="16"
				width="14"
				height="96"
				rx="7"
				className="fill-current text-primary"
			/>
			<rect
				x="99"
				y="40"
				width="14"
				height="48"
				rx="7"
				className="fill-current text-(--text-primary)"
			/>
		</svg>
	);
}
