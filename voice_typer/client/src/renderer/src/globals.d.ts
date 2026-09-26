// src/renderer/src/globals.d.ts
// Ambient module declarations for non-TS imports used by the renderer.
// Without these, `tsc --noEmit` fails with TS2882 on side-effect CSS
// imports (e.g. `import "./index.css"` in main.tsx / bubble-main.tsx).
// Vite handles CSS imports natively at build time, but the type-checker
// needs a ambient declaration to accept them.

declare module "*.css" {
	// CSS imports are side-effect only, the module has no exports.
	const content: Record<string, never>;
	export default content;
}

declare module "*.svg" {
	const content: string;
	export default content;
}

declare module "*.png" {
	const content: string;
	export default content;
}

declare module "*.jpg" {
	const content: string;
	export default content;
}

declare module "*.jpeg" {
	const content: string;
	export default content;
}

declare module "*.gif" {
	const content: string;
	export default content;
}

declare module "*.webp" {
	const content: string;
	export default content;
}

declare module "*.ico" {
	const content: string;
	export default content;
}

declare module "papaparse" {
	export interface ParseResult<T> {
		data: T[];
		errors: Array<{ message: string; row?: number }>;
		meta: { fields?: string[]; delimiter: string; linebreak: string };
	}
	export interface ParseConfig {
		delimiter?: string;
		header?: boolean;
		skipEmptyLines?: boolean | "greedy";
	}
	export function parse<T>(input: string, config?: ParseConfig): ParseResult<T>;
	const Papa: {
		parse<T>(input: string, config?: ParseConfig): ParseResult<T>;
	};
	export default Papa;
}
