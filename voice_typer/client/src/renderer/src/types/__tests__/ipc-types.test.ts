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
// PVT-G5-010 (part 3): the same rationale + guard was applied to
// ``TranscriptionPartialEvent`` — also a dead type with no publisher.
//
// These tests pin the deletions so a future contributor cannot silently
// reintroduce either orphaned contract without an explicit decision
// (which should also wire up both a publisher and a subscriber).
//
// PVT-G5-060 / PVT-G5-061: the acceptedTypes list + length assertion
// were extended to cover the 19 new event types added to the union
// (``state_changed`` + the 18 events previously flowing through
// ``onEvent`` untyped). The length was 27.
//
// GT-52: added ``tray_state`` + ``consent_required`` +
// ``parakeet_cpu_fallback`` (3 new events emitted by the Python backend
// but never modelled in the TS union). Length grew from 27 to 29.
//
// GT-55: removed ``relaunch_electron`` (RelaunchElectronEvent interface
// DELETED — verified the Python side emits only ``relaunch_app`` now).
// Length shrunk by 1, then grew by 3 (GT-52) for a net of 29.
//
// NOTE: ``types/ipc.ts`` exports ONLY TypeScript types/interfaces —
// there are no runtime values — so the bulk of these assertions are
// COMPILE-TIME checks.  If someone re-adds ``ModelLoadedEvent`` or
// ``TranscriptionPartialEvent`` to the ``PythonPushEvent`` union, the
// type-level guards below fail to compile and CI breaks.

import { describe, expect, it } from "vitest";
import type {
	AutostartStatus,
	DiskInfo,
	MicrophonePermissionResult,
	ModelStatusEntry,
	ModelStatusMap,
	PermissionsResult,
	PythonPushEvent,
} from "@/types/ipc";

describe("NEW-IPC-002 / PVT-G5-010: dead-type removal guards", () => {
	it("PythonPushEvent union does NOT include `model_loaded` or `transcription_partial` variants", () => {
		// Build the exhaustive list of ``type`` literals that the
		// ``PythonPushEvent`` union admits by inspecting a sample of
		// each member.  If the union ever grows to include
		// ``ModelLoadedEvent`` (type: "model_loaded") or
		// ``TranscriptionPartialEvent`` (type: "transcription_partial"),
		// this list must be updated — and the literal appearing in
		// the list below would make the assertion fail loudly.
		//
		// PVT-G5-060 / PVT-G5-061: the list was extended from 9 → 27
		// entries to cover the new event types added to the union.
		const acceptedTypes: PythonPushEvent["type"][] = [
			"status_change",
			"error",
			"transcription_final",
			"recording_started",
			"recording_stopped",
			"config_changed",
			"hotkey_capture_cancel",
			"history_changed",
			// PVT-G5-060: new — backend emits on every client connect.
			"state_changed",
			// PVT-G5-061: 18 previously-untyped event literals.
			"paste_failed",
			"download_progress",
			"notification",
			"vocabulary_suggestion",
			"microphones_changed",
			"microphone_test_complete",
			"audio_clip",
			"tray_menu",
			"navigate",
			"ready",
			"bubble_show",
			"bubble_hide",
			"bubble_set_state",
			"bubble_level",
			"bubble_config",
			"show_window",
			"quit_app",
			// GT-55: ``relaunch_electron`` REMOVED from this list
			// (RelaunchElectronEvent interface deleted — verified
			// no Python emitter; see the new compile-time guard
			// below). The canonical event is ``relaunch_app``.
			// GT-52: three new events emitted by the Python
			// backend but previously missing from the union.
			"tray_state",
			"consent_required",
			"parakeet_cpu_fallback",
		];

		// Runtime guard: the literals must NOT appear in the accepted
		// list.  This catches a contributor who adds the literal to
		// the array above AND reintroduces the type.
		expect(acceptedTypes).not.toContain("model_loaded");
		expect(acceptedTypes).not.toContain("transcription_partial");
		// GT-55: ``relaunch_electron`` must NOT be in the union
		// (RelaunchElectronEvent interface deleted).
		expect(acceptedTypes).not.toContain("relaunch_electron");
		// PVT-G5-060 / PVT-G5-061: was 9, then 27.
		// GT-55: -1 (relaunch_electron removed) = 26.
		// GT-52: +3 (tray_state + consent_required +
		// parakeet_cpu_fallback) = 29.
		expect(acceptedTypes).toHaveLength(29);
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

	it("a `{ type: 'transcription_partial' }` value is NOT assignable to PythonPushEvent (compile-time guard)", () => {
		// PVT-G5-010 (part 3): mirror of the model_loaded guard above
		// for the dead ``TranscriptionPartialEvent`` type. Same
		// mechanism: the conditional resolves to ``false`` while the
		// union does NOT contain a ``transcription_partial`` variant.
		type WouldBeTranscriptionPartial = {
			type: "transcription_partial";
			text: string;
		};
		type Guard = WouldBeTranscriptionPartial extends PythonPushEvent
			? true
			: false;
		const _typeGuard: Guard = false;
		expect(_typeGuard).toBe(false);
	});

	it("a `{ type: 'relaunch_electron' }` value is NOT assignable to PythonPushEvent (GT-55 compile-time guard)", () => {
		// GT-55: ``RelaunchElectronEvent`` was DELETED from the union
		// after verifying the Python side emits only ``relaunch_app``.
		// If a future contributor re-adds ``RelaunchElectronEvent`` to
		// the union, the conditional resolves to ``true`` and the
		// ``false`` assignment fails to compile — CI catches it before
		// the deprecated contract ships again.
		type WouldBeRelaunchElectron = {
			type: "relaunch_electron";
			data: Record<string, unknown>;
		};
		type Guard = WouldBeRelaunchElectron extends PythonPushEvent ? true : false;
		const _typeGuard: Guard = false;
		expect(_typeGuard).toBe(false);
	});

	it("GT-52: tray_state / consent_required / parakeet_cpu_fallback ARE assignable to PythonPushEvent (compile-time guard)", () => {
		// GT-52: three server-emitted push events added to the union.
		// If a future contributor removes any of the three interfaces
		// from the union, the corresponding conditional resolves to
		// ``false`` and the ``true`` assignment fails to compile.
		type HasTrayState = {
			type: "tray_state";
			data: { icon?: string; tooltip?: string };
		} extends PythonPushEvent
			? true
			: false;
		type HasConsentRequired = {
			type: "consent_required";
			data: { provider: string; model: string; message: string };
		} extends PythonPushEvent
			? true
			: false;
		type HasParakeetCpuFallback = {
			type: "parakeet_cpu_fallback";
			data: { device: string; reason: string };
		} extends PythonPushEvent
			? true
			: false;
		const _trayState: HasTrayState = true;
		const _consent: HasConsentRequired = true;
		const _parakeet: HasParakeetCpuFallback = true;
		expect(_trayState).toBe(true);
		expect(_consent).toBe(true);
		expect(_parakeet).toBe(true);
	});
});

describe("TASK-24-FIX-5/6/9/10/11: new IPC contract types exist with the expected shape", () => {
	// These are COMPILE-TIME guards: if a future refactor renames or
	// removes any of the new contract types declared in
	// ``types/ipc.ts``, the corresponding ``const _: TypeName = ...``
	// line below fails to compile and CI catches it before the contract
	// drifts.  The runtime ``expect`` calls just keep the test runner
	// happy (vitest requires at least one assertion per ``it``).

	it("DiskInfo has free_bytes + models_dir (TASK-24-FIX-5)", () => {
		const sample: DiskInfo = {
			free_bytes: 1024 ** 3,
			models_dir: "/home/user/.voice-typer/huggingface/hub",
		};
		expect(sample.free_bytes).toBe(1024 ** 3);
		expect(sample.models_dir).toContain("huggingface");
	});

	it("ModelStatusEntry has downloaded + deps_ok + optional hash_verified (TASK-24-FIX-6)", () => {
		const ok: ModelStatusEntry = {
			downloaded: true,
			deps_ok: true,
			hash_verified: "verified",
		};
		const legacy: ModelStatusEntry = {
			downloaded: false,
			deps_ok: true,
			// hash_verified intentionally omitted — backend
			// predates the field; absence is treated as "unknown".
		};
		expect(ok.hash_verified).toBe("verified");
		expect(legacy.hash_verified).toBeUndefined();
	});

	it("ModelStatusMap is a Record<string, ModelStatusEntry>", () => {
		const map: ModelStatusMap = {
			"tiny.en": { downloaded: true, deps_ok: true },
			qwen: {
				downloaded: false,
				deps_ok: false,
				hash_verified: "mismatch",
			},
		};
		expect(map["tiny.en"].downloaded).toBe(true);
		expect(map.qwen.hash_verified).toBe("mismatch");
	});

	it("PermissionsResult has platform + state + needed + instructions (TASK-24-FIX-9)", () => {
		const granted: PermissionsResult = {
			platform: "windows",
			state: "granted",
			needed: false,
			instructions: null,
		};
		const needsSetup: PermissionsResult = {
			platform: "macos",
			state: "denied",
			needed: true,
			instructions: {
				title: "Accessibility Permission Required",
				steps: ["Open System Settings…"],
				commands: null,
			},
		};
		const errored: PermissionsResult = {
			platform: "linux",
			state: "error",
			needed: true,
			instructions: null,
		};
		expect(granted.instructions).toBeNull();
		expect(needsSetup.instructions?.steps).toHaveLength(1);
		expect(errored.state).toBe("error");
	});

	it("AutostartStatus has registered + error (TASK-24-FIX-10)", () => {
		const ok: AutostartStatus = {
			registered: true,
			error: null,
		};
		const failed: AutostartStatus = {
			registered: false,
			error: "osascript: user denied",
		};
		expect(ok.registered).toBe(true);
		expect(failed.error).toContain("osascript");
	});

	it("MicrophonePermissionResult has state (TASK-24-FIX-11)", () => {
		const granted: MicrophonePermissionResult = { state: "granted" };
		const prompt: MicrophonePermissionResult = { state: "prompt" };
		expect(granted.state).toBe("granted");
		expect(prompt.state).toBe("prompt");
	});
});
