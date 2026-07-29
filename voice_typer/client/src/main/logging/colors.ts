/**
 * ANSI color constants shared by the Electron main-process loggers.
 *
 * Extracted from the original `main/logging.ts` (DT-35 Phase 4.5
 * spaghetti split). The palette matches the Python backend's
 * `_ColorFormatter` so Electron and Python log lines look identical in
 * the terminal.
 *
 * FR-35 / LOG-COLOR-FIX: WARN was previously `38;5;214` (orange
 * #FFAF00) on the Electron side while Python used `38;5;226` (yellow
 * #FFFF00). On Windows conhost (the default terminal for
 * `cargo tauri dev` on Windows), 256→16-color quantization maps
 * orange to bright-red — making WARN look red and ERROR look yellow
 * by comparison, the exact inversion the Python LOG-COLOR-FIX was
 * designed to prevent. The TS side now matches Python's `38;5;226`.
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

/**
 * Yellow (`38;5;226`) for `[WARN]` prefixes.
 *
 * FR-35 / LOG-COLOR-FIX: matches the Python backend's
 * `_ColorFormatter` WARNING color (`log.py:447`). The previous value
 * `38;5;214` (orange) quantized to bright-red on Windows conhost,
 * making WARN look like ERROR. Yellow matches Python WARN and is
 * visually distinct from `ERROR_CLR` (`38;5;196` bright-red).
 */
export const WARN_CLR = "\x1b[38;5;226m";

/** Bright red for `[ERROR]` prefixes. */
export const ERROR_CLR = "\x1b[38;5;196m";
