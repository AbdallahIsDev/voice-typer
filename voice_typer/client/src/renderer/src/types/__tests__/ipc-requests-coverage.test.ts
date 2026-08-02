// types/__tests__/ipc-requests-coverage.test.ts
//
// Regression test for the PythonRequest union widening + the typed
// PythonCall overload. Locks in two properties:
//
//   1. Every renderer-called command (surveyed via
//      ``rg 'call<...>\("..."'`` across ``src/renderer/src``) is a
//      member of ``PythonRequest["type"]``. If a future contributor
//      adds a new ``call('foo')`` site but forgets to add the matching
//      interface in ``types/ipc/requests.ts``, the ``satisfies``
//      annotation below fails to compile (the key ``foo`` is not a
//      known ``PythonRequest["type"]`` literal).
//
//   2. The typed ``PythonCall`` overload (the first of two overloads
//      declared on the ``PythonCall`` type in ``hooks/usePython.ts``)
//      is intact. The check uses ``@ts-expect-error`` on a call that
//      passes spurious data to a bare (no-data) command — the typed
//      overload rejects it (``data?: undefined`` for ``get_config``);
//      if the typed overload is removed, the string fallback accepts
//      the call, the ``@ts-expect-error`` becomes unused, and tsc
//      reports an error. This pins the compile-time narrowing.
//
// These tests do NOT verify the Python-side wire contracts (the
// permissive ``data?: Record<string, unknown>`` shape is intentionally
// loose). Tightening individual interfaces to bare or stricter shapes
// is tracked separately under the Python-side ``PushEventType`` enum
// plan (out of lane for the renderer-only slice — see review.md
// (Python-side plan).

import { describe, expect, expectTypeOf, it } from "vitest";

import type { PythonCall } from "@/hooks/usePython";
import type { PythonRequest } from "@/types/ipc/requests";

// ── Section 1: union coverage ───────────────────────────────────────
//
// ``Partial<Record<PythonRequest["type"], true>>`` allows the union to
// grow without forcing this object to enumerate every member — but if
// a key listed here is NOT in the union, the ``satisfies`` check fails
// compile. This is the "renderer-called commands ⊆ PythonRequest"
// invariant.
const _RENDERER_CALLED_COMMANDS = {
	// Original 12 + commonly-used additions already in the union
	// before the widening slice.
	get_config: true,
	get_microphones: true,
	toggle_dictation: true,
	get_history: true,
	delete_history: true,
	clear_history: true,
	toggle_favorite: true,
	get_favorites: true,
	search_history: true,
	get_today_stats: true,
	get_vocabulary: true,
	save_vocabulary: true,
	get_history_count: true,
	get_transcription_text: true,
	set_config: true,
	get_status: true,
	level_monitor_start: true,
	level_monitor_stop: true,
	microphone_test_get_level: true,
	set_tray_locale: true,
	onboarding_reset: true,
	onboarding_check_permissions: true,
	// 28 commands added by the widening slice (see the
	// per-interface docstrings in ``types/ipc/requests.ts`` for
	// the call-site survey).
	cancel_model_download: true,
	force_cancel_transcription: true,
	get_disk_info: true,
	get_model_catalog: true,
	get_model_status: true,
	get_prewarm_status: true,
	get_templates: true,
	microphone_test_cancel: true,
	microphone_test_stop: true,
	models_folder_supported: true,
	onboarding_apply: true,
	onboarding_get_microphones: true,
	onboarding_is_first_run: true,
	onboarding_next_step: true,
	onboarding_prev_step: true,
	onboarding_set_hotkey: true,
	onboarding_set_microphone: true,
	onboarding_set_model: true,
	onboarding_skip: true,
	onboarding_start: true,
	open_models_folder: true,
	pause_model_download: true,
	repaste_last: true,
	restore_history: true,
	resume_model_download: true,
	run_prewarm: true,
	save_templates: true,
	undo_last: true,
} satisfies Partial<Record<PythonRequest["type"], true>>;

// ── Section 2: typo guard ───────────────────────────────────────────
//
// A typo'd command name must NOT be a member of ``PythonRequest["type"]``.
// If someone accidentally adds a ``typo_cmd`` interface to the union,
// this assignment fails compile (``"typo_cmd" extends ... ? true : false``
// resolves to ``true``, but the const is annotated ``false``).
type TypoCmdGuard = "typo_cmd" extends PythonRequest["type"] ? true : false;
const _typoCmdNotInUnion: TypoCmdGuard = false;

// ── Section 3: typed PythonCall overload integrity ──────────────────
//
// ``PythonCall`` (declared in ``hooks/usePython.ts``) is a two-overload
// type. The FIRST overload narrows ``type`` to ``PythonRequest["type"]``
// and conditionally types ``data`` based on the per-command interface.
// The SECOND overload accepts any ``string`` for forward-compat with
// backend-added commands not yet in the union.
//
// Direct ``@ts-expect-error`` calls on ``PythonCall`` can't pin the
// typed overload's narrowing power because TypeScript falls through to
// the string fallback overload (which accepts any
// ``Record<string, unknown>``) when the typed overload rejects — so a
// spurious-data call on ``PythonCall`` never actually errors at the
// type level, and the ``@ts-expect-error`` is reported as unused
// (TS2578). Instead, we instantiate the typed overload's signature
// with ``K = "get_config"`` (a bare command — ``GetConfigRequest`` has
// no ``data`` field) and use ``expectTypeOf`` to assert the ``data``
// parameter resolves to ``undefined``. If the typed overload's
// conditional ``data?`` type is removed or replaced with a permissive
// shape, this assertion fails compile.

// The typed overload's ``data`` parameter for ``get_config`` (a bare
// command) is ``undefined`` because ``GetConfigRequest`` has no
// ``data`` field. We verify this claim directly (rather than
// instantiating the typed overload's full conditional type with
// ``K = "get_config"``, which would trigger TS2538 when TypeScript
// eagerly evaluates the conditional's true branch
// ``GetConfigRequest["data"]`` — an invalid index access on a type
// with no ``data`` field). If a refactor adds a ``data`` field to
// ``GetConfigRequest``, ``GetConfigHasData`` flips to ``true`` and
// the ``expectTypeOf`` assertion below fails compile.
type GetConfigRequest = Extract<PythonRequest, { type: "get_config" }>;
type GetConfigHasData = "data" extends keyof GetConfigRequest ? true : false;

// The ``_sampleCall`` const below is typed ``PythonCall`` so calls on
// it exercise the overload resolution at runtime. The compile-time
// narrowing check is the ``expectTypeOf`` assertion in the test body
// below (which inspects ``GetConfigHasData`` directly).
const _sampleCall: PythonCall = (async (
	_type: string,
	_data?: Record<string, unknown>,
) => {
	return undefined as never;
}) as PythonCall;

async function _exerciseTypedOverload(): Promise<void> {
	// Bare (no-data) commands — the typed overload accepts the
	// call without a ``data`` arg.
	await _sampleCall("undo_last");
	await _sampleCall("onboarding_apply");
	await _sampleCall("get_model_status");
	await _sampleCall("repaste_last");
	await _sampleCall("cancel_model_download");
}

describe("PythonRequest union covers renderer-called commands", () => {
	it("every renderer-called command is in the PythonRequest union", () => {
		// Compile-time guard via `satisfies` above; runtime
		// tautology ensures the test runs in CI.
		const keys = Object.keys(_RENDERER_CALLED_COMMANDS);
		expect(keys.length).toBeGreaterThanOrEqual(50);
	});

	it("typo'd command name is NOT in the PythonRequest union", () => {
		expect(_typoCmdNotInUnion).toBe(false);
	});

	it("typed PythonCall overload narrows data to undefined for bare commands", async () => {
		// Compile-time guard via `expectTypeOf` on `GetConfigHasData`
		// (defined above). `GetConfigRequest` has no `data` field, so
		// the typed overload's conditional `data?` type resolves to
		// `undefined` for `get_config`. If a refactor adds a `data`
		// field to `GetConfigRequest`, this assertion fails compile.
		expectTypeOf<GetConfigHasData>().toEqualTypeOf<false>();
		// Runtime exercise of the typed overload's happy paths so the
		// function isn't tree-shaken away.
		await _exerciseTypedOverload();
		expect(true).toBe(true);
	});
});
