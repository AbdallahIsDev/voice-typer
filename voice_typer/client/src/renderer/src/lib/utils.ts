import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
	return twMerge(clsx(inputs));
}

/** Shared focus-ring class for interactive primitives (WCAG 1.4.11 / 2.4.7). */
export const focusRing =
	"focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 outline-hidden";
