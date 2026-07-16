# RW-0 — Vitest Rewrite Progress Tracker

This document tracks the rewrite of the 87 string-pattern Python tests
across 5 test files as behavioral vitest unit tests.

## Background

The 5 Python test files listed below read TypeScript source files as
text and assert on substring patterns (e.g. `"formatHotkeyLabel" in
utils`, `"aria-current" in src`). These tests pass and give some
regression protection, but they are brittle: they fail on innocent
refactors (renaming a function, switching quote style, extracting a
constant) and they pass even when the actual behavior is broken
(because the string is present in a comment, not the live code path).

The vitest rewrites mount the real component (or import the real
function) and assert on the actual rendered DOM or returned value,
so the test only passes when the contract is honored at runtime.

## Per-file Stats

| Python test file | # tests total | # rewritten as vitest | # skipped | # remaining | Notes |
| --- | --- | --- | --- | --- | --- |
| `tests/test_hotkeys.py` | 20 | 8 | 8 | 12 | All `TestHotkeyUtilsFormatLabel` / `TestHotkeyUtilsValidate` / `TestRepasteKeySettingUsesHotkeyPicker` / `TestDictationKeySupportsExpandedPresets::test_single_key_presets_include_beyond_f12` rewritten. The 12 remaining are mostly Python-side behavioral tests (`PynputHotkey`, `_VK_MAP`, `_init_vk_map`) that don't assert on TS source strings. |
| `tests/test_ux_components.py` | 62 | 8 | 8 | 54 | All `TestAppHasHelpOverlayForShortcuts::test_app_has_question_mark_keydown_handler` + `test_help_overlay_closes_on_escape`, `TestBubbleSupportsKeyboardArrowMove::test_bubble_calls_move_by` + `test_bubble_respects_draggable_gate`, `TestAppHasSkipToMainContentLink::test_app_has_skip_link`, `TestAppAnnouncesRecordingStartStopWithAriaLive::test_app_has_aria_live`, `TestSidebarHasAriaCurrentPage::test_sidebar_has_aria_current`, `TestHistorySearchHasClearButton::test_history_has_clear_button` rewritten. The 54 remaining include CSS-string checks, type-only assertions, and many behavioral Python tests. |
| `tests/test_consent_and_privacy.py` | 40 | 2 | 2 | 38 | `TestAboutPageHasPrivacyDisclosure::test_about_page_has_privacy_section` and `TestAboutAndSettingsShowVoiceBiometricConsent::test_settings_has_all_consent_toggles_consolidated` rewritten. The 38 remaining are mostly Python-behavioral (Config dataclass, CloudEngine, TranscriptionEngine) — not TS-string tests. |
| `tests/test_feature_hardening_regressions.py` | 61 | 3 | 3 | 58 | `TestHomeRegistersSingleTranscriptionFinalListener::test_only_one_transcription_final_listener`, `TestRecordingStateEnumHasSixBackendStates::test_only_six_states` + `test_dead_states_removed` rewritten. The 58 remaining are mostly Python-behavioral (TCP auth, GPU release, RMS callback, offline mode) — not TS-string tests. |
| `tests/test_electron_ipc_and_build.py` | 90 | 0 | 0 | 90 | No rewrites done — most tests assert on Python source, `package.json`, `electron-builder.yml`, `.github/workflows/build.yml`, or `voice-typer.spec` (build infrastructure, not renderer behavior). The TS-string tests in this file (e.g. `TestElectronExposesDataExportHandlers`, `TestAllowlistCorrectness`, `TestRestartRequestRemoved`, `TestTypeScriptNonNullAssertions`) target `main/index.ts` / `preload/index.ts` which require an Electron runtime to test behaviorally — out of scope for vitest (which runs in jsdom). Tech debt: deferred to a future Electron-focused test pass. |
| **TOTAL** | **273** | **21** | **21** | **252** | (Note: the directive said "87 TS-string tests across 5 files"; the actual count is 273 total tests, of which ~87 are TS-string. 21 of those 87 are rewritten as vitest in this pass.) |

## Rewritten Tests

The 21 Python tests below are skipped via `@pytest.mark.skip(reason=...)`
with a pointer to the corresponding vitest file. They are NOT deleted —
they remain as a fallback until CI verifies the vitest versions pass on
all platforms.

| # | Python test | Vitest file |
| --- | --- | --- |
| 1 | `test_hotkeys.py::TestHotkeyUtilsFormatLabel::test_formats_single_key` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/hotkey-utils-behavior.test.ts` |
| 2 | `test_hotkeys.py::TestHotkeyUtilsFormatLabel::test_formats_combo` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/hotkey-utils-behavior.test.ts` |
| 3 | `test_hotkeys.py::TestHotkeyUtilsValidate::test_validate_rejects_empty` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/hotkey-utils-behavior.test.ts` |
| 4 | `test_hotkeys.py::TestHotkeyUtilsValidate::test_validate_rejects_modifiers_only_in_combo` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/hotkey-utils-behavior.test.ts` |
| 5 | `test_hotkeys.py::TestHotkeyUtilsValidate::test_validate_rejects_multi_key_in_single_mode` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/hotkey-utils-behavior.test.ts` |
| 6 | `test_hotkeys.py::TestDictationKeySupportsExpandedPresets::test_single_key_presets_include_beyond_f12` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/hotkey-utils-behavior.test.ts` |
| 7 | `test_hotkeys.py::TestRepasteKeySettingUsesHotkeyPicker::test_settings_imports_hotkey_picker` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/RecordingSettings-hotkey-picker.test.tsx` |
| 8 | `test_hotkeys.py::TestRepasteKeySettingUsesHotkeyPicker::test_repaste_key_uses_hotkey_picker_combo_mode` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/RecordingSettings-hotkey-picker.test.tsx` |
| 9 | `test_ux_components.py::TestAppHasHelpOverlayForShortcuts::test_app_has_question_mark_keydown_handler` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/App-help-overlay.test.tsx` |
| 10 | `test_ux_components.py::TestAppHasHelpOverlayForShortcuts::test_help_overlay_closes_on_escape` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/App-help-overlay.test.tsx` |
| 11 | `test_ux_components.py::TestBubbleSupportsKeyboardArrowMove::test_bubble_calls_move_by` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/Bubble-keyboard-move.test.tsx` |
| 12 | `test_ux_components.py::TestBubbleSupportsKeyboardArrowMove::test_bubble_respects_draggable_gate` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/Bubble-keyboard-move.test.tsx` |
| 13 | `test_ux_components.py::TestSidebarHasAriaCurrentPage::test_sidebar_has_aria_current` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/Sidebar-aria-current.test.tsx` |
| 14 | `test_ux_components.py::TestAppHasSkipToMainContentLink::test_app_has_skip_link` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/App-a11y.test.tsx` |
| 15 | `test_ux_components.py::TestAppAnnouncesRecordingStartStopWithAriaLive::test_app_has_aria_live` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/App-a11y.test.tsx` |
| 16 | `test_ux_components.py::TestHistorySearchHasClearButton::test_history_has_clear_button` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/SearchField-clear-button.test.tsx` |
| 17 | `test_consent_and_privacy.py::TestAboutPageHasPrivacyDisclosure::test_about_page_has_privacy_section` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/About-privacy.test.tsx` |
| 18 | `test_consent_and_privacy.py::TestAboutAndSettingsShowVoiceBiometricConsent::test_settings_has_all_consent_toggles_consolidated` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/PrivacySettings-consent.test.tsx` |
| 19 | `test_feature_hardening_regressions.py::TestHomeRegistersSingleTranscriptionFinalListener::test_only_one_transcription_final_listener` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/Home-transcription-final.test.tsx` |
| 20 | `test_feature_hardening_regressions.py::TestRecordingStateEnumHasSixBackendStates::test_only_six_states` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/RecordingState-types.test.ts` |
| 21 | `test_feature_hardening_regressions.py::TestRecordingStateEnumHasSixBackendStates::test_dead_states_removed` | `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/RecordingState-types.test.ts` |

## Vitest Files Created

All vitest files live under
`voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/`:

| File | Test count | Python tests covered |
| --- | --- | --- |
| `hotkey-utils-behavior.test.ts` | 11 | 6 (formatHotkeyLabel, validateHotkey, KEY_CODE_TO_PYNPUT) |
| `Bubble-keyboard-move.test.tsx` | 7 | 2 (moveBy on arrow keys, draggable gate) |
| `App-help-overlay.test.tsx` | 4 | 2 (? opens overlay, Escape closes) |
| `App-a11y.test.tsx` | 3 | 2 (skip link, aria-live region) |
| `Sidebar-aria-current.test.tsx` | 4 | 1 (aria-current="page" on active nav button) |
| `SearchField-clear-button.test.tsx` | 4 | 1 (clear button appears + clears value) |
| `About-privacy.test.tsx` | 2 | 1 (privacy disclosure headings render) |
| `PrivacySettings-consent.test.tsx` | 6 | 1 (six consent toggles each wired to updateConfig) |
| `Home-transcription-final.test.tsx` | 3 | 1 (exactly one usePythonEvent listener for "transcription_final") |
| `RecordingState-types.test.ts` | 3 | 2 (6-state union, no dead states — type-level compile check) |
| `RecordingSettings-hotkey-picker.test.tsx` | 6 | 2 (HotkeyPicker rendered for dictation + repaste keys with mode="combo") |
| **TOTAL** | **53 vitest tests** | **21 Python tests rewritten** |

## Pattern for Future Contributors

To rewrite a remaining TS-string Python test as a vitest:

### 1. Identify the TS source file & the invariant being protected

Read the Python test and identify:
- Which TS file is being read (`_read("path/to/file.tsx")`).
- What string is being asserted (`"X" in src`).
- What invariant that string is meant to protect (e.g. "the help
  overlay opens when `?` is pressed", "the privacy section renders
  all 6 disclosure headings", "the RecordingState union has exactly
  6 values").

The invariant is the contract the test is **supposed** to enforce.
The string check is just a proxy for that contract — your job is to
test the contract directly.

### 2. Decide on the test strategy

| Invariant type | Strategy |
| --- | --- |
| **Function returns a value** (e.g. `formatHotkeyLabel`, `validateHotkey`) | Call the function, assert on the return value. |
| **Component renders specific DOM** (e.g. aria-current, skip link, clear button) | Mount the component with `@testing-library/react`, query the DOM via `screen.getByRole` / `getByText` / `querySelector`, assert on the rendered structure. |
| **Component reacts to user events** (e.g. `?` opens overlay, Escape closes, Switch toggle fires callback) | Mount the component, `fireEvent` / `dispatchEvent` the event, assert on the resulting DOM or callback mock. |
| **Hook subscribes to a specific event** (e.g. `usePythonEvent("transcription_final")`) | Mock the hook (`vi.mock("@/hooks/usePython", ...)`), mount the consumer, assert the mock was called with the right event name the right number of times. |
| **TS type union has specific members** (e.g. `RecordingState`) | Use a TypeScript type-level helper (e.g. `IsExact<A, B>`, `X extends Union ? true : false`) bound to a runtime `const`. The compile-time const fails to compile if the union drifts — caught by `tsc --noEmit` in CI. The vitest `it()` block is a tautological runtime assert that ensures the file actually runs. |
| **Source code lacks an anti-pattern** (e.g. no `result.path!` non-null assertion) | Mount the component, trigger the code path that would crash if the anti-pattern were present, assert no crash. (Harder — may require deep mocking. Consider leaving as a Python source-string check if the rewrite cost is too high.) |

### 3. Mock the dependencies

Use `vi.mock` for any module that:
- Talks to the backend (`@/hooks/usePython`).
- Touches the filesystem / localStorage / network.
- Pulls in a heavy dependency tree that would slow the test down
  (`@hugeicons/react`, `sonner`, `next-themes`).

Use `vi.hoisted` for mock state that needs to be referenced inside
the `vi.mock` factory (because `vi.mock` factories are hoisted above
all top-level statements).

### 4. Assert on observable behavior, not source strings

| ❌ Don't | ✅ Do |
| --- | --- |
| `expect(src).toContain("aria-current")` | `expect(document.querySelector('button[aria-current="page"]')).toBeTruthy()` |
| `expect(src).toContain("moveBy")` | `expect(mockBubble.moveBy).toHaveBeenCalledTimes(1)` |
| `expect(src).toContain("Hotkey is empty")` | `expect(validateHotkey("", "single")).toMatch(/empty/)` |
| Count `usePythonEvent("transcription_final"` occurrences in source | `expect(mockPythonEvent.mock.calls.filter(a => a[0] === "transcription_final")).toHaveLength(1)` |

### 5. Skip the Python test (don't delete it)

Add `@pytest.mark.skip(reason="rewritten as vitest in <path> — remove this Python test once the vitest is verified on CI")` to the Python test. Keep the test body intact so it can be unsquashed if the vitest turns out to be wrong.

### 6. Update this doc

Append a row to the "Rewritten Tests" table above and bump the per-file stats.

## Known Limitations

- **Electron-only paths**: tests that assert on `src/main/index.ts` /
  `src/preload/index.ts` (e.g. `TestElectronExposesDataExportHandlers`,
  `TestAllowlistCorrectness`) are NOT candidates for vitest rewriting
   because vitest runs in jsdom (no Electron `ipcMain`, no preload
   bridge). These need either an Electron-focused integration test
   framework or a manual smoke test runbook. Deferred to a future round.
- **CSS source checks**: tests like `TestCssHandlesPrefersReducedMotion`
  (which asserts on `index.css` source containing
  `prefers-reduced-motion`) could be rewritten by mounting a
  component that uses the CSS class and asserting the computed style
   — but jsdom doesn't actually compute CSS, so this requires a real
   browser environment. Deferred.
- **Type-only assertions**: tests like `TestOnNavigateTypedAsPageLiteralUnion`
  (which asserts that `onNavigate?: (page: Page) => void` appears in
  source) are essentially type-level checks already enforced by
  `tsc --noEmit` — they could be rewritten as compile-time
  `type _Check = ...` asserts in a vitest file, but the value-add is
  marginal vs. the existing TypeScript compiler guarantee.

## Validation

Run from the repo root:

```bash
# Vitest — all 53 new tests must pass
cd voice_typer/client && npx vitest run src/renderer/src/__tests__/rw0-rewrite/

# Typecheck — must be 0 errors (pre-existing ipc-types.test.ts error
# is owned by another sub-agent, NOT this rewrite)
cd voice_typer/client && npx tsc --noEmit -p tsconfig.web.json

# Lint — rw0-rewrite/ must be clean (pre-existing errors in
# main/index.ts, KeyringStatusBadge.tsx, ModelSettingsSection.tsx,
# Models.tsx, config.ts are owned by other sub-agents)
cd voice_typer/client && npm run lint

# Python — 252 passed, 21 skipped (the 21 skipped are the rewritten
# tests; their @pytest.mark.skip reason points to the vitest file)
cd /home/z/my-project/voice-typer && python -m pytest \
  tests/test_hotkeys.py tests/test_ux_components.py \
  tests/test_consent_and_privacy.py \
  tests/test_feature_hardening_regressions.py \
  tests/test_electron_ipc_and_build.py -q --no-header
```
