/**
 * Console logging helpers shared across the main-process modules.
 *
 * Extracted from `index.ts` (REF-2). The ANSI color constants match the
 * Python backend's `_ColorFormatter` so Electron and Python log lines
 * look identical in the terminal.
 */

// ANSI color constants — match the Python backend's _ColorFormatter so
// Electron and Python log lines look identical in the terminal.
export const DIM = "\x1b[38;5;242m"; // dim grey for timestamps
export const RESET = "\x1b[0m";
export const BUBBLE_CLR = "\x1b[38;5;39m"; // bright cyan for [BUBBLE] tags
export const RENDERER_CLR = "\x1b[38;5;227m"; // bright yellow for [MAIN renderer] tags

// Clean Electron console-message format strings for terminal output.
// Strips printf-style format specifiers (%c, %o, %s, %d, %i, %f) that
// Electron's console-message event doesn't interpolate — it only
// captures the first argument (the format string).  React error
// boundaries commonly log with console.error('%o\n\n%s\n%s', obj, ...)
// which would otherwise leave raw "%o\n\n%s\n%s" artifacts in the log.
// Also collapses runs of whitespace/newlines into a single space.
export const cleanConsoleMsg = (msg: string): string =>
	msg
		.replace(/^%c[^;]+;\s*/, "")
		.replace(/%[csoidf]/g, "")
		.replace(/\n{3,}/g, "\n\n")
		.replace(/[ \t]+/g, " ")
		.trim();

/**
 * Format current time as H:MM:SS (12h, no leading zero), wrapped in ANSI
 * dim-grey, matching the Python backend's timestamp format/color so the
 * terminal output is visually consistent across both processes.
 */
export function ts(): string {
	const d = new Date();
	const h = d.getHours() % 12 || 12;
	const m = String(d.getMinutes()).padStart(2, "0");
	const s = String(d.getSeconds()).padStart(2, "0");
	return `${DIM}${h}:${m}:${s}${RESET}`;
}
