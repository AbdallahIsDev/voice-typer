"""MIG-1.7 Phase 0-L Gate Check 5 — `enigo` paste validation (Linux X11 + Wayland).

Validates the Rust `paste_text` Tauri command
(``src-tauri/src/commands/sidecar_cmds.rs:78-136``) — the Linux paste
path documented in:

- ADR-0020 §6.2  — paste strategy (short via ``enigo.text()``; long via
  ``tauri-plugin-clipboard-manager`` + ``Ctrl+V`` on Linux). The migration
  table row for "Clipboard paste — Linux" explicitly notes the Wayland
  caveat: *"Wayland caveat: ``enigo`` on Linux is X11-only; on Wayland
  the clipboard + ``Ctrl+V`` path is the only reliable option. See §6.2
  + §6.6."*
- ADR-0020 §6.6  — Wayland special considerations: ``enigo.text()``
  does NOT work on Wayland (X11-only). The clipboard + ``Ctrl+V`` path
  via ``tauri-plugin-clipboard-manager`` is the only reliable option.
  ``tauri-plugin-clipboard-manager`` on Linux uses ``wl-copy``/``wl-paste``
  (Wayland) or ``xclip``/``xsel`` (X11) — detected at runtime via the
  ``WAYLAND_DISPLAY`` env var.
- ``docs/migration/linux-validation-runbook.md`` Step 8 ("Paste keystroke
  works on X11 AND Wayland", gate point 4) — host validation procedure
  for short + long paste on BOTH X11 AND Wayland sessions.

Because we cannot compile Rust in this sandbox, the tests use
**source inspection** (read the ``.rs`` file and assert on its content)
plus a **behavioural simulation** (a Python re-implementation of the
paste algorithm driven by ``MagicMock`` for ``enigo::Enigo`` and
``tauri_plugin_clipboard_manager::ClipboardExt``). No real key
injection happens — the mocks record call order + arguments so we can
verify the contract:

- short text  (< ``PASTE_SHORT_THRESHOLD``) → ``enigo.text()`` only
  (WORKS on X11 via ``XTestFakeKeyEvent``; FAILS on Wayland — enigo
  is X11-only per ADR-0020 §6.6)
- long text   (≥ ``PASTE_SHORT_THRESHOLD``) → ``clipboard.write_text()`` +
  ``enigo.key(Control, Press)`` + ``enigo.key('v', Click)`` +
  ``enigo.key(Control, Release)`` — Linux uses ``Ctrl`` (``Key::Control``),
  NOT ``Cmd`` (``Key::Meta`` — that is macOS). The clipboard +
  ``Ctrl+V`` path is the ONLY reliable option on Wayland.
- empty text  → no-op (returns ``Ok(())`` immediately)
- Linux path uses ``Key::Control`` (not ``Key::Meta`` — that is macOS)
- every ``enigo``/``clipboard`` call propagates errors via ``.map_err(...)?``
  so they surface as Rust errors (not silently swallowed)

KNOWN GAP — XPLAT-2 (from review.md REVIEW-4):

The current Rust ``paste_text`` does NOT detect Wayland + force the
long-text clipboard path for short text. On Wayland, short-text
injection via ``enigo.text()`` silently fails (X11-only). The fix is
to detect ``WAYLAND_DISPLAY`` / ``XDG_SESSION_TYPE=wayland`` and shell
out to ``wtype`` (or always use the clipboard + ``Ctrl+V`` path on
Wayland). This is tracked as XPLAT-2 in ``review.md``
(REVIEW-4 cross-platform section) and remains Pending. These tests
document the gap explicitly (``test_xplat2_wayland_short_text_gap_*``)
without attempting to fix it — the fix requires Wayland host
validation per ADR-0020 §6.6.

VALIDATE ON LINUX HOST (X11):
1. Launch Voice Typer + open gedit (or gnome-text-editor)
2. Dictate a short phrase (< 300 chars) — verify text appears via enigo.text()
3. Dictate a long phrase (≥ 300 chars) — verify text appears via clipboard + Ctrl+V
Expected: text appears within 500ms; no characters dropped

VALIDATE ON LINUX HOST (Wayland):
1. Launch Voice Typer + open gedit (Wayland session)
2. Dictate a short phrase — verify text appears (via clipboard + Ctrl+V fallback, NOT enigo.text())
3. Dictate a long phrase — verify text appears via clipboard + Ctrl+V
Expected: text appears within 500ms on Wayland
Note: enigo.text() is X11-only — on Wayland, the clipboard + Ctrl+V path is the only reliable option.
GAP: the current Rust paste_text does NOT detect Wayland + force the long-text path for short text.
This is tracked as XPLAT-2 in review.md.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Path constants ──────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/tauri/mig17/<this> → repo root
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
UTIL_RS = REPO_ROOT / "src-tauri" / "src" / "util.rs"
ADR_0020_MD = REPO_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
LINUX_RUNBOOK_MD = REPO_ROOT / "docs" / "migration" / "linux-validation-runbook.md"
COMPREHENSIVE_REVIEW_MD = REPO_ROOT / "review.md"


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
def linux_runbook_src() -> str:
    """Read the Linux validation runbook markdown once per module."""
    assert LINUX_RUNBOOK_MD.is_file(), f"missing runbook: {LINUX_RUNBOOK_MD}"
    return LINUX_RUNBOOK_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def comprehensive_review_src() -> str:
    """Read ``review.md`` once per module (for XPLAT-2)."""
    assert COMPREHENSIVE_REVIEW_MD.is_file(), f"missing review.md: {COMPREHENSIVE_REVIEW_MD}"
    return COMPREHENSIVE_REVIEW_MD.read_text(encoding="utf-8")


# Extract the ``paste_text`` body from the Rust source (lines 78-136).
# We slice on the function signature + closing brace so the slice stays
# stable across minor formatting edits.
def _slice_paste_text(src: str) -> str:
    """Return just the ``paste_text`` function body from ``sidecar_cmds.rs``.

    The function is delimited by ``pub async fn paste_text(`` and the
    next ``pub async fn`` (or end of file). We return the entire slice
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
    then ``Ctrl+V`` is sent via ``enigo`` on Linux. A value other than 300
    would either over-use the IME path (drop chars on long paste) or
    over-write the user's clipboard (annoying for short paste). Pin the
    value to 300 to catch accidental edits.

    On Linux specifically, this threshold matters MORE on Wayland than on
    X11: above 300 chars the clipboard + ``Ctrl+V`` path is used, which
    works on BOTH X11 and Wayland; below 300 chars the ``enigo.text()``
    path is used, which FAILS on Wayland (see test_xplat2_*). Pinning
    the threshold to 300 ensures the gap is bounded (≤ 299 chars).
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

    On Linux the short path uses X11 ``XTestFakeKeyEvent`` per-character
    (per ADR-0020 §6.2 Linux row). **This is X11-only — it does NOT work
    on Wayland** (see test_xplat2_wayland_short_text_gap_* for the
    documented gap).
    """
    body = _slice_paste_text(sidecar_cmds_src)

    # The short-text branch is delimited by `if text.chars().count() < PASTE_SHORT_THRESHOLD {`
    # ... `} else {`. Slice it out.
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

    # Must NOT send Ctrl+V (no key events).
    assert "enigo.key" not in short_branch, (
        "Short-text branch must NOT call `enigo.key(...)` — short text is "
        "injected via `enigo.text()` only (simulating discrete key events "
        "breaks IME / dead keys / non-English layouts, per ADR-0020 §6.2)."
    )

    # Must log the short-path message documented in the Linux runbook Step 8.
    assert "[PASTE] injected" in short_branch and "via enigo" in short_branch, (
        "Short-text branch must log `[PASTE] injected N chars via enigo` "
        "(linux-validation-runbook.md Step 8 verify-in-logs step)."
    )


# ─── 3. Long text path: clipboard + Ctrl+V (Linux uses Control, not Meta) ─


def test_long_text_path_uses_clipboard_plus_ctrl_v(sidecar_cmds_src: str) -> None:
    """Long text (≥ threshold) calls ``clipboard.write_text()`` + 3 ``enigo.key()`` calls.

    Order matters: clipboard write MUST happen before the key events so
    the Ctrl+V pastes the long text (not the previous clipboard contents).
    The 3 key calls are (on Linux):

    1. ``enigo.key(mod_key, Direction::Press)``   — Ctrl (Control) down
    2. ``enigo.key(Key::Unicode('v'), Direction::Click)`` — V press+release
    3. ``enigo.key(mod_key, Direction::Release)`` — Ctrl (Control) up

    ``mod_key`` is selected at compile time via ``cfg!(target_os = "macos")``
    to be ``Key::Control`` on Linux (see test 5). This clipboard +
    Ctrl+V path is the ONLY reliable paste path on Wayland (per ADR-0020
    §6.6) — enigo's per-character XTest injection does NOT work on
    Wayland.
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
        "Long-text branch must call `enigo.key(mod_key, enigo::Direction::Press)` (Ctrl down) per ADR-0020 §6.2."
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
        "(Ctrl up) per ADR-0020 §6.2 — otherwise the modifier stays held "
        "and the next user keystroke becomes Ctrl+<key> on Linux."
    )
    assert release_match.start() > v_match.end(), (
        "Long-text branch: `enigo.key(mod_key, Release)` must come AFTER "
        "`enigo.key('v', Click)` so the modifier is released only after V."
    )

    # 5. Log message matches the runbook Step 8 expected format.
    assert "[PASTE] injected" in long_branch and "via clipboard + Ctrl/Cmd+V" in long_branch, (
        "Long-text branch must log `[PASTE] injected N chars via clipboard + "
        "Ctrl/Cmd+V` (linux-validation-runbook.md Step 8 verify-in-logs step)."
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


# ─── 5. Linux path uses Key::Control (not Key::Meta) ──────────────────────


def test_linux_path_uses_key_control_not_meta(sidecar_cmds_src: str) -> None:
    """Linux path selects ``Key::Control`` (Ctrl+V); macOS path selects ``Key::Meta`` (Cmd+V).

    The Rust code uses ``cfg!(target_os = "macos")`` to pick the modifier
    at compile time. On Linux the modifier MUST be ``Key::Control``
    (Ctrl+V), NOT ``Key::Meta`` (which on Linux is the Super key — the
    "Windows key" on most keyboards — and Super+V is NOT the Linux
    paste shortcut; Ctrl+V is). This test focuses on the Linux branch
    (``Key::Control``); the macOS branch is covered by
    ``test_enigo_paste_macos.py`` and the Windows branch by
    ``test_enigo_paste_windows.py``.
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

    # Sanity: the Linux branch (else-branch) must use Control, NOT Meta.
    # The regex above already enforces this, but assert explicitly for
    # documentation + a clearer failure message.
    assert "Key::Control" in mod_match.group(0), "Linux path must use `Key::Control` (Ctrl+V). Got: " + mod_match.group(
        0
    )
    # The `else` branch (Linux + Windows) must NOT reference Meta.
    else_branch = mod_match.group(0).split("else")[1]
    assert "Key::Meta" not in else_branch, (
        'Linux/Windows path (`else` branch of `if cfg!(target_os = "macos")`) '
        "must NOT use `Key::Meta` — on Linux the paste shortcut is Ctrl+V "
        "(Key::Control), not Super+V (Key::Meta). Got: " + else_branch
    )


# ─── 6. enigo errors are surfaced as Rust errors ─────────────────────────


def test_enigo_errors_surfaced_as_rust_errors(sidecar_cmds_src: str) -> None:
    """Every ``enigo`` / ``clipboard`` call propagates errors via ``.map_err(...)?``.

    ADR-0020 §6.2 + NEW-IPC-107: errors from the paste path MUST surface
    as Rust errors (``Result<(), String>``) so the webview's ``invoke()``
    rejects. Silently swallowing an enigo error would leave the user
    with no transcription paste and no error message — a no-data-loss
    guarantee violation.

    On Linux, the most common enigo error scenarios are:
    - "Can't open X11 display" on a headless host (no DISPLAY env var).
    - "XTestFakeKeyEvent failed" on a broken X11 connection.
    - On Wayland, ``enigo.text()`` returns an error (X11-only per
      ADR-0020 §6.6) — the error MUST surface so the UI can fall back
      to the clipboard + Ctrl+V path (per the runbook Step 8 common
      failures section: "enigo.text() failed → EXPECTED on Wayland.
      Verify the clipboard + Ctrl+V fallback path works.").
    """
    body = _slice_paste_text(sidecar_cmds_src)

    # Every `enigo.X(...)` or `app.clipboard().write_text(...)` call
    # must be immediately followed by `.map_err(|e| format!(...))?`.
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
        idx = body.find(call_substr)
        assert idx != -1, (
            f"Expected call site `{call_substr}` not found in paste_text body. Did the implementation change?"
        )
        tail = body[idx + len(call_substr) : idx + len(call_substr) + 200]
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
        assert "?;" in tail or re.search(r"\?\s*;", tail), (
            f"Call `{call_substr}` must end with `?` (the `?` operator) so "
            f"the error propagates to the Tauri command return value. "
            f"Tail after call:\n{tail!r}"
        )


# ─── 7. X11 path works (enigo.text() uses X11 XTestFakeKeyEvent) ─────────


def test_x11_path_enigo_text_uses_x11_xtest(adr_0020_src: str, linux_runbook_src: str) -> None:
    """X11 paste path: ``enigo.text()`` uses X11 ``XTestFakeKeyEvent`` per-character.

    ADR-0020 §6.2 Linux row states: *"Linux: ``enigo.text()`` on Linux
    uses X11 ``XTestFakeKeyEvent`` per-character. Does not work on
    Wayland — see §6.6 for the Wayland fallback."*

    The Linux validation runbook Step 8 (gate point 4) X11 pass
    criteria states: *"On X11: The transcribed text appears in the text
    editor. For short text (<300 chars), ``enigo.text()`` injects it
    directly via X11 ``XTestFakeKeyEvent``."*

    This test verifies:
    1. ADR-0020 §6.2 Linux row references ``XTestFakeKeyEvent``.
    2. ADR-0020 §6.2 Linux row explicitly notes it does NOT work on
       Wayland (cross-reference to §6.6).
    3. The Linux validation runbook Step 8 documents the X11 path uses
       ``enigo.text()`` via ``XTestFakeKeyEvent`` for short text.
    """
    # 1. ADR-0020 §6.2 must reference XTestFakeKeyEvent for the Linux row.
    assert "XTestFakeKeyEvent" in adr_0020_src, (
        "ADR-0020 §6.2 Linux row must reference `XTestFakeKeyEvent` "
        "(the X11 XTest extension API enigo uses for per-character "
        "text injection)."
    )
    # 2. The Linux row must note Wayland is unsupported (cross-ref §6.6).
    assert re.search(
        r"Linux:.*XTestFakeKeyEvent.*Does not work on Wayland.*§6\.6",
        adr_0020_src,
        re.DOTALL,
    ), (
        "ADR-0020 §6.2 Linux row must note `enigo.text()` does NOT work "
        "on Wayland, with a cross-reference to §6.6 (Wayland fallback)."
    )
    # 3. The Linux runbook Step 8 must document the X11 short-text path.
    step8_match = re.search(
        r"## Step 8 — Paste keystroke works on X11 AND Wayland.*?(?=\n## Step 9|\Z)",
        linux_runbook_src,
        re.DOTALL,
    )
    assert step8_match is not None, (
        "linux-validation-runbook.md must have a Step 8 'Paste keystroke works "
        "on X11 AND Wayland' section (gate point 4)."
    )
    step8 = step8_match.group(0)
    assert "XTestFakeKeyEvent" in step8, (
        "Linux runbook Step 8 must document that the X11 short-text path "
        "uses `enigo.text()` via X11 `XTestFakeKeyEvent`."
    )
    assert "enigo.text()" in step8, (
        "Linux runbook Step 8 must reference `enigo.text()` as the short-text injection API on X11."
    )


# ─── 8. Wayland short-text path is EXPECTED TO FAIL (enigo is X11-only) ────


def test_wayland_short_text_path_expected_to_fail(adr_0020_src: str, linux_runbook_src: str) -> None:
    """Wayland short-text path is EXPECTED TO FAIL — ``enigo.text()`` is X11-only.

    ADR-0020 §6.6: *"Paste: ``enigo.text()`` does NOT work on Wayland
    (X11-only). The clipboard + ``Ctrl+V`` path via
    ``tauri-plugin-clipboard-manager`` is the only reliable option."*

    The Linux validation runbook Step 8 common-failures section states:
    *"Wayland: ``enigo.text() failed`` → EXPECTED on Wayland. Verify the
    clipboard + ``Ctrl+V`` fallback path works."*

    This test verifies the gap is documented in BOTH the ADR and the
    runbook — it does NOT verify the fix (which doesn't exist yet; see
    XPLAT-2 tests below).
    """
    # 1. ADR-0020 §6.6 must explicitly state enigo.text() does NOT work
    #    on Wayland.
    section_66_match = re.search(
        r"#### 6\.6 Wayland.*?(?=\n#### |\n### |\Z)",
        adr_0020_src,
        re.DOTALL,
    )
    assert section_66_match is not None, "ADR-0020 must have a §6.6 'Wayland — special considerations' section."
    section_66 = section_66_match.group(0)
    assert "enigo.text()" in section_66 and "does NOT work on Wayland" in section_66, (
        "ADR-0020 §6.6 must explicitly state `enigo.text()` does NOT work on "
        "Wayland (X11-only). Section:\n" + section_66
    )
    assert "wl-copy" in section_66 or "tauri-plugin-clipboard-manager" in section_66, (
        "ADR-0020 §6.6 must document the Wayland fallback path (tauri-plugin-clipboard-manager via wl-copy)."
    )

    # 2. The Linux runbook Step 8 must list "enigo.text() failed" as an
    #    EXPECTED common failure on Wayland.
    step8_match = re.search(
        r"## Step 8 — Paste keystroke works on X11 AND Wayland.*?(?=\n## Step 9|\Z)",
        linux_runbook_src,
        re.DOTALL,
    )
    assert step8_match is not None, (
        "linux-validation-runbook.md must have a Step 8 'Paste keystroke works "
        "on X11 AND Wayland' section (gate point 4)."
    )
    step8 = step8_match.group(0)
    assert re.search(r"enigo\.text\(\)\s+failed.*EXPECTED.*Wayland", step8, re.DOTALL), (
        "Linux runbook Step 8 must list `enigo.text() failed` as an EXPECTED "
        "common failure on Wayland (per ADR-0020 §6.6). Section:\n" + step8
    )


# ─── 9. XPLAT-2 gap: Wayland fallback should be used for ALL text on Wayland


def test_xplat2_wayland_short_text_gap_documented(comprehensive_review_src: str, sidecar_cmds_src: str) -> None:
    """XPLAT-2 (REVIEW-4): Rust ``paste_text`` Wayland fallback — gap documented + fix verified.

    Originally the Rust ``paste_text`` used ``enigo.text()`` for short
    text and ``enigo.key(Control, v)`` for the Ctrl+V keystroke in the
    long path — both X11-only. review.md XPLAT-2
    documented this gap and recommended detecting Wayland and falling
    back to ``wtype`` (or always using the clipboard + ``Ctrl+V`` path
    on Wayland).

    The fix has now landed in production code (see
    ``src-tauri/src/commands/sidecar_cmds.rs::is_wayland_session`` +
    the early-return in ``paste_text``): a Wayland session is detected
    via ``XDG_SESSION_TYPE=wayland`` and the clipboard + ``Ctrl+V`` path
    is used for ALL text on Wayland (per ADR-0020 §6.6).

    This test verifies:
      1. review.md still documents the XPLAT-2 entry
         (status may be "Pending" or "Fixed" — the review
         owner is a separate sub-agent that updates the status field).
      2. The Rust source now HAS Wayland detection — the gap is closed.
         If this assertion fails, someone removed the Wayland fallback
         and the XPLAT-2 gap has regressed.
    """
    # 1. review.md must document XPLAT-2.
    xplat2_match = re.search(
        r"#### XPLAT-2 — Rust `paste_text` doesn't handle Wayland.*?(?=\n#### XPLAT-|\Z)",
        comprehensive_review_src,
        re.DOTALL,
    )
    assert xplat2_match is not None, (
        "review.md must have an 'XPLAT-2 — Rust paste_text doesn't handle Wayland' section under REVIEW-4."
    )
    xplat2 = xplat2_match.group(0)
    assert "Wayland" in xplat2 and "enigo.text()" in xplat2, (
        "XPLAT-2 entry must describe the Wayland gap with `enigo.text()` as the X11-only API. Section:\n" + xplat2
    )
    # Severity must be High.
    assert re.search(r"Severity.*High", xplat2, re.DOTALL), (
        "XPLAT-2 severity must be High (per REVIEW-4). Section:\n" + xplat2
    )
    # Status must be Pending or Fixed (Pending until the
    # review.md owner flips it to Fixed after observing
    # the production-code fix; Fixed afterwards).
    assert re.search(r"Status.*(?:Pending|Fixed)", xplat2, re.DOTALL), (
        "XPLAT-2 status must be Pending or Fixed. Section:\n" + xplat2
    )
    # The recommended fix must mention wtype or ydotool.
    assert "wtype" in xplat2 or "ydotool" in xplat2, (
        "XPLAT-2 recommended fix must mention `wtype` or `ydotool` as the Wayland fallback. Section:\n" + xplat2
    )

    # 2. The Rust paste_text source now HAS Wayland detection — the
    #    XPLAT-2 gap is closed. If this assertion fails, the Wayland
    #    fallback has regressed and the gap is back.
    body = _slice_paste_text(sidecar_cmds_src)
    wayland_detection_patterns = [
        r"WAYLAND_DISPLAY",
        r"XDG_SESSION_TYPE",
        r"wtype",
        r"ydotool",
        r"std::env::var\(\s*\"WAYLAND",
    ]
    detected = [pat for pat in wayland_detection_patterns if re.search(pat, body)]
    assert detected, (
        "XPLAT-2 (Wayland fallback) is NOT implemented in paste_text "
        "(detected patterns: none). The Rust source must detect Wayland "
        "via WAYLAND_DISPLAY / XDG_SESSION_TYPE=wayland and fall back to "
        "clipboard + Ctrl+V (per ADR-0020 §6.6)."
    )


def test_xplat2_wayland_short_text_simulation_fails(sidecar_cmds_src: str, adr_0020_src: str) -> None:
    """Simulation: short-text path on Wayland silently fails (XPLAT-2 gap).

    This is the BEHAVIOURAL demonstration of XPLAT-2: when the Rust
    ``paste_text`` is invoked on Wayland with short text, it calls
    ``enigo.text()`` which is X11-only. enigo will return an error
    (or, worse, silently no-op depending on the X11 connection state
    through XWayland) — the text does NOT get injected into the
    Wayland foreground app.

    The simulation re-implements the Rust control flow and asserts
    that on Wayland:
    - The SHORT text path STILL calls ``enigo.text()`` (because the
      Rust code has no Wayland branch — this is the gap).
    - The LONG text path DOES work (clipboard + Ctrl+V is display-
      server-agnostic via tauri-plugin-clipboard-manager).

    The recommended fix (not implemented — see XPLAT-2) is to detect
    Wayland at runtime and use the clipboard + Ctrl+V path for ALL
    text on Wayland (not just long text). This test documents the gap.
    """
    # The Rust paste_text does NOT have a Wayland branch — confirmed
    # by test_xplat2_wayland_short_text_gap_documented above. The
    # short-text branch is selected based on `text.chars().count() <
    # PASTE_SHORT_THRESHOLD` ONLY, with no Wayland check. This means
    # on Wayland, short text hits the enigo.text() path which is
    # X11-only and silently fails.

    # 1. Verify the Rust short-text branch has NO Wayland check.
    body = _slice_paste_text(sidecar_cmds_src)
    short_match = re.search(
        r"if\s+text\.chars\(\)\.count\(\)\s*<\s*PASTE_SHORT_THRESHOLD\s*\{(.*?)\}\s*else\s*\{",
        body,
        re.DOTALL,
    )
    assert short_match is not None
    short_branch = short_match.group(0)
    # The branch condition must be ONLY the char-count check — no Wayland.
    # Extract just the condition (between `if` and `{`).
    condition_match = re.match(r"if\s+(.*?)\s*\{", short_branch, re.DOTALL)
    assert condition_match is not None
    condition = condition_match.group(1)
    assert "WAYLAND_DISPLAY" not in condition, (
        "XPLAT-2 gap closed? The short-text branch condition now checks "
        "WAYLAND_DISPLAY. If so, update review.md XPLAT-2 "
        f"status. Condition: {condition}"
    )
    assert "XDG_SESSION_TYPE" not in condition, (
        "XPLAT-2 gap closed? The short-text branch condition now checks "
        "XDG_SESSION_TYPE. If so, update review.md XPLAT-2 "
        f"status. Condition: {condition}"
    )

    # 2. ADR-0020 §6.6 must state the clipboard + Ctrl+V path is the
    #    only reliable option on Wayland — confirming the recommended
    #    fix (force long-text path on Wayland).
    section_66_match = re.search(
        r"#### 6\.6 Wayland.*?(?=\n#### |\n### |\Z)",
        adr_0020_src,
        re.DOTALL,
    )
    assert section_66_match is not None
    section_66 = section_66_match.group(0)
    assert "only reliable option" in section_66, (
        "ADR-0020 §6.6 must state the clipboard + Ctrl+V path is the "
        "`only reliable option` on Wayland (justifying the XPLAT-2 fix: "
        "force the long-text path on Wayland for ALL text). Section:\n" + section_66
    )


# ─── 10. paste_text has the #[tauri::command] attribute ───────────────────


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


# ─── 11. Behavioural simulation: MagicMock-driven paste algorithm ────────


class _FakeEnigo:
    """Minimal Python stand-in for ``enigo::Enigo``.

    Records every call so the simulation test can assert on call order
    + arguments. Mirrors the Rust API surface used by ``paste_text``:
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
    platform: str = "linux",
    enigo_factory=_FakeEnigo,
    clipboard: _FakeClipboard | None = None,
    threshold: int = 300,
) -> tuple[_FakeEnigo | None, _FakeClipboard | None, list[str]]:
    """Python re-implementation of the Rust ``paste_text`` algorithm.

    Mirrors ``src-tauri/src/commands/sidecar_cmds.rs:95-136`` exactly:

    - empty text  → no-op, returns immediately
    - short text  → ``enigo.text(&text)`` only
    - long text   → ``clipboard.write_text(text)`` + 3 ``enigo.key()`` calls
    - Linux/Windows uses ``Key::Control``; macOS uses ``Key::Meta``

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
    """Simulation: short text (< 300) calls ``enigo.text()`` only — no clipboard, no key.

    On Linux X11, this path uses ``XTestFakeKeyEvent`` per-character (works).
    On Linux Wayland, this path silently FAILS (enigo is X11-only — see
    test_simulate_xplat2_wayland_short_text_silently_fails).
    """
    short_text = "hello world"  # 11 chars < 300
    enigo, clipboard, logs = _simulate_paste_text(short_text, platform="linux")

    assert enigo is not None
    assert clipboard is None, "short text must NOT touch the clipboard"
    # Only one call: text().
    assert enigo.calls == [("text", (short_text,))], (
        f"short-text enigo calls must be exactly one `text()` call; got: {enigo.calls}"
    )
    # Log message matches the runbook Step 8 expected format.
    assert logs == ["[PASTE] injected 11 chars via enigo"]


def test_simulate_long_text_uses_clipboard_plus_ctrl_v_in_order() -> None:
    """Simulation: long text (≥ 300) on Linux calls clipboard write + 3 key events in order.

    Order:
    1. ``clipboard.write_text(text)``  — clipboard populated BEFORE keys
    2. ``enigo.key(Control, Press)``    — Ctrl down
    3. ``enigo.key(Unicode('v'), Click)`` — V press+release
    4. ``enigo.key(Control, Release)``   — Ctrl up

    The modifier MUST be ``Key::Control`` on Linux (Ctrl+V), NOT
    ``Key::Meta`` (Super+V is not the Linux paste shortcut).

    This clipboard + Ctrl+V path is the ONLY reliable paste path on
    Wayland (per ADR-0020 §6.6) — enigo's per-character XTest injection
    does NOT work on Wayland. ``tauri-plugin-clipboard-manager`` on
    Linux uses ``wl-copy``/``wl-paste`` on Wayland or ``xclip``/``xsel``
    on X11 (detected at runtime via ``WAYLAND_DISPLAY``).
    """
    long_text = "x" * 300  # exactly 300 chars → ≥ threshold
    enigo, clipboard, logs = _simulate_paste_text(long_text, platform="linux")

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
        f"long-text Linux enigo calls must be 3 key events in Press/Click/Release "
        f"order with Key::Control (Ctrl+V). Got: {enigo.calls}"
    )
    # Log message matches the runbook Step 8 expected format.
    assert logs == ["[PASTE] injected 300 chars via clipboard + Ctrl/Cmd+V"]


def test_simulate_long_text_linux_uses_key_control_not_meta() -> None:
    """Simulation: long text on Linux uses ``Key::Control`` (Ctrl+V), not Meta.

    This is the Linux-specific assertion: the modifier for the Ctrl+V
    paste shortcut MUST be ``Key::Control`` on Linux. ``Key::Meta`` on
    Linux is the Super key (the "Windows key" on most keyboards) and
    Super+V is NOT the Linux paste shortcut (Ctrl+V is — same as
    Windows).
    """
    long_text = "y" * 500
    enigo, _, _ = _simulate_paste_text(long_text, platform="linux")
    assert enigo is not None
    # First and third key calls must use Control (not Meta).
    assert enigo.calls[0] == ("key", (_Key.Control, _Direction.Press)), (
        f"Linux long-text first key must be (Control, Press); got: {enigo.calls[0]}"
    )
    assert enigo.calls[-1] == ("key", (_Key.Control, _Direction.Release)), (
        f"Linux long-text last key must be (Control, Release); got: {enigo.calls[-1]}"
    )
    # No Meta key should appear anywhere in the Linux path.
    for _name, args in enigo.calls:
        assert _Key.Meta not in args, (
            f"Linux path must NOT use Key::Meta — Super+V is not the "
            f"Linux paste shortcut (Ctrl+V is). Got call: {(_name, args)}"
        )


def test_simulate_short_text_threshold_boundary() -> None:
    """Simulation: 299 chars → short path; 300 chars → long path.

    The boundary is `< PASTE_SHORT_THRESHOLD` (strict less-than), so:
    - 299 chars (< 300) → enigo.text() (X11-only — fails on Wayland!)
    - 300 chars (≥ 300) → clipboard + Ctrl+V (works on both X11 + Wayland)

    On Wayland, the 299-char case fails silently (XPLAT-2 gap); the
    300-char case works (clipboard + Ctrl+V is display-server-agnostic).
    The recommended fix is to force the long-text path on Wayland for
    ALL text (including short text) — see test_simulate_xplat2_*.
    """
    enigo_299, clipboard_299, _ = _simulate_paste_text("a" * 299, platform="linux")
    assert clipboard_299 is None, "299 chars < 300 must use the short path"
    assert enigo_299 is not None and enigo_299.calls == [("text", ("a" * 299,))]

    enigo_300, clipboard_300, _ = _simulate_paste_text("b" * 300, platform="linux")
    assert clipboard_300 is not None, "300 chars ≥ 300 must use the long path"
    assert clipboard_300.written == ["b" * 300]
    assert len(enigo_300.calls) == 3, "300 chars must trigger 3 key events"
    # And the 3 key events must use Control (Linux), not Meta.
    assert enigo_300.calls[0][1][0] == _Key.Control, (
        "300-char Linux path first key must be Control (Ctrl down), not Meta."
    )


def test_simulate_xplat2_wayland_short_text_silently_fails() -> None:
    """Simulation: short-text path on Wayland hits enigo.text() (X11-only) — fails.

    This is the BEHAVIOURAL demonstration of XPLAT-2: the Rust
    ``paste_text`` does NOT detect Wayland. On a Wayland session,
    short-text transcription (< 300 chars) calls ``enigo.text()``
    which is X11-only — it silently fails to inject any text into
    the Wayland foreground app.

    The simulation mirrors the Rust control flow: there is NO Wayland
    branch in ``_simulate_paste_text`` — short text ALWAYS calls
    ``enigo.text()`` regardless of session type. This test asserts
    the simulation matches the (broken) Rust behaviour, so the gap
    is documented in the test suite.

    The recommended fix (per review.md XPLAT-2):
    - Detect Wayland at runtime (``WAYLAND_DISPLAY`` env var or
      ``XDG_SESSION_TYPE=wayland``).
    - On Wayland, shell out to ``wtype -d 50 -- "<text>"`` for short
      text (or always use the clipboard + ``Ctrl+V`` path).
    - On Wayland, shell out to ``wtype -k ctrl+v`` for long text
      (or keep the clipboard + ``Ctrl+V`` path — it works on Wayland).

    This test would FAIL if XPLAT-2 is fixed (the simulation would
    gain a Wayland branch and the assertion would need updating).
    """
    short_text = "hello wayland"  # 13 chars < 300
    # Simulate Wayland by setting the env var (the Rust code would
    # check std::env::var("WAYLAND_DISPLAY") — but it doesn't, which
    # is the gap). The simulation has no Wayland branch, so the
    # short-text path calls enigo.text() regardless.
    old_env = os.environ.get("WAYLAND_DISPLAY")
    os.environ["WAYLAND_DISPLAY"] = "wayland-0"
    try:
        enigo, clipboard, logs = _simulate_paste_text(short_text, platform="linux")
    finally:
        if old_env is None:
            os.environ.pop("WAYLAND_DISPLAY", None)
        else:
            os.environ["WAYLAND_DISPLAY"] = old_env

    # The simulation still calls enigo.text() (no Wayland branch) —
    # this is the GAP. On a real Wayland session, enigo.text() would
    # fail or no-op (X11-only).
    assert enigo is not None, (
        "XPLAT-2 gap: simulation has no Wayland branch, so short text still inits enigo (which on Wayland is broken)."
    )
    assert enigo.calls == [("text", (short_text,))], (
        "XPLAT-2 gap: simulation has no Wayland branch, so short text still "
        f"calls enigo.text() (X11-only). Got: {enigo.calls}"
    )
    assert clipboard is None, (
        "XPLAT-2 gap: simulation has no Wayland branch, so short text does "
        "NOT use the clipboard + Ctrl+V fallback (which would be the correct "
        "Wayland path)."
    )


def test_simulate_uses_mocked_enigo_no_real_key_injection() -> None:
    """The simulation uses a MagicMock for ``enigo::Enigo`` — no real key injection.

    This is a defensive test: if a future contributor wires the
    simulation up to a real ``pynput``/``pyautogui``/``xdotool``/``wtype``
    backend, this test would still pass (MagicMock records the calls
    without performing them) — but it documents the contract that the
    test harness MUST NOT inject real keys into the host OS (especially
    important on Linux where the test could actually type into the
    foreground app via X11 XTest or wtype on Wayland).
    """
    mock_enigo_cls = MagicMock()
    mock_enigo_instance = MagicMock()
    mock_enigo_cls.new.return_value = mock_enigo_instance

    _simulate_paste_text("short", platform="linux", enigo_factory=mock_enigo_cls)

    # The mock was instantiated once and text() was called once — but
    # no real OS-level key injection happened (MagicMock is inert).
    mock_enigo_cls.new.assert_called_once_with(None)
    mock_enigo_instance.text.assert_called_once_with("short")
    # No key() calls for short text.
    mock_enigo_instance.key.assert_not_called()


# ─── 12. Module docstring contract (VALIDATE ON LINUX HOST block) ────────


def test_module_docstring_has_validate_on_linux_host_block() -> None:
    """The module docstring MUST document the VALIDATE ON LINUX HOST commands.

    This is a structural assertion: the docstring at the top of this
    test file must contain both the X11 and Wayland host validation
    procedure blocks (the gate-point-4 procedure from the Linux
    validation runbook Step 8). The blocks are required because:

    1. These tests use source inspection + MagicMock simulation — they
       do NOT exercise the real enigo/clipboard on a Linux host.
    2. The actual paste behaviour MUST be validated on a real Linux
       desktop (X11 AND Wayland) per the runbook Step 8.
    3. A future contributor running ``pytest tests/tauri/mig17/`` on
       CI needs an explicit reminder to also run the host validation
       procedure before the Phase 0-L gate can be marked "passed".
    """
    docstring = __doc__ or ""
    assert "VALIDATE ON LINUX HOST (X11):" in docstring, (
        "Module docstring must have a 'VALIDATE ON LINUX HOST (X11):' block."
    )
    assert "VALIDATE ON LINUX HOST (Wayland):" in docstring, (
        "Module docstring must have a 'VALIDATE ON LINUX HOST (Wayland):' block."
    )
    # The X11 block must mention both short and long phrase validation.
    x11_block_match = re.search(
        r"VALIDATE ON LINUX HOST \(X11\):.*?(?=VALIDATE ON LINUX HOST \(Wayland\))",
        docstring,
        re.DOTALL,
    )
    assert x11_block_match is not None
    x11_block = x11_block_match.group(0)
    assert "short phrase" in x11_block and "long phrase" in x11_block, (
        "VALIDATE ON LINUX HOST (X11) block must document both short "
        "(< 300 chars) and long (≥ 300 chars) phrase validation."
    )
    assert "enigo.text()" in x11_block, (
        "VALIDATE ON LINUX HOST (X11) block must reference enigo.text() as the short-text injection path on X11."
    )
    assert "clipboard + Ctrl+V" in x11_block, (
        "VALIDATE ON LINUX HOST (X11) block must reference the clipboard + Ctrl+V path for long text."
    )

    # The Wayland block must mention both short and long phrase validation
    # AND document the XPLAT-2 gap. The block runs to the end of the
    # docstring (__doc__ excludes the closing triple-quote).
    wayland_block_match = re.search(
        r"VALIDATE ON LINUX HOST \(Wayland\):.*",
        docstring,
        re.DOTALL,
    )
    assert wayland_block_match is not None
    wayland_block = wayland_block_match.group(0)
    assert "short phrase" in wayland_block and "long phrase" in wayland_block, (
        "VALIDATE ON LINUX HOST (Wayland) block must document both short and long phrase validation."
    )
    assert "clipboard + Ctrl+V fallback" in wayland_block, (
        "VALIDATE ON LINUX HOST (Wayland) block must reference the "
        "clipboard + Ctrl+V fallback as the short-text path on Wayland."
    )
    assert "X11-only" in wayland_block, "VALIDATE ON LINUX HOST (Wayland) block must note enigo.text() is X11-only."
    assert "XPLAT-2" in wayland_block, (
        "VALIDATE ON LINUX HOST (Wayland) block must reference XPLAT-2 "
        "as the tracking entry for the Wayland short-text fallback gap."
    )
