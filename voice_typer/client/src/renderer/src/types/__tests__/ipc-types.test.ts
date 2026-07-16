// src/renderer/src/types/__tests__/ipc-types.test.ts
//
// NEW-IPC-002 (d-review): regression guard for the removal of the dead
// ``ModelLoadedEvent`` type.
//
// Background
// ----------
// ``ModelLoadedEvent`` used to be declared in ``types/ipc.ts`` and
// listed in the ``PythonPushEvent`` union.  It claimed the backend
// pushes a ``model_loaded`` event with ``{ model, device }`` fields.
// Investigation found:
//   - NO renderer code subscribed to ``"model_loaded"`` (the only
//     ``usePythonEvent`` callers pass ``"status_change"``,
//     ``"transcription_final"``, ``"recording_started"``,
//     ``"navigate"``, etc.).
//   - The server never PUBLISHED ``"model_loaded"`` via
//     ``event_bus.publish(...)`` — the only ``model_loaded`` symbol in
//     the Python tree is a LOCAL log variable in
//     ``recording_controller.py:145`` that is fed to ``log.info(...)``
//     and nothing else.
// Keeping the type gave a false impression of an IPC contract that
// didn't exist.  The dead type was deleted.
//
// These tests pin the deletion so a future contributor cannot silently
// reintroduce the orphaned contract without an explicit decision
// (which should also wire up both a publisher and a subscriber).
//
// NOTE: ``types/ipc.ts`` exports ONLY TypeScript types/interfaces —
// there are no runtime values — so the bulk of these assertions are
// COMPILE-TIME checks.  If someone re-adds ``ModelLoadedEvent`` to
// the ``PythonPushEvent`` union, the type-level guard in
// ``model_loaded_not_in_union`` fails to compile and CI breaks.

import { describe, expect, it } from "vitest";
import type { PythonPushEvent } from "@/types/ipc";

describe("NEW-IPC-002: ModelLoadedEvent dead-type removal", () => {
	it("PythonPushEvent union does NOT include a `model_loaded` variant", () => {
		// Build the exhaustive list of ``type`` literals that the
		// ``PythonPushEvent`` union admits by inspecting a sample of
		// each member.  If the union ever grows to include
		// ``ModelLoadedEvent`` (type: "model_loaded"), this list must
		// be updated — and the literal "model_loaded" appearing in
		// the list below would make the assertion fail loudly.
		const acceptedTypes: PythonPushEvent["type"][] = [
			"status_change",
			"error",
			"transcription_partial",
			"transcription_final",
			"recording_started",
			"recording_stopped",
			"config_changed",
			"hotkey_capture_cancel",
			"history_changed",
		];

		// Runtime guard: the literal must NOT appear in the accepted
		// list.  This catches a contributor who adds the literal to
		// the array above AND reintroduces the type.
		expect(acceptedTypes).not.toContain("model_loaded");
		expect(acceptedTypes).toHaveLength(9);
	});

	it("a `{ type: 'model_loaded' }` value is NOT assignable to PythonPushEvent (compile-time guard)", () => {
		// Compile-time guard: if ``ModelLoadedEvent`` is ever added
		// back to the ``PythonPushEvent`` union, the conditional type
		// below resolves to ``true`` and the assignment of ``false``
		// to the annotated variable fails to compile — CI breaks
		// before the dead contract ships.
		type WouldBeModelLoaded = {
			type: "model_loaded";
			model: string;
			device: string;
		};

		// Type-level assertion (no runtime cost). ``extends`` here
		// checks assignability — ``WouldBeModelLoaded`` is assignable
		// to ``PythonPushEvent`` ONLY if the union still contains
		// ``ModelLoadedEvent``.
		//
		// Today the union has been pruned, so the conditional resolves
		// to ``false`` and the assignment is legal.  If a future
		// contributor re-adds ``ModelLoadedEvent`` to the union, the
		// conditional resolves to ``true`` and ``false`` is no longer
		// assignable to ``true`` — ``tsc`` fails and CI catches it.
		type Guard = WouldBeModelLoaded extends PythonPushEvent ? true : false;
		const _typeGuard: Guard = false;
		expect(_typeGuard).toBe(false);
	});
});
