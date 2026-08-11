/**
 * Public barrel for the Electron main-process logging package.
 *
 * Split out from the original 750-line `main/logging.ts` (spaghetti
 * split). All `import { X } from "../logging"` (or
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
 *   - `fileSizeCache.ts`    — file-size cache +
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
 * Public API surface: the barrel re-exports ONLY the names
 * that have at least one external (out-of-package) importer. Internal-
 * only helpers — `DIM`, `INFO_CLR`, `WARN_CLR`, `ERROR_CLR`,
 * `mainLogPath`, `mainRuntimeLogger` — are NOT re-exported from this
 * barrel. They remain `export`ed from their own leaf modules so the
 * cross-file split can consume them, but external code must import
 * them directly from the leaf (`./colors`, `./structuredLogger`,
 * `./printfLogger`) if it really needs them — making the leak visible
 * at the import site. `mainRuntimeLogger` is not even `export`ed
 * from `printfLogger.ts` (it's truly module-private).
 *
 * `mainLogPath` was originally module-private in the monolithic
 * `main/logging.ts` ("verified zero external importers"); the split
 * lifted it to public visibility on `structuredLogger.ts`, but since
 * no external code imports it, so it is kept out of the barrel.
 * Same for `lifecycleLogPath` (only consumed by `appendLifecycleLine`
 * inside the package) and `appendLifecycleLine` itself (consumed by
 * `printfLogger.ts` inside the package).
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

// Colors (leaf). Only the colors that have an external importer
// are re-exported from the barrel. `DIM` / `INFO_CLR` / `WARN_CLR` /
// `ERROR_CLR` are consumed only inside this package (by `rotation.ts`
// and `printfLogger.ts`), so they are intentionally NOT re-exported —
// dropping them from the public surface makes accidental external use
// visible at the import site.
export { BUBBLE_CLR, RENDERER_CLR, RESET } from "./colors";

// Max-bytes constants (leaf).
export {
	DEFAULT_CRASH_LOG_MAX_BYTES,
	DEFAULT_MAIN_LOG_MAX_BYTES,
	RUNTIME_LOG_MAX_BYTES,
} from "./constants";
// Structured logger path memoization test seam. Exported alongside
// `_resetFileSizeCacheForTest` / `_resetRuntimeLogPathForTest` so test
// suites can reset ALL logging-module memoization state in one import.
// Not consumed by production code — see the docstring on
// `_resetMainLogPathForTest` in `./structuredLogger`.
// Consecutive-identical log deduper (collapses repeat lines into an
// `(xN)` summary). Exported for `ipc/python-call-handler.ts`, which
// wraps its `python-call rejected` warns so backend-disconnect retry
// bursts don't flood the log.
export {
	type DedupeRepeatedLogsOptions,
	dedupeRepeatedLogs,
} from "./dedupeRepeatedLogs";
// File-size cache (leaf).
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
// `redactPii` is re-exported here so external callers
// (notably `windows/main-window.ts`'s `appendRendererError` call site,
// which writes via direct `appendLogLine` and bypasses
// `formatArgsForFile`) can apply the same PII / API-key / URL
// redaction that the format helpers apply internally. Internal callers
// (`printfLogger.ts`, `structuredLogger.ts`) import directly from
// `./rotation` to avoid the barrel re-export overhead on the hot log
// path.
export {
	appendLogLine,
	cleanConsoleMsg,
	redactPii,
	rotateIfNeeded,
	ts,
} from "./rotation";
// Message-first structured logger + path resolvers + opt-in lifecycle
// persistence. `mainLogPath` / `lifecycleLogPath` /
// `appendLifecycleLine` are NOT re-exported — they have zero external
// importers (verified via `rg` across `voice_typer/client/src`). Only
// `logger` (consumed by `ipc/python-call-handler.ts`, `ipc/window-handlers.ts`,
// and tests) and `rendererErrorsLogPath` (consumed by
// `ipc/window-handlers.ts`) are public.
//
export {
	_resetMainLogPathForTest,
	logger,
	rendererErrorsLogPath,
} from "./structuredLogger";
