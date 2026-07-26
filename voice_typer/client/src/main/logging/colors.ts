/**
 * ANSI color constants shared by the Electron main-process loggers.
 *
 * Extracted from the original `main/logging.ts` (DT-35 Phase 4.5
 * spaghetti split). The palette matches the Python backend's
 * `_ColorFormatter` so Electron and Python log lines look identical in
 * the terminal.
 *
 * Original visibility:
 *   - `RESET`, `BUBBLE_CLR`, `RENDERER_CLR` were already exported from
 *     `main/logging.ts` (imported by `index.ts`, `handle-message.ts`,
 *     `bubble-window.ts`, `main-window.ts`).
 *   - `DIM`, `INFO_CLR`, `WARN_CLR`, `ERROR_CLR` were module-private
 *     (used only by `ts()` / `writeStdout()`). They are now exported
 *     from this leaf module so the cross-file split can consume them
 *     without re-declaring them; the barrel (`logging/index.ts`)
 *     re-exports them so existing `import { RESET, ... } from
 *     "../logging"` call sites continue to resolve.
 *
 * Leaf module — no imports.
 */

/** Dim grey for timestamps. */
export const DIM = "\x1b[38;5;242m";

/** ANSI reset. */
export const RESET = "\x1b[0m";

/** Bright cyan for `[BUBBLE]` tags. */
export const BUBBLE_CLR = "\x1b[38;5;39m";

/** Bright yellow for `[MAIN renderer]` tags. */
export const RENDERER_CLR = "\x1b[38;5;227m";

/**
 * Bright cyan for the structured `log` logger's `[INFO]` prefix.
 * Intentionally matches `BUBBLE_CLR` — INFO is the "happy" level and
 * visually parallels the `[BUBBLE]` tag color.
 */
export const INFO_CLR = "\x1b[38;5;39m";

/** Orange for `[WARN]` prefixes. */
export const WARN_CLR = "\x1b[38;5;214m";

/** Bright red for `[ERROR]` prefixes. */
export const ERROR_CLR = "\x1b[38;5;196m";
