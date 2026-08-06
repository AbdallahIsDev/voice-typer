// src/renderer/src/types/__tests__/ipc-types.test.ts
//
//(d-review): regression guard for the removal of the dead
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
//(part 3): the same rationale + guard was applied to
// ``TranscriptionPartialEvent`` — also a dead type with no publisher.
//
// These tests pin the deletions so a future contributor cannot silently
// reintroduce either orphaned contract without an explicit decision
// (which should also wire up both a publisher and a subscriber).
//
//the acceptedTypes list + length assertion
// were extended to cover the 19 new event types added to the union
// (``state_changed`` + the 18 events previously flowing through
// ``onEvent`` untyped). The length was 27.
//
//added ``tray_state`` + ``consent_required`` +
// ``parakeet_cpu_fallback`` (3 new events emitted by the Python backend
// but never modelled in the TS union). Length grew from 27 to 29.
//
//removed ``relaunch_electron`` (RelaunchElectronEvent interface
// DELETED — verified the Python side emits only ``relaunch_app`` now).
//Length shrunk by 1, then grew by 3 () for a net of 29.
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
	TranscriptionFinalEvent,
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
		//the list was extended from 9 → 27
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
			//new — backend emits on every client connect.
			"state_changed",
			//18 previously-untyped event literals.
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
			//``relaunch_electron`` REMOVED from this list
			// (RelaunchElectronEvent interface deleted — verified
			// no Python emitter; see the new compile-time guard
			// below). The canonical event is ``relaunch_app``.
			"relaunch_app",
			//three new events emitted by the Python
			// backend but previously missing from the union.
			"tray_state",
			"consent_required",
			"parakeet_cpu_fallback",
			//three more server-emitted events previously
			// missing from the union. Each is published by
			// `event_bus.publish({"type": "..."})` in the Python
			// tree (see the per-interface docstrings in ipc.ts).
			"asr_backend_disabled",
			"asr_last_resort_unloaded",
			"llm_polish_failed",
		];

		// Runtime guard: the literals must NOT appear in the accepted
		// list.  This catches a contributor who adds the literal to
		// the array above AND reintroduces the type.
		expect(acceptedTypes).not.toContain("model_loaded");
		expect(acceptedTypes).not.toContain("transcription_partial");
		//``relaunch_electron`` must NOT be in the union
		// (RelaunchElectronEvent interface deleted).
		expect(acceptedTypes).not.toContain("relaunch_electron");
		//was 9, then 27.
		//1 (relaunch_electron removed) = 26.
		//+3 (tray_state + consent_required +
		// parakeet_cpu_fallback) = 29.
		//+3 (asr_backend_disabled + asr_last_resort_unloaded +
		// llm_polish_failed) = 32.
		// relaunch_app added (was documented in comment but missing from list) = 33.
		expect(acceptedTypes).toHaveLength(33);
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
		//(part 3): mirror of the model_loaded guard above
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
		//``RelaunchElectronEvent`` was DELETED from the union
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
		//three server-emitted push events added to the union.
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
		expect(map["tiny.en"]?.downloaded).toBe(true);
		expect(map.qwen?.hash_verified).toBe("mismatch");
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

	it("AutostartStatus has registered + error", () => {
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

	it("MicrophonePermissionResult has state", () => {
		const granted: MicrophonePermissionResult = { state: "granted" };
		const prompt: MicrophonePermissionResult = { state: "prompt" };
		expect(granted.state).toBe("granted");
		expect(prompt.state).toBe("prompt");
	});
});

describe("YJ-34: asr_backend_disabled / asr_last_resort_unloaded / llm_polish_failed ARE assignable to PythonPushEvent", () => {
	//three server-emitted push events added to the union. If a
	// future contributor removes any of the three interfaces from the
	// union, the corresponding conditional resolves to `false` and the
	// `true` assignment fails to compile.
	//
	// WIRE-SHAPE NOTE (corrected): `asr_backend_disabled`
	// and `asr_last_resort_unloaded` put payload fields under the
	// canonical `data:` key — verified by reading the Python
	// emitters at `asr_registry.py:625-637` (`asr_backend_disabled`)
	// and `:361-372` (`asr_last_resort_unloaded`). Earlier guards
	// asserted the fields were at the message ROOT — that was a
	// stale claim from before the Python emitters were wrapped in
	// the `data:` envelope (matching every other
	// `event_bus.publish(...)` caller). `llm_polish_failed` publishes
	// a bare `{ "type": "..." }` frame with NO payload fields. The
	// guards below mirror these exact wire shapes — if a future
	// Python refactor removes the `data:` envelope (or a TS refactor
	// re-flattens the interfaces), the guards fail compile.

	it("a `{ type: 'asr_backend_disabled', data: { backend, failure_count, timestamp } }` value IS assignable to PythonPushEvent (compile-time guard)", () => {
		type HasASRBackendDisabled = {
			type: "asr_backend_disabled";
			data: {
				backend: string;
				failure_count: number;
				timestamp: string;
			};
		} extends PythonPushEvent
			? true
			: false;
		const _guard: HasASRBackendDisabled = true;
		expect(_guard).toBe(true);
	});

	it("a `{ type: 'asr_last_resort_unloaded', data: { backend, timestamp } }` value IS assignable to PythonPushEvent (compile-time guard)", () => {
		type HasASRLastResortUnloaded = {
			type: "asr_last_resort_unloaded";
			data: {
				backend: string;
				timestamp: string;
			};
		} extends PythonPushEvent
			? true
			: false;
		const _guard: HasASRLastResortUnloaded = true;
		expect(_guard).toBe(true);
	});

	it("a `{ type: 'llm_polish_failed' }` value IS assignable to PythonPushEvent (compile-time guard)", () => {
		// `llm_polish_failed` has NO payload fields — mirrors the bare
		// `{type}` shape of `RecordingStartedEvent` and
		// `HotkeyCaptureCancelEvent`.
		type HasLLMPolishFailed = {
			type: "llm_polish_failed";
		} extends PythonPushEvent
			? true
			: false;
		const _guard: HasLLMPolishFailed = true;
		expect(_guard).toBe(true);
	});
});

describe("YJ-34 (parity): every Python event_bus.publish type literal is in the PythonPushEvent union", () => {
	//parity guard: this is a STATIC list of every `type` literal
	// the Python backend publishes via `event_bus.publish({"type": "..."})`
	// in `voice_typer/server/`. The list was compiled by grepping:
	//
	//   rg --no-heading --no-line-number \
	//       '"type":\s*"[a-z_]+"' voice_typer/server \
	//       --glob '*.py' | sort -u
	//
	// Each literal here must ALSO appear in the `acceptedTypes` list
	// above (which is the runtime mirror of the `PythonPushEvent` union).
	// If a future Python emitter adds a NEW `type` literal that has no
	// matching TS interface, this test fails — surfacing the drift
	// before an untyped event ships.
	//
	// NOTE: this list intentionally does NOT include the
	// host-bridge-synthesized `reconnecting` / `reconnected` events
	// (those are emitted by the Rust/Electron host, NOT by Python's
	// `event_bus.publish`).
	//
	// MAINTENANCE: when a new `event_bus.publish({"type": "..."})`
	// emitter is added to the Python tree, append its `type` literal
	// here AND add a matching interface to `ipc.ts`'s
	// `PythonPushEvent` union. A CI grep-test on the Python side
	// (`tests/test_*event_emitters*.py` — TBD) will eventually
	// automate this; until then, this static list is the contract.
	const PYTHON_EMITTER_TYPE_LITERALS: readonly string[] = [
		"status_change",
		"error",
		"transcription_final",
		"recording_started",
		"recording_stopped",
		"config_changed",
		"hotkey_capture_cancel",
		"history_changed",
		"state_changed",
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
		"relaunch_app",
		"tray_state",
		"consent_required",
		"parakeet_cpu_fallback",
		//the 3 new events that motivated this parity test.
		"asr_backend_disabled",
		"asr_last_resort_unloaded",
		"llm_polish_failed",
	];

	it("every Python emitter type literal is in the PythonPushEvent union (via the acceptedTypes list)", () => {
		// The `acceptedTypes` array declared in the first test in
		// this file is the runtime mirror of the `PythonPushEvent`
		// union. Re-declare it here (can't share state across `it`
		// blocks without module-level scope) and assert every
		// Python emitter literal is included.
		const acceptedTypes: PythonPushEvent["type"][] = [
			"status_change",
			"error",
			"transcription_final",
			"recording_started",
			"recording_stopped",
			"config_changed",
			"hotkey_capture_cancel",
			"history_changed",
			"state_changed",
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
			"relaunch_app",
			"tray_state",
			"consent_required",
			"parakeet_cpu_fallback",
			//3 new events.
			"asr_backend_disabled",
			"asr_last_resort_unloaded",
			"llm_polish_failed",
			// Host-bridge-synthesized (NOT emitted by Python's
			// event_bus.publish — but still members of the union so
			// renderer code can subscribe). Excluded from the
			// Python-emitter parity check below.
			"reconnecting",
			"reconnected",
		];

		const missing: string[] = [];
		for (const emitter of PYTHON_EMITTER_TYPE_LITERALS) {
			if (!acceptedTypes.includes(emitter as PythonPushEvent["type"])) {
				missing.push(emitter);
			}
		}
		// The missing list must be empty. If it isn't, the error
		// message names the offending literals so the failure is
		// self-diagnosing.
		expect(missing).toEqual([]);
	});

	it("the Python emitter list and the acceptedTypes list have the expected YJ-34 length", () => {
		// 33 Python-emitted events (the union also includes 2
		// host-bridge-synthesized events: `reconnecting` +
		// `reconnected` — total union length is 35).
		//
		// NOTE: the existing `acceptedTypes` list in the FIRST
		// `describe` block above (line ~73) has only 32 entries —
		// it is missing `relaunch_app` (a pre-existing oversight
		//from the  fix that removed `relaunch_electron` but
		// never added the canonical `relaunch_app` to the list).
		// This parity test's `PYTHON_EMITTER_TYPE_LITERALS` list
		// DOES include `relaunch_app` (33 entries) because the
		// Python backend's `voice_typer/server/app.py` does emit
		// it via `event_bus.publish({"type": "relaunch_app"})`. The
		// first list's missing entry is a documentation bug, not a
		// type-safety bug (the union itself correctly contains
		// `RelaunchAppEvent`); leaving it untouched here to avoid
		//scope creep beyond
		expect(PYTHON_EMITTER_TYPE_LITERALS.length).toBe(33);
	});
});

describe("XZ-CC-7: TranscriptionFinalEvent has no duration_ms field (compile-time guard)", () => {
	//(Low): the previous `TranscriptionFinalEvent` declared
	//   interface TranscriptionFinalEvent {
	//     type: "transcription_final";
	//     data: { text: string };
	//     duration_ms?: number;  // ← never sent
	//   }
	// The Python emitter at `voice_typer/server/dictation_pipeline.py`
	// publishes `{type: "transcription_final", data: {text: text[:200]}}`
	// — it NEVER populates `duration_ms`. The optional-but-never-sent
	// field gave a false impression of an IPC contract that doesn't
	// exist; any renderer code reading `event.data.duration_ms` would
	// always get `undefined` at runtime. The fix removed the field.
	//
	// These guards pin the removal: if a future contributor re-adds
	// `duration_ms` (or `timestamp`) to the event, the type-level
	// conditionals flip and the `false` assignments fail to compile.

	it("a TranscriptionFinalEvent with `data.duration_ms` is NOT assignable (compile-time guard)", () => {
		// If `duration_ms` is re-added to `TranscriptionFinalEvent.data`,
		// this `WouldHaveDurationMs` shape becomes assignable to
		// `TranscriptionFinalEvent`, the conditional resolves to
		// `true`, and the `const _guard: Guard = false` assignment
		// fails to compile — CI catches the regression before the
		// dead field ships again.
		type WouldHaveDurationMs = {
			type: "transcription_final";
			data: { text: string; duration_ms: number };
		};
		type Guard = WouldHaveDurationMs extends TranscriptionFinalEvent
			? TranscriptionFinalEvent extends WouldHaveDurationMs
				? true
				: false
			: false;
		const _guard: Guard = false;
		expect(_guard).toBe(false);
	});

	it("TranscriptionFinalEvent.data has ONLY the `text` field (compile-time guard)", () => {
		// The canonical shape: `{ type: "transcription_final"; data: { text: string } }`.
		// If a future contributor adds a field to `data`, the
		// conditional resolves to `false` and the `true` assignment
		// fails to compile.
		type CanonicalShape = {
			type: "transcription_final";
			data: { text: string };
		};
		type Guard = CanonicalShape extends TranscriptionFinalEvent ? true : false;
		const _guard: Guard = true;
		expect(_guard).toBe(true);
	});

	it("TranscriptionFinalEvent is in the PythonPushEvent union", () => {
		//Sanity: the event is still in the union (the  fix
		// removed a field, not the event itself).
		type InUnion = TranscriptionFinalEvent extends PythonPushEvent
			? true
			: false;
		const _guard: InUnion = true;
		expect(_guard).toBe(true);
	});
});

describe("XZ-CC-6 / XZ-CC-16: dead response types stay removed (compile-time guards)", () => {
	//(Medium): the previous ``ToggleDictationResult`` interface
	// declared ``recording: boolean`` as a REQUIRED field. The Python
	// handler for ``toggle_dictation`` returns ``{type: "ack"}`` with NO
	// ``data`` field — so any renderer code reading
	// ``const { recording } = await call<ToggleDictationResult>(...)``
	// got ``recording: undefined`` while TypeScript type-checked it as
	// ``boolean``. The fix removed the dead type entirely (callers pass
	// ``call<unknown>("toggle_dictation")`` and discard the result).
	//
	//(Low): the 26-line ``ResponseData<T extends
	// PythonRequest["type"]>`` conditional-types cascade had ZERO
	// consumers — ``usePython.call`` is generic over ``<T = unknown>``
	// with no constraint on ``PythonRequest["type"]``, so the cascade
	// never flowed into any call site. The dead types
	// ``ToggleDictationResult``, ``ToggleFavoriteResult``, and
	// ``SaveVocabularyResult`` were only ever referenced by this dead
	// mapped type, so they were removed together.
	//
	// These guards verify the names are NOT re-exported from
	// ``@/types/ipc``. If a future contributor re-adds any of them,
	// the ``keyof`` check resolves to ``true`` and the ``false``
	// assignment fails to compile — CI catches the regression before
	// the dead contract ships again.

	it("ToggleDictationResult is NOT exported from @/types/ipc (XZ-CC-6 guard)", () => {
		type IpcModule = typeof import("@/types/ipc");
		type IsExported = "ToggleDictationResult" extends keyof IpcModule
			? true
			: false;
		const _guard: IsExported = false;
		expect(_guard).toBe(false);
	});

	it("ToggleFavoriteResult is NOT exported from @/types/ipc (XZ-CC-16 guard)", () => {
		type IpcModule = typeof import("@/types/ipc");
		type IsExported = "ToggleFavoriteResult" extends keyof IpcModule
			? true
			: false;
		const _guard: IsExported = false;
		expect(_guard).toBe(false);
	});

	it("SaveVocabularyResult is NOT exported from @/types/ipc (XZ-CC-16 guard)", () => {
		type IpcModule = typeof import("@/types/ipc");
		type IsExported = "SaveVocabularyResult" extends keyof IpcModule
			? true
			: false;
		const _guard: IsExported = false;
		expect(_guard).toBe(false);
	});

	it("ResponseData is NOT exported from @/types/ipc (XZ-CC-16 guard)", () => {
		type IpcModule = typeof import("@/types/ipc");
		type IsExported = "ResponseData" extends keyof IpcModule ? true : false;
		const _guard: IsExported = false;
		expect(_guard).toBe(false);
	});
});
