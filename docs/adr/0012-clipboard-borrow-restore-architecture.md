# ADR 0012: Clipboard Borrow/Restore Architecture

**Status**: Final
**Date**: 2026-07-13
**Revised**: 2026-07-13 (wired `refresh_config()` to correct file `service.apply_config()`; added required `IPC_CONFIG_ALLOWLIST` entries; hardened `get_latest_text()` ordering; synced `paste_enabled` from `paste_on_stop` in `refresh_config()` and added `paste_on_stop` trigger; added `force` bypass so repaste is independent of auto-paste; added redundant-clipboard-cycle optimization)
**Supersedes**: Dead code at `clipboard.py:812` (`schedule_clipboard_clear`), `clipboard.py:543` (`_saved_clipboard`), `clipboard.py:541` (`_clear_thread`)
**Affected files**: `clipboard.py`, `dictation_pipeline.py`, `app.py`, `history_db.py`, `config.py`, `config_validators.py`, `service.py`, new `clipboard_snapshot.py`

---

## 1. Problem Statement

The clipboard is a shared resource. The app borrows it to deliver transcriptions; the user also uses it for their own copying. Today these two consumers collide in eight documented ways (P1–P8). The root cause: the app overwrites the clipboard and never restores it, and the repaste feature relies on a volatile in-memory variable instead of the persistent history database.

This ADR defines a **clipboard borrow/restore** architecture in which:

1. Every clipboard borrow is paired with a restore — no exceptions, no flags that silently disable safety.
2. The snapshot captures **all** clipboard formats (text, RTF, HTML, images, file lists) on Windows and macOS; text-only on Linux (documented limitation).
3. The restore fires **independent of the paste keystroke** — `paste_on_stop = False` does not cause data loss.
4. The restore runs on a **daemon thread** so the transcription pipeline's `finally` block is not delayed.
5. The snapshot is passed as a **value parameter**, not stored as shared mutable instance state — overlapping cycles cannot clobber each other.
6. Repaste reads from the **history database**, surviving app restarts.

---

## 2. Critical Findings From Codebase Review

These findings drive the design. Each is verified against the actual source.

### 2.1 `schedule_clipboard_clear()` is dead code

`clipboard.py:812` defines `schedule_clipboard_clear()`. It is **never called from production code** — only from unit tests (`test_clipboard_coverage.py`, `test_clipboard_security.py`, `test_plat_fixes.py`, `test_clipboard_win32_coverage.py`). The snapshot at `clipboard.py:714` (`self._saved_clipboard = pyperclip.paste()`) fires on every `copy()` but the captured value is orphaned and overwritten on the next `copy()`.

**Consequence**: Today, transcription text lands on the clipboard and stays there indefinitely. The user's previous clipboard content is captured but never restored.

### 2.2 The existing snapshot captures plain text only

`clipboard.py:714`: `self._saved_clipboard = pyperclip.paste()`. `pyperclip.paste()` returns `str` — only `CF_UNICODETEXT` (Windows), `pbpaste` plain text (macOS), or `xclip -o` default target (Linux). No image, RTF, HTML, or file-list format is captured.

### 2.3 `copy()` actively destroys non-text formats on Windows

`clipboard.py:697` calls `_win32_empty_clipboard()` before `pyperclip.copy(text)`. This is `EmptyClipboard()` — it wipes **all** formats before writing text. The comment at line 693 says this is intentional ("PLAT-006: clear rich text artifacts").

**Consequence**: Even if we added multi-format save, the snapshot must be captured **before** the empty, which is the current ordering — so the snapshot is salvageable. But on restore, we must write back all formats, not just text.

### 2.4 `refresh_config()` exists but is never called

`clipboard.py:550` defines `refresh_config(config)`, which sets `self._clipboard_save_restore_enabled` from `config.clipboard_save_restore`. A full-repo grep shows **zero call sites** outside the class itself. `app.py:259` constructs `ClipboardManager(paste_enabled=self.config.paste_on_stop)` and never calls `refresh_config`. The flag is initialized to `True` at `clipboard.py:548` and stays `True` forever.

### 2.5 `_last_transcription` is in-memory, set by the pipeline

`app.py:359` initializes `self._last_transcription = ""`. The real value is set at `dictation_pipeline.py:591`: `self._app._last_transcription = text`. It is cleared only at `app.py:1033` (after `undo_last()`). It is **not persisted** — on app restart, it resets to `""`.

### 2.6 `history_db` has no `get_latest()` method

The transcription table schema:

```sql
CREATE TABLE transcriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    duration REAL DEFAULT 0,
    model TEXT DEFAULT '',
    device TEXT DEFAULT '',
    word_count INTEGER DEFAULT 0,
    char_count INTEGER DEFAULT 0,
    favorite INTEGER DEFAULT 0,
    language TEXT DEFAULT ''
)
```

Existing read methods: `get_recent(limit=50)`, `search(query)`, `get_favorites()`, `get_stats()`, `get_today_stats()`. **No `get_latest()` or `get_by_id()`.** Closest: `get_recent(limit=1)` returns `list[dict]`.

### 2.7 `add_transcription()` is fire-and-forget; `flush()` blocks

`history_db.py:add_transcription()` enqueues onto a writer-thread `queue.Queue` with `wait=False` and returns the placeholder `1` immediately. The row may not be committed when the function returns.

`history_db.py:flush()` enqueues a no-op write with `wait=True` and blocks on its future. Because the queue is FIFO, all writes submitted before `flush()` will have completed by the time the no-op runs. **`flush()` is the blocking primitive.**

### 2.8 `paste()` already has Windows safety checks

`clipboard.py:957` calls `_is_safe_paste_target()`, which checks:
- Window class blocklist: `{"#32770", "Credential Dialog Xaml Host", "CredDialog"}`
- Elevated-target detection (token elevation comparison via `OpenProcessToken` + `GetTokenInformation`)
- Password-field detection via UI Automation `IsPassword` property (comtypes), with hardcoded fallback classes if comtypes is unavailable
- Rich-editor detection (logging-only, not blocking)

On macOS and Linux, `_is_safe_paste_target()` returns `True` immediately — no checks.

### 2.9 The transcription pipeline runs on a background thread

`dictation_pipeline.py:60` (docstring): "The pipeline is run on a background thread by VoiceTyperApp." `copy()` and `paste()` are called inline from this thread (lines 628, 669). The `finally` block at lines 211–265 zeros the audio buffer, resets the watchdog, and clears `_transcription_thread`. **Blocking the pipeline thread delays cleanup.**

### 2.10 Config changes flow through `config_handlers.py` → `service.apply_config()`

`config_handlers.py:121` delegates to `self.service.apply_config(validated)`. The actual mutation (lock + `setattr` + `apply_config_side_effects` + `config.save()`) happens in `service.py:apply_config()` (lines 1082–1129), **NOT** in `config_handlers.py`. The handler does not hold the lock or call `config.save()` itself. This is the single integration point for `refresh_config()`.

> The earlier draft of this ADR (§8.3) pointed at `config_handlers.py:140`. That location is wrong — the wiring must be inside `service.apply_config()`, after `app.config.save()`.

### 2.11 Clipboard config keys are NOT in the IPC allowlist (BLOCKER for DP7)

`config_validators.py:IPC_CONFIG_ALLOWLIST` (lines 466–544) contains `paste_on_stop` (line 494) but **NOT** `clipboard_save_restore` or `clipboard_restore_delay_ms`. Consequences if this is not fixed:

- `validate_config_update()` drops those keys from `validated`.
- `service.apply_config()` never `setattr`s them; `config.save()` does not persist them.
- The `refresh_config()` trigger (`if clipboard_keys & set(validated.keys())`) is **always False** → `refresh_config()` never fires at runtime.
- The renderer / Settings UI cannot change these settings at all.

This silently breaks the entire DP7 goal ("config flags are actually consulted"). Both keys **MUST** be added to `IPC_CONFIG_ALLOWLIST` with validators `(bool, _bool_validator)` and `(int, _make_int_validator(lo=0, hi=2000))`, and exposed in the renderer config schema so the UI can reach them. See §8.3 and §14.

### 2.12 `paste_enabled` is stale — runtime `paste_on_stop` toggle is broken (BLOCKER for UX)

`clipboard.py:530` stores `self.paste_enabled = paste_enabled` **once**, at construction (`app.py:280` passes `self.config.paste_on_stop`). `refresh_config()` (lines 550–562) does **not** update `paste_enabled`. The auto-paste decision in the pipeline reads `config.paste_on_stop` directly (`dictation_pipeline.py:688`), so `apply_config` toggling the flag *does* change the pipeline branch — but `paste()` re-checks `self.paste_enabled` internally (`clipboard.py:953`, `if not self.paste_enabled: return False`).

Failure mode: start with `paste_on_stop = False` (so `paste_enabled = False`), toggle auto-paste **ON** in the UI. `config.paste_on_stop` becomes `True`, the pipeline calls `paste(snapshot)` — but `paste()`'s stale `paste_enabled = False` gate returns `False` before sending the keystroke. Auto-paste silently does nothing until restart. The same stale gate also breaks **repaste** (`app.py:repaste_last` calls `clipboard.paste()`), because repaste is a manual user action that must never be coupled to the auto-paste setting.

Fix (§5.5, §5.3, §7.1, §8.3): `refresh_config()` must sync `self.paste_enabled = bool(getattr(config, "paste_on_stop", True))`, the `refresh_config()` trigger set must include `paste_on_stop`, and `paste()` must accept `force=True` so `repaste_last` bypasses the `paste_enabled` gate.

---

## 3. Design Principles

These principles are non-negotiable. Every design decision below traces back to one of these.

| # | Principle | Rationale |
|---|---|---|
| DP1 | **Every borrow is paired with a restore.** No code path may overwrite the clipboard without restoring the prior content (unless the snapshot is `None` due to capture failure). | Eliminates P1, P4, P5, P7 data loss. |
| DP2 | **Restore is decoupled from paste.** The restore fires whether or not a paste keystroke is sent. `paste_on_stop = False` does not skip restore. | Eliminates the critical data-loss bug where `paste_on_stop = False` permanently destroys the user's clipboard. |
| DP3 | **Restore runs on a daemon thread.** The transcription pipeline thread is never blocked by a restore delay. | Eliminates the pipeline-stall regression. |
| DP4 | **Snapshots are passed as values, not stored as instance state.** `copy()` returns a snapshot; `paste()` and `restore_now()` accept one. No `self._snapshot` attribute. | Eliminates overlapping-cycle race conditions. |
| DP5 | **The snapshot captures all formats on Windows and macOS; text-only on Linux.** Linux X11/Wayland limitations are documented, not hidden. | Preserves user's images, RTF, HTML, file lists where platform APIs allow. |
| DP6 | **Repaste reads from the database, not in-memory.** Survives app restarts. | Eliminates P6. |
| DP7 | **Config flags are actually consulted.** Every `config.py` field added must have a read site in the production code path. | Eliminates inert-flag bugs. |
| DP8 | **Dead code is removed, not left alongside.** `schedule_clipboard_clear`, `_saved_clipboard`, `_clear_thread` are deleted. | Prevents confusion about which path is live. |

---

## 4. Architecture: `clipboard_snapshot.py` (new file)

A standalone module that captures and restores **all** clipboard formats. Platform-dispatched. No dependency on `pyperclip` (which is text-only).

### 4.1 Public interface

```python
# clipboard_snapshot.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClipboardSnapshot:
    """A captured snapshot of clipboard content at a point in time.

    Captures all formats (text, RTF, HTML, image, file lists) on
    Windows and macOS. Captures text-only on Linux (X11 and Wayland)
    due to CLI tool limitations — see §4.5 and §4.6.

    Usage:
        snap = ClipboardSnapshot.capture()
        if snap is not None:
            try:
                pyperclip.copy(transcription_text)
                send_paste_keystroke()
                time.sleep(0.15)
            finally:
                snap.restore()
    """

    platform: str  # "windows" | "macos" | "linux-x11" | "linux-wayland"
    items: list[tuple[Any, ...]]  # platform-specific payload
    captured_at: float  # monotonic timestamp for debugging

    @classmethod
    def capture(cls) -> "ClipboardSnapshot | None":
        """Capture the current clipboard. Returns None on failure."""
        ...

    def restore(self) -> bool:
        """Restore all captured formats. Returns False on failure."""
        if self.platform == "windows":
            return self._restore_windows()
        elif self.platform == "macos":
            return self._restore_macos()
        elif self.platform == "linux-x11":
            return self._restore_x11()
        elif self.platform == "linux-wayland":
            return self._restore_wayland()
        return False

    # Platform-specific capture/restore methods (see §4.2–§4.6)
```

**Key design decisions:**
- `capture()` is a classmethod returning a **new instance** (or `None`). The snapshot is a value, not stored on a manager.
- `restore()` dispatches on `self.platform` — the platform tag is captured at creation time and travels with the snapshot. No global state.
- The snapshot is **immutable** (dataclass). Overlapping cycles each get their own instance.

### 4.2 Windows capture (Win32 API)

```python
@classmethod
def _capture_windows(cls) -> "ClipboardSnapshot | None":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # GMEM_MOVEABLE | GMEM_ZEROINIT
    GMEM_MOVEABLE = 0x0002

    if not user32.OpenClipboard(0):
        return None
    try:
        items: list[tuple[int, str, bytes]] = []
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if fmt == 0:
                break

            # Get human-readable name (for registered formats)
            name_buf = ctypes.create_unicode_buffer(256)
            name_len = user32.GetClipboardFormatNameW(fmt, name_buf, 256)
            name = name_buf.value if name_len > 0 else _builtin_format_name(fmt)

            handle = user32.GetClipboardData(fmt)
            if not handle:
                continue

            size = kernel32.GlobalSize(handle)
            if size == 0:
                continue

            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                continue
            try:
                data = ctypes.string_at(ptr, size)
            finally:
                kernel32.GlobalUnlock(handle)

            items.append((fmt, name, data))

        return cls(
            platform="windows",
            items=items,
            captured_at=time.monotonic(),
        )
    finally:
        user32.CloseClipboard()
```

**Formats captured on Windows:**

| Format ID | Name | Content |
|---|---|---|
| 13 | `CF_UNICODETEXT` | Plain text (UTF-16) |
| 1 | `CF_TEXT` | Plain text (ANSI) |
| 7 | `CF_OEMTEXT` | Plain text (OEM) |
| 8 | `CF_DIB` | Bitmap (DIB bits) |
| 17 | `CF_DIBV5` | Bitmap v5 |
| 2 | `CF_BITMAP` | Bitmap handle (note: cannot be round-tripped via GlobalAlloc; skipped on restore) |
| 3 | `CF_METAFILEPICT` | Metafile |
| 14 | `CF_ENHMETAFILE` | Enhanced metafile |
| 15 | `CF_HDROP` | File list (HDROP) |
| registered | `"Rich Text Format"` | RTF |
| registered | `"HTML Format"` | CF_HTML |
| registered | `"UniformResourceLocator"` | URL |
| registered | `"UniformResourceLocatorW"` | URL (wide) |
| registered | `"text/html"` | HTML (alt) |
| registered | `"text/uri-list"` | URI list (alt) |
| registered | `"PNG"` | PNG image |
| registered | `"JFIF"` / `"JPEG"` | JPEG image |

`_builtin_format_name(fmt)` maps standard IDs (1, 2, 3, 7, 8, 13, 14, 15, 17) to names; returns `""` for unknown builtins.

### 4.3 Windows restore (Win32 API)

```python
def _restore_windows(self) -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    GMEM_MOVEABLE = 0x0002

    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        for fmt, name, data in self.items:
            # CF_BITMAP (2) and metafile handles cannot be restored
            # from raw bytes — skip them.
            if fmt in (2, 3, 14):
                continue

            # For registered formats, re-register to get the format ID.
            # The ID may differ from the original (Windows assigns IDs
            # dynamically), but the name match is what matters.
            target_fmt = fmt
            if name:
                target_fmt = user32.RegisterClipboardFormatW(name)
                if target_fmt == 0:
                    continue

            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h_mem:
                continue
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                continue
            try:
                ctypes.memmove(ptr, data, len(data))
            finally:
                kernel32.GlobalUnlock(h_mem)

            # SetClipboardData takes ownership of h_mem.
            if not user32.SetClipboardData(target_fmt, h_mem):
                kernel32.GlobalFree(h_mem)
        return True
    finally:
        user32.CloseClipboard()
```

**Edge cases handled:**
- `CF_BITMAP` (2), `CF_METAFILEPICT` (3), `CF_ENHMETAFILE` (14) are GDI handles, not byte streams — skipped on restore (documented limitation; rare in practice since `CF_DIB` / `CF_DIBV5` carry the actual image bits).
- Registered format IDs are re-obtained via `RegisterClipboardFormatW(name)` — the ID may differ from the original but the name match ensures correct rendering.
- `SetClipboardData` ownership transfer: if it fails, we free the handle to avoid leaks.

### 4.4 macOS capture and restore (NSPasteboard)

```python
@classmethod
def _capture_macos(cls) -> "ClipboardSnapshot | None":
    import AppKit
    import Foundation

    pb = AppKit.NSPasteboard.generalPasteboard()
    items: list[tuple[int, str, bytes]] = []

    for idx, item in enumerate(pb.pasteboardItems()):
        for type_name in item.types():
            nsdata = item.dataForType_(type_name)
            if nsdata is None:
                continue
            # Convert NSData to Python bytes
            length = nsdata.length()
            if length == 0:
                data = b""
            else:
                data = bytes(nsdata.bytes().as_buffer(length))
            items.append((idx, str(type_name), data))

    return cls(
        platform="macos",
        items=items,
        captured_at=time.monotonic(),
    )


def _restore_macos(self) -> bool:
    import AppKit
    import Foundation

    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()

    # Group items by their original pasteboard item index.
    # NSPasteboard can hold multiple NSPasteboardItem objects;
    # we restore the same structure.
    from collections import defaultdict

    grouped: dict[int, list[tuple[str, bytes]]] = defaultdict(list)
    for idx, type_name, data in self.items:
        grouped[idx].append((type_name, data))

    ns_items = []
    for idx in sorted(grouped.keys()):
        item = AppKit.NSPasteboardItem.alloc().init()
        for type_name, data in grouped[idx]:
            if data:
                nsdata = Foundation.NSData.dataWithBytes_length_(data, len(data))
            else:
                nsdata = Foundation.NSData.data()
            item.setData_forType_(nsdata, type_name)
        ns_items.append(item)

    if ns_items:
        pb.writeObjects_(ns_items)
    return True
```

**Key fix vs. earlier draft**: capture records the **pasteboard item index** (`idx`), and restore writes one `NSPasteboardItem` per original index. Multi-item pasteboards (e.g., copying multiple files from Finder) are preserved.

**Formats captured on macOS** (NSPasteboard handles all automatically):
`public.utf8-plain-text`, `public.utf16-plain-text`, `public.rtf`, `public.html`, `public.png`, `public.tiff`, `public.jpeg`, `public.file-url`, `NSFilenamesPboardType`, `public.url`, `public.url-name`, and any custom type the source app wrote.

### 4.5 Linux X11 capture and restore (text-only — documented limitation)

```python
@classmethod
def _capture_x11(cls) -> "ClipboardSnapshot | None":
    import subprocess

    # On X11, we capture text targets only. xclip can only hold one
    # target at a time on the clipboard, so multi-format restore is
    # not achievable via the CLI tool. A full multi-format X11
    # implementation would require Gtk.Clipboard (PyGObject), which
    # is not a dependency of this project.
    #
    # Documented limitation: if the user copied an image or file on
    # X11, it will be replaced by the transcription text and not
    # restored. Users who need image clipboard preservation on X11
    # should use Wayland or a different platform.

    text_targets = [
        "text/plain;charset=utf-8",
        "UTF8_STRING",
        "text/plain",
        "STRING",
    ]

    items: list[tuple[str, bytes]] = []
    for target in text_targets:
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", target, "-o"],
                capture_output=True,
                timeout=2.0,
            )
            if result.returncode == 0 and result.stdout:
                items.append((target, result.stdout))
                break  # first available text target is sufficient
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    return cls(
        platform="linux-x11",
        items=items,
        captured_at=time.monotonic(),
    )


def _restore_x11(self) -> bool:
    import subprocess

    if not self.items:
        return True  # nothing to restore

    target, data = self.items[0]
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", target, "-i"],
            input=data,
            timeout=2.0,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
```

**Why text-only on X11**: `xclip` can only offer one target per clipboard selection. There is no CLI way to atomically set multiple targets (text + image + files). A full multi-format X11 implementation requires `Gtk.Clipboard.set_can_store()` via PyGObject, which would add a `pygobject` dependency. The project currently does not depend on PyGObject; adding it for a marginal X11 use case is not justified. This limitation is documented in §11.

### 4.6 Linux Wayland capture and restore (text-only — documented limitation)

```python
@classmethod
def _capture_wayland(cls) -> "ClipboardSnapshot | None":
    import subprocess

    # Same limitation as X11: wl-copy can only serve one stdin stream
    # for all --type flags, so per-type data cannot be restored.
    # Text-only capture and restore.

    text_targets = [
        "text/plain;charset=utf-8",
        "text/plain",
        "UTF8_STRING",
    ]

    items: list[tuple[str, bytes]] = []
    for target in text_targets:
        try:
            result = subprocess.run(
                ["wl-paste", "--type", target],
                capture_output=True,
                timeout=2.0,
            )
            if result.returncode == 0 and result.stdout:
                items.append((target, result.stdout))
                break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    return cls(
        platform="linux-wayland",
        items=items,
        captured_at=time.monotonic(),
    )


def _restore_wayland(self) -> bool:
    import subprocess

    if not self.items:
        return True

    target, data = self.items[0]
    try:
        subprocess.run(
            ["wl-copy", "--type", target],
            input=data,
            timeout=2.0,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
```

**Why text-only on Wayland**: `wl-copy --type T1 --type T2` reads **one** stdin stream and serves it for **all** types. To restore an image alongside text, each type would need its own data source, which `wl-copy` does not support via CLI. A full multi-format Wayland implementation requires a custom `wl_data_source` client using `libwayland-client`, which is out of scope. This limitation is documented in §11.

### 4.7 Platform dispatch

```python
@classmethod
def capture(cls) -> "ClipboardSnapshot | None":
    """Capture the current clipboard across all formats.

    Returns None if the clipboard cannot be opened (another app holds
    the lock) or if no formats are present.
    """
    from voice_typer.server.platform_utils import is_macos, is_windows

    try:
        if is_windows():
            return cls._capture_windows()
        elif is_macos():
            return cls._capture_macos()
        else:
            import os

            session = os.environ.get("XDG_SESSION_TYPE", "x11").lower()
            if session == "wayland":
                snap = cls._capture_wayland()
                # XWayland fallback: if wl-paste fails, try xclip
                if snap is None or not snap.items:
                    snap = cls._capture_x11()
                return snap
            return cls._capture_x11()
    except Exception:
        log.exception("[CLIPBOARD-SNAPSHOT] capture failed")
        return None
```

**Dispatch guarantees:**
- The platform tag is set at capture time and stored in `self.platform`.
- `restore()` branches on `self.platform` — no re-detection needed, no global state.
- If capture returns `None` (failure), the caller skips restore (see §5.2).

---

## 5. Architecture: `clipboard.py` changes

### 5.1 Remove dead code

**Delete the following from `clipboard.py`:**

| Line(s) | Code | Reason |
|---|---|---|
| 541 | `self._clear_thread = None` | Unused after deleting `schedule_clipboard_clear` |
| 543 | `self._saved_clipboard: str \| None = None` | Replaced by snapshot return value |
| 812–877 | `def schedule_clipboard_clear(self, delay: float = 0) -> None:` (entire method) | Dead code; replaced by `_schedule_restore()` |
| 821–825 | Docstring referencing `clipboard_clear_delay_seconds` | Config field removed (see §8.1) |

**Keep the following** (still used for seq-mismatch recovery in `paste()`):

| Line(s) | Code | Reason |
|---|---|---|
| 540 | `self._last_copied_text: str = ""` | Used at lines 915/919/920 for seq-mismatch re-copy |
| 548 | `self._clipboard_save_restore_enabled: bool = True` | Now actually consulted (see §5.2) |

### 5.2 Revised `copy()` — returns snapshot instead of storing it

```python
def copy(self, text: str) -> "ClipboardSnapshot | None":
    """Copy text to clipboard. Returns a snapshot of the prior content.

    Returns None if:
      - clipboard_save_restore is disabled (config flag)
      - snapshot capture failed (clipboard locked, etc.)

    Raises `ClipboardCopyError` if the text copy/verify fails after
    retries (caller should write to crash recovery). The snapshot, if
    captured, is restored before raising so the clipboard is never
    left torn.

    The caller is responsible for restoring the snapshot after the
    text has been consumed (pasted or read). See paste() and
    restore_now().
    """
    if not text:
        return None

    # ① SNAPSHOT (gated by config flag — DP7)
    snapshot: ClipboardSnapshot | None = None
    if self._clipboard_save_restore_enabled:
        snapshot = ClipboardSnapshot.capture()
        # snapshot may be None if capture failed — that's OK, we
        # just won't restore. Log for debugging.
        if snapshot is None:
            log.debug("[CLIPBOARD] Snapshot capture returned None (clipboard locked or empty)")

    try:
        # ② WIN32 EMPTY (existing line 697)
        if is_windows():
            _win32_empty_clipboard()

        # ③ COPY TEXT (existing lines 727–739, with retry)
        for attempt in range(3):
            try:
                pyperclip.copy(text)
                break
            except OSError as copy_err:
                winerror = getattr(copy_err, "winerror", None)
                if winerror == 5 and attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise copy_err

        # ④ VERIFY (existing lines 744–758)
        for verify_attempt in range(3):
            try:
                actual = pyperclip.paste()
                if actual == text:
                    break
                pyperclip.copy(text)
            except Exception:
                pass

        # ⑤ STORE METADATA (existing lines 761–764)
        self._last_copied_text = text
        self._clipboard_seq = self._get_clipboard_sequence_number()
        log.info(
            "[CLIPBOARD-AUDIT] Copied %d chars to clipboard (seq=%d, snapshot=%s)",
            len(text),
            self._clipboard_seq,
            "captured" if snapshot is not None else "none",
        )
        return snapshot

    except Exception as e:
        log.error("[CLIPBOARD] Failed to copy to clipboard: %s", e)
        # If copy failed, restore the snapshot immediately so we don't
        # leave the clipboard in a torn state, then signal failure.
        if snapshot is not None:
            with contextlib.suppress(Exception):
                snapshot.restore()
        raise ClipboardCopyError(str(e)) from e
```

**Key changes from current `copy()`:**
- Returns `ClipboardSnapshot | None` instead of `bool`.
- Snapshot is gated on `self._clipboard_save_restore_enabled` (DP7 — the flag is now actually consulted).
- On copy failure, raises `ClipboardCopyError` after immediately restoring the snapshot (defensive — don't leave clipboard torn; the caller handles crash recovery).
- `_saved_clipboard` attribute is gone; the snapshot travels as a return value (DP4).

> `ClipboardCopyError` is a new exception defined in `clipboard.py` (subclass of `RuntimeError`), used so `_copy_and_paste` can distinguish "copy failed" from "save/restore disabled" via the return type / exception rather than reading private state (see §6.1).
>
> **Import requirement**: because `dictation_pipeline.py` (§6.1) and `app.py` (§7.1) `except ClipboardCopyError`, both modules must import it: `from voice_typer.server.clipboard import ClipboardCopyError`. Add this to the existing clipboard import in each file (do **not** create a circular import — `clipboard.py` must not import from `dictation_pipeline`/`app`).

### 5.3 Revised `paste()` — accepts snapshot, spawns restore thread

```python
def paste(
    self,
    snapshot: "ClipboardSnapshot | None" = None,
    restore_delay: float | None = None,
    pasted_text: str | None = None,
    force: bool = False,
) -> bool:
    """Send a paste keystroke into the focused window.

    If a snapshot is provided, a delayed restore is ALWAYS scheduled on
    a daemon thread (DP3) — even when the keystroke is later skipped
    (pynput missing, rate-limited, paste disabled, unsafe target). This
    guarantees the clipboard borrow is always paired with a restore
    (DP1/DP2), so the user's original clipboard is never orphaned.

    `force=True` bypasses the `paste_enabled` gate. Used by `repaste_last()`
    — a manual user action that must never be coupled to the auto-paste
    (`paste_on_stop`) setting. See §2.12.

    The snapshot and the expected pasted text are passed as value
    parameters — no instance state is read or written for the snapshot
    or the restore guard (DP4). This makes overlapping cycles safe:
    cycle B's copy() cannot corrupt cycle A's restore, because every
    cycle carries its own expected text. The transcription thread is
    never blocked by the restore (it runs on a daemon thread).
    """
    # ── schedule restore FIRST, before any early return (DP1/DP2) ──
    # The borrow happened in copy(); failure to send the keystroke must
    # not prevent the paired restore. `_delayed_restore` re-checks the
    # clipboard before restoring, so this is safe even if the paste
    # never lands.
    if snapshot is not None:
        delay = restore_delay if restore_delay is not None else (self._restore_delay_ms / 1000.0)
        expected = pasted_text if pasted_text is not None else self._last_copied_text
        threading.Thread(
            target=self._delayed_restore,
            args=(snapshot, expected, delay),
            daemon=True,
            name="clipboard-restore",
        ).start()

    # ── existing pre-paste logic (unchanged) ──
    self._release_stuck_modifiers()

    if _Controller is None and not is_windows():
        log.warning("[CLIPBOARD] pynput unavailable — cannot paste")
        return False

    # PLAT-CLIPRACE: seq-mismatch recovery (existing lines 906–930)
    # ... unchanged ...

    # paste_delay (existing lines 932–943)
    paste_delay = 0.02
    if is_windows():
        try:
            from voice_typer.server.server_platform import is_remote_session

            if is_remote_session():
                paste_delay = 0.10
        except Exception:
            pass

    # rate-limit (existing lines 945–951)
    now = time.monotonic()
    if now - self._last_paste_time < self._PASTE_RATE_LIMIT:
        log.debug("[CLIPBOARD] paste rate-limited")
        return False

    if not self.paste_enabled and not force:
        return False

    if not self._is_safe_paste_target():
        log.warning("[CLIPBOARD] paste blocked — unsafe target")
        return False

    time.sleep(paste_delay)

    process_name = self._detect_focused_process()
    is_terminal = self._is_terminal_process(process_name)

    # ── send keystroke (unchanged) ──
    if is_terminal:
        if is_macos():
            self._safe_key_press(_Key.cmd, "v")
        else:
            self._safe_key_press(_Key.shift, _Key.insert)
    elif is_macos():
        self._safe_key_press(_Key.cmd, "v")
    elif is_windows():
        self._send_ctrl_v_win32()
    else:
        self._safe_key_press(_Key.ctrl, "v")

    self._last_paste_time = time.monotonic()
    log.info(
        "[CLIPBOARD-AUDIT] Sent paste keystroke (terminal=%s, target=%s, restore_scheduled=%s)",
        is_terminal,
        process_name or "unknown",
        snapshot is not None,
    )

    return True


def _delayed_restore(
    self,
    snapshot: "ClipboardSnapshot",
    pasted_text: str,
    delay: float,
) -> None:
    """Restore a snapshot after a delay. Runs on a daemon thread.

    Defensive check: if the clipboard no longer contains pasted_text
    (user copied something else, or target app rewrote it), skip
    restore to avoid clobbering the new content.
    """
    try:
        time.sleep(delay)
        try:
            current = pyperclip.paste()
        except Exception:
            current = None
        if current == pasted_text:
            snapshot.restore()
            log.info(
                "[CLIPBOARD-AUDIT] Restored snapshot after %.3fs delay",
                delay,
            )
        else:
            log.debug(
                "[CLIPBOARD-AUDIT] Restore skipped — clipboard changed (current=%d chars, expected=%d chars)",
                len(current) if current else 0,
                len(pasted_text),
            )
    except Exception:
        log.exception("[CLIPBOARD] Delayed restore failed")
```

**Key changes from current `paste()`:**
- Accepts `snapshot`, `restore_delay`, and `pasted_text` as parameters (DP4). `pasted_text` is the expected clipboard content after copy, passed as a value so overlapping cycles stay isolated.
- **Restore is scheduled at the top of `paste()`, before any early return** (DP1/DP2). This was the data-loss hole: previously, early returns (pynput missing, rate-limited, `paste_enabled` False, unsafe target) fired before the restore block, orphaning the snapshot and destroying the user's original clipboard. Now every borrow is paired with a restore.
- Restore runs on a **daemon thread** (DP3) — the transcription thread is never blocked.
- The defensive check (`current == pasted_text`) is kept — it's free insurance against the target app rewriting the clipboard (terminals normalizing line endings, IDEs reformatting on paste). With `pasted_text` passed as a value, this guard is correct even under overlapping cycles.
- `self._restore_delay_ms` is actually used (DP7 — the config field is now read).

### 5.4 New `restore_now()` — for the `paste_on_stop = False` case

```python
def restore_now(self, snapshot: "ClipboardSnapshot | None") -> None:
    """Restore a snapshot immediately (no paste keystroke, no delay).

    Used when copy() borrowed the clipboard but no paste follows
    (paste_on_stop = False). This is the critical fix for the
    data-loss bug where paste_on_stop=False permanently destroyed
    the user's clipboard (DP2).
    """
    if snapshot is None:
        return
    try:
        snapshot.restore()
        log.info("[CLIPBOARD-AUDIT] Restored snapshot immediately (no paste)")
    except Exception:
        log.exception("[CLIPBOARD] Immediate restore failed")
```

**Why this exists**: When `paste_on_stop = False`, the pipeline calls `copy()` (which borrows the clipboard) but never calls `paste()`. Without `restore_now()`, the snapshot would be orphaned and the user's original content lost. `restore_now()` closes that gap (DP2).

**Behavioral note**: This means `paste_on_stop = False` no longer leaves the transcription on the clipboard. The transcription is saved to the DB (§6) and accessible via the repaste hotkey (§7). This is a deliberate behavioral change — see §9.2 for rationale.

### 5.5 Revised `refresh_config()` — now actually called

```python
def refresh_config(self, config) -> None:
    """Refresh cached config flags from a Config object.

    Called from service.apply_config() after config.save() (see §8.3).
    Keeps the live ClipboardManager in sync with runtime config changes —
    including the `paste_enabled` ↔ `paste_on_stop` mirror (§2.12), which
    is otherwise stale until restart.
    """
    try:
        self._clipboard_save_restore_enabled = bool(getattr(config, "clipboard_save_restore", True))
    except Exception:
        self._clipboard_save_restore_enabled = True

    try:
        self._restore_delay_ms = int(getattr(config, "clipboard_restore_delay_ms", 150))
    except Exception:
        self._restore_delay_ms = 150

    # §2.12: mirror paste_on_stop → paste_enabled so a runtime toggle of
    # auto-paste actually takes effect. Without this, paste()'s internal
    # gate stays stale and auto-paste (and repaste) silently no-ops.
    try:
        self.paste_enabled = bool(getattr(config, "paste_on_stop", True))
    except Exception:
        self.paste_enabled = True

    log.debug(
        "[CLIPBOARD] refresh_config: save_restore=%s, restore_delay=%dms, paste_enabled=%s",
        self._clipboard_save_restore_enabled,
        self._restore_delay_ms,
        self.paste_enabled,
    )
```

### 5.6 New `__init__` attributes

```python
def __init__(self, paste_enabled: bool = True):
    self.paste_enabled = paste_enabled
    _ensure_pynput_imported()
    self._keyboard = _Controller() if _Controller is not None else None
    self._last_paste_time: float = 0.0
    self._clipboard_seq: int = 0
    self._last_copied_text: str = ""

    # REMOVED: self._clear_thread, self._saved_clipboard
    # (dead code — see §5.1)

    self._clipboard_save_restore_enabled: bool = True
    self._restore_delay_ms: int = 150  # NEW — actually used in paste()
```

---

## 6. Architecture: `dictation_pipeline.py` changes

### 6.1 Revised `_copy_and_paste()` — snapshot/restore is explicit

```python
def _copy_and_paste(self, text: str) -> None:
    """Step 9: Copy to clipboard and attempt paste.

    The snapshot/restore cycle is explicit here (not hidden inside
    copy()/paste()) so the borrow/restore pairing is visible at the
    call site. This is the single place that orchestrates the
    clipboard borrow lifecycle.

    ERR-004: If clipboard.copy() fails, we write the text to crash
    recovery and notify the user.
    """
    # ── OPTIMIZATION (§9.2): if paste_on_stop is OFF and save/restore is
    #    ON, we would copy the transcription and instantly restore the
    #    user's clipboard — a redundant clipboard lock round-trip (and its
    #    error surface) for zero benefit. Skip the clipboard entirely; the
    #    transcription is already persisted to the DB by _store_result()
    #    and reachable via the repaste hotkey. We only skip the clipboard
    #    borrow here — the UI teardown below (bubble/tray/timer) still runs.
    skip_clipboard = not self._app.config.paste_on_stop and self._app.config.clipboard_save_restore

    pasted = False
    snapshot = None
    if not skip_clipboard:
        # ① COPY (returns snapshot, or None when save/restore is disabled;
        #    raises ClipboardCopyError on genuine copy failure)
        try:
            snapshot = self._app.clipboard.copy(text)
        except ClipboardCopyError:
            log.error("[CLIPBOARD] Clipboard copy failed")
            # ... existing crash-recovery path (lines 630–665) ...
            return

        # ② PASTE (if enabled) — paste() schedules the restore thread
        if self._app.config.paste_on_stop:
            pasted = self._app.clipboard.paste(snapshot, pasted_text=text)
        else:
            # paste_on_stop is False + save/restore OFF: leave the
            # transcription on the clipboard for the user to paste manually
            # (legacy behavior). copy() returned None (no snapshot captured),
            # so there is nothing to restore — the user's original content
            # was never captured.
            log.info(
                "[CLIPBOARD-AUDIT] paste_on_stop=False + save/restore off — "
                "transcription left on clipboard for manual paste"
            )
    else:
        log.info(
            "[CLIPBOARD-AUDIT] paste_on_stop=False + save/restore on — "
            "clipboard untouched; transcription persisted to DB"
        )

    # ③ Mark crash recovery as pasted (if applicable)
    if pasted and self._app.config.crash_recovery_enabled:
        with contextlib.suppress(Exception):
            self._app._crash_recovery.mark_latest_pasted()

    # ④ Status + tray + bubble (existing lines 675–692, unchanged)
    if pasted:
        status = f"Done -- {len(text)} chars (pasted)"
    elif skip_clipboard:
        status = f"Done -- {len(text)} chars (in DB, use repaste hotkey)"
    else:
        # paste_on_stop=False + save/restore off: legacy "left on clipboard"
        status = f"Done -- {len(text)} chars (in clipboard)"
    # ... existing bubble/tray/timer logic ...
```

**Key changes:**
- `copy()` return value is captured as `snapshot` (DP4 — value parameter, not instance state); genuine copy failure now raises `ClipboardCopyError` instead of reading the private `_clipboard_save_restore_enabled` flag.
- `paste(snapshot, pasted_text=text)` receives the snapshot and the expected pasted text as values (DP4 — fixes overlapping-cycle restore clobbering).
- **`paste_on_stop = False` + `clipboard_save_restore = True` skips the clipboard entirely** (OPTIMIZATION, §9.2). There is no borrow to restore — the transcription goes to the DB only and the user's clipboard is never touched. This is the fix for the critical data-loss bug (DP2) without the redundant copy-then-restore round-trip.
- **`paste_on_stop = False` + `clipboard_save_restore = False`** leaves the transcription on the clipboard for manual paste (legacy behavior); `copy()` returns `None` so no restore is scheduled.
- Status string is three-way: `"(pasted)"` / `"(in DB, use repaste hotkey)"` (skip case) / `"(in clipboard)"` (legacy off+off case).

### 6.2 `flush()` after `add_transcription()` — guarantee DB commit

In `_store_result()` (Step 8), after `add_transcription()`:

```python
def _store_result(self, text: str) -> None:
    try:
        self._app.history_db.add_transcription(
            text,
            duration=self._duration,
            model=self._app.config.model_size,
            device=self._app.config.device,
        )
        # NEW: flush to guarantee the row is committed before
        # repaste could fire. flush() blocks until the writer
        # thread processes all queued writes (FIFO no-op with
        # wait=True). See history_db.py:flush().
        self._app.history_db.flush()
    except Exception:
        log.exception("[PIPELINE] History DB add failed")
        # ... existing error handling ...

    # Save for undo (kept for backward compat with undo_last)
    self._app._last_transcription = text
    # ... existing IPC push event ...
```

**Why `flush()` is safe to call here**: `flush()` enqueues a no-op write with `wait=True` and blocks on its future. The writer thread is daemon, FIFO, and single-threaded — no deadlock risk. The block duration is bounded by the time to write one row to SQLite WAL (~1–5ms typical). This is acceptable on the transcription thread (which is already blocking on transcription itself).

---

## 7. Architecture: `app.py` repaste changes

### 7.1 Revised `repaste_last()` — reads from DB, uses snapshot/restore

Replace `app.py:966–1003`:

```python
def repaste_last(self) -> None:
    """Repaste the most recent transcription, sourced from the DB.

    Reads from history_db (NOT _last_transcription), so it survives
    app restarts. Uses the same snapshot/restore mechanism as
    auto-paste so the user's clipboard is preserved.

    Fallback chain:
      1. history_db.get_latest_text()  (primary — survives restart)
      2. self._last_transcription       (fallback if DB read fails)
      3. "No previous transcription" toast (both empty)
    """
    # ① READ FROM DB
    text = ""
    try:
        text = self.history_db.get_latest_text()
    except Exception as e:
        log.warning("[REPASTE] DB read failed, falling back to memory: %s", e)
        text = self._last_transcription

    if not text:
        self.tray.notify(APP_NAME, "No previous transcription to re-paste.")
        return

    # ② COPY (snapshot + empty + pyperclip.copy + verify)
    # copy() returns None when save/restore is disabled; it raises
    # ClipboardCopyError only on a genuine copy failure.
    snapshot = None
    try:
        snapshot = self.clipboard.copy(text)
    except ClipboardCopyError as e:
        log.warning("[REPASTE] Clipboard copy failed: %s", e)
        self.tray.notify(
            APP_NAME,
            "Could not copy the transcription to the clipboard. Another app may be holding the clipboard lock.",
        )
        return

    # ③ PASTE (keystroke + delayed restore scheduled inside paste())
    # paste() schedules the restore of the user's ORIGINAL clipboard at
    # its top, before any early return (DP1). It returns False (does not
    # raise) when the keystroke is skipped/blocked/rate-limited — and the
    # restore is still scheduled. We therefore do NOT call restore_now()
    # here: that would be redundant and would remove the transcription
    # from the clipboard. The transcription is safely stored in the DB.
    # `force=True` bypasses the paste_enabled gate (§2.12) so a manual
    # repaste works regardless of the auto-paste (paste_on_stop) setting.
    pasted = self.clipboard.paste(snapshot, pasted_text=text, force=True)
    if pasted:
        log.info("[REPASTE] Repasted transcription (%d chars)", len(text))
        self.tray.notify(APP_NAME, "Last transcription re-pasted")
    else:
        log.warning("[REPASTE] Paste keystroke was skipped/blocked")
        self.tray.notify(
            APP_NAME,
            "Re-paste was blocked (unsafe target or rate-limited). "
            "Your previous clipboard was preserved. Use the repaste "
            "hotkey again to try pasting.",
        )
```

**Key changes:**
- Reads from `history_db.get_latest_text()` (primary) with `_last_transcription` fallback (§7.2).
- `copy()` return value is captured as `snapshot`; genuine copy failure raises `ClipboardCopyError` (no private-flag read).
- `paste(snapshot, pasted_text=text)` receives the snapshot and the expected text as values (DP4).
- Restore of the user's original clipboard is handled inside `paste()` (scheduled before any early return, DP1) — it is NOT re-done here. On a skipped/blocked paste the transcription stays in the DB; the user re-triggers repaste rather than pressing Ctrl+V (the transcription is no longer on the clipboard).

### 7.2 `_last_transcription` — kept for undo, no longer primary source for repaste

`_last_transcription` is **not removed** — it's still used by `undo_last()` (lines 1005–1038) to know what to undo. But repaste no longer depends on it; the DB is the primary source.

| Field | Set by | Read by | Cleared by |
|---|---|---|---|
| `_last_transcription` | `dictation_pipeline.py:591` (after transcription) | `undo_last()` (line 1015), `repaste_last()` fallback (§7.1) | `undo_last()` success (line 1033) |
| `history_db.transcriptions` table | `dictation_pipeline.py:549` (after transcription) | `repaste_last()` primary (§7.1), History page | `delete()`, `clear_all()`, `apply_retention()` |

---

## 8. Architecture: `history_db.py` and `config.py` changes

### 8.1 New `history_db.get_latest_text()` method

Add to `history_db.py`:

```python
def get_latest_text(self) -> str:
    """Return the most recent transcription text, or '' if DB is empty.

    Uses the existing thread-local read-only connection
    (PRAGMA query_only=1), so it's safe to call from the hotkey
    handler thread. Backed by idx_timestamp.

    Note: if you just called add_transcription(), call flush()
    first to guarantee the row is committed before this read.
    """
    conn = self._get_read_conn()
    cur = conn.cursor()
    # Order by the autoincrement PK (DESC), not `timestamp DESC`:
    # `timestamp` defaults to CURRENT_TIMESTAMP, so transcriptions written
    # within the same second tie and the "latest" becomes ambiguous. The PK
    # is monotonic and is the only correct "most recent" signal.
    cur.execute(
        "SELECT text FROM transcriptions ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else ""

**Thread safety**: `_get_read_conn()` returns a `threading.local()` connection with `PRAGMA query_only=1`. The hotkey handler thread gets its own connection on first call. WAL mode means readers never block the writer. Safe to call concurrently with `add_transcription()`.

### 8.2 New `config.py` field; remove dead field

```python
# config.py

# REMOVE (dead — was only read by the now-deleted schedule_clipboard_clear):
# clipboard_clear_delay_seconds: int = 5

# KEEP (now actually consulted in clipboard.py:5.2):
clipboard_save_restore: bool = True

# NEW (now actually consulted in clipboard.py:5.3):
clipboard_restore_delay_ms: int = 150
"""Delay between paste keystroke and clipboard restore, in milliseconds.

The target app needs time to read the clipboard after receiving
Ctrl+V. 150ms is safe for all known apps; bump to 250ms if pastes
appear as the restored content instead of the transcription.

This value is read by ClipboardManager.paste() and passed to the
delayed-restore daemon thread. It is refreshed at runtime via
refresh_config() when the user changes settings.
"""
```

### 8.3 Wire `refresh_config()` into `service.apply_config()` AND add allowlist entries

Two changes are required — **both**, not just one. Skipping (a) makes the runtime-setting path dead; skipping (b) means `refresh_config()` never fires.

**(a) Add the clipboard keys to `IPC_CONFIG_ALLOWLIST` (`config_validators.py`).**

This is the blocker from §2.11. Without it, `validate_config_update()` drops the keys and the rest of the wiring is moot.

```python
# config_validators.py — inside IPC_CONFIG_ALLOWLIST
"clipboard_save_restore":      (bool, _bool_validator),
"clipboard_restore_delay_ms":  (int, _make_int_validator(lo=0, hi=2000)),
```

Also expose both keys in the renderer config schema so the Settings UI can read/set them.

**(b) Call `refresh_config()` inside `service.apply_config()`.**

In `service.py:apply_config()`, after `app.config.save()` (line 1122), still inside the `with app._config_mutation_lock:` block, add:

```python
# Propagate clipboard config changes to ClipboardManager (DP7).
# Without this, clipboard_save_restore / clipboard_restore_delay_ms
# changes would not take effect until app restart. The keys are only
# present in `updates` because they passed validation (see §2.11 / §8.3a).
clipboard_keys = {"clipboard_save_restore", "clipboard_restore_delay_ms", "paste_on_stop"}
if clipboard_keys & set(updates.keys()):
    with contextlib.suppress(Exception):
        app.clipboard.refresh_config(app.config)
```

**Why inside the lock / after save**: ensures `refresh_config` reads a consistent, persisted config snapshot, not a torn one from a concurrent IPC update.

> The earlier draft wired this into `config_handlers.py` after `config.save()` at line 140. That is wrong: `config_handlers.py` only delegates to `service.apply_config()` and does not hold the lock or call `config.save()` itself (§2.10). The wiring must be in `service.apply_config()`.

---

## 9. Behavioral Changes and Rationale

### 9.1 What stays the same

| Behavior | Status |
|---|---|
| `paste_on_stop = True` (default): transcription auto-pasted, clipboard restored | Unchanged from current intent (but now actually works — current code never restores) |
| Repaste hotkey (Ctrl+Alt+V): pastes last transcription | Unchanged, but now reads from DB instead of memory |
| Undo (in `undo_last()`): backspaces over last transcription | Unchanged (still uses `_last_transcription`) |
| Windows safety checks (UAC, password fields, elevated targets) | Unchanged (existing `_is_safe_paste_target()` logic) |
| History page: browse/search/copy old transcriptions | Unchanged |

### 9.2 What changes — `paste_on_stop = False` behavior

**Before**: Transcription is copied to the clipboard and stays there. User's original clipboard content is lost. User pastes manually with Ctrl+V.

**After** (`paste_on_stop = False` + `clipboard_save_restore = True`, the default-off combination):
The clipboard is **never touched**. The transcription is persisted to the DB only (via `_store_result()`) and is accessible via the repaste hotkey. The user's original clipboard content is untouched — no borrow, no restore. This is strictly better than the earlier "copy-then-restore" draft (§6.1 OPTIMIZATION): it removes a redundant clipboard lock round-trip and its error surface.

**After** (`paste_on_stop = False` + `clipboard_save_restore = False`): legacy behavior — the transcription is copied to the clipboard for the user to paste manually; no snapshot is captured, so nothing is restored.

**Rationale**: This is the fix for the critical data-loss bug (DP2). The old behavior was inconsistent — `paste_on_stop = True` would (in the new design) restore the clipboard, but `paste_on_stop = False` would not. The new behavior is consistent: **the clipboard is always restored, or never borrowed**. Users who disable auto-paste because they want to paste manually should use the repaste hotkey (Ctrl+Alt+V) instead — it pastes the latest transcription without destroying the clipboard.

**Migration note**: This is a behavioral change that affects users who currently rely on `paste_on_stop = False` leaving the transcription on the clipboard. Document this in the changelog and the settings UI tooltip: *"When off, transcriptions are saved to history but not auto-pasted. Use the repaste hotkey to paste the last transcription."*

### 9.3 What changes — repaste after app restart

**Before**: Repaste hotkey does nothing after restart (`_last_transcription` is `""`).

**After**: Repaste hotkey reads the latest transcription from the DB and pastes it. Survives restarts.

---

## 10. Test Strategy

For a subsystem whose failure mode is **clipboard data loss**, tests are mandatory. The strategy has three tiers.

### 10.1 Unit tests — format round-trips (per platform)

**File**: `tests/test_clipboard_snapshot.py` (new)

```python
class TestClipboardSnapshotWindows:
    """Windows multi-format round-trip tests.

    These tests require Windows and are skipped on other platforms.
    They use real clipboard operations (no mocking) because the
    failure mode is platform-API misuse, which mocking hides.
    """

    def test_plain_text_round_trip(self):
        """Set text → capture → overwrite → restore → verify text matches."""
        pyperclip.copy("hello world")
        snap = ClipboardSnapshot.capture()
        assert snap is not None
        assert snap.platform == "windows"
        pyperclip.copy("transcription")
        snap.restore()
        assert pyperclip.paste() == "hello world"

    def test_image_round_trip(self):
        """Set DIB → capture → overwrite with text → restore → verify DIB back."""
        # Write a 1x1 red pixel as CF_DIB
        _set_clipboard_dib(_red_pixel_dib())
        snap = ClipboardSnapshot.capture()
        pyperclip.copy("transcription")
        snap.restore()
        restored = _get_clipboard_dib()
        assert restored == _red_pixel_dib()

    def test_rtf_round_trip(self):
        """Set RTF → capture → overwrite → restore → verify RTF back."""
        _set_clipboard_registered_format("Rich Text Format", b"{\\rtf1\\ansi test}")
        snap = ClipboardSnapshot.capture()
        pyperclip.copy("transcription")
        snap.restore()
        assert _get_clipboard_registered_format("Rich Text Format") == b"{\\rtf1\\ansi test}"

    def test_html_round_trip(self):
        """Set HTML Format → capture → overwrite → restore → verify HTML back."""
        html = b"Version:0.9\r\nStartHTML:00000097\r\n...\r\n<html>test</html>"
        _set_clipboard_registered_format("HTML Format", html)
        snap = ClipboardSnapshot.capture()
        pyperclip.copy("transcription")
        snap.restore()
        assert _get_clipboard_registered_format("HTML Format") == html

    def test_file_list_round_trip(self):
        """Set CF_HDROP with 2 files → capture → overwrite → restore → verify files back."""
        _set_clipboard_hdrop([r"C:\tmp\a.txt", r"C:\tmp\b.txt"])
        snap = ClipboardSnapshot.capture()
        pyperclip.copy("transcription")
        snap.restore()
        assert _get_clipboard_hdrop() == [r"C:\tmp\a.txt", r"C:\tmp\b.txt"]

    def test_multi_format_simultaneous(self):
        """Set text + RTF + HTML + image → capture → overwrite → restore → all back."""
        # ... set all formats ...
        snap = ClipboardSnapshot.capture()
        pyperclip.copy("transcription")
        snap.restore()
        # ... verify all four formats ...

    def test_capture_returns_none_on_locked_clipboard(self):
        """If another process holds the clipboard, capture returns None."""
        # Hold the clipboard open in a background thread, then try capture.
        # ...

    def test_restore_preserves_empty_clipboard(self):
        """Capturing an empty clipboard and restoring leaves it empty."""
        _empty_clipboard()
        snap = ClipboardSnapshot.capture()
        pyperclip.copy("transcription")
        snap.restore()
        assert pyperclip.paste() == ""


class TestClipboardSnapshotMacOS:
    """macOS multi-format round-trip tests. Skipped on non-macOS."""

    def test_plain_text_round_trip(self): ...
    def test_rtf_round_trip(self): ...
    def test_image_round_trip(self): ...
    def test_file_url_round_trip(self): ...
    def test_multi_item_pasteboard(self):
        """Multiple NSPasteboardItems are preserved as separate items."""
        # ... set two items, capture, restore, verify two items back ...


class TestClipboardSnapshotLinux:
    """Linux text-only round-trip tests. Skipped on non-Linux."""

    def test_plain_text_round_trip_x11(self): ...
    def test_plain_text_round_trip_wayland(self): ...
    def test_image_not_preserved_documented_limitation(self):
        """Images are not preserved on Linux (documented limitation).
        This test verifies the limitation is intentional, not a bug."""
        # ... set image, capture, restore, verify image is gone ...
```

### 10.2 Integration tests — borrow/restore cycle

**File**: `tests/test_clipboard_borrow_restore.py` (new)

```python
class TestCopyPasteRestoreCycle:
    """End-to-end tests of the borrow/restore cycle via ClipboardManager."""

    def test_paste_on_stop_true_restores_clipboard(self):
        """paste_on_stop=True: after paste, original clipboard content is back."""
        cm = ClipboardManager(paste_enabled=True)
        pyperclip.copy("user's URL")
        cm.copy("transcription text")
        # paste() would send keystrokes — mock the keystroke sender
        with patch.object(cm, "_send_ctrl_v_win32"):
            cm.paste(snapshot=cm._last_snapshot)  # or however we wire it
        # Wait for restore thread
        time.sleep(0.3)
        assert pyperclip.paste() == "user's URL"

    def test_paste_on_stop_false_restores_clipboard(self):
        """paste_on_stop=False: restore_now() is called, clipboard is back.
        This is the regression test for the critical data-loss bug."""
        cm = ClipboardManager(paste_enabled=False)
        pyperclip.copy("user's URL")
        snap = cm.copy("transcription text")
        cm.restore_now(snap)
        assert pyperclip.paste() == "user's URL"

    def test_overlapping_cycles_do_not_clobber(self):
        """Two rapid copy/paste cycles do not clobber each other's snapshots."""
        cm = ClipboardManager(paste_enabled=True)
        pyperclip.copy("original A")
        snap_a = cm.copy("text A")
        pyperclip.copy("original B")  # user copies something else
        snap_b = cm.copy("text B")
        # snap_a and snap_b are different objects
        assert snap_a is not snap_b
        # Restoring snap_a should put "original A" back, not "original B"
        snap_a.restore()
        assert pyperclip.paste() == "original A"
        snap_b.restore()
        assert pyperclip.paste() == "original B"

    def test_save_restore_disabled_skips_snapshot(self):
        """When clipboard_save_restore=False, copy() returns None and no restore happens."""
        cm = ClipboardManager(paste_enabled=True)
        cm._clipboard_save_restore_enabled = False
        pyperclip.copy("user's URL")
        snap = cm.copy("transcription")
        assert snap is None
        # Transcription is on clipboard, user's URL is gone (documented trade-off)
        assert pyperclip.paste() == "transcription"

    def test_force_bypasses_paste_enabled_gate(self):
        """paste(force=True) sends the keystroke even when paste_enabled is False.

        This is how repaste_last() works regardless of the auto-paste setting (§2.12)."""
        cm = ClipboardManager(paste_enabled=False)
        pyperclip.copy("user's URL")
        snap = cm.copy("transcription")
        with patch.object(cm, "_send_ctrl_v_win32") as mock_send:
            sent = cm.paste(snapshot=snap, force=True)
        assert sent is True
        mock_send.assert_called_once()

    def test_refresh_config_syncs_paste_enabled(self):
        """refresh_config() mirrors paste_on_stop -> paste_enabled (§2.12).

        Without this, toggling auto-paste in the UI leaves paste_enabled stale
        and auto-paste / repaste silently no-op until restart."""
        cm = ClipboardManager(paste_enabled=False)
        cfg = Config()
        cfg.paste_on_stop = True
        cm.refresh_config(cfg)
        assert cm.paste_enabled is True
        cfg.paste_on_stop = False
        cm.refresh_config(cfg)
        assert cm.paste_enabled is False

    def test_restore_skipped_when_clipboard_changed(self):
        """If user copies something during the 150ms window, restore is skipped."""
        cm = ClipboardManager(paste_enabled=True)
        pyperclip.copy("user's URL")
        snap = cm.copy("transcription")
        # Simulate paste + user copying something else during the window
        with patch.object(cm, "_send_ctrl_v_win32"):
            cm.paste(snapshot=snap)
        # User copies phone number during the 150ms window
        time.sleep(0.05)
        pyperclip.copy("user's phone number")
        # Wait for restore thread
        time.sleep(0.3)
        # Restore was skipped — phone number is still on clipboard
        assert pyperclip.paste() == "user's phone number"


class TestRepasteFromDB:
    """Tests that repaste reads from the DB, not in-memory."""

    def test_repaste_after_restart(self):
        """Repaste works after app restart because it reads from DB."""
        # Session 1: transcribe, close app
        app1 = VoiceTyperApp(...)
        app1.history_db.add_transcription("call mom")
        app1.history_db.flush()
        app1.cleanup()

        # Session 2: new app instance, _last_transcription is ""
        app2 = VoiceTyperApp(...)
        assert app2._last_transcription == ""

        # Repaste reads from DB
        with patch.object(app2.clipboard, "paste") as mock_paste:
            app2.repaste_last()
            mock_paste.assert_called_once()
            # Verify the snapshot was created from "call mom"
            snap = mock_paste.call_args.kwargs["snapshot"]
            # ... verify snap restores to prior content, paste sends "call mom" ...

    def test_repaste_fallback_to_memory_on_db_failure(self):
        """If DB read throws, repaste falls back to _last_transcription."""
        app = VoiceTyperApp(...)
        app._last_transcription = "from memory"
        with patch.object(app.history_db, "get_latest_text", side_effect=Exception("DB error")):
            with patch.object(app.clipboard, "paste") as mock_paste:
                app.repaste_last()
                mock_paste.assert_called_once()

    def test_repaste_empty_db_notifies_user(self):
        """If DB is empty and memory is empty, user is notified."""
        app = VoiceTyperApp(...)
        app._last_transcription = ""
        with patch.object(app.history_db, "get_latest_text", return_value=""):
            with patch.object(app.tray, "notify") as mock_notify:
                app.repaste_last()
                mock_notify.assert_called_once()
                assert "No previous transcription" in mock_notify.call_args[0][1]
```

### 10.3 Regression tests — existing behavior preserved

**File**: `tests/test_clipboard_regression.py` (new)

```python
class TestExistingBehaviorPreserved:
    """Verify that changes don't break existing functionality."""

    def test_copy_still_writes_text_to_clipboard(self):
        """copy() still puts text on the clipboard (just also returns a snapshot)."""
        cm = ClipboardManager()
        cm.copy("test text")
        assert pyperclip.paste() == "test text"

    def test_paste_still_sends_keystroke(self):
        """paste() still sends Ctrl+V (or platform equivalent)."""
        cm = ClipboardManager(paste_enabled=True)
        cm.copy("test")
        with patch.object(cm, "_send_ctrl_v_win32") as mock_send:
            cm.paste()
            mock_send.assert_called_once()

    def test_password_field_still_blocked(self):
        """paste() still refuses to paste into password fields (Windows)."""
        # ... existing test from test_clipboard_security.py ...

    def test_elevated_window_still_blocked(self):
        """paste() still refuses to paste into elevated windows (Windows)."""
        # ... existing test ...

    def test_rate_limit_still_enforced(self):
        """paste() still rate-limits to 1 paste per 0.5s."""
        # ... existing test ...

    def test_seq_mismatch_recovery_still_works(self):
        """paste() still re-copies on clipboard sequence number mismatch."""
        # ... existing test ...

    def test_clipboard_config_keys_pass_validation(self):
        """clipboard_save_restore / clipboard_restore_delay_ms are in the
        IPC allowlist and survive validate_config_update() (§2.11)."""
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update(
            {
                "clipboard_save_restore": False,
                "clipboard_restore_delay_ms": 250,
            }
        )
        assert "clipboard_save_restore" in validated
        assert "clipboard_restore_delay_ms" in validated
        assert validated["clipboard_restore_delay_ms"] == 250

    def test_clipboard_restore_delay_ms_rejects_out_of_range(self):
        """clipboard_restore_delay_ms is bounded by the validator (0..2000)."""
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update(
            {
                "clipboard_restore_delay_ms": 999999,
            }
        )
        # Out-of-range value is rejected (not present in validated) or coerced.
        assert "clipboard_restore_delay_ms" not in validated

    def test_get_latest_text_orders_by_id(self):
        """get_latest_text() returns the row with the highest id, even when
        two rows share the same CURRENT_TIMESTAMP (§8.1)."""
        db = HistoryDB(":memory:")
        db.add_transcription("first")
        db.add_transcription("second")
        db.flush()
        assert db.get_latest_text() == "second"
```

### 10.4 Test coverage targets

| Area | Target coverage |
|---|---|
| `ClipboardSnapshot.capture()` per platform | ≥90% (all format branches) |
| `ClipboardSnapshot.restore()` per platform | ≥90% |
| `ClipboardManager.copy()` | ≥95% (including failure paths) |
| `ClipboardManager.paste()` | ≥95% (including restore thread) |
| `ClipboardManager.restore_now()` | 100% |
| `ClipboardManager._delayed_restore()` | ≥90% (including defensive check) |
| `repaste_last()` | ≥90% (including DB fallback) |
| `history_db.get_latest_text()` | 100% |
| `IPC_CONFIG_ALLOWLIST` clipboard keys | 100% (pass + reject out-of-range) |

---

## 11. Known Limitations

These limitations are **documented and intentional**, not bugs.

### 11.1 Linux X11 — text-only snapshot/restore

`xclip` can only hold one target per clipboard selection. There is no CLI way to atomically set multiple targets (text + image + files). A full multi-format X11 implementation requires `Gtk.Clipboard.set_can_store()` via PyGObject, which would add a `pygobject` dependency. The project does not currently depend on PyGobject.

**Impact**: If an X11 user copied an image or file list, it will be replaced by the transcription text during the borrow window and not restored. The text portion of their clipboard is restored.

**Workaround**: Users who need image clipboard preservation on X11 should use Wayland, macOS, or Windows.

### 11.2 Linux Wayland — text-only snapshot/restore

`wl-copy --type T1 --type T2` reads **one** stdin stream and serves it for **all** types. To restore an image alongside text, each type would need its own data source, which `wl-copy` does not support via CLI. A full multi-format Wayland implementation requires a custom `wl_data_source` client using `libwayland-client`, which is out of scope.

**Impact**: Same as X11 — images and file lists are not preserved on Wayland. Text is preserved.

### 11.3 Windows — `CF_BITMAP`, `CF_METAFILEPICT`, `CF_ENHMETAFILE` not restored

These formats are GDI handles, not byte streams. They cannot be round-tripped through `GlobalAlloc` + `memmove`. The bitmap **data** is preserved via `CF_DIB` and `CF_DIBV5` (which are byte streams), so images are not lost — only the GDI handle wrappers are.

**Impact**: Negligible. Apps that paste bitmaps read `CF_DIB` / `CF_DIBV5`, not `CF_BITMAP` directly.

### 11.4 `paste_on_stop = False` no longer leaves transcription on clipboard

See §9.2. This is a deliberate behavioral change to prevent data loss. Users who relied on the old behavior should use the repaste hotkey.

### 11.5 Problem 3 (auto-paste into wrong window) — user responsibility

The existing `_is_safe_paste_target()` blocks the worst cases (UAC prompts, password fields, elevated targets) on Windows. Pasting into a normal but unintended app (user alt-tabbed during recording) is **not** blocked and is the user's responsibility. Documented in the README.

### 11.6 Problem 8 (no clipboard history on some systems) — partially solved

The History page in the app UI allows manual copy of old transcriptions. A Paste Palette (Windows+V-like popup for transcriptions) was considered and rejected to avoid adding a third hotkey. Users without OS clipboard history can use the History page.

### 11.7 Residual risks (accepted, not fixed)

These are low-severity and intentionally left as-is:

- **Crash window between `copy()` returning and `paste()` being called.** No clipboard-mutating code runs between these two statements (only a `config.paste_on_stop` read), so the window is microseconds and effectively unreachable. The restore is scheduled at the top of `paste()` by design (DP1/DP2).
- **`snapshot.restore()` failure in `_delayed_restore()` when the clipboard is locked.** `_delayed_restore()` already wraps `restore()` in `try/except` and logs (§5.3); if the OS clipboard is locked at restore time, the user's original content may not be restored. This matches the current code's best-effort behavior and is not fully solvable without OS-level guarantees.

---

## 12. Summary of Changes by File

| File | Change | Lines affected | Effort |
|---|---|---|---|
| **`clipboard_snapshot.py`** (NEW) | Multi-format snapshot/restore. Windows (Win32 API), macOS (NSPasteboard), Linux X11 (xclip, text-only), Linux Wayland (wl-copy, text-only). | New file, ~400 lines | 1.5 days |
| **`clipboard.py`** | (a) Delete `_saved_clipboard` (line 543), `_clear_thread` (line 541), `schedule_clipboard_clear()` (lines 812–877). (b) Add `_restore_delay_ms` to `__init__`. (c) Rewrite `copy()` to return snapshot. (d) Rewrite `paste()` to accept snapshot + `force` param, spawn restore thread, and bypass the `paste_enabled` gate when `force=True`. (e) Add `restore_now()`. (f) Update `refresh_config()` to set `_restore_delay_ms` **and** mirror `paste_on_stop` → `paste_enabled` (§2.12). | ~540, ~548, ~689–765, ~879–990, new method, ~550 | 1 day |
| **`dictation_pipeline.py`** | (a) Rewrite `_copy_and_paste()` to capture snapshot, pass to `paste()` or `restore_now()`. (b) Add `history_db.flush()` after `add_transcription()`. | ~549, ~620–692 | 0.5 day |
| **`history_db.py`** | Add `get_latest_text() -> str` method. | New method, ~8 lines | 0.25 day |
| **`dictation_pipeline.py`** | (c) Import `ClipboardCopyError` from `clipboard` (for §6.1). | import line | 0.0 day |
| **`app.py`** | (b) Import `ClipboardCopyError` from `clipboard` (for §7.1). | import line | 0.0 day |
| **`app.py`** | Replace `repaste_last()` body (lines 966–1003) with DB-reading version. | ~966–1003 | 0.25 day |
| **`config.py`** | (a) Remove `clipboard_clear_delay_seconds` (line 594). (b) Add `clipboard_restore_delay_ms: int = 150`. (`clipboard_save_restore` already exists at line 595 — keep.) | ~594, new field | 0.1 day |
| **`config_validators.py`** | Add `clipboard_save_restore` and `clipboard_restore_delay_ms` to `IPC_CONFIG_ALLOWLIST` (lines 466–544) with validators. Expose in renderer config schema. | ~466 | 0.1 day |
| **`service.py`** | Call `clipboard.refresh_config()` inside `apply_config()` after `config.save()` (line 1122), inside the lock. | ~1122 | 0.1 day |
| **Tests** | New: `test_clipboard_snapshot.py`, `test_clipboard_borrow_restore.py`, `test_clipboard_regression.py`. | ~600 lines total | 1.5 days |

**Total estimated effort: ~5 days** (including tests, which are ~30% of the effort).

---

## 13. Implementation Phases

Each phase is independently shippable. Phase 1+2 solves the critical data-loss bug for Windows users (the majority of the user base).

### Phase 1: Windows multi-format snapshot (2 days)
- Create `clipboard_snapshot.py` with Windows capture/restore only.
- Unit tests for all Windows format round-trips (§10.1).
- **Deliverable**: `ClipboardSnapshot.capture()` and `.restore()` work on Windows.

### Phase 2: Wire snapshot into ClipboardManager (1 day)
- Rewrite `copy()`, `paste()`, add `restore_now()` in `clipboard.py`.
- Delete dead code (`_saved_clipboard`, `schedule_clipboard_clear`, `_clear_thread`).
- Add `_restore_delay_ms` to `__init__` and `refresh_config()`.
- Integration tests for borrow/restore cycle (§10.2).
- Update `dictation_pipeline.py:_copy_and_paste()` to use new API.
- **Deliverable**: Auto-paste preserves clipboard on Windows. `paste_on_stop=False` no longer loses data.

### Phase 3: DB-backed repaste (0.5 day)
- Add `history_db.get_latest_text()`.
- Rewrite `app.py:repaste_last()`.
- Add `history_db.flush()` to pipeline.
- Tests for repaste-from-DB (§10.2).
- **Deliverable**: Repaste survives app restart.

### Phase 4: Config wiring (0.25 day)
- Add `clipboard_restore_delay_ms` to `config.py` (remove `clipboard_clear_delay_seconds`).
- **Add `clipboard_save_restore` and `clipboard_restore_delay_ms` to `IPC_CONFIG_ALLOWLIST` in `config_validators.py`** (required — see §2.11).
- Expose both keys in the renderer config schema.
- Call `clipboard.refresh_config()` inside `service.apply_config()` (after `config.save()`, in-lock).
- **Deliverable**: Config changes take effect without restart (and the Settings UI can actually reach the keys).

### Phase 5: macOS support (1 day)
- Add macOS capture/restore to `clipboard_snapshot.py`.
- Unit tests for macOS format round-trips.
- **Deliverable**: Multi-format snapshot/restore works on macOS.

### Phase 6: Linux support (0.5 day)
- Add X11 and Wayland text-only capture/restore to `clipboard_snapshot.py`.
- Unit tests for Linux text round-trips.
- Document X11/Wayland image limitation in README.
- **Deliverable**: Text-only snapshot/restore works on Linux.

### Phase 7: Regression tests and docs (0.5 day)
- Run full test suite (§10.3).
- Update README with new `paste_on_stop` behavior.
- Update SETTINGS.md with `clipboard_restore_delay_ms`.
- Update CHANGELOG.md.
- **Deliverable**: Shippable.

---

## 14. Verification Checklist

Before merging, verify every item:

- [ ] `paste_on_stop = True`: after paste, user's original clipboard content (text + image + RTF + HTML + files) is restored on Windows.
- [ ] `paste_on_stop = False`: `restore_now()` fires, user's original content is restored, transcription is in DB.
- [ ] `clipboard_save_restore = False`: `copy()` returns `None`, no snapshot captured, no restore attempted.
- [ ] `clipboard_restore_delay_ms = 250`: restore fires after 250ms, not 150ms.
- [ ] `clipboard_save_restore` and `clipboard_restore_delay_ms` are present in `IPC_CONFIG_ALLOWLIST` (`config_validators.py`).
- [ ] Setting either clipboard key from the UI reaches `service.apply_config()` (not silently dropped by `validate_config_update()`).
- [ ] Toggling auto-paste (`paste_on_stop`) in Settings updates `ClipboardManager.paste_enabled` via `refresh_config()` — auto-paste and repaste work immediately without restart (§2.12).
- [ ] Repaste hotkey works even when auto-paste (`paste_on_stop`) is OFF (`paste(force=True)` bypasses the `paste_enabled` gate).
- [ ] `paste_on_stop = False` + `clipboard_save_restore = True`: pipeline skips the clipboard entirely; transcription is only in the DB (no redundant copy/restore cycle).
- [ ] Changing `clipboard_restore_delay_ms` in Settings takes effect without restart.
- [ ] Changing `clipboard_save_restore` in Settings takes effect without restart.
- [ ] `history_db.get_latest_text()` orders by `id DESC` (not `timestamp DESC`) so same-second transcriptions return the true latest.
- [ ] Renderer config schema (Settings UI) exposes `clipboard_save_restore` and `clipboard_restore_delay_ms` so the user can actually change them (§8.3a). Without this, the IPC allowlist entries are unreachable from the UI.
- [ ] Accepted residual risk: tiny crash window between `copy()` returning and `paste()` scheduling the restore (no clipboard-mutating code runs between them; effectively unreachable — §11.7).
- [ ] Accepted residual risk: `_delayed_restore()` skips restore if the clipboard is locked at restore time (best-effort, same as current code — §11.7).
- [ ] Repaste hotkey works after app restart (reads from DB).
- [ ] Repaste hotkey falls back to `_last_transcription` if DB read throws.
- [ ] Repaste hotkey shows "No previous transcription" if DB is empty.
- [ ] Two rapid copy/paste cycles do not clobber each other's snapshots.
- [ ] Restore is skipped if user copies something during the 150ms window.
- [ ] Restore runs on a daemon thread — pipeline `finally` block is not delayed.
- [ ] `copy()` failure restores the snapshot immediately (defensive).
- [ ] Repaste with `paste()` skipped/blocked: `paste()` already scheduled the restore of the user's original clipboard before the early return (DP1); no `restore_now()` re-done here, transcription stays in DB, toast instructs re-triggering repaste (not Ctrl+V).
- [ ] Password fields still blocked (Windows).
- [ ] Elevated windows still blocked (Windows).
- [ ] Rate limit (0.5s) still enforced.
- [ ] Seq-mismatch recovery still works.
- [ ] `schedule_clipboard_clear` and `_saved_clipboard` are deleted (grep returns no hits in `server/`).
- [ ] `clipboard_clear_delay_seconds` is deleted from `config.py`.
- [ ] All new tests pass.
- [ ] `ClipboardCopyError` is imported in `dictation_pipeline.py` and `app.py` (not defined locally); no circular import introduced.
- [ ] No existing tests break (beyond intended behavior changes in `paste_on_stop = False`).

---

## 15. What This ADR Does NOT Do

- **No 10-second timer.** Restore is immediate (150ms after paste, or instant if no paste). The 10s timer was rejected earlier in the design discussion.
- **No Paste Palette.** A third hotkey was rejected to avoid overwhelming users. P8 is partially solved by the existing History page.
- **No text injection.** Clipboard borrow/restore via existing Ctrl+V path. Text injection (Solution B) was rejected due to Wayland root requirements and macOS Accessibility permission friction.
- **No window-focus tracking for P3.** Auto-paste goes to whatever window is focused when `paste()` fires. P3 is user responsibility (§11.5).
- **No removal of `_last_transcription`.** Kept for `undo_last()` compatibility. Repaste no longer depends on it, but undo still does.
- **No X11/Wayland multi-format restore.** Text-only on Linux (§11.1, §11.2). Documented limitation, not a bug.
- **No PII redaction or encryption in `history_db`.** Out of scope for this ADR. The DB already has POSIX permission hardening (`chmod 0600`).
