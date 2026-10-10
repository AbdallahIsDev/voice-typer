# CI Errors

> Auto-generated from the latest GitHub Actions run via `scripts/ci/write_ci_errors.py`. Do not edit by hand, it is overwritten on every CI run.

**39 failing/errored tests** across 6 matrix legs.

### 1. `tests.handlers.test_error_envelope_code_field.TestHandlerFilesUseHelper.test_every_handler_file_uses_a_standardized_helper`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/handlers/test_error_envelope_code_field.py:122`

```
assert not ['system_permissions_handlers.py']

AssertionError: every handler file must use either _respond_with_error or _error_response; missing: ['system_permissions_handlers.py']
assert not ['system_permissions_handlers.py']
tests/handlers/test_error_envelope_code_field.py:122: in test_every_handler_file_uses_a_standardized_helper
    assert not missing, (
E   AssertionError: every handler file must use either _respond_with_error or _error_response; missing: ['system_permissions_handlers.py']
E   assert not ['system_permissions_handlers.py']
```

### 2. `tests.tauri.mig16.test_native_key_listener_macos.TestSidecarOwnership.test_adr_mandates_keeping_native_binary`

- Legs: macos-14-3.11, macos-14-3.12
- Location: `tests/tauri/mig16/test_native_key_listener_macos.py:603`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig16/test_native_key_listener_macos.py:603: in test_adr_mandates_keeping_native_binary
    src = ADR_0020.read_text(encoding="utf-8")
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 3. `tests.tauri.mig16.test_native_key_listener_macos.TestSidecarOwnership.test_adr_states_sidecar_owns_hotkey_subsystem`

- Legs: macos-14-3.11, macos-14-3.12
- Location: `tests/tauri/mig16/test_native_key_listener_macos.py:590`

```
+    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file

AssertionError: assert False
 +  where False = is_file()
 +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
tests/tauri/mig16/test_native_key_listener_macos.py:590: in test_adr_states_sidecar_owns_hotkey_subsystem
    assert ADR_0020.is_file()
E   AssertionError: assert False
E    +  where False = is_file()
E    +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
```

### 4. `tests.tauri.mig16.test_native_key_listener_macos.TestSidecarOwnership.test_adr_documents_fn_globe_key_regression`

- Legs: macos-14-3.11, macos-14-3.12
- Location: `tests/tauri/mig16/test_native_key_listener_macos.py:613`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig16/test_native_key_listener_macos.py:613: in test_adr_documents_fn_globe_key_regression
    src = ADR_0020.read_text(encoding="utf-8")
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 5. `tests.tauri.mig19.test_phase4_validation.test_validate_dict_payload_is_referenced_in_adr`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_phase4_validation.py:391`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig19/test_phase4_validation.py:391: in test_validate_dict_payload_is_referenced_in_adr
    text = ADR_0020.read_text(encoding="utf-8")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 6. `tests.tauri.mig19.test_phase4_validation.test_command_contract_is_frozen_no_untested_additions`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_phase4_validation.py:642`

```
Do NOT silently grow the wire contract.

Failed: ADR-0020 §16: _COMMAND_REGISTRY contains commands NOT in the frozen 68-command table AND NOT in the KNOWN_UNDOCUMENTED_COMMANDS allowlist:
  open_mic_settings
  screenshot_capture
  screenshot_clear_cycle
  screenshot_get_status
  screenshot_set_consent

To resolve, EITHER:
  (a) Remove the command from _COMMAND_REGISTRY (it was added without an ADR addendum), OR
  (b) Add it to EXPECTED_COMMANDS in this test + add an ADR-0020 addendum + add a _validate_dict_payload schema + add a test in tests/test_ipc_dispatch_errors.py, OR
  (c) Add it to KNOWN_UNDOCUMENTED_COMMANDS in this test with a comment naming the PR + reason (this is the explicit-gap path; the test_known_undocumented_commands_are_reported test below will then keep the entry in sync with reality).
Do NOT silently grow the wire contract.
tests/tauri/mig19/test_phase4_validation.py:642: in test_command_contract_is_frozen_no_untested_additions
    pytest.fail(
E   Failed: ADR-0020 §16: _COMMAND_REGISTRY contains commands NOT in the frozen 68-command table AND NOT in the KNOWN_UNDOCUMENTED_COMMANDS allowlist:
E     open_mic_settings
E     screenshot_capture
E     screenshot_clear_cycle
E     screenshot_get_status
E     screenshot_set_consent
E   
E   To resolve, EITHER:
E     (a) Remove the command from _COMMAND_REGISTRY (it was added without an ADR addendum), OR
E     (b) Add it to EXPECTED_COMMANDS in this test + add an ADR-0020 addendum + add a _validate_dict_payload schema + add a test in tests/test_ipc_dispatch_errors.py, OR
E     (c) Add it to KNOWN_UNDOCUMENTED_COMMANDS in this test with a comment naming the PR + reason (this is the explicit-gap path; the test_known_undocumented_commands_are_reported test below will then keep the entry in sync with reality).
E   Do NOT silently grow the wire contract.
```

### 7. `tests.tauri.mig19.test_phase4_validation.test_known_undocumented_commands_are_reported`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_phase4_validation.py:684`

```
screenshot_set_consent

Failed: Commands in _COMMAND_REGISTRY but NOT in EXPECTED_COMMANDS and NOT in KNOWN_UNDOCUMENTED_COMMANDS (add to KNOWN_UNDOCUMENTED_COMMANDS with a comment, OR close the gap by adding to EXPECTED_COMMANDS + ADR addendum):
  open_mic_settings
  screenshot_capture
  screenshot_clear_cycle
  screenshot_get_status
  screenshot_set_consent
tests/tauri/mig19/test_phase4_validation.py:684: in test_known_undocumented_commands_are_reported
    pytest.fail("\n\n".join(msg_parts))
E   Failed: Commands in _COMMAND_REGISTRY but NOT in EXPECTED_COMMANDS and NOT in KNOWN_UNDOCUMENTED_COMMANDS (add to KNOWN_UNDOCUMENTED_COMMANDS with a comment, OR close the gap by adding to EXPECTED_COMMANDS + ADR addendum):
E     open_mic_settings
E     screenshot_capture
E     screenshot_clear_cycle
E     screenshot_get_status
E     screenshot_set_consent
```

### 8. `tests.tauri.mig19.test_phase4_validation.test_adr_0020_states_61_command_contract`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_phase4_validation.py:697`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig19/test_phase4_validation.py:697: in test_adr_0020_states_61_command_contract
    text = ADR_0020.read_text(encoding="utf-8")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 9. `tests.tauri.mig19.test_phase4_validation.test_adr_0020_states_24_event_contract`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_phase4_validation.py:710`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig19/test_phase4_validation.py:710: in test_adr_0020_states_24_event_contract
    text = ADR_0020.read_text(encoding="utf-8")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 10. `tests.tauri.mig19.test_phase4_validation.test_adr_0020_states_frozen_contract_clause`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_phase4_validation.py:720`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig19/test_phase4_validation.py:720: in test_adr_0020_states_frozen_contract_clause
    text = ADR_0020.read_text(encoding="utf-8")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 11. `tests.tauri.mig19.test_phase4_validation.test_adr_0020_documents_new_command_process`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_phase4_validation.py:728`

```
FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig19/test_phase4_validation.py:728: in test_adr_0020_documents_new_command_process
    text = ADR_0020.read_text(encoding="utf-8")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1058: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/pathlib.py:1044: in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 12. `tests.tauri.mig19.test_reconnect_ux.test_use_python_throws_when_bridge_missing`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_reconnect_ux.py:425`

```
+    where <built-in method search of re.Pattern object at 0x13ed8f990> = re.compile('withCommandTimeout\\s*\\(\\s*api\\.call', re.MULTILINE|re.DOTALL).search

AssertionError: usePython.ts must call withCommandTimeout(api.call(...)) AFTER the `if (!api)` guard, otherwise the renderer would wait for the 120s command timeout instead of surfacing the 'Python bridge not available' error immediately.
assert None
 +  where None = <built-in method search of re.Pattern object at 0x13ed8f990>(');\n\t\t\tconst execute = async (): Promise<T> => {\n\t\t\t\t// Race the underlying bridge call against a per-command\n\t\t\t\t// timeout so a hung trivial command (e.g. `get_status`) surfaces\n\t\t\t\t// an error in seconds instead of the prior blanket 120s timeout\n\t\t\t\t// imposed by the predecessor main / Rust host. The underlying\n\t\t\t\t// promise may still resolve later; the caller sees the timeout\n\t\t\t\t// rejection first.\n\t\t\t\t//\n\t\t\t\t// Tauri/predecessor error-envelope normalization. On\n\t\t\t\t// Tauri v2, `invoke` rejects with a RAW STRING (not an Error)\n\t\t\t\t// when the Rust `dispatch` command returns an Err, the host\'s\n\t\t\t\t// `e.to_string()` becomes the rejection value verbatim. Callers\n\t\t\t\t// that guard with `err instanceof Error ? err.message : String(err)`\n\t\t\t\t// work, but callers that do `err.message` directly\n\t\t\t\t// (e.g. `Microphone.tsx:278`, `lib/utils/models.ts:252`) read\n\t\t\t\t// `undefined` and lose the server error message. We wrap the\n\t\t\t\t// `await withCommandTimeout` call in try/catch and re-throw:\n\t\t\t\t//   - Error instances propagate unchanged (no double-wrapping);\n\t\t\t\t//   - string rejections ...\n\t// better than implicit so future contributors don\'t accidentally\n\t// remove the entry thinking it\'s the default).\n\ttoggle_dictation: 30_000,\n\t// ADR-0023: resolves the pasted URL (yt-dlp extract) synchronously\n\t// before acknowledging. 115s = 5s BELOW the host\'s 120s\n\t// `DISPATCH_TIMEOUT_SECS` budget for the same command (see\n\t// `_LONG_RUNNING_COMMANDS` in `dispatch.rs`), so the renderer\n\t// surfaces the command-specific timeout first (house convention).\n\tmedia_transcribe_start: 115_000,\n};\n\nconst DEFAULT_COMMAND_TIMEOUT_MS = 30_000;\n\nexport function getTimeout(cmd: string): number {\n\treturn COMMAND_TIMEOUTS[cmd] ?? DEFAULT_COMMAND_TIMEOUT_MS;\n}\n\nexport function withCommandTimeout<T>(\n\tpromise: Promise<T>,\n\tcmd: string,\n): Promise<T> {\n\tconst timeoutMs = getTimeout(cmd);\n\tlet timer: ReturnType<typeof setTimeout> | undefined;\n\tconst timeoutPromise = new Promise<never>((_, reject) => {\n\t\ttimer = setTimeout(() => {\n\t\t\treject(new Error(`IPC command "${cmd}" timed out after ${timeoutMs}ms`));\n\t\t}, timeoutMs);\n\t});\n\treturn Promise.race([promise, timeoutPromise]).finally(() => {\n\t\tif (timer) clearTimeout(timer);\n\t});\n}\n')
 +    where <built-in method search of re.Pattern object at 0x13ed8f990> = re.compile('withCommandTimeout\\s*\\(\\s*api\\.call', re.MULTILINE|re.DOTALL).search
tests/tauri/mig19/test_reconnect_ux.py:425: in test_use_python_throws_when_bridge_missing
    assert call_re.search(rest), (
E   AssertionError: usePython.ts must call withCommandTimeout(api.call(...)) AFTER the `if (!api)` guard, otherwise the renderer would wait for the 120s command timeout instead of surfacing the 'Python bridge not available' error immediately.
E   assert None
E    +  where None = <built-in method search of re.Pattern object at 0x13ed8f990>(');\n\t\t\tconst execute = async (): Promise<T> => {\n\t\t\t\t// Race the underlying bridge call against a per-command\n\t\t\t\t// timeout so a hung trivial command (e.g. `get_status`) surfaces\n\t\t\t\t// an error in seconds instead of the prior blanket 120s timeout\n\t\t\t\t// imposed by the predecessor main / Rust host. The underlying\n\t\t\t\t// promise may still resolve later; the caller sees the timeout\n\t\t\t\t// rejection first.\n\t\t\t\t//\n\t\t\t\t// Tauri/predecessor
… (truncated)
```

### 13. `tests.tauri.mig19.test_wire_swap_recovery.test_adr_0020_section_10_documents_backoff`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_wire_swap_recovery.py:99`

```
+    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file

failed on setup with "AssertionError: missing: /Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md
assert False
 +  where False = is_file()
 +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file"
tests/tauri/mig19/test_wire_swap_recovery.py:99: in adr_0020_source
    assert ADR_0020.is_file(), f"missing: {ADR_0020}"
E   AssertionError: missing: /Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md
E   assert False
E    +  where False = is_file()
E    +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
```

### 14. `tests.tauri.mig19.test_wire_swap_recovery.test_adr_0020_section_10_documents_frame_cap`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_wire_swap_recovery.py:99`

```
+    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file

failed on setup with "AssertionError: missing: /Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md
assert False
 +  where False = is_file()
 +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file"
tests/tauri/mig19/test_wire_swap_recovery.py:99: in adr_0020_source
    assert ADR_0020.is_file(), f"missing: {ADR_0020}"
E   AssertionError: missing: /Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md
E   assert False
E    +  where False = is_file()
E    +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
```

### 15. `tests.tauri.mig19.test_wire_swap_recovery.test_adr_0020_section_10_documents_rate_limiter_port`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig19/test_wire_swap_recovery.py:99`

```
+    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file

failed on setup with "AssertionError: missing: /Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md
assert False
 +  where False = is_file()
 +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file"
tests/tauri/mig19/test_wire_swap_recovery.py:99: in adr_0020_source
    assert ADR_0020.is_file(), f"missing: {ADR_0020}"
E   AssertionError: missing: /Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md
E   assert False
E    +  where False = is_file()
E    +    where is_file = PosixPath('/Users/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
```

### 16. `tests.test_architecture_doc_accuracy.test_event_bus_count_matches_doc_and_code`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_architecture_doc_accuracy.py:40`

```
+  where 51 = len(frozenset({'asr_backend_disabled', 'asr_backend_load_failed', 'asr_backend_ready', 'asr_last_resort_unloaded', 'audio_clip', 'bubble_config', ...}))

AssertionError: EVENT_TYPES in voice_typer/server/event_bus.py must have 52 entries (actual: 51). Update doc + test together.
assert 51 == 52
 +  where 51 = len(frozenset({'asr_backend_disabled', 'asr_backend_load_failed', 'asr_backend_ready', 'asr_last_resort_unloaded', 'audio_clip', 'bubble_config', ...}))
tests/test_architecture_doc_accuracy.py:40: in test_event_bus_count_matches_doc_and_code
    assert len(EVENT_TYPES) == 52, (
E   AssertionError: EVENT_TYPES in voice_typer/server/event_bus.py must have 52 entries (actual: 51). Update doc + test together.
E   assert 51 == 52
E    +  where 51 = len(frozenset({'asr_backend_disabled', 'asr_backend_load_failed', 'asr_backend_ready', 'asr_last_resort_unloaded', 'audio_clip', 'bubble_config', ...}))
```

### 17. `tests.test_bubble_idle_state_push.test_recording_start_pushes_recording_state`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_bubble_idle_state_push.py:18`

```
assert 'set_state("recording")' in 'app._waveform_bubble.show()\n                app._waveform_bubble.set_state("permission_revoked")\n                self._schedule_permission_revoked_bubble_reset(app)\n        except Exception:\n            log.debug("[DICTATION] permission bubble surface failed", exc_info=True)\n        try:\n            app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.recording_failed_permission"))\n        except Exception:\n            log.debug("[DICTATION] permission tray surface failed", exc_'

assert 'set_state("recording")' in 'app._waveform_bubble.show()\n                app._waveform_bubble.set_state("permission_revoked")\n                self._schedule_permission_revoked_bubble_reset(app)\n        except Exception:\n            log.debug("[DICTATION] permission bubble surface failed", exc_info=True)\n        try:\n            app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.recording_failed_permission"))\n        except Exception:\n            log.debug("[DICTATION] permission tray surface failed", exc_'
tests/test_bubble_idle_state_push.py:18: in test_recording_start_pushes_recording_state
    assert 'set_state("recording")' in tail
E   assert 'set_state("recording")' in 'app._waveform_bubble.show()\n                app._waveform_bubble.set_state("permission_revoked")\n                self._schedule_permission_revoked_bubble_reset(app)\n        except Exception:\n            log.debug("[DICTATION] permission bubble surface failed", exc_info=True)\n        try:\n            app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.recording_failed_permission"))\n        except Exception:\n            log.debug("[DICTATION] permission tray surface failed", exc_'
```

### 18. `tests.test_bubble_idle_state_push.test_recording_start_skips_bubble_when_hidden`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_bubble_idle_state_push.py:89`

```
assert 'set_state("recording")' in 'bubble_behavior", "show_on_record") != "hidden":\n                app._waveform_bubble.show()\n                app._waveform_bubble.set_state("permission_revoked")\n                self._schedule_permission_revoked_bubble_reset(app)\n        except Exception:\n            log.debug("[DICTATION] permission bubble surface failed", exc_info=True)\n        try:\n            app.tray.set_state(AppState.ERROR,'

assert 'set_state("recording")' in 'bubble_behavior", "show_on_record") != "hidden":\n                app._waveform_bubble.show()\n                app._waveform_bubble.set_state("permission_revoked")\n                self._schedule_permission_revoked_bubble_reset(app)\n        except Exception:\n            log.debug("[DICTATION] permission bubble surface failed", exc_info=True)\n        try:\n            app.tray.set_state(AppState.ERROR,'
tests/test_bubble_idle_state_push.py:89: in test_recording_start_skips_bubble_when_hidden
    assert 'set_state("recording")' in tail
E   assert 'set_state("recording")' in 'bubble_behavior", "show_on_record") != "hidden":\n                app._waveform_bubble.show()\n                app._waveform_bubble.set_state("permission_revoked")\n                self._schedule_permission_revoked_bubble_reset(app)\n        except Exception:\n            log.debug("[DICTATION] permission bubble surface failed", exc_info=True)\n        try:\n            app.tray.set_state(AppState.ERROR,'
```

### 19. `tests.test_config_validators_split.TestAllowlistSnapshot.test_allowlist_size_unchanged`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_config_validators_split.py:165`

```
+  where 134 = len({'hotkey': (<class 'str'>, <function _validate_hotkey at 0x105b09760>), 'repaste_hotkey': (<class 'str'>, <function _validate_hotkey at 0x105b09760>), 'microphone': ((<class 'str'>, <class 'NoneType'>), <function _make_optional_str_validator.<locals>._validate at 0x105b0ab60>), 'model_size': (<class 'str'>, <function _make_enum_validator.<locals>._validate at 0x105b0aca0>), ...})

AssertionError: IPC_CONFIG_ALLOWLIST size drifted: expected 132, got 134. SEC-002 contract (AGENTS.md §6.3), adding/removing keys is a security-sensitive change that must be reviewed explicitly. Latest reviewed growth: 130 → 132, `screenshot_beta_enabled` + `screenshot_consent` (one-shot screenshot beta flags; bool-validated). Prior 129 → 130 growth: `active_plugin` (Plugins page activation switch; slug-validated, empty = local model). Prior 128 → 129 growth: `hallucination_filter_mode` (separate in-flight change).
assert 134 == 132
 +  where 134 = len({'hotkey': (<class 'str'>, <function _validate_hotkey at 0x105b09760>), 'repaste_hotkey': (<class 'str'>, <function _validate_hotkey at 0x105b09760>), 'microphone': ((<class 'str'>, <class 'NoneType'>), <function _make_optional_str_validator.<locals>._validate at 0x105b0ab60>), 'model_size': (<class 'str'>, <function _make_enum_validator.<locals>._validate at 0x105b0aca0>), ...})
tests/test_config_validators_split.py:165: in test_allowlist_size_unchanged
    assert len(IPC_CONFIG_ALLOWLIST) == 132, (
E   AssertionError: IPC_CONFIG_ALLOWLIST size drifted: expected 132, got 134. SEC-002 contract (AGENTS.md §6.3), adding/removing keys is a security-sensitive change that must be reviewed explicitly. Latest reviewed growth: 130 → 132, `screenshot_beta_enabled` + `screenshot_consent` (one-shot screenshot beta flags; bool-validated). Prior 129 → 130 growth: `active_plugin` (Plugins page activation switch; slug-validated, empty = local model). Prior 128 → 129 growth: `hallucination_filter_mode` (separate in-flight change).
E   assert 134 == 132
E    +  where 134 = len({'hotkey': (<class 'str'>, <function _validate_hotkey at 0x105b09760>), 'repaste_hotkey': (<class 'str'>, <function _validate_hotkey at 0x105b09760>), 'microphone': ((<class 'str'>, <class 'NoneType'>), <function _make_optional_str_validator.<locals>._validate at 0x105b0ab60>), 'model_size': (<class 'str'>, <function _make_enum_validator.<locals>._validate at 0x105b0aca0>), ...})
```

### 20. `tests.test_config_validators_split.TestAllowlistSnapshot.test_allowlist_keys_match_frozen_snapshot`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_config_validators_split.py:185`

```
assert not frozenset({'cloud_gemini_consent', 'gemini_api_key'})

AssertionError: IPC_CONFIG_ALLOWLIST has extra keys not present in the pre-split snapshot: ['cloud_gemini_consent', 'gemini_api_key']. SEC-002 allowlist grew during the split, must be reviewed explicitly.
assert not frozenset({'cloud_gemini_consent', 'gemini_api_key'})
tests/test_config_validators_split.py:185: in test_allowlist_keys_match_frozen_snapshot
    assert not extra, (
E   AssertionError: IPC_CONFIG_ALLOWLIST has extra keys not present in the pre-split snapshot: ['cloud_gemini_consent', 'gemini_api_key']. SEC-002 allowlist grew during the split, must be reviewed explicitly.
E   assert not frozenset({'cloud_gemini_consent', 'gemini_api_key'})
```

### 21. `tests.test_doc_command_counts.test_contributing_md_states_registry_count`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_doc_command_counts.py:100`

```
+      where <built-in method group of re.Match object at 0x122e79b40> = <re.Match object; span=(23931, 23961), match='reuses the 84-command registry'>.group

AssertionError: CONTRIBUTING.md documents 84-command registry but the actual _COMMAND_REGISTRY count is 85. Update the sidecar_ws.py row in CONTRIBUTING.md.
assert 84 == 85
 +  where 84 = int('84')
 +    where '84' = <built-in method group of re.Match object at 0x122e79b40>(1)
 +      where <built-in method group of re.Match object at 0x122e79b40> = <re.Match object; span=(23931, 23961), match='reuses the 84-command registry'>.group
tests/test_doc_command_counts.py:100: in test_contributing_md_states_registry_count
    assert int(m.group(1)) == actual, (
E   AssertionError: CONTRIBUTING.md documents 84-command registry but the actual _COMMAND_REGISTRY count is 85. Update the sidecar_ws.py row in CONTRIBUTING.md.
E   assert 84 == 85
E    +  where 84 = int('84')
E    +    where '84' = <built-in method group of re.Match object at 0x122e79b40>(1)
E    +      where <built-in method group of re.Match object at 0x122e79b40> = <re.Match object; span=(23931, 23961), match='reuses the 84-command registry'>.group
```

### 22. `tests.test_error_codes_registry.TestEmittedCodesAreRegisteredOrLegacy.test_all_emitted_codes_known`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_error_codes_registry.py:150`

```
voice_typer/server/handlers/screenshot_handlers.py:40 -> 'screenshot_already_captured'

Failed: Unknown error codes emitted in the server tree. Either add the namespaced form to ERROR_CODES in voice_typer/server/ipc/validation.py, OR add the legacy form to LEGACY_ALIASES in this test (if it's a backward-compat alias).
Unknown emissions:
  voice_typer/server/handlers/screenshot_handlers.py:16 -> 'screenshot_unsupported'
  voice_typer/server/handlers/screenshot_handlers.py:18 -> 'screenshot_disabled'
  voice_typer/server/handlers/screenshot_handlers.py:20 -> 'screenshot_no_consent'
  voice_typer/server/handlers/screenshot_handlers.py:36 -> 'screenshot_unsupported'
  voice_typer/server/handlers/screenshot_handlers.py:40 -> 'screenshot_already_captured'
tests/test_error_codes_registry.py:150: in test_all_emitted_codes_known
    pytest.fail(
E   Failed: Unknown error codes emitted in the server tree. Either add the namespaced form to ERROR_CODES in voice_typer/server/ipc/validation.py, OR add the legacy form to LEGACY_ALIASES in this test (if it's a backward-compat alias).
E   Unknown emissions:
E     voice_typer/server/handlers/screenshot_handlers.py:16 -> 'screenshot_unsupported'
E     voice_typer/server/handlers/screenshot_handlers.py:18 -> 'screenshot_disabled'
E     voice_typer/server/handlers/screenshot_handlers.py:20 -> 'screenshot_no_consent'
E     voice_typer/server/handlers/screenshot_handlers.py:36 -> 'screenshot_unsupported'
E     voice_typer/server/handlers/screenshot_handlers.py:40 -> 'screenshot_already_captured'
```

### 23. `tests.test_hotkeys.TestApplyConfigReRegistersHotkeyForPushToTalk.test_service_apply_config_side_effects_handles_recording_mode`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_hotkeys.py:29`

```
assert 'recording_mode' in '"""Config side-effect dispatcher (registered handlers, not an if-chain).\n\nNOTE: see docs/code-notes/security-config.md#config-preset-handlers\n\nImplementation split (every moved name re-exported here, so the\nhistorical import path keeps resolving):\n:mod:`voice_typer.server.config_applier_handlers` (the side-effect\nhandler registry + support helpers). This module keeps the ACL notify\npath, the preset-override keys, and :class:`ConfigApplier` -- the\nRACE-011 config-mutation lock and the SEC-002 allowlist check stay on\nthis facade, where tests exercise them.\n"""\n\nfrom __future__ import annotations\n\nimport contextlib\nimport json\nimport logging\nfrom typing import Any\n\nfrom voice_typer.server import i18n\nfrom voice_typer.server.branding import APP_NAME\nfrom voice_typer.server.config_applier_handlers import (  # noqa: F401  # facade re-export\n    _FILTER_CHAIN_KEYS,\n    ConfigSideEffect,\n    SideEffectContext,\n    SideEffectStatus,\n    _apply_audio_preset,\n    _AudioPresetHandler,\n    _AutostartSyncHandler,\n    _BubbleBehaviorHandler,\n    _DictationHotkeyHandler,\n    _EscHotkeyHandler,\n    _FilterChainHandler,\n    _NotificationsHandler,\n    _notify_sid..."non-allowlisted keys {sorted(_unknown)}; the IPC "\n                f"set_config handler should have dropped these via "\n                f"validate_config_update. Internal callers must only "\n                f"pass IPC_CONFIG_ALLOWLIST keys."\n            )\n        app = self._app\n        # (session-3): capture the side-effect status dict for\n        side_effect_status: SideEffectStatus = self._empty_side_effect_status()\n        # + : snapshot pre-setattr Config state. Used for\n        with app._config_mutation_lock:\n            updates = self._maybe_autoswitch_audio_preset(updates)\n            set_keys = self._setattr_updates(app, updates)\n            self._maybe_invalidate_llm_polisher(app, updates)\n            # Apply side effects inside the lock so Config mutations\n            side_effect_status = self.apply_config_side_effects(updates)\n            # ``save_strict`` raises RuntimeError if ``save()`` returned\n            self._save_updates_strict(app, updates, set_keys)\n            self._maybe_refresh_clipboard(app, updates)\n        # invalidate the tray menu cache so the next menu\n        self._post_save_tray_cleanup(app)\n        return side_effect_status\n'

assert 'recording_mode' in '"""Config side-effect dispatcher (registered handlers, not an if-chain).\n\nNOTE: see docs/code-notes/security-config.md#config-preset-handlers\n\nImplementation split (every moved name re-exported here, so the\nhistorical import path keeps resolving):\n:mod:`voice_typer.server.config_applier_handlers` (the side-effect\nhandler registry + support helpers). This module keeps the ACL notify\npath, the preset-override keys, and :class:`ConfigApplier` -- the\nRACE-011 config-mutation lock and the SEC-002 allowlist check stay on\nthis facade, where tests exercise them.\n"""\n\nfrom __future__ import annotations\n\nimport contextlib\nimport json\nimport logging\nfrom typing import Any\n\nfrom voice_typer.server import i18n\nfrom voice_typer.server.branding import APP_NAME\nfrom voice_typer.server.config_applier_handlers import (  # noqa: F401  # facade re-export\n    _FILTER_CHAIN_KEYS,\n    ConfigSideEffect,\n    SideEffectContext,\n    SideEffectStatus,\n    _apply_audio_preset,\n    _AudioPresetHandler,\n    _AutostartSyncHandler,\n    _BubbleBehaviorHandler,\n    _DictationHotkeyHandler,\n    _EscHotkeyHandler,\n    _FilterChainHandler,\n    _NotificationsHandler,\n    _notify_sid..."non-allowlisted keys {sorted(_unknown)}; the IPC "\n                f"set_config handler should have dropped these via "\n                f"validate_config_update. Internal callers must only "\n                f"pass IPC_CONFIG_ALLOWLIST keys."\n            )\n        app = self._app\n        # (session-3): capture the side-effect status di
… (truncated)
```

### 24. `tests.test_hotkeys.TestApplyConfigReRegistersHotkeyForPushToTalk.test_service_handles_hotkey_change`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_hotkeys.py:34`

```
assert '"hotkey" in updates' in '"""Config side-effect dispatcher (registered handlers, not an if-chain).\n\nNOTE: see docs/code-notes/security-config.md#config-preset-handlers\n\nImplementation split (every moved name re-exported here, so the\nhistorical import path keeps resolving):\n:mod:`voice_typer.server.config_applier_handlers` (the side-effect\nhandler registry + support helpers). This module keeps the ACL notify\npath, the preset-override keys, and :class:`ConfigApplier` -- the\nRACE-011 config-mutation lock and the SEC-002 allowlist check stay on\nthis facade, where tests exercise them.\n"""\n\nfrom __future__ import annotations\n\nimport contextlib\nimport json\nimport logging\nfrom typing import Any\n\nfrom voice_typer.server import i18n\nfrom voice_typer.server.branding import APP_NAME\nfrom voice_typer.server.config_applier_handlers import (  # noqa: F401  # facade re-export\n    _FILTER_CHAIN_KEYS,\n    ConfigSideEffect,\n    SideEffectContext,\n    SideEffectStatus,\n    _apply_audio_preset,\n    _AudioPresetHandler,\n    _AutostartSyncHandler,\n    _BubbleBehaviorHandler,\n    _DictationHotkeyHandler,\n    _EscHotkeyHandler,\n    _FilterChainHandler,\n    _NotificationsHandler,\n    _notify_sid..."non-allowlisted keys {sorted(_unknown)}; the IPC "\n                f"set_config handler should have dropped these via "\n                f"validate_config_update. Internal callers must only "\n                f"pass IPC_CONFIG_ALLOWLIST keys."\n            )\n        app = self._app\n        # (session-3): capture the side-effect status dict for\n        side_effect_status: SideEffectStatus = self._empty_side_effect_status()\n        # + : snapshot pre-setattr Config state. Used for\n        with app._config_mutation_lock:\n            updates = self._maybe_autoswitch_audio_preset(updates)\n            set_keys = self._setattr_updates(app, updates)\n            self._maybe_invalidate_llm_polisher(app, updates)\n            # Apply side effects inside the lock so Config mutations\n            side_effect_status = self.apply_config_side_effects(updates)\n            # ``save_strict`` raises RuntimeError if ``save()`` returned\n            self._save_updates_strict(app, updates, set_keys)\n            self._maybe_refresh_clipboard(app, updates)\n        # invalidate the tray menu cache so the next menu\n        self._post_save_tray_cleanup(app)\n        return side_effect_status\n'

assert '"hotkey" in updates' in '"""Config side-effect dispatcher (registered handlers, not an if-chain).\n\nNOTE: see docs/code-notes/security-config.md#config-preset-handlers\n\nImplementation split (every moved name re-exported here, so the\nhistorical import path keeps resolving):\n:mod:`voice_typer.server.config_applier_handlers` (the side-effect\nhandler registry + support helpers). This module keeps the ACL notify\npath, the preset-override keys, and :class:`ConfigApplier` -- the\nRACE-011 config-mutation lock and the SEC-002 allowlist check stay on\nthis facade, where tests exercise them.\n"""\n\nfrom __future__ import annotations\n\nimport contextlib\nimport json\nimport logging\nfrom typing import Any\n\nfrom voice_typer.server import i18n\nfrom voice_typer.server.branding import APP_NAME\nfrom voice_typer.server.config_applier_handlers import (  # noqa: F401  # facade re-export\n    _FILTER_CHAIN_KEYS,\n    ConfigSideEffect,\n    SideEffectContext,\n    SideEffectStatus,\n    _apply_audio_preset,\n    _AudioPresetHandler,\n    _AutostartSyncHandler,\n    _BubbleBehaviorHandler,\n    _DictationHotkeyHandler,\n    _EscHotkeyHandler,\n    _FilterChainHandler,\n    _NotificationsHandler,\n    _notify_sid..."non-allowlisted keys {sorted(_unknown)}; the IPC "\n                f"set_config handler should have dropped these via "\n                f"validate_config_update. Internal callers must only "\n                f"pass IPC_CONFIG_ALLOWLIST keys."\n            )\n        app = self._app\n        # (session-3): capture the side-effect
… (truncated)
```

### 25. `tests.test_hotkey_watchdog.TestPynputWatchdog.test_watchdog_resets_failure_count_on_recovery`

- Legs: macos-14-3.11
- Location: `tests/test_hotkey_watchdog.py:154`

```
+  where 3 = <voice_typer.server.hotkeys.pynput_backend.PynputHotkey object at 0x144f2e710>._watchdog_failure_count

AssertionError: Watchdog should reset failure count to 0 when listener is healthy
assert 3 == 0
 +  where 3 = <voice_typer.server.hotkeys.pynput_backend.PynputHotkey object at 0x144f2e710>._watchdog_failure_count
tests/test_hotkey_watchdog.py:154: in test_watchdog_resets_failure_count_on_recovery
    assert backend._watchdog_failure_count == 0, (
E   AssertionError: Watchdog should reset failure count to 0 when listener is healthy
E   assert 3 == 0
E    +  where 3 = <voice_typer.server.hotkeys.pynput_backend.PynputHotkey object at 0x144f2e710>._watchdog_failure_count
```

### 26. `tests.test_ipc_reference_doc_accuracy.test_ipc_reference_doc_has_row_for_every_registry_command`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_ipc_reference_doc_accuracy.py:118`

```
assert not {'open_mic_settings', 'screenshot_capture', 'screenshot_clear_cycle', 'screenshot_get_status', 'screenshot_set_consent'}

AssertionError: _COMMAND_REGISTRY has 5 commands with no row in docs/ipc-reference.md: ['open_mic_settings', 'screenshot_capture', 'screenshot_clear_cycle', 'screenshot_get_status', 'screenshot_set_consent']. Add a row in the appropriate namespace section of the doc.
assert not {'open_mic_settings', 'screenshot_capture', 'screenshot_clear_cycle', 'screenshot_get_status', 'screenshot_set_consent'}
tests/test_ipc_reference_doc_accuracy.py:118: in test_ipc_reference_doc_has_row_for_every_registry_command
    assert not missing_from_doc, (
E   AssertionError: _COMMAND_REGISTRY has 5 commands with no row in docs/ipc-reference.md: ['open_mic_settings', 'screenshot_capture', 'screenshot_clear_cycle', 'screenshot_get_status', 'screenshot_set_consent']. Add a row in the appropriate namespace section of the doc.
E   assert not {'open_mic_settings', 'screenshot_capture', 'screenshot_clear_cycle', 'screenshot_get_status', 'screenshot_set_consent'}
```

### 27. `tests.test_ipc_reference_doc_accuracy.test_ipc_reference_doc_push_events_header_count_matches_source`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_ipc_reference_doc_accuracy.py:159`

```
assert 63 == 62

AssertionError: docs/ipc-reference.md documents 63 typed push events but the renderer's types/ipc/push_events.ts declares 62 (via `type: "<name>"` literals). Update the header.
assert 63 == 62
tests/test_ipc_reference_doc_accuracy.py:159: in test_ipc_reference_doc_push_events_header_count_matches_source
    assert documented == actual, (
E   AssertionError: docs/ipc-reference.md documents 63 typed push events but the renderer's types/ipc/push_events.ts declares 62 (via `type: "<name>"` literals). Update the header.
E   assert 63 == 62
```

### 28. `tests.test_ipc_reference_doc_accuracy.test_ipc_reference_doc_commands_header_count_matches_registry`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_ipc_reference_doc_accuracy.py:146`

```
assert 80 == 85

AssertionError: docs/ipc-reference.md documents 80 total commands but _COMMAND_REGISTRY has 85. Update the header.
assert 80 == 85
tests/test_ipc_reference_doc_accuracy.py:146: in test_ipc_reference_doc_commands_header_count_matches_registry
    assert documented == actual, (
E   AssertionError: docs/ipc-reference.md documents 80 total commands but _COMMAND_REGISTRY has 85. Update the header.
E   assert 80 == 85
```

### 29. `tests.test_ipc_reference_doc_accuracy.test_ipc_reference_doc_push_event_rows_match_source_types`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_ipc_reference_doc_accuracy.py:183`

```
assert not {'text_enhancement_failed'}

AssertionError: docs/ipc-reference.md lists 1 push-event types that are NOT in types/ipc/push_events.ts: ['text_enhancement_failed']. Either add the type to the TS union or remove the row.
assert not {'text_enhancement_failed'}
tests/test_ipc_reference_doc_accuracy.py:183: in test_ipc_reference_doc_push_event_rows_match_source_types
    assert not unknown, (
E   AssertionError: docs/ipc-reference.md lists 1 push-event types that are NOT in types/ipc/push_events.ts: ['text_enhancement_failed']. Either add the type to the TS union or remove the row.
E   assert not {'text_enhancement_failed'}
```

### 30. `tests.test_macos_bundle_id.TestOnboardingSource.test_uses_runtime_resolution_and_no_hardcoded_bundle_id`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_macos_bundle_id.py:281`

```
assert 'resolve_host_bundle_id()' in '"""First-run detection + 4-step onboarding wizard controller.\n\nDetects whether the app is running for the first time (no config.json\nexists) and guides the user through initial setup:\n\nStep 1: Welcome screen, brief explanation of what the app does + the\n        app-language picker (changeable later in Settings).\nStep 2: Consent, consolidated grant of every consent flag (voice\n        biometric, HuggingFace model downloads, OpenAI / Groq /\n        Deepgram cloud ASR, LLM polish) with an "Agree to All"\n        convenience; the renderer persists each toggle immediately via\n        the allowlisted set_config fields, so no backend-side\n        collection is needed.\nStep 3: Model selection, local-vs-cloud backend choice + per-model\n        download, tiny (default), large-v3, large-v3-turbo\n        (multilingual Whisper variants), plus Parakeet\nStep 4: Hotkey selection, F2-F12 or custom combo. This is the LAST\n        step: its Continue button finalizes the wizard (applies every\n        selection + marks onboarding complete via ``apply_settings``\n        through the service layer\'s ``onboarding_apply``).\n\nRemoved from the original 7-step flow (user decision 2026-0...rror": ...}`` envelope so the\n        IPC handler surfaces it to the user). The config flag was\n        already persisted by the ``config.save()`` call above, so the\n        wizard will NOT reappear on the next launch even though the\n        marker file is missing, :meth:`is_first_run` falls through to\n        the config check and returns ``False``.\n        """\n        if self.selected_microphone is not None:\n            config.microphone = self.selected_microphone\n        config.hotkey = self.selected_hotkey\n        config.model_size = self.selected_model\n        # set the onboarding-completed flag BEFORE ``config.save()``\n        config.onboarding_completed = True\n        # ``config.save()`` returns ``False`` on failure (errors\n        save_result = config.save()\n        if save_result is False:\n            raise RuntimeError("failed to persist onboarding settings")\n        # only mark complete once the config has been\n        self.mark_complete()\n        log.info(\n            "[ONBOARDING] Settings applied: mic=%s | hotkey=%s | model=%s",\n            self.selected_microphone,\n            self.selected_hotkey,\n            self.selected_model,\n        )\n'

AssertionError: onboarding.py must resolve the host bundle ID at runtime (resolve_host_bundle_id) for the macOS permissions guidance (the tccutil re-grant command in the onboarding walkthrough).
assert 'resolve_host_bundle_id()' in '"""First-run detection + 4-step onboarding wizard controller.\n\nDetects whether the app is running for the first time (no config.json\nexists) and guides the user through initial setup:\n\nStep 1: Welcome screen, brief explanation of what the app does + the\n        app-language picker (changeable later in Settings).\nStep 2: Consent, consolidated grant of every consent flag (voice\n        biometric, HuggingFace model downloads, OpenAI / Groq /\n        Deepgram cloud ASR, LLM polish) with an "Agree to All"\n        convenience; the renderer persists each toggle immediately via\n        the allowlisted set_config fields, so no backend-side\n        collection is needed.\nStep 3: Model selection, local-vs-cloud backend choice + per-model\n        download, tiny (default), large-v3, large-v3-turbo\n        (multilingual Whisper variants), plus Parakeet\nStep 4: Hotkey selection, F2-F12 or custom combo. This is the LAST\n        step: its Continue button finalizes the wizard (applies every\n        selection + marks onboarding complete via ``apply_settings``\n        through the service layer\'s ``onboarding_apply``).\n\nRemoved from the original 7-step flow (user decision 2026-0...rror": ...}`` envelope so the\n        IPC handler surfaces it to the user). The config flag was\n        already persisted by t
… (truncated)
```

### 31. `tests.test_notifications.TestCriticalNotificationsBypassToggle.test_model_load_failure_uses_notify_safety`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_notifications.py:88`

```
assert 'notify_safety(' in 'reason=f"all backends failed to load (primary={_primary})",\n                )\n                self._app.tray.notify(\n                    APP_NAME,\n                    i18n.t(\n                        "notify.model_manager.load_failed_critical",\n                        hotkey=notification_hotkey_label(self._app.config.hotkey),\n                    ),\n                )\n                # Clear the pend'

assert 'notify_safety(' in 'reason=f"all backends failed to load (primary={_primary})",\n                )\n                self._app.tray.notify(\n                    APP_NAME,\n                    i18n.t(\n                        "notify.model_manager.load_failed_critical",\n                        hotkey=notification_hotkey_label(self._app.config.hotkey),\n                    ),\n                )\n                # Clear the pend'
tests/test_notifications.py:88: in test_model_load_failure_uses_notify_safety
    assert "notify_safety(" in block
E   assert 'notify_safety(' in 'reason=f"all backends failed to load (primary={_primary})",\n                )\n                self._app.tray.notify(\n                    APP_NAME,\n                    i18n.t(\n                        "notify.model_manager.load_failed_critical",\n                        hotkey=notification_hotkey_label(self._app.config.hotkey),\n                    ),\n                )\n                # Clear the pend'
```

### 32. `tests.test_pyrefly_baseline_accuracy.test_errors_array_has_no_stale_entries`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_pyrefly_baseline_accuracy.py:65`

```
[277] voice_typer/server/service/model/_downloads.py:739 -- line 739 past EOF (407 lines) of voice_typer/server/service/model/_downloads.py

Failed: pyrefly-baseline.json: 2 stale entries in `errors` array (of 359 total). Each stale entry must be either remapped to its live location or dropped.
  [276] voice_typer/server/service/model/_downloads.py:697 -- line 697 past EOF (407 lines) of voice_typer/server/service/model/_downloads.py
  [277] voice_typer/server/service/model/_downloads.py:739 -- line 739 past EOF (407 lines) of voice_typer/server/service/model/_downloads.py
tests/test_pyrefly_baseline_accuracy.py:65: in test_errors_array_has_no_stale_entries
    pytest.fail(
E   Failed: pyrefly-baseline.json: 2 stale entries in `errors` array (of 359 total). Each stale entry must be either remapped to its live location or dropped.
E     [276] voice_typer/server/service/model/_downloads.py:697 -- line 697 past EOF (407 lines) of voice_typer/server/service/model/_downloads.py
E     [277] voice_typer/server/service/model/_downloads.py:739 -- line 739 past EOF (407 lines) of voice_typer/server/service/model/_downloads.py
```

### 33. `tests.test_recording_lifecycle_threaded.TestRecordingStartFailureReason.test_permission_denied_surfaces_reason`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_recording_lifecycle_threaded.py:425`

```
AttributeError: 'NoneType' object has no attribute 'args'

AttributeError: 'NoneType' object has no attribute 'args'
tests/test_recording_lifecycle_threaded.py:425: in test_permission_denied_surfaces_reason
    notify_msg = str(notify_mock.call_args.args[1])
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^
E   AttributeError: 'NoneType' object has no attribute 'args'
```

### 34. `tests.test_service_i18n_tray_notices.TestHotkeyDispatcherSourceUsesI18n.test_hotkey_dispatcher_no_hardcoded_notice_bodies`

- Legs: macos-14-3.11, macos-14-3.12, ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/test_service_i18n_tray_notices.py:225`

```
assert 'i18n_t(' in '"""#2 HotkeyDispatcher, extracted from LausuApp.\n\nOwns global hotkey registration: dictation toggle hotkey, ESC cancel\nhotkey, and repaste hotkey. Each hotkey gets its own HotkeyBackend\ninstance (Win32 native, pynput, or Wayland), unless an identical spec\nis already tracked in ``_shared_backend_pool``, in which case the\nexisting backend is reused (rare; e.g. two roles bound to the same key).\n\nPreviously this concern lived in LausuApp as ~100 LOC across:\n    _register_hotkey, _register_esc_hotkey, _unregister_esc_hotkey,\n    _register_repaste_hotkey, _restart_hotkey\n\nThe bodies live in the split mixins composed below (pool / registration /\ndispatch / ptt-safety / lifecycle); this module keeps the class, the\nfacade-owned state, and the module names tests monkeypatch.\n\nTODO, full per-spec backend pooling (deferred; touches native binary\nwire protocol)\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\nThe current implementation pools the THREE ROLES (dictation / ESC /\nrepaste) into a single native subprocess via the ``_shared_backend``\nextra-matcher mechanism (see class docstring). It ALSO tracks every\ncreated backend by spec in ``_shared_backend...p\n        self._hotkey_backend: HotkeyBackend | None = None\n        self._esc_backend: HotkeyBackend | None = None\n        self._repaste_backend: HotkeyBackend | None = None\n        # Shared backend handle, the dictation backend, whose native\n        self._shared_backend: HotkeyBackend | None = None\n        # Per-spec backend pool, tracks every live backend by its\n        self._shared_backend_pool: dict[str, HotkeyBackend] = {}\n        # Stashed ESC / repaste callbacks so :meth:`_repool_aux_into_shared`\n        self._esc_callback: Any = None\n        self._repaste_callback: Any = None\n        # track the last-registered ESC and repaste specs so\n        self._esc_spec: str | None = None\n        self._repaste_spec: str | None = None\n        # re-entrancy guard for\n        self._resyncing_aux = False\n        # threading.Event for atomic cross-\n        self._esc_pending_capture_exit_event: threading.Event = threading.Event()\n        # PTT safety timer. None when not armed (toggle mode,\n        self._ptt_safety_timer: threading.Timer | None = None\n\n    # PTT safety timeout. Push-to-talk starts recording on key-down\n    _PTT_SAFETY_TIMEOUT_SECONDS: float = 60.0\n\n'

assert 'i18n_t(' in '"""#2 HotkeyDispatcher, extracted from LausuApp.\n\nOwns global hotkey registration: dictation toggle hotkey, ESC cancel\nhotkey, and repaste hotkey. Each hotkey gets its own HotkeyBackend\ninstance (Win32 native, pynput, or Wayland), unless an identical spec\nis already tracked in ``_shared_backend_pool``, in which case the\nexisting backend is reused (rare; e.g. two roles bound to the same key).\n\nPreviously this concern lived in LausuApp as ~100 LOC across:\n    _register_hotkey, _register_esc_hotkey, _unregister_esc_hotkey,\n    _register_repaste_hotkey, _restart_hotkey\n\nThe bodies live in the split mixins composed below (pool / registration /\ndispatch / ptt-safety / lifecycle); this module keeps the class, the\nfacade-owned state, and the module names tests monkeypatch.\n\nTODO, full per-spec backend pooling (deferred; touches native binary\nwire protocol)\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\nThe current implementation pools the THREE ROLES (dictation / ESC /\nrepaste) into a single native subprocess via the ``_shared_backend``\nextra-matcher mechanism (see class docstring). It ALSO tracks every\ncreated backend by spec in ``_shared_backend...p\n        self._hotkey_backend: HotkeyBackend | None = None\n        self._esc_backend: HotkeyBackend | None = None\n        self._repaste_backend: HotkeyBackend | None = None\n        # Shared backend handle, the dictation backend, whose native\n        self._shared_backend: HotkeyBackend | None = None\n        # Per-spec backend pool, tracks every live
… (truncated)
```

### 35. `tests.tauri.mig17.test_native_key_listener_linux.TestEvdevGlobalHotkeys.test_adr_documents_evdev_works_on_wayland`

- Legs: ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig17/test_native_key_listener_linux.py:549`

```
+    where is_file = PosixPath('/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file

AssertionError: assert False
 +  where False = is_file()
 +    where is_file = PosixPath('/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
tests/tauri/mig17/test_native_key_listener_linux.py:549: in test_adr_documents_evdev_works_on_wayland
    assert ADR_0020.is_file()
E   AssertionError: assert False
E    +  where False = is_file()
E    +    where is_file = PosixPath('/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
```

### 36. `tests.tauri.mig17.test_native_key_listener_linux.TestKeySuppressionNotSupported.test_adr_documents_linux_no_suppression`

- Legs: ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig17/test_native_key_listener_linux.py:598`

```
FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig17/test_native_key_listener_linux.py:598: in test_adr_documents_linux_no_suppression
    src = ADR_0020.read_text(encoding="utf-8")
/opt/hostedtoolcache/Python/3.10.22/x64/lib/python3.10/pathlib.py:1134: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
/opt/hostedtoolcache/Python/3.10.22/x64/lib/python3.10/pathlib.py:1119: in open
    return self._accessor.open(self, mode, buffering, encoding, errors,
E   FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 37. `tests.tauri.mig17.test_native_key_listener_linux.TestSidecarOwnership.test_adr_states_sidecar_owns_hotkey_subsystem`

- Legs: ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig17/test_native_key_listener_linux.py:708`

```
+    where is_file = PosixPath('/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file

AssertionError: assert False
 +  where False = is_file()
 +    where is_file = PosixPath('/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
tests/tauri/mig17/test_native_key_listener_linux.py:708: in test_adr_states_sidecar_owns_hotkey_subsystem
    assert ADR_0020.is_file()
E   AssertionError: assert False
E    +  where False = is_file()
E    +    where is_file = PosixPath('/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md').is_file
```

### 38. `tests.tauri.mig17.test_native_key_listener_linux.TestSidecarOwnership.test_adr_mandates_keeping_native_binary`

- Legs: ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig17/test_native_key_listener_linux.py:719`

```
FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig17/test_native_key_listener_linux.py:719: in test_adr_mandates_keeping_native_binary
    src = ADR_0020.read_text(encoding="utf-8")
/opt/hostedtoolcache/Python/3.10.22/x64/lib/python3.10/pathlib.py:1134: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
/opt/hostedtoolcache/Python/3.10.22/x64/lib/python3.10/pathlib.py:1119: in open
    return self._accessor.open(self, mode, buffering, encoding, errors,
E   FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```

### 39. `tests.tauri.mig17.test_native_key_listener_linux.TestSidecarOwnership.test_adr_documents_wayland_regression_risk`

- Legs: ubuntu-22.04-3.10, ubuntu-22.04-3.11, ubuntu-22.04-3.12, ubuntu-22.04-3.13
- Location: `tests/tauri/mig17/test_native_key_listener_linux.py:729`

```
FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'

FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
tests/tauri/mig17/test_native_key_listener_linux.py:729: in test_adr_documents_wayland_regression_risk
    src = ADR_0020.read_text(encoding="utf-8")
/opt/hostedtoolcache/Python/3.10.22/x64/lib/python3.10/pathlib.py:1134: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
/opt/hostedtoolcache/Python/3.10.22/x64/lib/python3.10/pathlib.py:1119: in open
    return self._accessor.open(self, mode, buffering, encoding, errors,
E   FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/Lausu/Lausu/docs/adr/0020-desktop-runtime-migration-analysis.md'
```
