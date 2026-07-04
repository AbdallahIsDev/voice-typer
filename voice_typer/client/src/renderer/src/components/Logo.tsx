interface LogoProps {
	size?: number;
	className?: string;
}

import { APP_NAME } from "./../branding";

export function Logo({ size = 20, className }: LogoProps) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 128 128"
			fill="none"
			xmlns="http://www.w3.org/2000/svg"
			className={className}
			role="img"
			aria-label={APP_NAME}
		>
			<title>{APP_NAME}</title>
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
			<rect
				x="71"
				y="16"
				width="14"
				height="96"
				rx="7"
				className="fill-current text-(--text-primary)"
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
