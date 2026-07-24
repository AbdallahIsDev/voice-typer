"""MIG-1.5 Phase 0-W Gate Check 5 — `enigo` paste validation (Windows).

Validates the Rust `paste_text` Tauri command
(``src-tauri/src/commands/sidecar_cmds.rs:78-136``) — the Windows paste
path documented in:

- ADR-0020 §6.2  — paste strategy (short via ``enigo.text()``; long via
  ``tauri-plugin-clipboard-manager`` + Ctrl+V / Cmd+V).
- ADR-0020 §6.3  — Windows focus-restore dance (``AttachThreadInput`` +
  ``SetForegroundWindow``). Documented but NOT yet implemented in the
  Rust ``paste_text`` — see test ``test_focus_restore_logic_documented_in_code``.
- ``docs/migration/windows-validation-runbook.md`` §6.4 — host validation
  procedure for short + long paste.

Because we cannot compile Rust in this sandbox, the tests use
**source inspection** (read the ``.rs`` file and assert on its content)
plus a **behavioural simulation** (a Python re-implementation of the
paste algorithm driven by ``MagicMock`` for ``enigo::Enigo`` and
``tauri_plugin_clipboard_manager::ClipboardExt``).  No real key
injection happens — the mocks record call order + arguments so we can
verify the contract:

- short text  (< ``PASTE_SHORT_THRESHOLD``) → ``enigo.text()`` only
- long text   (≥ ``PASTE_SHORT_THRESHOLD``) → ``clipboard.write_text()`` +
  ``enigo.key(Control, Press)`` + ``enigo.key('v', Click)`` +
  ``enigo.key(Control, Release)``
- empty text  → no-op (returns ``Ok(())`` immediately)
- Windows path uses ``Key::Control`` (not ``Key::Meta`` — that is macOS)
- every ``enigo``/``clipboard`` call propagates errors via ``.map_err(...)?``
  so they surface as Rust errors (not silently swallowed)
- the focus-restore dance (``AttachThreadInput`` + ``SetForegroundWindow``)
  is documented in ADR-0020 §6.3 and the code's docstring references
  ADR-0020 §6.2 — see test for the known implementation gap.

VALIDATE ON WINDOWS HOST:
1. Launch Voice Typer + open Notepad
2. Dictate a short phrase (< 300 chars) — verify text appears in Notepad via enigo.text()
3. Dictate a long phrase (≥ 300 chars) — verify text appears in Notepad via clipboard + Ctrl+V
4. Check log for:
   - "[PASTE] injected N chars via enigo" (short)
   - "[PASTE] injected N chars via clipboard + Ctrl/Cmd+V" (long)
Expected: text appears in Notepad within 500ms; no characters dropped
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Path constants ──────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/tauri/mig15/<this> → repo root
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
UTIL_RS = REPO_ROOT / "src-tauri" / "src" / "util.rs"
ADR_0020_MD = REPO_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
WINDOWS_RUNBOOK_MD = REPO_ROOT / "docs" / "migration" / "windows-validation-runbook.md"


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
def windows_runbook_src() -> str:
    """Read the Windows validation runbook markdown once per module."""
    assert WINDOWS_RUNBOOK_MD.is_file(), f"missing runbook: {WINDOWS_RUNBOOK_MD}"
    return WINDOWS_RUNBOOK_MD.read_text(encoding="utf-8")


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
    then ``Ctrl+V`` / ``Cmd+V`` is sent via ``enigo``.  A value other
    than 300 would either over-use the IME path (drop chars on long
    paste) or over-write the user's clipboard (annoying for short
    paste).  Pin the value to 300 to catch accidental edits.
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
    - NOT call ``enigo.key(..., Press|Click|Release)`` (no Ctrl+V).
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

    # Must log the short-path message documented in the Windows runbook §6.4.
    assert "[PASTE] injected" in short_branch and "via enigo" in short_branch, (
        "Short-text branch must log `[PASTE] injected N chars via enigo` "
        "(windows-validation-runbook.md §6.4 verify-in-logs step)."
    )


# ─── 3. Long text path: clipboard + Ctrl+V ───────────────────────────────


def test_long_text_path_uses_clipboard_plus_ctrl_v(sidecar_cmds_src: str) -> None:
    """Long text (≥ threshold) calls ``clipboard.write_text()`` + 3 ``enigo.key()`` calls.

    Order matters: clipboard write MUST happen before the key events so
    the Ctrl+V pastes the long text (not the previous clipboard
    contents).  The 3 key calls are:

    1. ``enigo.key(mod_key, Direction::Press)``   — Ctrl down
    2. ``enigo.key(Key::Unicode('v'), Direction::Click)`` — V press+release
    3. ``enigo.key(mod_key, Direction::Release)`` — Ctrl up
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
        "Long-text branch must call `enigo.key(mod_key, enigo::Direction::Press)` (Ctrl/Cmd down) per ADR-0020 §6.2."
    )
    assert press_match.start() > cb_end, (
        "Long-text branch: `enigo.key(mod_key, Press)` must come AFTER "
        "`app.clipboard().write_text(...)` — otherwise Ctrl+V would paste "
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
        "(Ctrl/Cmd up) per ADR-0020 §6.2 — otherwise the modifier stays held "
        "and the next user keystroke becomes Ctrl+<key>."
    )
    assert release_match.start() > v_match.end(), (
        "Long-text branch: `enigo.key(mod_key, Release)` must come AFTER "
        "`enigo.key('v', Click)` so the modifier is released only after V."
    )

    # 5. Log message matches the runbook §6.4 expected format.
    assert "[PASTE] injected" in long_branch and "via clipboard + Ctrl/Cmd+V" in long_branch, (
        "Long-text branch must log `[PASTE] injected N chars via clipboard + "
        "Ctrl/Cmd+V` (windows-validation-runbook.md §6.4 verify-in-logs step)."
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


# ─── 5. Windows path uses Key::Control (not Key::Meta) ───────────────────


def test_windows_path_uses_key_control_not_meta(sidecar_cmds_src: str) -> None:
    """Windows/Linux path selects ``Key::Control``; macOS path selects ``Key::Meta``.

    The Rust code uses ``cfg!(target_os = "macos")`` to pick the modifier
    at compile time.  On Windows the modifier MUST be ``Key::Control``
    (Ctrl+V), NOT ``Key::Meta`` (which on Windows is the Win key — Win+V
    opens the clipboard history, not a paste).
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

    # Sanity: the Windows branch (else) must use Control, NOT Meta.
    # The regex above already enforces this, but assert explicitly for
    # documentation + a clearer failure message.
    assert "Key::Control" in mod_match.group(0), (
        "Windows/Linux path must use `Key::Control` (Ctrl+V). Got: " + mod_match.group(0)
    )
    # The `else` branch must NOT reference Key::Meta.
    else_branch = mod_match.group(0).split("else")[1]
    assert "Key::Meta" not in else_branch, (
        "Windows/Linux path (`else` branch) must NOT use `Key::Meta` — that "
        "is the macOS Win/Cmd key. Win+V on Windows opens the clipboard "
        "history, not a paste. Got: " + else_branch
    )


# ─── 6. enigo errors are surfaced as Rust errors ─────────────────────────


def test_enigo_errors_surfaced_as_rust_errors(sidecar_cmds_src: str) -> None:
    """Every ``enigo`` / ``clipboard`` call propagates errors via ``.map_err(...)?``.

    ADR-0020 §6.2 + NEW-IPC-107: errors from the paste path MUST surface
    as Rust errors (``Result<(), String>``) so the webview's ``invoke()``
    rejects.  Silently swallowing an enigo error would leave the user
    with no transcription paste and no error message — a no-data-loss
    guarantee violation.
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
        # The next ~120 chars after the call must contain `.map_err(|e| format!(...))?`
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


# ─── 7. Focus-restore logic is documented in the code ────────────────────


def test_focus_restore_logic_documented_in_code(
    sidecar_cmds_src: str, adr_0020_src: str, windows_runbook_src: str
) -> None:
    """ADR-0020 §6.3 focus-restore dance is documented; impl gap acknowledged.

    The Windows focus-restore dance — ``AttachThreadInput`` +
    ``SetForegroundWindow`` + ``GetForegroundWindow`` — is documented
    in ADR-0020 §6.3 and the Windows validation runbook §11.5.  The
    current Rust ``paste_text`` does NOT implement it (it delegates to
    ``enigo`` directly, which injects into whatever window currently has
    focus).  This test verifies:

    1. ADR-0020 §6.3 documents the dance (``AttachThreadInput`` +
       ``SetForegroundWindow``).
    2. The Rust ``paste_text`` docstring references ADR-0020 §6.2 (the
       parent section that introduces paste + links to §6.3 for the
       focus-restore sub-section).
    3. The Windows runbook §11.5 documents the implementation gap
       (paste goes to the wrong window on Windows 11 if the Voice Typer
       window steals focus).

    This is the "documented in the code" contract — the focus-restore
    dance is NOT yet implemented in Rust (a known gap, tracked in
    REVIEW-4 / XPLAT-2), but the documentation chain (ADR → docstring →
    runbook) is in place so the implementer knows where to add it.
    """
    # 1. ADR-0020 §6.3 must document the focus-restore dance.
    assert "AttachThreadInput" in adr_0020_src, (
        "ADR-0020 must document the Win32 `AttachThreadInput` focus-restore API (§6.3 — Focus restore — Windows)."
    )
    assert "SetForegroundWindow" in adr_0020_src, (
        "ADR-0020 must document the Win32 `SetForegroundWindow` focus-restore API (§6.3 — Focus restore — Windows)."
    )
    # §6.3 section header must be present.
    assert "#### 6.3 Focus restore" in adr_0020_src, "ADR-0020 must have a §6.3 'Focus restore' section."

    # 2. The Rust paste_text docstring must reference ADR-0020 §6.2.
    # (§6.2 is the parent paste-strategy section; §6.3 is the Windows
    # focus-restore sub-section linked from §6.2.)
    body = _slice_paste_text(sidecar_cmds_src)
    assert "ADR-0020 §6.2" in body, (
        "paste_text docstring must reference `ADR-0020 §6.2` (the paste "
        "strategy section that links to §6.3 focus-restore)."
    )

    # 3. Windows runbook §11.5 must document the implementation gap.
    assert "§11.5" in windows_runbook_src and "enigo" in windows_runbook_src, (
        "windows-validation-runbook.md must have a §11.5 section on the enigo focus-restore gap."
    )
    # The runbook must explicitly state the focus-restore is NOT yet
    # implemented in the Rust paste_text (the known gap).
    gap_pattern = re.compile(
        r"(NOT yet implemented|NOT yet implemented in the Rust|"
        r"known gap|delegates to `enigo` directly WITHOUT)",
        re.IGNORECASE,
    )
    assert gap_pattern.search(windows_runbook_src), (
        "windows-validation-runbook.md §11.5 must explicitly state that "
        "the focus-restore dance is NOT yet implemented in the Rust "
        "paste_text (a known gap — REVIEW-4 / XPLAT-2)."
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
        self.fail_on: str | None = None  # if set, the named method raises

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
    platform: str = "windows",
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
    enigo, clipboard, logs = _simulate_paste_text(short_text, platform="windows")

    assert enigo is not None
    assert clipboard is None, "short text must NOT touch the clipboard"
    # Only one call: text().
    assert enigo.calls == [("text", (short_text,))], (
        f"short-text enigo calls must be exactly one `text()` call; got: {enigo.calls}"
    )
    # Log message matches the runbook §6.4 expected format.
    assert logs == ["[PASTE] injected 11 chars via enigo"]


def test_simulate_long_text_uses_clipboard_plus_ctrl_v_in_order() -> None:
    """Simulation: long text (≥ 300) calls clipboard write + 3 key events in order."""
    long_text = "x" * 300  # exactly 300 chars → ≥ threshold
    enigo, clipboard, logs = _simulate_paste_text(long_text, platform="windows")

    assert enigo is not None
    assert clipboard is not None, "long text MUST write to the clipboard"
    # Clipboard was written with the long text (BEFORE any key event).
    assert clipboard.written == [long_text], (
        f"clipboard.write_text must be called once with the long text; got: {clipboard.written}"
    )
    # 3 key events in order: (Control, Press), (Unicode('v'), Click), (Control, Release).
    assert enigo.calls == [
        ("key", (_Key.Control, _Direction.Press)),
        ("key", ("Key::Unicode('v')", _Direction.Click)),
        ("key", (_Key.Control, _Direction.Release)),
    ], (
        f"long-text enigo calls must be 3 key events in Press/Click/Release "
        f"order with Key::Control (Windows). Got: {enigo.calls}"
    )
    # Log message matches the runbook §6.4 expected format.
    assert logs == ["[PASTE] injected 300 chars via clipboard + Ctrl/Cmd+V"]


def test_simulate_long_text_macos_uses_key_meta() -> None:
    """Simulation: long text on macOS uses ``Key::Meta`` (Cmd+V), not Control."""
    long_text = "y" * 500
    enigo, clipboard, _ = _simulate_paste_text(long_text, platform="macos")
    assert enigo is not None
    # First and third key calls must use Meta (not Control).
    assert enigo.calls[0] == ("key", (_Key.Meta, _Direction.Press)), (
        f"macOS long-text first key must be (Meta, Press); got: {enigo.calls[0]}"
    )
    assert enigo.calls[-1] == ("key", (_Key.Meta, _Direction.Release)), (
        f"macOS long-text last key must be (Meta, Release); got: {enigo.calls[-1]}"
    )


def test_simulate_short_text_threshold_boundary() -> None:
    """Simulation: 299 chars → short path; 300 chars → long path.

    The boundary is `< PASTE_SHORT_THRESHOLD` (strict less-than), so:
    - 299 chars (< 300) → enigo.text()
    - 300 chars (≥ 300) → clipboard + Ctrl+V
    """
    enigo_299, clipboard_299, _ = _simulate_paste_text("a" * 299)
    assert clipboard_299 is None, "299 chars < 300 must use the short path"
    assert enigo_299 is not None and enigo_299.calls == [("text", ("a" * 299,))]

    enigo_300, clipboard_300, _ = _simulate_paste_text("b" * 300)
    assert clipboard_300 is not None, "300 chars ≥ 300 must use the long path"
    assert clipboard_300.written == ["b" * 300]
    assert len(enigo_300.calls) == 3, "300 chars must trigger 3 key events"


def test_simulate_uses_mocked_enigo_no_real_key_injection() -> None:
    """The simulation uses a MagicMock for ``enigo::Enigo`` — no real key injection.

    This is a defensive test: if a future contributor wires the
    simulation up to a real ``pynput``/``xdotool`` backend, this test
    would still pass (MagicMock records the calls without performing
    them) — but it documents the contract that the test harness MUST
    NOT inject real keys into the host OS.
    """
    mock_enigo_cls = MagicMock()
    mock_enigo_instance = MagicMock()
    mock_enigo_cls.new.return_value = mock_enigo_instance

    _simulate_paste_text("short", enigo_factory=mock_enigo_cls)

    # The mock was instantiated once and text() was called once — but
    # no real OS-level key injection happened (MagicMock is inert).
    mock_enigo_cls.new.assert_called_once_with(None)
    mock_enigo_instance.text.assert_called_once_with("short")
    # No key() calls for short text.
    mock_enigo_instance.key.assert_not_called()
