"""MIG-1.5 Phase 0-W Gate Check (sub1) — paste focus-restore (Windows).

Validates the **focus-restore dance** + **UIPI fallback** + **Wayland
fallback** added to ``src-tauri/src/commands/sidecar_cmds.rs::paste_text``
to close the ADR-0020 §6.3 implementation gap that the MIG-1.5 audit
(``worklog.md`` entry "Sub-agent Audit: 1-audit-mig15", finding #12 +
top-5 improvement #2) flagged.

This file is **DISJOINT** from ``test_enigo_paste_windows.py``: that file
pins the short/long paste-decision contract (threshold, enigo.text() vs
clipboard + Ctrl+V, error propagation). This file pins ONLY the
focus-restore + UIPI fallback + Wayland-fallback additions, so the two
files can evolve independently without test-name collisions.

What this file pins (the focus-restore + fallback contract):

1. **Win32 focus-restore APIs present** (ADR-0020 §6.3):
   - ``GetForegroundWindow`` — capture the foreground window BEFORE paste
     + re-read it AFTER paste to detect focus theft.
   - ``GetWindowThreadProcessId`` — get the thread id of the captured
     foreground window (needed for ``AttachThreadInput``).
   - ``AttachThreadInput`` — attach input processing between our thread
     and the target window's thread so ``SetForegroundWindow`` can
     steal focus back from a window that grabbed it during the dispatch
     round-trip.
   - ``SetForegroundWindow`` — restore the foreground window to the
     captured hwnd.
   - ``GetCurrentThreadId`` — get our own thread id (first arg of
     ``AttachThreadInput``).

2. **Focus-restore is Windows-only** (``#[cfg(target_os = "windows")]``):
   macOS + Linux X11 paths are unchanged (per the task spec + ADR §6.3
   which says the dance is Windows-only).

3. **UIPI fallback** (ADR-0020 §6.3): if ``AttachThreadInput`` returns
   ``0`` (UIPI blocks the attach — common when the foreground window
   runs at a higher integrity level than Voice Typer), the code MUST
   fall back IMMEDIATELY (no retry of the window switch):
   - **clipboard write** via ``tauri-plugin-clipboard-manager`` (so the
     user's text is not lost),
   - **crash_recovery event** emitted via ``app.emit("crash_recovery", ...)``
     (so the React UI can show its own recovery UI),
   - **toast notification** via ``tauri-plugin-notification``
     (``app.notification().builder()...show()``) with a body telling the
     user to press Ctrl+V manually.

4. **Wayland fallback** (ADR-0020 §6.6): on a Wayland session (detected
   via ``XDG_SESSION_TYPE=wayland``), ``enigo.text()`` is X11-only and
   expected to fail — the code MUST use the clipboard + Ctrl+V path
   always on Wayland, regardless of text length.

5. **``windows`` crate declared in ``Cargo.toml``** with the three
   required feature flags: ``Win32_UI_WindowsAndMessaging``,
   ``Win32_Foundation``, ``Win32_System_Threading``. The dep is
   target-gated (``[target.'cfg(windows)'.dependencies]``) so Linux/macOS
   builds do NOT pull in the ``windows`` crate.

Why source-grep is the best we can do without ``cargo`` in the sandbox
=====================================================================

The sandbox has no Rust toolchain (``cargo`` / ``rustc`` are not
installed — see ``worklog.md`` "Important Discoveries"). We cannot
compile the Rust source to verify the Win32 calls actually link, nor
run a behavioral test that exercises ``AttachThreadInput`` returning 0.
The strongest validation available in the sandbox is source inspection:

- **Symbol presence**: assert each Win32 API name appears in
  ``sidecar_cmds.rs``. A typo (``AttachThreadInput`` vs
  ``AttachThreadInputEx``) or omission would fail this.
- **cfg gating**: assert the focus-restore block is behind
  ``#[cfg(target_os = "windows")]`` so a regression that compiles the
  Win32 calls on Linux/macOS (where they don't exist) is caught.
- **Fallback wiring**: assert the UIPI fallback references
  ``ClipboardExt`` + ``Emitter`` + ``NotificationExt`` traits, so a
  regression that drops one of the three fallback actions (clipboard,
  event, toast) is caught.

The behavioral validation — "does the dance actually restore focus on
Windows 11?" and "does the UIPI fallback fire when an elevated window
has focus?" — MUST be run on a real Windows host with MSVC + Win32 SDK
via the VALIDATE ON WINDOWS HOST block below. Source-grep catches
symbol-level regressions; cargo + a Windows host catches semantic bugs.

VALIDATE ON WINDOWS HOST
========================

1. ``cargo check`` — verify the ``windows`` crate resolves + the
   ``#[cfg(target_os = "windows")]`` block compiles against the
   ``Win32_UI_WindowsAndMessaging`` / ``Win32_Foundation`` /
   ``Win32_System_Threading`` feature flags. Expected: exit 0, no
   "unresolved import" or "feature `Win32_*` not enabled" errors.

   .. code-block:: bash

      cargo check --manifest-path src-tauri/Cargo.toml

2. ``cargo test paste_text`` — run any Rust unit tests touching
   ``paste_text`` (currently the source has no Rust unit tests for
   ``paste_text`` — the behavioral contract is pinned by this Python
   source-grep file + ``test_enigo_paste_windows.py``). Expected: exit 0
   (or "0 tests run" if no Rust unit tests match the filter — neither is
   a failure for this gate).

   .. code-block:: bash

      cargo test --manifest-path src-tauri/Cargo.toml paste_text

3. Manual UIPI-fallback smoke test (run on a Windows 11 host with an
   elevated Notepad — `notepad.exe` launched via "Run as administrator"):

   a. Launch Voice Typer (NOT elevated — standard user).
   b. Launch an elevated Notepad (`Start > Notepad > Run as administrator`).
   c. Focus the elevated Notepad window.
   d. Dictate a short phrase via Voice Typer.
   e. EXPECT: Voice Typer's ``paste_text`` calls ``AttachThreadInput``,
      which returns ``0`` (UIPI blocks the attach). The UIPI fallback
      fires:
        - text is written to the clipboard (verify with Win+V clipboard
          history),
        - a ``crash_recovery`` Tauri event is emitted (verify via the
          React devtools — the UI's recovery banner should appear),
        - a toast "Paste failed — clipboard copied. Press Ctrl+V
          manually." appears (verify visually).
        - the React UI's ``invoke('paste_text')`` rejects with the
          error string "paste focus-restore failed (UIPI): text copied
          to clipboard".
   f. Press Ctrl+V in the elevated Notepad — the dictated text pastes.

4. Manual focus-restore smoke test (run on a Windows 11 host, standard
   user, no elevation):

   a. Launch Voice Typer + open Notepad (standard user).
   b. Focus Notepad.
   c. Trigger a dictation that causes the Voice Typer webview to briefly
      steal focus (e.g. a long transcription that triggers a UI update).
   d. EXPECT: the text pastes into Notepad (NOT into Voice Typer's
      webview). The focus-restore dance should have switched foreground
      back to Notepad before the paste.

   If the paste lands in Voice Typer's webview instead of Notepad, the
   focus-restore dance is broken — file a bug against
   ``commands::sidecar_cmds::paste_text``.

5. Manual Wayland-fallback smoke test (run on a Linux Wayland host —
   Fedora 40 default, or Ubuntu 22.04 with the GNOME session):

   a. Launch Voice Typer on a Wayland session (verify with
      ``echo $XDG_SESSION_TYPE`` → ``wayland``).
   b. Dictate a SHORT phrase (< 300 chars).
   c. EXPECT: text appears in the target window via clipboard + Ctrl+V
      (NOT via ``enigo.text()``, which is X11-only and would fail).
      Check the log for ``[PASTE] Wayland session detected
      (XDG_SESSION_TYPE=wayland) — using clipboard + Ctrl+V fallback``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ─── Path constants ──────────────────────────────────────────────────────
# Repo root is three levels up from this file:
#   tests/tauri/mig15/test_paste_focus_restore_windows.py → repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
CARGO_TOML = REPO_ROOT / "src-tauri" / "Cargo.toml"
ADR_0020_MD = REPO_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"


# ─── Source-reading fixtures ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def sidecar_cmds_src() -> str:
    """Read the ``sidecar_cmds.rs`` source once per module.

    Hard-fails if the file is missing — this test file's entire contract
    is pinned to that Rust source, so a missing file is a real
    regression (not a soft skip).
    """
    assert SIDECAR_CMDS_RS.is_file(), f"missing Rust source: {SIDECAR_CMDS_RS} — did the file move?"
    return SIDECAR_CMDS_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cargo_toml_src() -> str:
    """Read ``src-tauri/Cargo.toml`` once per module."""
    assert CARGO_TOML.is_file(), f"missing Cargo.toml: {CARGO_TOML}"
    return CARGO_TOML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def adr_0020_src() -> str:
    """Read ADR-0020 markdown once per module.

    Used to verify the ADR §6.3 + §6.6 sections that this file's
    contract derives from are still present in the ADR (guards against
    an ADR rewrite that drops the focus-restore / Wayland-fallback
    language without updating the implementation).
    """
    assert ADR_0020_MD.is_file(), f"missing ADR: {ADR_0020_MD}"
    return ADR_0020_MD.read_text(encoding="utf-8")


def _slice_paste_text(src: str) -> str:
    """Return just the ``paste_text`` function (docstring + body).

    Mirrors the slicing helper in ``test_enigo_paste_windows.py`` so the
    two files agree on what counts as "inside ``paste_text``" vs "in a
    helper function". The slice spans from the docstring opener to the
    next ``// ───`` section header (the cooperative-shutdown section).
    """
    start = src.index("/// ADR-0020 §6.2: paste transcribed text")
    end_match = re.search(r"\n// ─── Tauri command: cooperative shutdown", src[start:])
    end = start + end_match.start() if end_match else len(src)
    return src[start:end]


# ─── 1. Win32 focus-restore APIs are present ─────────────────────────────


@pytest.mark.parametrize(
    "symbol",
    [
        "GetForegroundWindow",
        "GetWindowThreadProcessId",
        "AttachThreadInput",
        "SetForegroundWindow",
        "GetCurrentThreadId",
    ],
)
def test_win32_focus_restore_api_present(sidecar_cmds_src: str, symbol: str) -> None:
    """ADR-0020 §6.3: ``paste_text`` must call each Win32 focus-restore API.

    The dance requires all 5 Win32 functions:

    - ``GetForegroundWindow`` — snapshot the foreground window BEFORE
      paste + re-read it AFTER paste to detect focus theft.
    - ``GetWindowThreadProcessId`` — get the thread id of the captured
      foreground window (needed for ``AttachThreadInput``).
    - ``AttachThreadInput`` — attach input processing between our thread
      and the target window's thread so ``SetForegroundWindow`` can
      bypass the "foreground window cannot be changed by a non-
      foreground process" restriction.
    - ``SetForegroundWindow`` — restore the foreground window to the
      captured hwnd.
    - ``GetCurrentThreadId`` — get our own thread id (first arg of
      ``AttachThreadInput``).

    A typo or omission of any one of these breaks the dance. This test
    catches symbol-level regressions; the behavioral "does it actually
    restore focus?" check is in the VALIDATE ON WINDOWS HOST block.
    """
    assert symbol in sidecar_cmds_src, (
        f"ADR-0020 §6.3 focus-restore dance requires the Win32 API "
        f"`{symbol}` to be called in `sidecar_cmds.rs::paste_text`, but "
        f"the symbol is missing from the source. Did the focus-restore "
        f"block get removed or refactored away?"
    )


# ─── 2. Focus-restore block is Windows-only (cfg-gated) ──────────────────


def test_focus_restore_gated_on_windows_target(sidecar_cmds_src: str) -> None:
    """The focus-restore dance is Windows-only — must be ``cfg``-gated.

    ADR-0020 §6.3 says the ``AttachThreadInput`` + ``SetForegroundWindow``
    dance is Windows-only. macOS uses CGEvent (via ``enigo``); Linux X11
    uses XTest (via ``enigo``); Wayland uses the clipboard + Ctrl+V
    fallback. The Win32 calls MUST be behind
    ``#[cfg(target_os = "windows")]`` so a Linux/macOS build does not
    fail to link (the symbols don't exist there).

    We assert at least 2 ``#[cfg(target_os = "windows")]`` gates in the
    file (one for the focus-capture block at the top of ``paste_text``,
    one for the focus-restore block after the paste). A regression that
    drops either gate would either fail to compile on Linux (caught by
    ``cargo check`` on a Linux host) or silently re-introduce the
    cross-platform paste bug.
    """
    gate_count = sidecar_cmds_src.count('#[cfg(target_os = "windows")]')
    assert gate_count >= 2, (
        f'Expected at least 2 `#[cfg(target_os = "windows")]` gates in '
        f"sidecar_cmds.rs (one for focus-capture, one for focus-restore). "
        f"Found {gate_count}. The Win32 focus-restore calls would leak "
        f"into Linux/macOS builds without these gates."
    )


def test_focus_capture_precedes_paste_execution(sidecar_cmds_src: str) -> None:
    """The focus-capture block runs BEFORE the short/long branch.

    ADR-0020 §6.3: "Before injecting, capture the current foreground
    window with ``GetForegroundWindow()`` + its thread id". The capture
    must precede the paste execution (the short/long ``enigo`` branch)
    so the snapshot reflects the user's intended target window, not a
    window that grabbed focus mid-paste.
    """
    body = _slice_paste_text(sidecar_cmds_src)
    capture_pos = body.find("GetForegroundWindow")
    paste_pos = body.find("if text.chars().count() < PASTE_SHORT_THRESHOLD")
    assert capture_pos != -1, "GetForegroundWindow not found in paste_text body"
    assert paste_pos != -1, "short/long branch marker not found in paste_text body"
    assert capture_pos < paste_pos, (
        "Focus-capture (`GetForegroundWindow`) must come BEFORE the "
        "short/long paste branch (`if text.chars().count() < "
        "PASTE_SHORT_THRESHOLD`). A regression that moves the capture "
        "AFTER the paste would snapshot the WRONG window (the one that "
        "stole focus during paste)."
    )


def test_focus_restore_runs_after_paste_execution(sidecar_cmds_src: str) -> None:
    """The focus-restore block runs AFTER the short/long branch.

    The capture-before-paste + restore-after-paste ordering is what
    makes the dance work: snapshot the target, do the paste, restore
    the snapshot. If the restore ran BEFORE the paste, the paste would
    land in the wrong window.
    """
    body = _slice_paste_text(sidecar_cmds_src)
    paste_pos = body.find("if text.chars().count() < PASTE_SHORT_THRESHOLD")
    # The restore block's actual `AttachThreadInput` CALL (not the
    # docstring mention) is `AttachThreadInput(current_thread, ...)`.
    # We search for that specific call signature so the docstring's
    # `AttachThreadInput` mention (which appears BEFORE the paste branch)
    # doesn't fool the test.
    restore_pos = body.find("AttachThreadInput(current_thread")
    assert restore_pos != -1, (
        "Restore-block `AttachThreadInput(current_thread, ...)` call "
        "not found in paste_text body. Did the restore block get "
        "removed, or did the call signature change?"
    )
    assert restore_pos > paste_pos, (
        "Focus-restore (`AttachThreadInput(current_thread, ...)`) must "
        "come AFTER the short/long paste branch. A regression that "
        "moves the restore BEFORE the paste would attempt to switch "
        "foreground BEFORE the paste, then the paste would land in "
        "whatever window has focus at paste-time — defeating the dance."
    )


# ─── 3. UIPI fallback (clipboard + crash_recovery + toast) ───────────────


def test_uipi_fallback_writes_to_clipboard(sidecar_cmds_src: str) -> None:
    """ADR-0020 §6.3 UIPI fallback: write text to clipboard via
    ``tauri-plugin-clipboard-manager``.

    If ``AttachThreadInput`` returns 0 (UIPI blocked the attach), the
    code MUST fall back immediately (no retry of the window switch). The
    first fallback action is to write the text to the system clipboard
    via ``app.clipboard().write_text(...)`` so the user can paste
    manually with Ctrl+V — this preserves the no-data-loss guarantee.
    """
    body = _slice_paste_text(sidecar_cmds_src)
    # The fallback uses `app.clipboard().write_text(text.clone())`.
    # We assert the substring `write_text` appears in the body (the
    # long-text branch also has a `write_text`, so the substring is
    # present twice — once for the long-text path, once for the UIPI
    # fallback). To specifically pin the UIPI fallback's clipboard
    # write, we assert the substring appears in a `let _ =` context
    # (the fallback discards errors via `let _ =`).
    assert "write_text" in body, (
        "paste_text must call `app.clipboard().write_text(...)` — "
        "both the long-text branch and the UIPI fallback use it."
    )
    # Pin the UIPI-fallback's clipboard write specifically: it uses
    # `let _ = app.clipboard().write_text(...)` (discards errors because
    # we're already in a fallback path; surfacing a clipboard error
    # here would mask the original UIPI failure).
    fallback_cb_pattern = re.compile(
        r"let\s+_\s*=\s*app\.clipboard\(\)\s*\.write_text\([^)]+\)",
        re.DOTALL,
    )
    assert fallback_cb_pattern.search(body), (
        "UIPI fallback must write to the clipboard via "
        "`let _ = app.clipboard().write_text(text.clone())` (best-effort, "
        "discards errors) so the user's text is not lost when the "
        "focus-restore dance fails. Pattern not found in paste_text body."
    )


def test_uipi_fallback_emits_crash_recovery_event(sidecar_cmds_src: str) -> None:
    """ADR-0020 §6.3 UIPI fallback: emit a ``crash_recovery`` Tauri event.

    The fallback emits ``app.emit("crash_recovery", ...)`` so the React
    UI can show its own recovery UI (e.g. offer to copy the text again,
    or restart the sidecar). The event name ``crash_recovery`` is
    hard-coded — a typo would silently break the UI's recovery hook.
    """
    body = _slice_paste_text(sidecar_cmds_src)
    assert "crash_recovery" in body, (
        "UIPI fallback must emit a `crash_recovery` Tauri event so the "
        "React UI's recovery hook fires. The literal string "
        "`crash_recovery` not found in paste_text body."
    )
    # Pin the emit call structure: `app.emit("crash_recovery", ...)`.
    emit_pattern = re.compile(
        r'app\.emit\(\s*"crash_recovery"\s*,',
        re.DOTALL,
    )
    assert emit_pattern.search(body), (
        'UIPI fallback must call `app.emit("crash_recovery", ...)` '
        '(tauri::Emitter trait). The `app.emit("crash_recovery", ...)` '
        "pattern not found in paste_text body — did the event name get "
        "misspelled or the emit call dropped?"
    )
    # The `tauri::Emitter` trait must be imported (Tauri v2 moved `emit`
    # off `Manager` and onto the dedicated `Emitter` trait).
    assert "use tauri::Emitter" in body, (
        "UIPI fallback must `use tauri::Emitter;` so `app.emit(...)` "
        "resolves. Tauri v2 moved `emit` from the `Manager` trait to "
        "the dedicated `Emitter` trait — a regression that drops the "
        "`use tauri::Emitter;` import would fail to compile with "
        "`no method named `emit` found`."
    )


def test_uipi_fallback_posts_toast_via_notification_plugin(
    sidecar_cmds_src: str,
) -> None:
    """ADR-0020 §6.3 UIPI fallback: post a toast via
    ``tauri-plugin-notification``.

    The fallback posts a toast with body ``"Paste failed — clipboard
    copied. Press Ctrl+V manually."`` so the user knows what to do.
    The toast is built via ``app.notification().builder()...show()``
    (the ``NotificationExt`` trait from ``tauri-plugin-notification``).
    """
    body = _slice_paste_text(sidecar_cmds_src)
    assert "use tauri_plugin_notification::NotificationExt" in body, (
        "UIPI fallback must `use tauri_plugin_notification::NotificationExt;` "
        "so `app.notification()` resolves. A regression that drops the "
        "import would fail to compile with `no method named `notification` "
        "found`."
    )
    # Pin the builder call: `app.notification().builder()...show()`.
    builder_pattern = re.compile(
        r"app\s*\.notification\(\)\s*\.builder\(\)",
        re.DOTALL,
    )
    assert builder_pattern.search(body), (
        "UIPI fallback must call `app.notification().builder()...show()` "
        "(tauri-plugin-notification v2 API) to post the toast. Pattern "
        "not found in paste_text body."
    )
    # The toast body must mention Ctrl+V (so the user knows the
    # recovery action). The exact string is pinned to the ADR §6.3
    # contract.
    assert "Ctrl+V" in body, (
        "UIPI fallback toast body must mention `Ctrl+V` (the manual "
        "recovery action). The literal `Ctrl+V` not found in "
        "paste_text body."
    )
    # The full toast body string from the ADR §6.3 contract.
    assert "Paste failed — clipboard copied. Press Ctrl+V manually." in body, (
        "UIPI fallback toast body must be exactly "
        "`Paste failed — clipboard copied. Press Ctrl+V manually.` "
        "(ADR-0020 §6.3 contract). The literal string not found in "
        "paste_text body."
    )


def test_uipi_fallback_returns_err(sidecar_cmds_src: str) -> None:
    """ADR-0020 §6.3 UIPI fallback: returns ``Err`` so the webview's
    ``invoke()`` rejects (NEW-IPC-107 contract).

    After the fallback fires (clipboard + event + toast), the command
    returns ``Err(...)`` so the React UI's ``invoke('paste_text')``
    promise rejects. This lets the UI's error handler log the failure
    and (optionally) show its own recovery UI on top of the toast.
    Returning ``Ok(())`` would silently swallow the UIPI failure at
    the invoke boundary.
    """
    body = _slice_paste_text(sidecar_cmds_src)
    # The fallback's return is `return Err("paste focus-restore failed
    # (UIPI): text copied to clipboard".to_string());`. Pin the literal
    # error string so a regression that changes the message (and
    # breaks the UI's error-classification logic) is caught.
    err_pattern = re.compile(
        r'return\s+Err\(\s*"paste focus-restore failed \(UIPI\)[^"]*"',
        re.DOTALL,
    )
    assert err_pattern.search(body), (
        'UIPI fallback must `return Err("paste focus-restore failed '
        '(UIPI): text copied to clipboard".to_string());` so the '
        "webview's `invoke('paste_text')` rejects (NEW-IPC-107). "
        "Pattern not found in paste_text body."
    )


def test_uipi_fallback_branch_gated_on_attach_result(sidecar_cmds_src: str) -> None:
    """The UIPI fallback fires when ``AttachThreadInput`` returns 0.

    ADR-0020 §6.3: "if it returns 0, do NOT retry the window-switch —
    fall back immediately". The code MUST branch on the BOOL return of
    ``AttachThreadInput`` and run the fallback in the FALSE branch.
    A regression that runs the fallback unconditionally (regardless of
    ``AttachThreadInput``'s return) would fire the toast on EVERY
    paste, even when the dance succeeded — a UX disaster.
    """
    body = _slice_paste_text(sidecar_cmds_src)
    # The branch structure: `let attached = unsafe { AttachThreadInput(...) };`
    # followed by `if attached.as_bool() { ... } else { ... UIPI fallback ... }`.
    # We assert the `attached` binding + the `as_bool()` call + an `else`
    # branch all appear in the body, in that order.
    attached_pos = body.find("let attached")
    as_bool_pos = body.find("attached.as_bool()")
    else_pos = body.find("} else {", attached_pos)
    assert attached_pos != -1, (
        "`let attached = unsafe { AttachThreadInput(...) };` not found "
        "— the UIPI fallback must be gated on `AttachThreadInput`'s "
        "BOOL return value."
    )
    assert as_bool_pos != -1 and as_bool_pos > attached_pos, (
        "`attached.as_bool()` must follow `let attached = ...` so the "
        "BOOL return is converted to a Rust `bool` for the `if` branch."
    )
    assert else_pos != -1 and else_pos > as_bool_pos, (
        "An `} else {` branch must follow `if attached.as_bool() { ... }` "
        "— the UIPI fallback lives in the else branch (when "
        "AttachThreadInput returns 0)."
    )


# ─── 4. Wayland fallback (XDG_SESSION_TYPE=wayland) ──────────────────────


def test_wayland_fallback_checks_xdg_session_type(sidecar_cmds_src: str) -> None:
    """ADR-0020 §6.6: Wayland fallback detects via ``XDG_SESSION_TYPE``.

    ``enigo`` on Linux is X11-only (XTest extension). On a Wayland
    session, ``enigo.text()`` is expected to FAIL. The code MUST detect
    Wayland via the ``XDG_SESSION_TYPE`` env var and use the clipboard +
    Ctrl+V path always (regardless of text length).
    """
    assert "XDG_SESSION_TYPE" in sidecar_cmds_src, (
        "Wayland fallback must check the `XDG_SESSION_TYPE` env var "
        "(ADR-0020 §6.6). The literal `XDG_SESSION_TYPE` not found in "
        "sidecar_cmds.rs."
    )
    assert "wayland" in sidecar_cmds_src.lower(), (
        "Wayland fallback must compare `XDG_SESSION_TYPE` to `wayland` "
        "(case-insensitive). The literal `wayland` not found in "
        "sidecar_cmds.rs."
    )


def test_wayland_fallback_gated_on_linux_target(sidecar_cmds_src: str) -> None:
    """The Wayland check is Linux-only — must be ``cfg``-gated.

    ``XDG_SESSION_TYPE`` is a Linux-only env var. On macOS/Windows it's
    unset, so the check would always return false — harmless but
    wasteful. The check MUST be behind
    ``#[cfg(target_os = "linux")]`` so a future regression that adds
    the check to macOS/Windows is caught.
    """
    assert '#[cfg(target_os = "linux")]' in sidecar_cmds_src, (
        'Wayland fallback must be gated on `#[cfg(target_os = "linux")]` '
        "— `XDG_SESSION_TYPE` is a Linux-only env var. The gate not "
        "found in sidecar_cmds.rs."
    )


def test_wayland_fallback_uses_clipboard_plus_ctrl_v(sidecar_cmds_src: str) -> None:
    """Wayland fallback routes through the clipboard + Ctrl+V path.

    The Wayland branch must call a helper (or inline the logic) that
    writes to the clipboard + sends Ctrl+V via enigo. We assert the
    branch contains a call to ``paste_via_clipboard_and_ctrl_v`` (the
    shared helper) OR an inline ``clipboard().write_text(...)`` +
    ``enigo.key(...)`` sequence.
    """
    assert "paste_via_clipboard_and_ctrl_v" in sidecar_cmds_src, (
        "Wayland fallback must route through a shared clipboard + Ctrl+V "
        "helper. The function `paste_via_clipboard_and_ctrl_v` not found "
        "in sidecar_cmds.rs — did the helper get renamed or inlined?"
    )


# ─── 5. `windows` crate declared in Cargo.toml ───────────────────────────


def test_windows_crate_declared_in_cargo_toml(cargo_toml_src: str) -> None:
    """The ``windows`` crate is declared in ``Cargo.toml`` with the
    required feature flags.

    The focus-restore dance needs three ``windows`` crate features:

    - ``Win32_UI_WindowsAndMessaging`` — ``GetForegroundWindow``,
      ``SetForegroundWindow``, ``GetWindowThreadProcessId``,
      ``AttachThreadInput``.
    - ``Win32_Foundation`` — ``HWND``, ``BOOL``.
    - ``Win32_System_Threading`` — ``GetCurrentThreadId``.

    Without all three features, the build fails with "feature
    `Win32_*` not enabled" errors. The dep is target-gated
    (``[target.'cfg(windows)'.dependencies]``) so Linux/macOS builds do
    NOT pull in the ``windows`` crate (keeps the dep graph clean for
    the non-Windows MIG-1.6 / MIG-1.7 spikes).
    """
    # The `windows =` line must appear in Cargo.toml.
    assert re.search(r"^windows\s*=\s*", cargo_toml_src, re.MULTILINE), (
        "The `windows` crate must be declared in src-tauri/Cargo.toml "
        "(target-gated under `[target.'cfg(windows)'.dependencies]`) "
        "for the focus-restore dance to compile on Windows."
    )
    # All three required features must be present.
    for feature in [
        "Win32_UI_WindowsAndMessaging",
        "Win32_Foundation",
        "Win32_System_Threading",
    ]:
        assert feature in cargo_toml_src, (
            f"The `windows` crate feature `{feature}` must be enabled in "
            f"src-tauri/Cargo.toml for the focus-restore Win32 APIs to "
            f"resolve. Feature not found in Cargo.toml."
        )
    # The dep must be target-gated to Windows (so Linux/macOS builds
    # don't pull it in).
    assert "[target.'cfg(windows)'.dependencies]" in cargo_toml_src, (
        "The `windows` crate dependency MUST be target-gated under "
        "`[target.'cfg(windows)'.dependencies]` so Linux/macOS builds "
        "do NOT pull in the `windows` crate (keeps the dependency "
        "graph clean for the non-Windows MIG-1.6 / MIG-1.7 spikes)."
    )


def test_existing_deps_preserved_in_cargo_toml(cargo_toml_src: str) -> None:
    """The refactor must not have removed existing deps from Cargo.toml.

    Defensive: the task allows modifying Cargo.toml ONLY to add the
    ``windows`` crate. This test asserts the pre-existing deps
    (enigo, tauri, tauri-plugin-clipboard-manager,
    tauri-plugin-notification, etc.) are still present, so a regression
    that accidentally deletes a dep block is caught.
    """
    required_deps = [
        "tauri",
        "tauri-plugin-shell",
        "tauri-plugin-notification",
        "tauri-plugin-clipboard-manager",
        "tauri-plugin-single-instance",
        "tauri-plugin-dialog",
        "enigo",
        "tokio",
        "tokio-tungstenite",
        "rand",
        "serde",
        "serde_json",
        "log",
        "env_logger",
    ]
    for dep in required_deps:
        # Match `dep` as a key in a `dep = ...` or `dep = { ... }` line.
        # Use a regex that matches the dep name at the start of a line
        # (after optional whitespace) followed by `=`.
        pattern = re.compile(rf"^{re.escape(dep)}\s*=", re.MULTILINE)
        assert pattern.search(cargo_toml_src), (
            f"Pre-existing dep `{dep}` must still be declared in "
            f"src-tauri/Cargo.toml. The refactor should only ADD the "
            f"`windows` crate, not remove existing deps."
        )


# ─── 6. ADR-0020 §6.3 + §6.6 sections still present ──────────────────────


def test_adr_0020_section_6_3_present(adr_0020_src: str) -> None:
    """ADR-0020 §6.3 (Focus restore — Windows) is still in the ADR.

    Guards against an ADR rewrite that drops the §6.3 section without
    updating the implementation. If §6.3 disappears, this test fails
    and forces a reconciliation between the ADR and the implementation.
    """
    assert "#### 6.3 Focus restore" in adr_0020_src, (
        "ADR-0020 must have a `#### 6.3 Focus restore` section. The "
        "section is the source-of-truth for the focus-restore dance "
        "this file pins — its disappearance would mean the spec "
        "changed without the implementation being updated."
    )
    assert "AttachThreadInput" in adr_0020_src, "ADR-0020 §6.3 must document the `AttachThreadInput` API."
    assert "SetForegroundWindow" in adr_0020_src, "ADR-0020 §6.3 must document the `SetForegroundWindow` API."


def test_adr_0020_section_6_6_wayland_present(adr_0020_src: str) -> None:
    """ADR-0020 §6.6 (Linux + Wayland caveats) is still in the ADR.

    Guards against an ADR rewrite that drops the Wayland fallback
    language. The implementation's Wayland check derives from §6.6.
    """
    # §6.6 is under "6.6 Linux + Wayland caveats" or similar.
    # Search for "Wayland" + "enigo" near each other.
    assert "Wayland" in adr_0020_src, "ADR-0020 must mention Wayland (§6.6 Linux + Wayland caveats)."
    # The ADR must say enigo is X11-only on Linux (the rationale for
    # the Wayland fallback).
    assert "X11" in adr_0020_src, (
        "ADR-0020 §6.6 must document that `enigo` on Linux is X11-only "
        "(the rationale for the Wayland clipboard + Ctrl+V fallback)."
    )


# ─── 7. macOS + Linux X11 paths unchanged (regression guard) ─────────────


def test_macos_path_unchanged_no_focus_restore(sidecar_cmds_src: str) -> None:
    """macOS path must NOT have any Windows-only focus-restore calls
    leaked into it.

    ADR-0020 §6.3: the focus-restore dance is Windows-only. macOS uses
    CGEvent (via ``enigo``). The ``#[cfg(target_os = "windows")]`` gates
    must ensure the Win32 calls are not compiled on macOS. This test is
    a defensive regression guard — the cfg gate test above already
    covers this, but we add an explicit "macOS path is clean" assertion
    for documentation.
    """
    # The focus-restore block (capture + restore) must be inside
    # `#[cfg(target_os = "windows")]` gates. We count the gates and
    # assert >= 2 (one for capture, one for restore).
    gate_count = sidecar_cmds_src.count('#[cfg(target_os = "windows")]')
    assert gate_count >= 2, (
        "Focus-restore must be Windows-only — at least 2 "
        '`#[cfg(target_os = "windows")]` gates required (capture + '
        "restore). macOS path must be unaffected."
    )
    # The macOS path uses Key::Meta for Cmd+V — this is in the long
    # branch, unchanged. We assert it's still there.
    body = _slice_paste_text(sidecar_cmds_src)
    assert "Key::Meta" in body, (
        "macOS long-text path must still use `Key::Meta` (Cmd+V) — "
        "the refactor must not have changed the macOS modifier."
    )


def test_linux_x11_path_unchanged_uses_enigo_text(sidecar_cmds_src: str) -> None:
    """Linux X11 path must still use ``enigo.text()`` for short text.

    ADR-0020 §6.6: on Linux X11, ``enigo`` works (via XTest). The
    short-text path must call ``enigo.text()`` unchanged. Only Wayland
    sessions fall back to clipboard + Ctrl+V.
    """
    body = _slice_paste_text(sidecar_cmds_src)
    assert "enigo.text(&text)" in body, (
        "Linux X11 short-text path must still call `enigo.text(&text)` "
        "(X11/XTest). The Wayland fallback is a separate code path — "
        "the X11 path must be unchanged."
    )
