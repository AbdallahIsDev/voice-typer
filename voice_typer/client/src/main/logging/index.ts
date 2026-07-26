/**
 * Public barrel for the Electron main-process logging package.
 *
 * Split out from the original 750-line `main/logging.ts` (DT-35 Phase
 * 4.5 spaghetti split). All `import { X } from "../logging"` (or
 * `"./logging"` / `"../../main/logging"`) call sites resolve to THIS
 * file — TypeScript's bundler resolution prefers a directory's
 * `index.ts` once the sibling `logging.ts` file is removed.
 *
 * Module map (each concern lives in its own file):
 *
 *   - `colors.ts`           — ANSI color constants (`DIM`, `RESET`,
 *                             `BUBBLE_CLR`, `RENDERER_CLR`, `INFO_CLR`,
 *                             `WARN_CLR`, `ERROR_CLR`).
 *   - `constants.ts`        — rotation-cap constants
 *                             (`DEFAULT_CRASH_LOG_MAX_BYTES`,
 *                             `DEFAULT_MAIN_LOG_MAX_BYTES`,
 *                             `RUNTIME_LOG_MAX_BYTES`).
 *   - `fileSizeCache.ts`    — XV-154 file-size cache +
 *                             `_resetFileSizeCacheForTest`.
 *   - `rotation.ts`         — `rotateIfNeeded`, `appendLogLine`,
 *                             `cleanConsoleMsg`, `ts`.
 *   - `structuredLogger.ts` — message-first `logger` + `mainLogPath` +
 *                             `rendererErrorsLogPath` + `lifecycleLogPath`
 *                             + `appendLifecycleLine` (+ internal
 *                             `PERSIST_INFO` / `formatLine`).
 *   - `printfLogger.ts`     — printf-style `log` + `LogShape` +
 *                             `getRuntimeLogPath` +
 *                             `_getRuntimeLogPathForTest` /
 *                             `_resetRuntimeLogPathForTest`.
 *
 * Every name that was `export`ed from the original `main/logging.ts`
 * is re-exported below. A small number of additional helpers that
 * were previously module-private (`mainLogPath`, `lifecycleLogPath`,
 * `appendLifecycleLine`, `getRuntimeLogPath`) are now also re-exported
 * — they were lifted to public visibility by the split and are safe
 * for external consumption (they have no internal-only invariants).
 *
 * The cross-file imports between the split modules form a DAG
 * (no cycles):
 *
 *     colors ←─────── rotation
 *     constants ←──── rotation
 *     fileSizeCache ← rotation
 *     rotation ←───── structuredLogger
 *     rotation ←───── printfLogger
 *     structuredLogger ← printfLogger  (PERSIST_INFO + appendLifecycleLine)
 */

// Colors (leaf).
export {
	BUBBLE_CLR,
	DIM,
	ERROR_CLR,
	INFO_CLR,
	RENDERER_CLR,
	RESET,
	WARN_CLR,
} from "./colors";

// Max-bytes constants (leaf).
export {
	DEFAULT_CRASH_LOG_MAX_BYTES,
	DEFAULT_MAIN_LOG_MAX_BYTES,
	RUNTIME_LOG_MAX_BYTES,
} from "./constants";

// XV-154 file-size cache (leaf).
export { _resetFileSizeCacheForTest } from "./fileSizeCache";
// Printf-style structured logger + memoized runtime-log path resolver.
export {
	_getRuntimeLogPathForTest,
	_resetRuntimeLogPathForTest,
	getRuntimeLogPath,
	type LogShape,
	log,
} from "./printfLogger";
// File-rotation primitive + low-level log-line helpers.
export { appendLogLine, cleanConsoleMsg, rotateIfNeeded, ts } from "./rotation";
// Message-first structured logger + path resolvers + opt-in lifecycle
// persistence.
export {
	appendLifecycleLine,
	lifecycleLogPath,
	logger,
	mainLogPath,
	rendererErrorsLogPath,
} from "./structuredLogger";
