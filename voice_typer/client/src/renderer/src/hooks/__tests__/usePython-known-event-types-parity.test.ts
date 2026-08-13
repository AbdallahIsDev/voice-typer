/**
 * : parity test for `KNOWN_EVENT_TYPES` in `hooks/usePython.ts`.
 *
 * `KNOWN_EVENT_TYPES` is a hand-maintained runtime mirror of the
 * `PythonPushEvent["type"]` TS union declared in
 * `types/ipc/push_events.ts`. TS cannot enumerate union members at
 * runtime, so the set is maintained by hand. The dev-time typo warning
 * in `usePythonEvent` consults this set to surface typos like
 * `usePythonEvent("past_failed", ...)` (intended `"paste_failed"`).
 *
 * The risk: a new event is added to the `PythonPushEvent` union but
 * the contributor forgets to add it to `KNOWN_EVENT_TYPES`. The
 * dev-time warning would then false-positive on the legitimate new
 * event, training developers to ignore the warning.
 *
 * This test pins parity in BOTH directions:
 *
 *   1. Compile-time: the `_PARITY` object below is annotated with
 *      `satisfies Record<PythonPushEvent["type"], true>`. If a type is
 *      added to the union but not listed here, the `satisfies` check
 *      fails (missing required property). If a type is listed here but
 *      not in the union, the `satisfies` check fails (excess
 *      property). This forces the `_PARITY` object to exactly match
 *      the union.
 *
 *   2. Runtime: the test asserts that `KNOWN_EVENT_TYPES` contains
 *      every key in `_PARITY` and has the same size. This catches the
 *      case where `_PARITY` is updated but `KNOWN_EVENT_TYPES` is not
 *      (or vice versa).
 *
 * Together, these two checks ensure `KNOWN_EVENT_TYPES` stays in sync
 * with the `PythonPushEvent` union.
 */
import { describe, expect, it } from "vitest";

import { KNOWN_EVENT_TYPES } from "@/hooks/usePython";
import type { PythonPushEvent } from "@/types/ipc";

// Compile-time parity: this object must list EVERY type in the
// `PythonPushEvent["type"]` union (no more, no less). The
// `satisfies Record<PythonPushEvent["type"], true>` annotation makes
// any divergence a compile error:
//   - Missing type → "Property 'X' is missing" error.
//   - Extra type → "Object literal may only specify known properties" error.
//
// If you add a new event to `PythonPushEvent`, add it here AND to
// `KNOWN_EVENT_TYPES` in `hooks/usePython.ts` — this test will fail
// tsc until both are updated.
const _PARITY = {
	status_change: true,
	error: true,
	transcription_final: true,
	recording_started: true,
	recording_stopped: true,
	config_changed: true,
	hotkey_capture_cancel: true,
	history_changed: true,
	state_changed: true,
	paste_failed: true,
	download_progress: true,
	notification: true,
	vocabulary_suggestion: true,
	microphones_changed: true,
	microphone_test_complete: true,
	audio_clip: true,
	tray_menu: true,
	navigate: true,
	ready: true,
	bubble_show: true,
	bubble_hide: true,
	bubble_set_state: true,
	bubble_level: true,
	bubble_config: true,
	show_window: true,
	quit_app: true,
	relaunch_app: true,
	tray_state: true,
	consent_required: true,
	parakeet_cpu_fallback: true,
	asr_backend_disabled: true,
	asr_last_resort_unloaded: true,
	llm_polish_failed: true,
	reconnecting: true,
	reconnected: true,
	mic_level: true,
	// Master plan §7.4 — 12 new push events from the
	// slim-core / runtime-pack split. Pinned by
	// `tests/test_event_types_parity.py` (Python-side cross-layer
	// parity test that also covers the Rust `ALLOWED_EVENT_TYPES`
	// slice + the Python `event_bus` catalogue docstring). The 13th
	// §7.4 event — `transcribe_offline` — is a REQUEST (member of
	// `PythonRequest`), NOT a push event, so it is absent here.
	pack_download_started: true,
	pack_download_progress: true,
	pack_download_completed: true,
	pack_download_failed: true,
	pack_verified: true,
	pack_missing: true,
	pack_corrupt: true,
	pack_ready: true,
	worker_started: true,
	worker_crashed: true,
	worker_unloaded: true,
	transcribe_offline_result: true,
} satisfies Record<PythonPushEvent["type"], true>;

describe("UE-39: KNOWN_EVENT_TYPES parity with PythonPushEvent type union", () => {
	it("KNOWN_EVENT_TYPES contains every PythonPushEvent type literal", () => {
		// Every key in the compile-time parity object must be
		// present in the runtime KNOWN_EVENT_TYPES set.
		const parityKeys = Object.keys(_PARITY) as PythonPushEvent["type"][];
		expect(parityKeys.length).toBeGreaterThan(0);
		for (const key of parityKeys) {
			expect(
				KNOWN_EVENT_TYPES.has(key),
				`KNOWN_EVENT_TYPES must contain "${key}"`,
			).toBe(true);
		}
	});

	it("KNOWN_EVENT_TYPES has no extra entries beyond PythonPushEvent types", () => {
		// The runtime set must not contain entries that are not
		// in the compile-time parity object (i.e. not in the
		// PythonPushEvent union).
		const parityKeys = new Set<string>(Object.keys(_PARITY));
		expect(KNOWN_EVENT_TYPES.size).toBe(parityKeys.size);
		for (const key of KNOWN_EVENT_TYPES) {
			expect(
				parityKeys.has(key),
				`KNOWN_EVENT_TYPES has extra entry "${key}" not in PythonPushEvent union`,
			).toBe(true);
		}
	});

	it("KNOWN_EVENT_TYPES is a ReadonlySet<string>", () => {
		// Sanity: the set is the expected type (guards against
		// an accidental refactor to an array or object).
		expect(KNOWN_EVENT_TYPES).toBeInstanceOf(Set);
		expect(KNOWN_EVENT_TYPES.size).toBeGreaterThan(20);
	});
});
