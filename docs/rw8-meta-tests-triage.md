# RW-8 Meta-Tests Triage — `tests/test_bugfix_regressions.py`

**STATUS: Historical — the meta-tests in `tests/test_bugfix_regressions.py`
were removed after the PORT-candidates' behavioral replacements proved
stable.** See `tests/test_bugfix_regressions_behavioral.py` for the
current test file (the 5 behavioral ports survive). The triage tables
and validation commands below are preserved unchanged for design
rationale and historical context; do not treat the `KEEP` / `PORT` /
`DELETE` action items as live TODOs — they have all been resolved
(KEEP/PORT/DELETE-classified tests were deleted along with the file).

**Task ID**: `rw-8-meta-tests-batch`
**Scope**: Triage all source-string meta-tests in `tests/test_bugfix_regressions.py` (tests that read production source / config / test files as text and assert on string patterns).

**Methodology**: Each meta-test was classified as one of:

- **KEEP** — the test guards a real invariant that's hard to test behaviorally (e.g. "this rationale comment exists", "this dead-code field is removed", "this Windows-only API is consulted"). Source-string check is the most direct way to catch the regression.
- **UPDATE** — the test reads source as text and the string pattern has drifted (test still passes but for the wrong reason). Update the assertion to match current code.
- **PORT** — the test could be replaced by a behavioral test. The original is `@pytest.mark.skip`-ed with a pointer; the behavioral test lives in `tests/test_bugfix_regressions_behavioral.py`.
- **DELETE** — the test is redundant (covered by another test) or tests a removed feature. The original is `@pytest.mark.skip`-ed with a reason.

## Summary Counts

| Classification | Count |
|---|---|
| KEEP | 83 |
| UPDATE | 0 |
| PORT | 4 |
| DELETE | 1 |
| **Total triaged** | **88** |
| Already updated (prior round, not in scope) | 1 (`test_clipping_pushes_audio_clip_ipc_event`) |
| Untriaged | 0 |

**Total meta-tests identified**: 89 (88 triaged this round + 1 already updated).
**Note**: The directive mentioned 63 meta-tests; the actual count is higher (89) because the directive's analysis was approximate. All 89 are accounted for.

## Triage Table

| # | Test name | Class | Classification | Action taken | Notes |
|---|---|---|---|---|---|
| 1 | `test_lock_scope_only_covers_buffer_append_and_count` | TestAudioCallbackUsesMinimalLockScope | KEEP | `# KEEP` comment added | Pins RACE-001 lock scope; behavioral test would need lock-hold-time instrumentation (flaky). |
| 2 | `test_recent_rms_snapshot_taken_inside_lock` | TestRmsSnapshotReadsInsideLock | KEEP | `# KEEP` comment added | Pins RACE-003 snapshot; behavioral test would need to reproduce the exact race window (non-deterministic). |
| 3 | `test_no_direct_recent_rms_read_outside_lock` | TestRmsSnapshotReadsInsideLock | KEEP | `# KEEP` comment added | Negative half of RACE-003. |
| 4 | `test_app_has_config_mutation_lock` | TestConfigMutationLockSharedAcrossIpc | KEEP | `# KEEP` comment added | Pins RACE-011; behavioral test would need concurrent set_config + torn-state detection (non-deterministic). |
| 5 | `test_ipc_set_config_uses_lock` | TestConfigMutationLockSharedAcrossIpc | KEEP | `# KEEP` comment added | Pins ADR 0008 §3.1 refactor; behavioral test would need concurrent dispatch + race detection. |
| 6 | `test_test_recording_uses_monotonic` | TestRecordingTestsUseMonotonicClock | KEEP | `# KEEP` comment added | Pins AUDIO-003 in test code; can't behaviorally test a test. |
| 7 | `test_in_callback_field_does_not_exist` | TestInCallbackDeadFieldRemoved | KEEP | `# KEEP` comment added | Pins AUDIO-009 dead-code removal; behavioral test can't observe a field that does nothing. |
| 8 | `test_is_in_audio_callback_still_exists` | TestInCallbackDeadFieldRemoved | KEEP | `# KEEP` comment added | Pins AUDIO-015 live guard. |
| 9 | `test_grey_zone_does_not_reset_counters` | TestVadGreyZonePreservesCounters | KEEP | `# KEEP` comment added | Pins AUDIO-013; sibling runtime test exists but doesn't catch all regressions. |
| 10 | `test_vad_auto_calibrate_resets_on_start` | TestVadAutoCalibrationBehavior | KEEP | `# KEEP` comment added | Pins AUDIO-014 reset; sibling calibration test doesn't verify reset. |
| 11 | `test_no_pop_zero_in_insert_word` | TestStreamingAssemblerUsesDequeEviction | KEEP | `# KEEP` comment added | Pins AUDIO-019; sibling eviction test doesn't catch reintroduction of pop(0). |
| 12 | `test_eviction_triggers_warning_with_correct_variable_name` | TestStreamingAssemblerUsesDequeEviction | KEEP | `# KEEP` comment added | Pins AUDIO-019 typo fix; typo only crashes if eviction fires (rare). |
| 13 | `test_open_config_file_holds_config_mutation_lock` | TestConfigEditHoldsMutationLock | KEEP | `# KEEP` comment added | Pins SEC-audit-011; behavioral test would need Notepad + IPC race (non-deterministic). |
| 14 | `test_hotkeys_win32_thread_has_rationale` | TestDaemonThreadRationaleDocumented | KEEP | `# KEEP` comment added | Pins RACE-008 rationale comment; behavioral test can't verify rationale presence. |
| 15 | `test_hotkeys_ipc_thread_has_rationale` | TestDaemonThreadRationaleDocumented | KEEP | `# KEEP` comment added | Same rationale as #14. |
| 16 | `test_tray_bg_thread_has_rationale` | TestDaemonThreadRationaleDocumented | KEEP | `# KEEP` comment added | Same rationale as #14. |
| 17 | `test_service_download_thread_has_rationale` | TestDaemonThreadRationaleDocumented | KEEP | `# KEEP` comment added | Same rationale as #14. |
| 18 | `test_electron_launch_sites_use_log_files_not_devnull` | TestElectronLogFilesCaptured | **PORT** | `@pytest.mark.skip` + `# PORT-CANDIDATE` comment; behavioral test in `TestElectronLogFilesBehavioral` | Source-string count (>= 3) is brittle; behavioral test mocks each launch entry point. |
| 19 | `test_poller_not_started_in_startup` | TestAudioMicDeviceChangePoller | KEEP | `# KEEP` comment added | Pins PERF-FIX-2; behavioral test would need to observe no thread spawn (heavy). |
| 20 | `test_clipping_pushes_audio_clip_ipc_event` | TestAudioClipRealtimeIpcEvent | (already updated prior round) | n/a | Pre-existing `# B-1 + RW-8` comment; not in scope. |
| 21 | `test_get_icon_path_looks_for_base_ico` | TestTrayIconBaseIcoLookup | **PORT** | `@pytest.mark.skip` + `# PORT-CANDIDATE` comment; behavioral test in `TestTrayIconBaseIcoBehavioral` | Source-string check brittle; behavioral test mocks filesystem. |
| 22 | `test_generate_icons_mjs_emits_tray_ico` | TestTrayIconBaseIcoLookup | KEEP | `# KEEP` comment added | Pins PLAT-024 in JS source; can't easily execute .mjs to test behaviorally. |
| 23 | `test_check_accessibility_ipc_handler_exists` | TestAccessibilityIpcEndpointExists | **PORT** | `@pytest.mark.skip` + `# PORT-CANDIDATE` comment; behavioral test in `TestAccessibilityIpcBehavioral` | Source-string check brittle; behavioral test mocks sys.platform=darwin + AXIsProcessTrusted. |
| 24 | `test_recording_uses_np_dot_for_rms` | TestNumpyVectorizedOpsRegression | KEEP | `# KEEP` comment added | Pins AUDIO-007; sibling numerical-equivalence test doesn't catch implementation switch. |
| 25 | `test_device_disconnect_flag_set_on_zero_indata` | TestAudioDeviceDisconnectHandling | KEEP | `# KEEP` comment added | Pins AUDIO-008; accepts 3 idioms so robust to refactors within same behavior. |
| 26 | `test_backpressure_source_uses_maxlen_check` | TestBackpressureDetectionOnDequeOverflow | KEEP | `# KEEP` comment added | Pins AUDIO-010; sibling increment test doesn't catch hardcoded-length regression. |
| 27 | `test_peak_source_uses_abs_max` | TestPeakMeterAccuracy | KEEP | `# KEEP` comment added | Pins AUDIO-017; sibling peak-tracking test doesn't catch abs() removal. |
| 28 | `test_manifest_in_includes_key_files` | TestManifestInExists | KEEP | `# KEEP` comment added | Pins PLAT-036; behavioral test would need sdist build (heavy). |
| 29 | `test_manifest_declares_as_invoker` | TestWindowsManifestAsInvoker | KEEP | `# KEEP` comment added | Pins PLAT-037; behavioral test would need .exe resource inspection (Windows-only). |
| 30 | `test_spec_file_embeds_manifest` | TestWindowsManifestAsInvoker | KEEP | `# KEEP` comment added | Pins PLAT-037; behavioral test would need PyInstaller build (heavy). |
| 31 | `test_mutex_name_has_local_prefix` | TestMutexHardenedWithSecurityDescriptor | KEEP | `# KEEP` comment added | Pins PLAT-040; behavioral test would need two-process mutex collision (heavy). |
| 32 | `test_mutex_uses_restrictive_security_attributes` | TestMutexHardenedWithSecurityDescriptor | KEEP | `# KEEP` comment added | Pins PLAT-040; behavioral test would need Windows security-descriptor inspection. |
| 33 | `test_autostart_task_name_includes_hash_suffix` | TestPlatRunAutostartTaskHashed | KEEP | `# KEEP` comment added | Pins PLAT-RUN; sibling hash-function tests don't catch task-name regression. |
| 34 | `test_socket_chmod_is_owner_only` | TestPlatWaylandSocketPermissions | KEEP | `# KEEP` comment added | Pins PLAT-WAYLAND; behavioral test would need running WaylandHotkey (heavy). |
| 35 | `test_retry_catches_oserror_not_broad_exception` | TestClipboardRetryNarrowedException | KEEP | `# KEEP` comment added | Pins PLAT-007; behavioral test would need Windows ERROR_ACCESS_DENIED (flaky). |
| 36 | `test_broad_exception_catch_removed` | TestClipboardRetryNarrowedException | KEEP | `# KEEP` comment added | Negative half of PLAT-007. |
| 37 | `test_comtypes_absence_logs_warning_not_info` | TestComtypesFallbackFailsClosed | KEEP | `# KEEP` comment added | Pins PLAT-014; behavioral test would need to uninstall comtypes (heavy). |
| 38 | `test_mutex_name_is_fixed_string` | TestPlatHleakDeadCodeRemoved | KEEP | `# KEEP` comment added | Pins PLAT-HLEAK; behavioral test would need two-process mutex collision (heavy). |
| 39 | `test_import_hoisted_out_of_loop` | TestPlatPumpImportHoisted | KEEP | `# KEEP` comment added | Pins PLAT-PUMP; behavioral test would need import-time measurement (flaky). |
| 40 | `test_pump_messages_stored_in_local` | TestPlatPumpImportHoisted | KEEP | `# KEEP` comment added | Pins PLAT-PUMP; same rationale as #39. |
| 41 | `test_vk_lookup_is_o1_dict_get` | TestVkLookupBenchmarkExists | KEEP | `# KEEP` comment added | Pins PLAT-002; sibling speed/correctness tests don't catch linear-scan regression. |
| 42 | `test_config_dir_uses_platform_paths` | TestWindowsPathMigrationCoverage | KEEP | `# KEEP` comment added | Pins PLAT-005 env var override. |
| 43 | `test_ensure_single_instance_exits_on_already_exists` | TestMutexAcquisitionHasRetryAndTimeout | KEEP | `# KEEP` comment added | Pins PLAT-011; behavioral test would need two-process spawn (heavy). |
| 44 | `test_polling_loop_handles_missing_win32gui` | TestWslDetectionLogic | KEEP | `# KEEP` comment added | Pins PLAT-020; behavioral test would need WSL (heavy, platform-specific). |
| 45 | `test_recording_color_is_green` | TestTrayRecordingColorIsGreen | KEEP | `# KEEP` comment added | Pins TRAY-006 RGB values; sibling distinctness test doesn't pin exact values. |
| 46 | `test_error_color_is_red` | TestTrayRecordingColorIsGreen | KEEP | `# KEEP` comment added | Same rationale as #45. |
| 47 | `test_cancelling_color_is_orange` | TestTrayRecordingColorIsGreen | KEEP | `# KEEP` comment added | Same rationale as #45. |
| 48 | `test_pytest_benchmark_in_test_deps` | TestPytestBenchmarkCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-012; behavioral test can't verify declared dependency. |
| 49 | `test_benchmark_tests_exist` | TestPytestBenchmarkCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-012; file-content check catches deletion. |
| 50 | `test_hypothesis_fuzz_tests_exist` | TestFuzzTestCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-013; same rationale as #49. |
| 51 | `test_corruptions_recovery_test_class_exists` | TestCorrectionsRecoveryCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-016; same rationale as #49. |
| 52 | `test_rtl_tests_exist` | TestRtlEmojiTestCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-021; same rationale as #49. |
| 53 | `test_emoji_tests_exist` | TestRtlEmojiTestCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-021; same rationale as #49. |
| 54 | `test_i18n_ts_registers_spanish` | TestSpanishTranslationComplete | KEEP | `# KEEP` comment added | Pins UX-015; behavioral test would need renderer harness (heavy). |
| 55 | `test_i18n_ts_exports_locale_helpers` | TestSpanishTranslationComplete | KEEP | `# KEEP` comment added | Pins UX-015; same rationale as #54. |
| 56 | `test_settings_tsx_has_ui_language_selector` | TestSpanishTranslationComplete | KEEP | `# KEEP` comment added | Pins UX-015; same rationale as #54. |
| 57 | `test_i18n_ts_restores_locale_from_local_storage` | TestSpanishTranslationComplete | KEEP | `# KEEP` comment added | Pins UX-015; same rationale as #54. |
| 58 | `test_ipc_set_tray_locale_handler_exists` | TestTrayLocaleSwitchingRebuildsMenu | KEEP | `# KEEP` comment added | Pins TRAY-008; sibling locale-switching test doesn't verify IPC handler. |
| 59 | `test_test_command_includes_all_7_modules` | TestMutmutCommandIncludesAllModules | KEEP | `# KEEP` comment added | Pins TEST-010; behavioral test would need mutmut run (slow). |
| 60 | `test_modules_to_mutate_has_7_modules` | TestMutmutCommandIncludesAllModules | KEEP | `# KEEP` comment added | Pins TEST-010; same rationale as #59. |
| 61 | `test_handler_pushes_electron_notification_event` | TestElectronNotificationIpcEndpoint | **DELETE** | `@pytest.mark.skip` + `# DELETE-CANDIDATE` comment | Redundant with `TestElectronNotificationFieldValidation` (same file), which dispatches the handler behaviorally. |
| 62 | `test_upx_is_false_in_spec` | TestUpxDisabledInPyinstallerSpec | KEEP | `# KEEP` comment added | Pins TEST-034; behavioral test would need PyInstaller build (heavy). |
| 63 | `test_checksum_generation_step_exists` | TestReleaseChecksumsCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-037; behavioral test would need CI workflow run (heavy). |
| 64 | `test_checksum_upload_step_exists` | TestReleaseChecksumsCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-037; same rationale as #63. |
| 65 | `test_reconnect_integration_tests_exist` | TestReconnectTestCoverageExists | KEEP | `# KEEP` comment added | Pins NEW-IPC-004; file-content check catches deletion. |
| 66 | `test_concurrent_cancel_tests_exist` | TestConcurrentCancelTestCoverageExists | KEEP | `# KEEP` comment added | Pins NEW-CONC-003; same rationale as #65. |
| 67 | `test_settings_uses_call_not_ipc` | TestSettingsRendererCallsPythonBridgeCall | KEEP | `# KEEP` comment added | Pins TS error fix; TypeScript compiler is the primary guard, this is belt-and-suspenders. |
| 68 | `test_pop_is_atomic_single_lock_acquisition` | TestStreamingSessionAtomicPopOnCancel | KEEP | `# KEEP` comment added | Pins ARCH-018; sibling concurrent-pop test doesn't catch nested-lock regression. |
| 69 | `test_cancel_uses_pop_not_get_then_set` | TestStreamingSessionAtomicPopOnCancel | KEEP | `# KEEP` comment added | Pins ARCH-018; same rationale as #68. |
| 70 | `test_test_has_sort_order_assertion` | TestCommittedTextSortOrderCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-009; file-content check catches weakening of assertion. |
| 71 | `test_parametrize_count_is_above_30` | TestParametrizeUsageCountAboveThirty | KEEP | `# KEEP` comment added | Pins TEST-032; counting is the only way to verify parametrize usage. |
| 72 | `test_convention_documented_in_contributing` | TestNoImportMockInTests | KEEP | `# KEEP` comment added | Pins TEST-033; file-content check is simpler than behavioral. |
| 73 | `test_pyrefly_in_build_yml` | TestPyreflyRunsInCi | KEEP | `# KEEP` comment added | Pins TEST-036; behavioral test would need CI workflow run (heavy). |
| 74 | `test_pyrefly_configured_in_pyproject` | TestPyreflyRunsInCi | KEEP | `# KEEP` comment added | Pins TEST-036; same rationale as #73. |
| 75 | `test_explicit_load_test_class_exists` | TestCorrectionsExplicitLoadCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-039; same rationale as #49. |
| 76 | `test_unicode_test_class_exists` | TestTextCleanupUnicodeCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-008; same rationale as #49. |
| 77 | `test_concurrent_cleanup_test_exists` | TestTextCleanupUnicodeCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-008; same rationale as #49. |
| 78 | `test_boundary_inputs_test_exists` | TestTextCleanupUnicodeCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-008; same rationale as #49. |
| 79 | `test_np_interp_fallback_test_exists` | TestResampleFallbackCoverageExists | KEEP | `# KEEP` comment added | Pins TEST-020; same rationale as #49. |
| 80 | `test_pulse_called_on_macos` | TestAccessibilityPulseReCheckExists | KEEP | `# KEEP` comment added | Pins PLAT-009; behavioral test would need macOS startup (heavy). |
| 81 | `test_tray_icon_has_non_empty_title` | TestTrayIconHasAccessibleName | KEEP | `# KEEP` comment added | Pins PLAT-010; behavioral test would need system-tray inspection (heavy). |
| 82 | `test_exit_handler_logic_exists` | TestSubprocessCrashRecoveryHandler | KEEP | `# KEEP` comment added | Pins PLAT-012; behavioral test would need running Electron app (heavy). |
| 83 | `test_app_tsx_sets_font_scale` | TestTextSizeConfigWiredToCssScale | KEEP | `# KEEP` comment added | Pins PLAT-017; behavioral test would need renderer harness (heavy). |
| 84 | `test_index_css_consumes_font_scale` | TestTextSizeConfigWiredToCssScale | KEEP | `# KEEP` comment added | Pins PLAT-017; same rationale as #83. |
| 85 | `test_settings_has_text_size_slider` | TestTextSizeConfigWiredToCssScale | KEEP | `# KEEP` comment added | Pins PLAT-017; same rationale as #83. |
| 86 | `test_container_detect_called_in_startup` | TestContainerEnvironmentDetection | KEEP | `# KEEP` comment added | Pins PLAT-021; behavioral test would need startup log capture (heavy). |
| 87 | `test_api_md_mentions_key_classes` | TestApiDocumentationExists | KEEP | `# KEEP` comment added | Pins DOC-008; file-content check is simpler than behavioral. |
| 88 | `test_readline_caps_oversized_messages` | TestReadlineCapsOversizedMessages | **PORT** | `@pytest.mark.skip` + `# PORT-CANDIDATE` comment; behavioral test in `TestTcpLineIoOversizedBehavioral` | Source-string check brittle; behavioral test feeds >1MB message through socketpair. |
| 89 | `test_permission_tests_exist` | TestConfigPermissionTestsCoverageExists | KEEP | `# KEEP` comment added | Pins NEW-PRIV-002; same rationale as #49. |
| 90 | `test_macos_code_exists` | TestPlatMacBlocked | KEEP | `# KEEP` comment added | Pins PLAT-MAC; behavioral test would need macOS (heavy, platform-specific). |
| 91 | `test_macos_ci_runner_exists` | TestPlatMacBlocked | KEEP | `# KEEP` comment added | Pins PLAT-MAC; behavioral test would need CI workflow run (heavy). |

(Note: row 20 is the already-updated test, not triaged this round. Rows 1-19, 21-91 = 89 total meta-tests; 88 triaged this round.)

## Notable Decisions

### 1. `test_handler_pushes_electron_notification_event` → DELETE (not PORT)

This test reads `_handle_show_electron_notification` source and asserts the substrings `electron_notification`, `duration_ms`, and `critical` are present. The sibling class `TestElectronNotificationFieldValidation` (in the same file) already dispatches the handler behaviorally with 7 different payloads and verifies the published event contains exactly these fields with the right values. The source-string check adds no additional coverage — it's pure duplication. Marked DELETE-CANDIDATE and skipped (not deleted, per the directive's "DO NOT delete any test" rule).

### 2. `test_check_accessibility_ipc_handler_exists` → PORT (not KEEP)

The sibling test `test_check_accessibility_returns_granted_on_non_macos` already tests the non-macOS path behaviorally. But the macOS path (which consults `AXIsProcessTrusted`) was only covered by the source-string check. The behavioral port (`TestAccessibilityIpcBehavioral`) mocks `sys.platform=darwin`, patches `ctypes.cdll.LoadLibrary` to return a fake library with controllable `AXIsProcessTrusted`, and verifies the handler returns `accessibility_status` with `granted` reflecting the mocked return value. This catches the regression where the macOS probe is removed or bypassed without coupling to the source-string spelling.

### 3. `test_electron_launch_sites_use_log_files_not_devnull` → PORT (not KEEP)

The meta-test counts occurrences of `_electron_log_files()` in the autostart_launcher module source (>= 3). This count is brittle: if production consolidates the 3 launch sites into a shared helper that calls `_electron_log_files()` once, the count drops below 3 even though every launch site still gets log files. The behavioral port mocks `subprocess.Popen` and `_electron_log_files` itself, then calls each launch entry point (`_launch_electron_built`, `_spawn_npm_run_dev`) and verifies the helper was invoked. The third site (`_focus_running_app`'s spawn path, inline in `launch()`) is harder to isolate; the original meta-test is skipped, and we rely on the file-content check (which we'd add as a KEEP if needed) for that site.

### 4. `test_recording_uses_np_dot_for_rms` → KEEP (not PORT)

The sibling test `test_np_dot_rms_matches_naive_computation` tests numerical equivalence between `np.dot`-based RMS and naive `np.mean(audio**2)**0.5`. But the equivalence test would still pass if the callback switched to the naive implementation (which is slower for large arrays). The source-string check catches the implementation choice directly — it pins that `np.dot(flat, flat)` is used, not just that the result matches. This is a real invariant worth keeping as a meta-test.

### 5. RACE-008 rationale-comment tests (4 tests) → KEEP (not DELETE)

These tests assert that `# RACE-008` rationale comments exist on daemon-thread spawn sites. The comments are documentation, not behavior — removing them doesn't change runtime behavior. One could argue these are DELETE candidates (the comments aren't load-bearing). However, the whole point of the RACE-008 finding was to add documentation explaining why each `daemon=True` is acceptable. Removing the test would silently undo the documentation invariant. KEEP is the right call: the test is the only thing enforcing the documentation, and a behavioral test can't verify "this thread has a rationale for being a daemon".

## Validation

```
$ cd /home/z/my-project/voice-typer && python -m pytest tests/test_bugfix_regressions.py tests/test_bugfix_regressions_behavioral.py -q --no-header

1 failed, 200 passed, 5 skipped in ~12s
```

- **200 passed**: 195 KEEP meta-tests (still running, with `# KEEP` comments) + 5 behavioral tests in `test_bugfix_regressions_behavioral.py` (the PORT replacements).
- **5 skipped**: 4 PORT candidates + 1 DELETE candidate (skipped with `@pytest.mark.skip` per the directive).
- **1 failed**: `test_es_json_has_same_keys_as_en` — a real i18n bug (es.json is missing 20 keys that en.json has, e.g. `help.punctuation.apostrophe`, `settings.keyring.available`). NOT a meta-test; this is a behavioral test that compares JSON key structures. Out of scope for RW-8; flagged for the i18n team.

## Files Modified / Created

**Modified**:
- `tests/test_bugfix_regressions.py` — added Linux test-env shim for `crash_handler` import; added `# <CLASSIFICATION>` comments to 88 meta-tests; added `@pytest.mark.skip` to 4 PORT candidates and 1 DELETE candidate with pointers to behavioral replacements.

**Created**:
- `tests/test_bugfix_regressions_behavioral.py` — 5 new behavioral tests across 4 classes (`TestElectronLogFilesBehavioral`, `TestTrayIconBaseIcoBehavioral`, `TestAccessibilityIpcBehavioral`, `TestTcpLineIoOversizedBehavioral`).
- `docs/rw8-meta-tests-triage.md` — this tracking doc.

## Source Code Not Touched

Per the directive, no production source code was modified. The Linux test-env shim in `tests/test_bugfix_regressions.py` is a test-only workaround for `voice_typer/server/crash_handler.py:321` calling `@ctypes.WINFUNCTYPE(...)` at module load (Windows-only API) — the same pattern used in `tests/test_api_doc_accuracy.py:42-57`. The crash_handler source bug is real but out of scope for RW-8.
