# CI Errors

> Auto-generated from the latest GitHub Actions run via
> `scripts/ci/write_ci_errors.py`. Do not edit by hand — it is
> overwritten on every CI run. If this file is empty, all tests passed.

**8 failing/errored test(s).**

### 1. `TestRecorderCallbackWithAudioProcessor.test_callback_does_not_raise_with_processor`

```
AssertionError: Expected 5 buffered chunks, got 4 — callback may have raised NameError (the bug we're regression-testing)
assert 4 == 5
```

### 2. `test_microphone_watcher_coreaudio.test_coreaudio_watcher_start_raises_on_non_macos`

```
AssertionError: Regex pattern did not match.
Regex: 'only available on macOS'
Input: 'pyobjc-framework-CoreAudio and pyobjc-framework-CoreFoundation are required for CoreAudioMicrophoneWatcher. Install with: pip install pyobjc-framework-CoreAudio pyobjc-framework-CoreFoundation'
```

### 3. `TestSidecarOwnership.test_native_hotkeys_module_defines_macos_backend`

```
AssertionError: native_hotkeys/base.py must define SubprocessHotkeyBackend (the base class that spawns the native binary via subprocess.Popen)
assert 'class SubprocessHotkeyBackend' in '<facade module re-exporting from ._core>'
```

### 4. `TestShutdownConstants.test_shutdown_poll_interval_constant_removed`

```
AssertionError: SHUTDOWN_POLL_INTERVAL_MS must not exist in util.rs — the dev-mode fallback is now a single bounded sleep, not a poll loop (dead code removed)
```

### 5. `TestConcurrentRestoreSerialization.test_two_concurrent_restores_do_not_overlap`

```
AssertionError: Expected 2 restore() calls; got 3
assert 3 == 2
```

### 6. `TestSha256ByArchSync.test_host_arch_entry_updated_in_lockstep_with_flat`

```
AssertionError: the non-built arch (x86_64) must not be fabricated by an update on this host
assert 'old' == ''
```

### 7. `TestLogCleanliness.test_happy_path_flows_are_log_clean`

```
AssertionError: WARNING voice_typer.server.microphone_watcher_coreaudio: [MIC-WATCHER-CA] AudioObjectAddPropertyListener raised, falling back to TTL polling
```

### 8. `TestPublishTrayStateThreadSafe.test_concurrent_publishes_no_duplicate_emit`

```
AssertionError: Concurrent publishes with the same state must emit exactly ONCE — the first caller sets _last_published and subsequent callers skip. Got 2 publishes.
```
