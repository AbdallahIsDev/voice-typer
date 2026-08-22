// Public barrel for the Python bridge hooks.
//
// The implementation lives in focused modules under
// `lib/python-bridge/` (event dispatcher, bridge-ready subscription
// gate, per-command timeouts, error-envelope parsing, and the
// `usePython` / `usePythonEvent` hook implementations). This file
// re-exports the IDENTICAL public API so every existing import site
// (`@/hooks/usePython`) keeps working unchanged — do NOT add logic
// here.

export { useBridgeReady } from "@/lib/python-bridge/bridge-ready";
export { getTimeout } from "@/lib/python-bridge/command-timeouts";
export { parseTauriErrorEnvelope } from "@/lib/python-bridge/error-envelope";
export { KNOWN_EVENT_TYPES } from "@/lib/python-bridge/known-event-types";
export { type PythonCall, usePython } from "@/lib/python-bridge/usePython";
export { usePythonEvent } from "@/lib/python-bridge/usePythonEvent";
