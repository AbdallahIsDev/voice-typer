"""MIG-1.6 Phase 0-M Gate Check 5 — `enigo` paste validation (macOS).

Validates the Rust `paste_text` Tauri command
(``src-tauri/src/commands/sidecar_cmds.rs:78-136``) — the macOS paste
path documented in:

- ADR-0020 §6.2  — paste strategy (short via ``enigo.text()``; long via
  ``tauri-plugin-clipboard-manager`` + ``Cmd+V`` on macOS). The migration
  table row for "Clipboard paste — macOS" explicitly notes *"macOS paste
  is generally simpler (no UIPI)"* — there is no Windows-style
  ``AttachThreadInput`` / ``SetForegroundWindow`` focus-restore dance
  on macOS.
- ADR-0020 §6.3  — Windows-only focus-restore dance (does NOT apply to
  macOS — UIPI is a Windows integrity-level concept).
- ``docs/migration/macos-validation-runbook.md`` §6.3 — host validation
  procedure for short + long paste (gate point 4, BOTH arches).

Because we cannot compile Rust in this sandbox, the tests use
**source inspection** (read the ``.rs`` file and assert on its content)
plus a **behavioural simulation** (a Python re-implementation of the
paste algorithm driven by ``MagicMock`` for ``enigo::Enigo`` and
``tauri_plugin_clipboard_manager::ClipboardExt``).  No real key
injection happens — the mocks record call order + arguments so we can
verify the contract:

- short text  (< ``PASTE_SHORT_THRESHOLD``) → ``enigo.text()`` only
- long text   (≥ ``PASTE_SHORT_THRESHOLD``) → ``clipboard.write_text()`` +
  ``enigo.key(Meta, Press)`` + ``enigo.key('v', Click)`` +
  ``enigo.key(Meta, Release)`` — macOS uses ``Cmd`` (``Key::Meta``),
  NOT ``Ctrl`` (``Key::Control``)
- empty text  → no-op (returns ``Ok(())`` immediately)
- macOS path uses ``Key::Meta`` (not ``Key::Control`` — that is Windows/Linux)
- every ``enigo``/``clipboard`` call propagates errors via ``.map_err(...)?``
  so they surface as Rust errors (not silently swallowed)
- macOS has NO UIPI + NO focus-restore dance (simpler than Windows —
  ``enigo`` injects into whatever window currently has keyboard focus,
  which on macOS is always the foreground app because there is no
  integrity-level boundary blocking ``CGEvent`` injection)

VALIDATE ON MACOS HOST:
1. Launch Voice Typer + open TextEdit
2. Dictate a short phrase (< 300 chars) — verify text appears in TextEdit via enigo.text()
3. Dictate a long phrase (≥ 300 chars) — verify text appears in TextEdit via clipboard + Cmd+V
4. Check log for:
   - "[PASTE] injected N chars via enigo" (short)
   - "[PASTE] injected N chars via clipboard + Ctrl/Cmd+V" (long)
Expected: text appears in TextEdit within 500ms; no characters dropped
(macOS paste is generally simpler than Windows — no UIPI, no focus-restore dance needed.)
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Path constants ──────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/tauri/mig16/<this> → repo root
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
UTIL_RS = REPO_ROOT / "src-tauri" / "src" / "util.rs"
ADR_0020_MD = REPO_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
MACOS_RUNBOOK_MD = REPO_ROOT / "docs" / "migration" / "macos-validation-runbook.md"


# ─── Source-reading fixtures ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def sidecar_cmds_src() -> str:
    """Read the ``sidecar_cmds.rs`` source file once per module."""
    assert SIDECAR_CMDS_RS.is_file(), f"missing Rust source: {SIDECAR_CMDS_RS}"
    return SIDECAR_CMDS_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def util_src() -> str:
    """Read the ``util.rs`` source file once per module."""
    assert UTIL_RS.is_file(), f"missing Rust source: {UTIL_RS}"
    return UTIL_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def adr_0020_src() -> str:
    """Read ADR-0020 markdown once per module."""
    assert ADR_0020_MD.is_file(), f"missing ADR: {ADR_0020_MD}"
    return ADR_0020_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def macos_runbook_src() -> str:
    """Read the macOS validation runbook markdown once per module."""
    assert MACOS_RUNBOOK_MD.is_file(), f"missing runbook: {MACOS_RUNBOOK_MD}"
    return MACOS_RUNBOOK_MD.read_text(encoding="utf-8")


# Extract the ``paste_text`` body from the Rust source (lines 78-136).
# We slice on the function signature + closing brace so the slice stays
# stable across minor formatting edits.
def _slice_paste_text(src: str) -> str:
    """Return just the ``paste_text`` function body from ``sidecar_cmds.rs``.

    The function is delimited by ``pub async fn paste_text(`` and the
    next ``pub async fn`` (or end of file).  We return the entire slice
    including the docstring + attribute + signature.
    """
    start = src.index("/// ADR-0020 §6.2: paste transcribed text")
    # find the end: next "// ───" section header after the function body
    end_match = re.search(r"\n// ─── Tauri command: cooperative shutdown", src[start:])
    end = start + end_match.start() if end_match else len(src)
    return src[start:end]


# ─── 1. PASTE_SHORT_THRESHOLD value ──────────────────────────────────────


def test_paste_short_threshold_is_300(util_src: str) -> None:
    """ADR-0020 §6.2: short/long paste threshold is 300 chars.

    Short text (< 300 chars) is injected via ``enigo.text()`` (IME-safe);
    long text (≥ 300 chars) is copied via ``tauri-plugin-clipboard-manager``
    then ``Cmd+V`` is sent via ``enigo`` on macOS.  A value other than 300
    would either over-use the IME path (drop chars on long paste) or
    over-write the user's clipboard (annoying for short paste).  Pin the
    value to 300 to catch accidental edits.
    """
    # Match: `pub(crate) const PASTE_SHORT_THRESHOLD: usize = 300;`
    match = re.search(
        r"pub\(crate\)\s+const\s+PASTE_SHORT_THRESHOLD\s*:\s*usize\s*=\s*(\d+)\s*;",
        util_src,
    )
    assert match is not None, (
        "PASTE_SHORT_THRESHOLD constant not found in util.rs — did the file move or rename the constant?"
    )
    value = int(match.group(1))
    assert value == 300, (
        f"PASTE_SHORT_THRESHOLD must be 300 (ADR-0020 §6.2: '< ~300 chars' is the short/long boundary). Got {value}."
    )

    # Also verify the sidecar_cmds.rs paste_text docstring anchors the
    # constant to the ADR's '~300 chars' boundary — guards against an
    # edit that changes the constant without updating the docstring.
    sidecar_src = SIDECAR_CMDS_RS.read_text(encoding="utf-8")
    assert re.search(r"<\s*~?\s*300\s*chars", sidecar_src), (
        "paste_text docstring in sidecar_cmds.rs must reference the '< ~300 chars' "
        "boundary from ADR-0020 §6.2 so the PASTE_SHORT_THRESHOLD constant stays "
        "anchored to the spec."
    )


# ─── 2. Short text path: enigo.text() only ───────────────────────────────


def test_short_text_path_uses_enigo_text_only(sidecar_cmds_src: str) -> None:
    """Short text (< threshold) calls ``enigo.text()`` only.

    The short-text branch must:
    - call ``enigo.text(&text)`` (NOT ``enigo.key`` for each character —
      that breaks IME / dead keys / non-English layouts).
    - NOT call ``app.clipboard().write_text(...)`` (no clipboard side-effect).
    - NOT call ``enigo.key(..., Press|Click|Release)`` (no Cmd+V).

    On macOS the short path uses ``CGEventCreateKeyboardEvent`` +
    ``CGEventKeyboardSetUnicodeString`` under the hood (per ADR-0020 §6.2
    macOS row) and requires Accessibility permission (see
    macos-validation-runbook.md §6.3 "Required permission").
    """
    body = _slice_paste_text(sidecar_cmds_src)

    # The short-text branch is delimited by `if text.chars().count() < PASTE_SHORT_THRESHOLD {`
    # ... `} else {`.  Slice it out.
    short_match = re.search(
        r"if\s+text\.chars\(\)\.count\(\)\s*<\s*PASTE_SHORT_THRESHOLD\s*\{(.*?)\}\s*else\s*\{",
        body,
        re.DOTALL,
    )
    assert short_match is not None, (
        "Could not locate the short-text branch (`if text.chars().count() < "
        "PASTE_SHORT_THRESHOLD { ... } else {`). Did the control flow change?"
    )
    short_branch = short_match.group(1)

    # Must call enigo.text(&text).
    assert "enigo.text" in short_branch, (
        "Short-text branch must call `enigo.text(&text)` (IME-safe Unicode "
        "injection per ADR-0020 §6.2). Got branch:\n" + short_branch
    )

    # Must NOT touch the clipboard.
    assert "write_text" not in short_branch, (
        "Short-text branch must NOT call `clipboard.write_text` — short paste "
        "must not replace the user's clipboard contents (ADR-0020 §6.2)."
    )

    # Must NOT send Ctrl+V / Cmd+V (no key events).
    assert "enigo.key" not in short_branch, (
        "Short-text branch must NOT call `enigo.key(...)` — short text is "
        "injected via `enigo.text()` only (simulating discrete key events "
        "breaks IME / dead keys / non-English layouts, per ADR-0020 §6.2)."
    )

    # Must log the short-path message documented in the macOS runbook §6.3.
    assert "[PASTE] injected" in short_branch and "via enigo" in short_branch, (
        "Short-text branch must log `[PASTE] injected N chars via enigo` "
        "(macos-validation-runbook.md §6.3 verify-in-logs step)."
    )


# ─── 3. Long text path: clipboard + Cmd+V ────────────────────────────────


def test_long_text_path_uses_clipboard_plus_cmd_v(sidecar_cmds_src: str) -> None:
    """Long text (≥ threshold) calls ``clipboard.write_text()`` + 3 ``enigo.key()`` calls.

    Order matters: clipboard write MUST happen before the key events so
    the Cmd+V pastes the long text (not the previous clipboard contents).
    The 3 key calls are (on macOS):

    1. ``enigo.key(mod_key, Direction::Press)``   — Cmd (Meta) down
    2. ``enigo.key(Key::Unicode('v'), Direction::Click)`` — V press+release
    3. ``enigo.key(mod_key, Direction::Release)`` — Cmd (Meta) up

    ``mod_key`` is selected at compile time via ``cfg!(target_os = "macos")``
    to be ``Key::Meta`` on macOS (see test 5).
    """
    body = _slice_paste_text(sidecar_cmds_src)

    # The long-text branch is everything after `} else {` up to the
    # function's closing `Ok(())` + `}`.
    else_match = re.search(r"\}\s*else\s*\{(.*?)(\n    Ok\(\(\)\)\n\})\s*$", body, re.DOTALL)
    assert else_match is not None, (
        "Could not locate the long-text branch (`} else { ... Ok(()) }`). Did the function structure change?"
    )
    long_branch = else_match.group(1)

    # 1. Clipboard write — must come BEFORE the enigo key calls.
    cb_match = re.search(r"app\.clipboard\(\)\s*\.write_text\([^)]+\)", long_branch)
    assert cb_match is not None, (
        "Long-text branch must call `app.clipboard().write_text(text.clone())` "
        "(tauri-plugin-clipboard-manager) per ADR-0020 §6.2."
    )
    cb_end = cb_match.end()

    # 2. mod_key Press — must come AFTER clipboard write.
    press_match = re.search(r"enigo\.key\(\s*mod_key\s*,\s*enigo::Direction::Press\s*\)", long_branch)
    assert press_match is not None, (
        "Long-text branch must call `enigo.key(mod_key, enigo::Direction::Press)` (Cmd/Ctrl down) per ADR-0020 §6.2."
    )
    assert press_match.start() > cb_end, (
        "Long-text branch: `enigo.key(mod_key, Press)` must come AFTER "
        "`app.clipboard().write_text(...)` — otherwise Cmd+V would paste "
        "the stale clipboard contents, not the new long text."
    )

    # 3. 'v' Click — must come AFTER mod_key Press.
    v_match = re.search(
        r"enigo\.key\(\s*Key::Unicode\('v'\)\s*,\s*enigo::Direction::Click\s*\)",
        long_branch,
    )
    assert v_match is not None, (
        "Long-text branch must call `enigo.key(Key::Unicode('v'), "
        "enigo::Direction::Click)` (the V keystroke) per ADR-0020 §6.2."
    )
    assert v_match.start() > press_match.end(), (
        "Long-text branch: `enigo.key('v', Click)` must come AFTER "
        "`enigo.key(mod_key, Press)` so the modifier is held when V is pressed."
    )

    # 4. mod_key Release — must come AFTER 'v' Click.
    release_match = re.search(
        r"enigo\.key\(\s*mod_key\s*,\s*enigo::Direction::Release\s*\)",
        long_branch,
    )
    assert release_match is not None, (
        "Long-text branch must call `enigo.key(mod_key, enigo::Direction::Release)` "
        "(Cmd/Ctrl up) per ADR-0020 §6.2 — otherwise the modifier stays held "
        "and the next user keystroke becomes Cmd+<key> on macOS."
    )
    assert release_match.start() > v_match.end(), (
        "Long-text branch: `enigo.key(mod_key, Release)` must come AFTER "
        "`enigo.key('v', Click)` so the modifier is released only after V."
    )

    # 5. Log message matches the runbook §6.3 expected format.
    assert "[PASTE] injected" in long_branch and "via clipboard + Ctrl/Cmd+V" in long_branch, (
        "Long-text branch must log `[PASTE] injected N chars via clipboard + "
        "Ctrl/Cmd+V` (macos-validation-runbook.md §6.3 verify-in-logs step)."
    )


# ─── 4. Empty text is a no-op ────────────────────────────────────────────


def test_empty_text_is_no_op(sidecar_cmds_src: str) -> None:
    """Empty text returns ``Ok(())`` immediately — no enigo, no clipboard.

    A no-op early-return guards against:
    - enigo init failure on an empty payload (would log noise).
    - Clipboard being clobbered with an empty string (would erase the
      user's current clipboard contents for no benefit).
    """
    body = _slice_paste_text(sidecar_cmds_src)

    # The early-return must be the FIRST statement after `let text = args.text;`.
    # Match: `let text = args.text;` then immediately `if text.is_empty() { return Ok(()); }`.
    early_return = re.search(
        r"let\s+text\s*=\s*args\.text\s*;\s*\n\s*if\s+text\.is_empty\(\)\s*\{\s*\n\s*return\s+Ok\(\(\)\)\s*;\s*\n\s*\}",
        body,
    )
    assert early_return is not None, (
        "paste_text must early-return `Ok(())` on empty text BEFORE any "
        "enigo / clipboard call. Pattern `let text = args.text; if text.is_empty() "
        "{ return Ok(()); }` not found at the expected position."
    )

    # The early-return must come BEFORE the first `use enigo::` import.
    use_enigo_pos = body.index("use enigo::")
    assert early_return.end() < use_enigo_pos, (
        "Empty-text early-return must come BEFORE the `use enigo::...` import "
        "so empty payloads don't pay the enigo init cost (and can't trigger "
        "an enigo init error)."
    )


# ─── 5. macOS path uses Key::Meta (not Key::Control) ──────────────────────


def test_macos_path_uses_key_meta_not_control(sidecar_cmds_src: str) -> None:
    """macOS path selects ``Key::Meta`` (Cmd); Windows/Linux path selects ``Key::Control``.

    The Rust code uses ``cfg!(target_os = "macos")`` to pick the modifier
    at compile time.  On macOS the modifier MUST be ``Key::Meta`` (Cmd+V),
    NOT ``Key::Control`` (which on macOS is the Control key — Cmd+V is
    the universal paste shortcut on macOS, Ctrl+V is not).  This test
    focuses on the macOS branch (``Key::Meta``); the Windows/Linux
    branch is covered by ``test_enigo_paste_windows.py``.
    """
    body = _slice_paste_text(sidecar_cmds_src)

    mod_match = re.search(
        r"let\s+mod_key\s*=\s*if\s+cfg!\(target_os\s*=\s*\"macos\"\)\s*\{\s*"
        r"Key::Meta\s*\}\s*else\s*\{\s*Key::Control\s*\}\s*;",
        body,
    )
    assert mod_match is not None, (
        "Long-text branch must select `mod_key` via `if cfg!(target_os = "
        '"macos") { Key::Meta } else { Key::Control }`. Pattern not found — '
        "did the modifier-selection logic change?"
    )

    # Sanity: the macOS branch (if-branch) must use Meta, NOT Control.
    # The regex above already enforces this, but assert explicitly for
    # documentation + a clearer failure message.
    assert "Key::Meta" in mod_match.group(0), "macOS path must use `Key::Meta` (Cmd+V). Got: " + mod_match.group(0)
    # The `if cfg!(target_os = "macos")` branch must NOT reference Control.
    if_branch = mod_match.group(0).split("else")[0]
    assert "Key::Control" not in if_branch, (
        'macOS path (`if cfg!(target_os = "macos")` branch) must NOT use '
        "`Key::Control` — on macOS the paste shortcut is Cmd+V (Key::Meta), "
        "not Ctrl+V. Got: " + if_branch
    )


# ─── 6. enigo errors are surfaced as Rust errors ─────────────────────────


def test_enigo_errors_surfaced_as_rust_errors(sidecar_cmds_src: str) -> None:
    """Every ``enigo`` / ``clipboard`` call propagates errors via ``.map_err(...)?``.

    ADR-0020 §6.2 + NEW-IPC-107: errors from the paste path MUST surface
    as Rust errors (``Result<(), String>``) so the webview's ``invoke()``
    rejects.  Silently swallowing an enigo error would leave the user
    with no transcription paste and no error message — a no-data-loss
    guarantee violation.

    On macOS, the most common enigo error is "Accessibility permission
    not granted" (``CGEvent`` API requires the app to be in the
    Accessibility TCC list — see macos-validation-runbook.md §6.3
    "Required permission").  This must surface as a Rust error so the
    UI can show a "grant Accessibility permission" prompt instead of
    silently failing to paste.
    """
    body = _slice_paste_text(sidecar_cmds_src)

    # Every `enigo.X(...)` or `app.clipboard().write_text(...)` call
    # must be immediately followed by `.map_err(|e| format!(...))?`.
    # We list each expected call site + the expected error-prefix string.
    expected_call_sites = [
        # (call substring, error-message-prefix substring)
        ("Enigo::new(&Settings::default())", "enigo init failed"),
        ("enigo.text(&text)", "enigo.text failed"),
        (".write_text(text.clone())", "clipboard write failed"),
        ("enigo.key(mod_key, enigo::Direction::Press)", "enigo mod press failed"),
        ("enigo.key(Key::Unicode('v'), enigo::Direction::Click)", "enigo v click failed"),
        ("enigo.key(mod_key, enigo::Direction::Release)", "enigo mod release failed"),
    ]

    for call_substr, err_prefix in expected_call_sites:
        # Find the call site.
        idx = body.find(call_substr)
        assert idx != -1, (
            f"Expected call site `{call_substr}` not found in paste_text body. Did the implementation change?"
        )
        # The next ~200 chars after the call must contain `.map_err(|e| format!(...))`
        # with the expected error prefix.
        tail = body[idx + len(call_substr) : idx + len(call_substr) + 200]
        # Permit any whitespace/newlines between the call and `.map_err`.
        map_err_pattern = re.compile(
            r"\s*\.map_err\(\s*\|e\|\s*format!\(\s*\"([^\"]+)\"",
            re.DOTALL,
        )
        m = map_err_pattern.match(tail)
        assert m is not None, (
            f"Call `{call_substr}` must be immediately followed by "
            f"`.map_err(|e| format!(...))?` to surface the error as a Rust "
            f"String. Tail after call:\n{tail!r}"
        )
        actual_prefix = m.group(1)
        assert actual_prefix.startswith(err_prefix), (
            f"Call `{call_substr}` must surface its error with the prefix "
            f"`{err_prefix}` (so the UI can pattern-match on the cause). "
            f"Got prefix: `{actual_prefix}`."
        )
        # And there must be a `?` after the .map_err(...) so the error
        # propagates (not just .map_err alone — that would discard the
        # Result and silently continue).
        assert "?;" in tail or re.search(r"\?\s*;", tail), (
            f"Call `{call_substr}` must end with `?` (the `?` operator) so "
            f"the error propagates to the Tauri command return value. "
            f"Tail after call:\n{tail!r}"
        )


# ─── 7. macOS paste is simpler than Windows (no UIPI, no focus-restore) ───


def test_macos_paste_simpler_than_windows_no_uipi(
    sidecar_cmds_src: str, adr_0020_src: str, macos_runbook_src: str
) -> None:
    """ADR-0020 §6.2: macOS paste is "generally simpler (no UIPI)".

    Unlike Windows (§6.3 — focus-restore dance with ``AttachThreadInput`` +
    ``SetForegroundWindow`` + UIPI fallback to clipboard + toast), macOS
    has NO UIPI concept and NO focus-restore dance in the paste path.
    ``enigo`` injects directly into the foreground app via ``CGEvent``,
    which always succeeds for standard-user targets (the only TCC
    requirement is Accessibility permission — see macos-validation-runbook.md
    §6.3 "Required permission").

    This test verifies:
    1. ADR-0020 §6.2 migration table explicitly states "macOS paste is
       generally simpler (no UIPI)".
    2. The Rust ``paste_text`` correctly isolates any Windows-only
       focus-restore calls (``AttachThreadInput`` / ``SetForegroundWindow``
       / ``GetForegroundWindow``) behind ``#[cfg(target_os = "windows")]``
       gates so they are EXCLUDED from the macOS build. The presence of
       these symbols in the source is fine (they're Windows-only); what
       matters is that they cannot leak into the macOS compile path.
       Before MIG-1.5 added the focus-restore dance (ADR §6.3), these
       symbols were entirely absent; now they exist but MUST be gated.
    3. The macOS validation runbook §6.3 does NOT document any
       focus-restore step for the macOS host validation procedure
       (it only documents Accessibility permission + short/long paste).
    """
    # 1. ADR-0020 §6.2 must explicitly state macOS paste is simpler (no UIPI).
    assert "no UIPI" in adr_0020_src, (
        "ADR-0020 §6.2 migration table must state 'macOS paste is generally simpler (no UIPI)' for the macOS paste row."
    )
    # And the macOS paste row must reference Cmd+V (not Ctrl+V).
    assert re.search(r"Clipboard paste — macOS.*Cmd\+V", adr_0020_src, re.DOTALL), (
        "ADR-0020 §6.2 migration table must have a 'Clipboard paste — macOS' "
        "row that references Cmd+V (the macOS paste shortcut)."
    )

    # 2. The Rust paste_text MUST gate any Windows-only focus-restore calls
    # behind `#[cfg(target_os = "windows")]` so they are excluded from the
    # macOS build. The presence of these symbols is fine (they're Windows-only
    # per ADR §6.3); what matters is that they cannot leak into the macOS
    # compile path. We assert:
    #   (a) The body contains at least one `#[cfg(target_os = "windows")]` gate.
    #   (b) Every Win32 focus API that is CALLED (not just mentioned in a
    #       comment) appears in a line that also contains `unsafe` OR is
    #       part of a `use` import statement. Both forms are gated by the
    #       enclosing `#[cfg(target_os = "windows")]` attribute.
    # Source-grepping cannot fully verify Rust cfg-gating semantics (that
    # requires parsing the AST). The authoritative validation is
    # `cargo check` on a macOS host — see VALIDATE ON MACOS HOST below.
    body = _slice_paste_text(sidecar_cmds_src)
    win32_focus_apis = ["AttachThreadInput", "SetForegroundWindow", "GetForegroundWindow"]
    gate_matches = list(re.finditer(r'#\[cfg\(target_os\s*=\s*"windows"\)\]', body))
    assert gate_matches, (
        "Rust `paste_text` must contain at least one "
        '`#[cfg(target_os = "windows")]` gate to isolate the Win32 '
        "focus-restore code from the macOS build (ADR §6.3 + §6.2)."
    )
    # For each Win32 focus API, verify it is referenced in a `use` import
    # or called inside an `unsafe` block (both forms are cfg-gated). This
    # is a conservative check — a stronger check would parse the Rust AST.
    for api in win32_focus_apis:
        if api in body:
            # Look for either `use ...api...` or `unsafe { ...api...(` patterns.
            api_in_use = re.search(r"use\s+[^;]*\b" + re.escape(api) + r"\b", body)
            api_in_unsafe_call = re.search(r"unsafe\s*\{[^}]*\b" + re.escape(api) + r"\s*\(", body, re.DOTALL)
            assert api_in_use or api_in_unsafe_call, (
                f"Rust `paste_text` references Win32 API `{api}` but it is "
                f"not in a `use` import or an `unsafe {{ ... }}` call. "
                f"Either the API is mentioned only in a comment (OK) or it "
                f"is being called without proper unsafe/gating (BAD). "
                f"VALIDATE ON MACOS HOST: `cargo check --manifest-path "
                f"src-tauri/Cargo.toml` must succeed on a macOS host (the "
                f"gated Win32 code is excluded)."
            )

    # 3. macOS runbook §6.3 must NOT document a focus-restore step.
    # Slice out the §6.3 section (up to the next "### Step 6.4" header).
    section_63_match = re.search(
        r"### Step 6\.3 — `enigo` paste.*?(?=\n### Step 6\.4|\Z)",
        macos_runbook_src,
        re.DOTALL,
    )
    assert section_63_match is not None, (
        "macos-validation-runbook.md must have a §6.3 'enigo paste' section (gate point 4)."
    )
    section_63 = section_63_match.group(0)
    for api in win32_focus_apis:
        assert api not in section_63, (
            f"macOS runbook §6.3 must NOT document the Win32 focus-restore "
            f"API `{api}` — that is Windows-only (ADR-0020 §6.3) and macOS "
            f"has no focus-restore dance (no UIPI). Section:\n{section_63}"
        )
    # §6.3 must NOT mention UIPI (a Windows integrity-level concept).
    assert "UIPI" not in section_63, (
        "macOS runbook §6.3 must NOT mention UIPI — UIPI is a Windows-only "
        "integrity-level concept (ADR-0020 §6.3). macOS has no equivalent "
        "boundary blocking CGEvent injection."
    )
    # §6.3 MUST mention the Accessibility permission (the macOS TCC
    # requirement that replaces Windows UIPI as the paste gate).
    assert "Accessibility" in section_63, (
        "macOS runbook §6.3 must mention the Accessibility permission "
        "requirement (the macOS TCC equivalent of Windows UIPI gate — "
        "CGEvent API requires it)."
    )


# ─── 8. paste_text has the #[tauri::command] attribute ───────────────────


def test_paste_text_has_tauri_command_attribute(sidecar_cmds_src: str) -> None:
    """``paste_text`` is exposed as a Tauri command via ``#[tauri::command]``.

    Without the attribute, the function would be a plain Rust function
    and the React UI's ``invoke('paste_text', ...)`` would fail with
    "command not found" at runtime (Tauri v2 silently blocks unknown
    commands).
    """
    body = _slice_paste_text(sidecar_cmds_src)
    assert "#[tauri::command]" in body, (
        "paste_text must have the `#[tauri::command]` attribute so the "
        "React UI can invoke it via `invoke('paste_text', ...)`."
    )
    # The function signature must be `pub async fn paste_text(...) -> Result<(), String>`.
    # G4-H-01: an optional `window: tauri::Window` parameter is now
    # accepted (added by the main-window guard fix — the regex keeps
    # backwards-compat with the old two-arg signature too). The
    # trailing `,?` allows for a trailing comma after the last
    # parameter (Rustfmt style).
    sig_match = re.search(
        r"pub\s+async\s+fn\s+paste_text\s*\(\s*args:\s*PasteTextArgs\s*,\s*app:\s*tauri::AppHandle\s*(?:,\s*window:\s*tauri::Window\s*,?\s*)?\)\s*->\s*Result<\(\),\s*String>",
        body,
    )
    assert sig_match is not None, (
        "paste_text signature must be `pub async fn paste_text(args: "
        "PasteTextArgs, app: tauri::AppHandle[, window: tauri::Window]) "
        "-> Result<(), String>` so Tauri can deserialize the args + the "
        "React UI gets a promise that rejects on error."
    )


# ─── 9. Behavioural simulation: MagicMock-driven paste algorithm ────────


class _FakeEnigo:
    """Minimal Python stand-in for ``enigo::Enigo``.

    Records every call so the simulation test can assert on call order
    + arguments.  Mirrors the Rust API surface used by ``paste_text``:
    - ``Enigo::new(&Settings::default()) -> Result<Enigo, EnigoError>``
    - ``enigo.text(&str) -> Result<(), EnigoError>``
    - ``enigo.key(Key, Direction) -> Result<(), EnigoError>``
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    @classmethod
    def new(cls, _settings) -> _FakeEnigo:
        return cls()

    def text(self, s: str) -> None:
        self.calls.append(("text", (s,)))

    def key(self, key, direction) -> None:
        self.calls.append(("key", (key, direction)))


class _FakeClipboard:
    """Minimal Python stand-in for ``tauri_plugin_clipboard_manager::ClipboardExt``."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write_text(self, text: str) -> None:
        self.written.append(text)


# Sentinel values mirroring the Rust enums.
class _Key:
    Control = "Key::Control"
    Meta = "Key::Meta"
    def Unicode(self, ch):  # noqa: N802
        return f"Key::Unicode({ch!r})"


class _Direction:
    Press = "Press"
    Click = "Click"
    Release = "Release"


def _simulate_paste_text(
    text: str,
    *,
    platform: str = "macos",
    enigo_factory=_FakeEnigo,
    clipboard: _FakeClipboard | None = None,
    threshold: int = 300,
) -> tuple[_FakeEnigo | None, _FakeClipboard | None, list[str]]:
    """Python re-implementation of the Rust ``paste_text`` algorithm.

    Mirrors ``src-tauri/src/commands/sidecar_cmds.rs:95-136`` exactly:

    - empty text  → no-op, returns immediately
    - short text  → ``enigo.text(&text)`` only
    - long text   → ``clipboard.write_text(text)`` + 3 ``enigo.key()`` calls
    - Windows uses ``Key::Control``; macOS uses ``Key::Meta``

    Returns ``(enigo, clipboard, log_messages)`` for assertion.
    """
    logs: list[str] = []
    if text == "":
        return None, None, logs

    char_count = len(text)
    enigo: _FakeEnigo | None = None
    if char_count < threshold:
        enigo = enigo_factory.new(None)
        enigo.text(text)
        logs.append(f"[PASTE] injected {char_count} chars via enigo")
    else:
        if clipboard is None:
            clipboard = _FakeClipboard()
        clipboard.write_text(text)
        enigo = enigo_factory.new(None)
        mod_key = _Key.Meta if platform == "macos" else _Key.Control
        enigo.key(mod_key, _Direction.Press)
        enigo.key(_Key().Unicode("v"), _Direction.Click)
        enigo.key(mod_key, _Direction.Release)
        logs.append(f"[PASTE] injected {char_count} chars via clipboard + Ctrl/Cmd+V")
    return enigo, clipboard, logs


def test_simulate_empty_text_is_no_op() -> None:
    """Simulation: empty text triggers no enigo + no clipboard calls."""
    enigo, clipboard, logs = _simulate_paste_text("")
    assert enigo is None, "empty text must not init enigo"
    assert clipboard is None, "empty text must not init clipboard"
    assert logs == [], "empty text must not log a [PASTE] line"


def test_simulate_short_text_uses_enigo_text_only() -> None:
    """Simulation: short text (< 300) calls ``enigo.text()`` only — no clipboard, no key."""
    short_text = "hello world"  # 11 chars < 300
    enigo, clipboard, logs = _simulate_paste_text(short_text, platform="macos")

    assert enigo is not None
    assert clipboard is None, "short text must NOT touch the clipboard"
    # Only one call: text().
    assert enigo.calls == [("text", (short_text,))], (
        f"short-text enigo calls must be exactly one `text()` call; got: {enigo.calls}"
    )
    # Log message matches the runbook §6.3 expected format.
    assert logs == ["[PASTE] injected 11 chars via enigo"]


def test_simulate_long_text_uses_clipboard_plus_meta_v_in_order() -> None:
    """Simulation: long text (≥ 300) on macOS calls clipboard write + 3 key events in order.

    Order:
    1. ``clipboard.write_text(text)``  — clipboard populated BEFORE keys
    2. ``enigo.key(Meta, Press)``       — Cmd down
    3. ``enigo.key(Unicode('v'), Click)`` — V press+release
    4. ``enigo.key(Meta, Release)``      — Cmd up

    The modifier MUST be ``Key::Meta`` on macOS (Cmd+V), NOT
    ``Key::Control`` (Ctrl+V is not the macOS paste shortcut).
    """
    long_text = "x" * 300  # exactly 300 chars → ≥ threshold
    enigo, clipboard, logs = _simulate_paste_text(long_text, platform="macos")

    assert enigo is not None
    assert clipboard is not None, "long text MUST write to the clipboard"
    # Clipboard was written with the long text (BEFORE any key event).
    assert clipboard.written == [long_text], (
        f"clipboard.write_text must be called once with the long text; got: {clipboard.written}"
    )
    # 3 key events in order: (Meta, Press), (Unicode('v'), Click), (Meta, Release).
    assert enigo.calls == [
        ("key", (_Key.Meta, _Direction.Press)),
        ("key", ("Key::Unicode('v')", _Direction.Click)),
        ("key", (_Key.Meta, _Direction.Release)),
    ], (
        f"long-text macOS enigo calls must be 3 key events in Press/Click/Release "
        f"order with Key::Meta (Cmd+V). Got: {enigo.calls}"
    )
    # Log message matches the runbook §6.3 expected format.
    assert logs == ["[PASTE] injected 300 chars via clipboard + Ctrl/Cmd+V"]


def test_simulate_long_text_macos_uses_key_meta_not_control() -> None:
    """Simulation: long text on macOS uses ``Key::Meta`` (Cmd+V), not Control.

    This is the macOS-specific assertion: the modifier for the Cmd+V
    paste shortcut MUST be ``Key::Meta`` on macOS.  ``Key::Control`` on
    macOS would send Ctrl+V (not the paste shortcut — on macOS the
    paste shortcut is Cmd+V, and Ctrl+V is the "page down" key in
    most text editors).
    """
    long_text = "y" * 500
    enigo, _, _ = _simulate_paste_text(long_text, platform="macos")
    assert enigo is not None
    # First and third key calls must use Meta (not Control).
    assert enigo.calls[0] == ("key", (_Key.Meta, _Direction.Press)), (
        f"macOS long-text first key must be (Meta, Press); got: {enigo.calls[0]}"
    )
    assert enigo.calls[-1] == ("key", (_Key.Meta, _Direction.Release)), (
        f"macOS long-text last key must be (Meta, Release); got: {enigo.calls[-1]}"
    )
    # No Control key should appear anywhere in the macOS path.
    for _name, args in enigo.calls:
        assert _Key.Control not in args, (
            f"macOS path must NOT use Key::Control — Ctrl+V is not the "
            f"macOS paste shortcut (Cmd+V is). Got call: {(_name, args)}"
        )


def test_simulate_short_text_threshold_boundary() -> None:
    """Simulation: 299 chars → short path; 300 chars → long path.

    The boundary is `< PASTE_SHORT_THRESHOLD` (strict less-than), so:
    - 299 chars (< 300) → enigo.text()
    - 300 chars (≥ 300) → clipboard + Cmd+V (macOS)
    """
    enigo_299, clipboard_299, _ = _simulate_paste_text("a" * 299, platform="macos")
    assert clipboard_299 is None, "299 chars < 300 must use the short path"
    assert enigo_299 is not None and enigo_299.calls == [("text", ("a" * 299,))]

    enigo_300, clipboard_300, _ = _simulate_paste_text("b" * 300, platform="macos")
    assert clipboard_300 is not None, "300 chars ≥ 300 must use the long path"
    assert clipboard_300.written == ["b" * 300]
    assert len(enigo_300.calls) == 3, "300 chars must trigger 3 key events"
    # And the 3 key events must use Meta (macOS), not Control.
    assert enigo_300.calls[0][1][0] == _Key.Meta, "300-char macOS path first key must be Meta (Cmd down), not Control."


def test_simulate_macos_no_focus_restore_dance() -> None:
    """Simulation: macOS paste path has NO focus-restore dance.

    Unlike Windows (which needs ``AttachThreadInput`` +
    ``SetForegroundWindow`` + UIPI fallback per ADR-0020 §6.3), the
    macOS paste path is simpler: ``enigo`` injects directly into the
    foreground app via ``CGEvent`` (no focus-restore needed).  This
    test verifies the simulation does NOT model any focus-restore
    state — there is no "captured foreground window" / "restored
    foreground window" step in the macOS path.
    """
    # The _FakeEnigo + _FakeClipboard classes have NO focus-related
    # attributes.  This is a structural assertion: if a future
    # contributor adds focus-restore to the macOS simulation, this
    # test would fail (the new attribute would be present).
    enigo, clipboard, _ = _simulate_paste_text("short", platform="macos")
    assert enigo is not None
    assert clipboard is None  # short path doesn't touch clipboard

    # The _FakeEnigo class must NOT have any focus-restore attributes.
    focus_attrs = [
        attr
        for attr in dir(enigo)
        if "foreground" in attr.lower() or "focus" in attr.lower() or "attach" in attr.lower() or "uipi" in attr.lower()
    ]
    assert focus_attrs == [], (
        f"macOS paste simulation must NOT model focus-restore state "
        f"(no UIPI on macOS per ADR-0020 §6.2). Unexpected focus attrs: "
        f"{focus_attrs}"
    )


def test_simulate_uses_mocked_enigo_no_real_key_injection() -> None:
    """The simulation uses a MagicMock for ``enigo::Enigo`` — no real key injection.

    This is a defensive test: if a future contributor wires the
    simulation up to a real ``pynput``/``pyautogui``/``cgevent`` backend,
    this test would still pass (MagicMock records the calls without
    performing them) — but it documents the contract that the test
    harness MUST NOT inject real keys into the host OS (especially
    important on macOS where Accessibility permission + CGEvent
    injection would actually type into the foreground app).
    """
    mock_enigo_cls = MagicMock()
    mock_enigo_instance = MagicMock()
    mock_enigo_cls.new.return_value = mock_enigo_instance

    _simulate_paste_text("short", platform="macos", enigo_factory=mock_enigo_cls)

    # The mock was instantiated once and text() was called once — but
    # no real OS-level key injection happened (MagicMock is inert).
    mock_enigo_cls.new.assert_called_once_with(None)
    mock_enigo_instance.text.assert_called_once_with("short")
    # No key() calls for short text.
    mock_enigo_instance.key.assert_not_called()
