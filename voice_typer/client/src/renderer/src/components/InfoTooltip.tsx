// src/renderer/src/components/InfoTooltip.tsx

import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { t } from "@/i18n/i18n";

interface InfoTooltipProps {
	text: string;
}

export function InfoTooltip({ text }: InfoTooltipProps) {
	return (
		<TooltipProvider delayDuration={200}>
			<Tooltip>
				<TooltipTrigger asChild>
					<span
						className="inline-flex size-4 items-center justify-center rounded-full text-(--text-muted) shrink-0"
						role="img"
						aria-label={t("a11y.moreInfo")}
					>
						<svg
							width="12"
							height="12"
							viewBox="0 0 16 16"
							fill="none"
							xmlns="http://www.w3.org/2000/svg"
						>
							<title>More info</title>
							<circle
								cx="8"
								cy="8"
								r="6.5"
								stroke="currentColor"
								strokeWidth="1.5"
							/>
							<path
								d="M6.4 6C6.4 4.8 7.2 4.4 8 4.4C8.8 4.4 9.6 4.8 9.6 6C9.6 7.2 8.8 7.6 8.4 8C8.2 8.4 8 8.8 8 9.2"
								stroke="currentColor"
								strokeWidth="1.5"
								strokeLinecap="round"
								strokeLinejoin="round"
							/>
							<circle cx="8" cy="11.2" r="0.6" fill="currentColor" />
						</svg>
					</span>
				</TooltipTrigger>
				<TooltipContent side="top" align="center" className="max-w-64">
					{text}
				</TooltipContent>
			</Tooltip>
		</TooltipProvider>
	);
}
