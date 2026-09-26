// src/renderer/src/types/__tests__/ipc-types.test.ts
// (which should also wire up both a publisher and a subscriber).
// pinned deletion.
// ``parakeet_cpu_fallback`` (3 new events emitted by the Python backend

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
	it("PythonPushEvent union does NOT include the `model_loaded` variant", () => {
		// must be updated, and the literal appearing in
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
			//three new events emitted by the Python
			"tray_state",
			"consent_required",
			"parakeet_cpu_fallback",
			"asr_backend_disabled",
			"asr_last_resort_unloaded",
			"llm_polish_failed",
			"text_enhancement_failed",
			// gate (and missing from this union). Now wired
			"asr_backend_ready",
			"asr_backend_load_failed",
			"microphone_permission_revoked",
			"microphone_disconnected",
			"cloud_fallback_used",
			"dictation_suppressed",
			"history_corrupted",
			"history_fts5_rebuild_failed",
			"paste_deferred",
			"tray_fallback_notification",
		];

		// Runtime guard: the literal must NOT appear in the accepted
		expect(acceptedTypes).not.toContain("model_loaded");
		expect(acceptedTypes).toContain("relaunch_app");
		expect(acceptedTypes).toHaveLength(44);
	});

	it("a `{ type: 'model_loaded' }` value is NOT assignable to PythonPushEvent (compile-time guard)", () => {
		type WouldBeModelLoaded = {
			type: "model_loaded";
			model: string;
			device: string;
		};

		type Guard = WouldBeModelLoaded extends PythonPushEvent ? true : false;
		const _typeGuard: Guard = false;
		expect(_typeGuard).toBe(false);
	});

	it("a `{ type: 'transcription_partial', data }` value IS assignable to PythonPushEvent (live publisher)", () => {
		type LivePartialShape = {
			type: "transcription_partial";
			data: { text: string; cycle_id?: string; supported?: false };
		};
		type InUnion = LivePartialShape extends PythonPushEvent ? true : false;
		const _inUnionGuard: InUnion = true;
		expect(_inUnionGuard).toBe(true);
	});

	it("a `{ type: 'relaunch_app' }` value IS assignable to PythonPushEvent (live publisher)", () => {
		type RelaunchAppShape = {
			type: "relaunch_app";
			data: Record<string, unknown>;
		};
		type Guard = RelaunchAppShape extends PythonPushEvent ? true : false;
		const _typeGuard: Guard = true;
		expect(_typeGuard).toBe(true);
	});

	it("GT-52: tray_state / consent_required / parakeet_cpu_fallback ARE assignable to PythonPushEvent (compile-time guard)", () => {
		//three server-emitted push events added to the union.
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
		// The consent_required payload is all-optional (derived
		// consent_field). The minimal shape below must therefore
		type HasConsentRequiredMinimal = {
			type: "consent_required";
			data: { consent_field: string };
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
		const _consentMinimal: HasConsentRequiredMinimal = true;
		const _parakeet: HasParakeetCpuFallback = true;
		expect(_trayState).toBe(true);
		expect(_consent).toBe(true);
		expect(_consentMinimal).toBe(true);
		expect(_parakeet).toBe(true);
	});
});

describe("TASK-24-FIX-5/6/9/10/11: new IPC contract types exist with the expected shape", () => {
	it("DiskInfo has free_bytes + models_dir (TASK-24-FIX-5)", () => {
		const sample: DiskInfo = {
			free_bytes: 1024 ** 3,
			models_dir: "/home/user/.lausu/huggingface/hub",
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
		};
		expect(ok.hash_verified).toBe("verified");
		expect(legacy.hash_verified).toBeUndefined();
	});

	it("ModelStatusMap is a Record<string, ModelStatusEntry>", () => {
		const map: ModelStatusMap = {
			"large-v3-turbo": { downloaded: true, deps_ok: true },
			qwen: {
				downloaded: false,
				deps_ok: false,
				hash_verified: "mismatch",
			},
		};
		expect(map["large-v3-turbo"]?.downloaded).toBe(true);
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
	// guards below mirror these exact wire shapes, if a future

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
	// Each literal here must ALSO appear in the `acceptedTypes` list
	// (those are emitted by the Rust/predecessor host, NOT by Python's
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
		"asr_backend_disabled",
		"asr_last_resort_unloaded",
		"llm_polish_failed",
		"text_enhancement_failed",
		"asr_backend_ready",
		"asr_backend_load_failed",
		"microphone_permission_revoked",
		"microphone_disconnected",
		"cloud_fallback_used",
		"dictation_suppressed",
		"history_corrupted",
		"history_fts5_rebuild_failed",
		"paste_deferred",
		"tray_fallback_notification",
	];

	it("every Python emitter type literal is in the PythonPushEvent union (via the acceptedTypes list)", () => {
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
			"asr_backend_disabled",
			"asr_last_resort_unloaded",
			"llm_polish_failed",
			"text_enhancement_failed",
			// wired end-to-end.
			"asr_backend_ready",
			"asr_backend_load_failed",
			"microphone_permission_revoked",
			"microphone_disconnected",
			"cloud_fallback_used",
			"dictation_suppressed",
			"history_corrupted",
			"history_fts5_rebuild_failed",
			"paste_deferred",
			"tray_fallback_notification",
			// Host-bridge-synthesized (NOT emitted by Python's
			"reconnecting",
			"reconnected",
		];

		const missing: string[] = [];
		for (const emitter of PYTHON_EMITTER_TYPE_LITERALS) {
			if (!acceptedTypes.includes(emitter as PythonPushEvent["type"])) {
				missing.push(emitter);
			}
		}
		expect(missing).toEqual([]);
	});

	it("the Python emitter list and the acceptedTypes list have the expected YJ-34 length", () => {
		// 33 Python-emitted events (the union also includes 2
		expect(PYTHON_EMITTER_TYPE_LITERALS.length).toBe(44);
	});
});

describe("XZ-CC-7: TranscriptionFinalEvent has no duration_ms field (compile-time guard)", () => {
	it("a TranscriptionFinalEvent with `data.duration_ms` is NOT assignable (compile-time guard)", () => {
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
		type CanonicalShape = {
			type: "transcription_final";
			data: { text: string };
		};
		type Guard = CanonicalShape extends TranscriptionFinalEvent ? true : false;
		const _guard: Guard = true;
		expect(_guard).toBe(true);
	});

	it("TranscriptionFinalEvent is in the PythonPushEvent union", () => {
		type InUnion = TranscriptionFinalEvent extends PythonPushEvent
			? true
			: false;
		const _guard: InUnion = true;
		expect(_guard).toBe(true);
	});
});

describe("dead response types stay removed (compile-time guards)", () => {
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
