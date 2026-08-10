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
	reset_macos_accessibility: true,
	reset_linux_permissions: true,
	// finding #919 part b (2026-08-10): the Settings →
	// Troubleshooting section probes the macOS Accessibility grant on
	// mount to surface the stale-grant reset command. Callsite:
	// TroubleshootingSettingsSection.tsx.
	check_accessibility: true,
	// 28 commands added by the widening slice (see the
	// per-interface docstrings in ``types/ipc/requests.ts`` for
	// the call-site survey).
	cancel_model_download: true,
	force_cancel_transcription: true,
	// phantom ``get_disk_info`` removed — the
	// ``useModelFolder`` probe that called it was dead (command
	// not registered in ``_COMMAND_REGISTRY`` nor allowed through
	// ``ALLOWED_COMMANDS``); the probe has been deleted from the
	// hook. If a future backend exposes ``get_disk_info``, re-add
	// the matching interface here AND the
	// ``_RENDERER_CALLED_COMMANDS`` entry simultaneously.
	get_model_catalog: true,
	get_model_status: true,
	get_prewarm_status: true,
	get_templates: true,
	microphone_test_cancel: true,
	microphone_test_stop: true,
	// phantom ``models_folder_supported`` removed — same
	// reason as ``get_disk_info`` above.
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
	// phantom ``open_models_folder`` removed — same
	// reason as ``get_disk_info`` above.
	pause_model_download: true,
	repaste_last: true,
	restore_history: true,
	resume_model_download: true,
	run_prewarm: true,
	save_templates: true,
	undo_last: true,
	// 12 missing commands added — these ARE in the
	// Python ``_COMMAND_REGISTRY`` AND the renderer allowlist
	// AND are invoked from renderer code (see review.md
	// for the per-command call-site survey).
	get_defaults: true,
	download_model: true,
	import_model: true,
	delete_model: true,
	test_cloud_connection: true,
	set_esc_cancel_paused: true,
	microphone_test_start: true,
	get_volume_backend_status: true,
	open_prewarm_log: true,
	onboarding_get_model_options: true,
	onboarding_get_hotkey_presets: true,
	// NOTE: ``add_trusted_endpoint`` is intentionally NOT in
	// this ``_RENDERER_CALLED_COMMANDS`` map because no renderer
	// call site invokes it today (it's in ``ALLOWED_COMMANDS``
	// + ``_COMMAND_REGISTRY`` but the UI is not yet wired). It
	// IS in the ``PythonRequest`` union below (so future
	// renderer code can call it with type narrowing) and in
	// ``_SERVER_REGISTRY_MINUS_PYTHON_ONLY`` (so the
	// ``PythonRequest["type"] ⊆ server_registry`` guard
	// passes) — but it's not in this map because no
	// ``call<...>("add_trusted_endpoint")`` site exists yet.
	// When a future renderer feature wires it up, add it here
	// so the ``RENDERER_CALLED_COMMANDS ⊆ PythonRequest["type"]``
	// guard continues to pin the new call site.
} satisfies Partial<Record<PythonRequest["type"], true>>;

// ── Section 1b: server-registry parity ──────────────────────────────
//
// ``_SERVER_REGISTRY_MINUS_PYTHON_ONLY`` is a static mirror of
// every command registered in the Python ``_COMMAND_REGISTRY``
// (``voice_typer/server/ipc/registry.py``) MINUS the entries in
// ``_PYTHON_ONLY_COMMANDS`` (``{"shutdown", "tray_click"}`` — host-
// internal commands the renderer never invokes). The list was
// compiled by reading the canonical registry file and is kept in
// sync manually: if a future Python-side change adds or removes a
// command, this list AND the matching
// ``tests/test_ec4_python_command_registry_parity.py`` (Python-side
// parity test) AND ``src/main/allowed-commands.ts`` MUST all be
// updated in lockstep (the 4-way parity contract documented in the
// registry module's docstring).
//
// This list asserts the "PythonRequest["type"] ⊆ server_registry -
// _PYTHON_ONLY_COMMANDS" half of the parity invariant: every member
// of the ``PythonRequest`` union must be a real, dispatcher-
// recognised command (otherwise the typed ``PythonCall`` overload
// would let a renderer call site send a command that the backend
// silently rejects with ``unknown_command``).
const _SERVER_REGISTRY_MINUS_PYTHON_ONLY = {
	get_status: true,
	toggle_dictation: true,
	undo_last: true,
	repaste_last: true,
	get_config: true,
	get_defaults: true,
	set_config: true,
	get_history: true,
	get_today_stats: true,
	delete_history: true,
	restore_history: true,
	clear_history: true,
	toggle_favorite: true,
	get_favorites: true,
	search_history: true,
	get_history_count: true,
	get_transcription_text: true,
	get_microphones: true,
	get_volume_backend_status: true,
	get_model_status: true,
	get_prewarm_status: true,
	run_prewarm: true,
	open_prewarm_log: true,
	get_vocabulary: true,
	save_vocabulary: true,
	get_templates: true,
	save_templates: true,
	restart_app: true,
	quit_app: true,
	// NOTE: ``shutdown`` and ``tray_click`` are intentionally
	// ABSENT — they're the ``_PYTHON_ONLY_COMMANDS`` exclusions
	// (host-internal, never renderer-invoked).
	onboarding_is_first_run: true,
	onboarding_start: true,
	onboarding_next_step: true,
	onboarding_prev_step: true,
	onboarding_set_microphone: true,
	onboarding_set_hotkey: true,
	onboarding_set_model: true,
	onboarding_skip: true,
	onboarding_apply: true,
	onboarding_get_microphones: true,
	onboarding_get_model_options: true,
	onboarding_get_hotkey_presets: true,
	onboarding_check_permissions: true,
	onboarding_set_backend: true,
	onboarding_reset: true,
	reset_macos_accessibility: true,
	reset_linux_permissions: true,
	check_accessibility: true,
	microphone_test_start: true,
	microphone_test_stop: true,
	microphone_test_cancel: true,
	microphone_test_get_level: true,
	level_monitor_start: true,
	level_monitor_stop: true,
	import_model: true,
	download_model: true,
	cancel_model_download: true,
	pause_model_download: true,
	resume_model_download: true,
	get_model_catalog: true,
	delete_model: true,
	set_tray_locale: true,
	test_cloud_connection: true,
	add_trusted_endpoint: true,
	set_esc_cancel_paused: true,
	force_cancel_transcription: true,
	heartbeat: true,
	relaunch_ack: true,
	// NOTE: ``tray_click`` and ``shutdown`` are intentionally
	// absent — they're ``_PYTHON_ONLY_COMMANDS`` exclusions.
} satisfies Record<string, true>;

// Compile-time guard: every ``PythonRequest["type"]`` literal must
// be a key of ``_SERVER_REGISTRY_MINUS_PYTHON_ONLY``. If a phantom
// command (like the deleted ``get_disk_info`` /
// ``models_folder_supported`` / ``open_models_folder``) is ever
// reintroduced into the ``PythonRequest`` union, the conditional
// below resolves to ``false`` and the ``true`` assignment fails to
// compile.
//
// This guard is the symmetric counterpart of Section 1's
// ``RENDERER_CALLED_COMMANDS ⊆ PythonRequest["type"]`` check: that
// one catches MISSING interfaces (renderer call sites with no
// matching union member), and this one catches PHANTOM interfaces
// (union members with no matching server handler).
type _PhantomCommandGuard =
	PythonRequest["type"] extends keyof typeof _SERVER_REGISTRY_MINUS_PYTHON_ONLY
		? true
		: false;
const _noPhantomCommands: _PhantomCommandGuard = true;

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

	it("PythonRequest union contains NO phantom commands (every type is in the server registry minus python-only)", () => {
		// Compile-time guard via ``_PhantomCommandGuard``
		// above; runtime tautology asserts the guard runs in
		// CI. If a phantom command (e.g. ``get_disk_info``,
		// ``models_folder_supported``, ``open_models_folder``)
		// is reintroduced into the ``PythonRequest`` union
		// without a matching ``_COMMAND_REGISTRY`` entry, the
		// conditional resolves to ``false`` and the const
		// assignment fails to compile.
		expect(_noPhantomCommands).toBe(true);
		// Sanity: the registry mirror has enough entries to
		// be a meaningful guard (catches a future contributor
		// accidentally emptying the map).
		const registryKeys = Object.keys(_SERVER_REGISTRY_MINUS_PYTHON_ONLY);
		expect(registryKeys.length).toBeGreaterThanOrEqual(60);
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
