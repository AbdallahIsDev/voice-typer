"""Multi-format clipboard snapshot/restore.

ADR-0010 §4: standalone module that captures and restores **all** clipboard
formats. Platform-dispatched. No dependency on ``pyperclip`` (which is
text-only).

Design principles (ADR-0010 §3):

* DP1 — every borrow is paired with a restore.
* DP4 — snapshots are passed as values, not stored as instance state.
* DP5 — capture all formats on Windows and macOS; text-only on Linux
  (X11 and Wayland). Linux limitations are documented, not hidden.

The snapshot is an immutable ``@dataclass``. ``capture()`` is a classmethod
returning a new instance (or ``None``). ``restore()`` dispatches on
``self.platform`` — the platform tag is captured at creation time and travels
with the snapshot, so no global state is consulted at restore time.

Cross-platform safety: every platform branch is wrapped so that an import
failure or API misuse on a non-target platform logs and returns ``None``
rather than crashing the caller. The transcription pipeline treats
``None`` as "no snapshot to restore" — a degraded but safe mode.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from voice_typer.server.platform_utils import is_macos, is_windows

log = logging.getLogger(__name__)


# cross-thread serialization lock for restore() ──────
#
# Platform clipboard APIs are NOT thread-safe:
#
#   * Win32 ``OpenClipboard`` / ``EmptyClipboard`` / ``SetClipboardData``:
#     only one thread per process can hold the clipboard open at a time.
#     A second concurrent ``OpenClipboard`` on the same process fails
#     (returns 0), and the subsequent ``SetClipboardData`` calls are
#     silently dropped — the user's original clipboard content is lost.
#   * macOS ``NSPasteboard.clearContents`` / ``writeObjects_``: AppKit
#     documents NSPasteboard as main-thread-only; concurrent access from
#     background threads is undefined behavior.
#   * Linux X11 / Wayland: ``xclip`` and ``wl-copy`` are external
#     subprocesses, but two concurrent invocations race on the clipboard
#     selection ownership and the second one's content can be lost.
#
# ``ClipboardManager._delayed_restore`` runs on a daemon thread (one per
# paste() cycle). ``_force_restore_pending_at_exit`` runs on the main
# thread during interpreter shutdown. Without serialization, the daemon
# thread for cycle A can call ``snapshot_A.restore()`` concurrently with
# the atexit handler calling ``snapshot_B.restore()`` for a different
# pending entry B — racing on the platform clipboard APIs and leaving
# the clipboard in an indeterminate state (typically: empty, or stuck
# with the dictated text from one of the two cycles).
#
# The  fix in ``manager.py`` already prevents atexit and the SAME
# snapshot's daemon from both calling ``snapshot.restore()`` (the daemon
# claims its entry under ``_pending_restores_lock`` before restoring,
# and short-circuits if atexit already took it). But it does NOT prevent
# two DIFFERENT snapshots from being restored concurrently — that's the
# residual race this lock closes.
#
# ``threading.Lock`` (not ``RLock``) is correct here: ``restore()``
# dispatches to ``_restore_windows`` / ``_restore_macos`` /
# ``_restore_x11`` / ``_restore_wayland``, none of which call back into
# ``restore()``. No reentrancy → no deadlock risk from a non-reentrant
# lock. A ``Lock`` is also marginally faster (no owner-tracking) and
# surfaces reentrancy bugs as a deadlock rather than silently allowing
# them.
#
# The lock is module-level (not per-instance) because the race is
# between DIFFERENT snapshots on different threads — a per-instance lock
# would not serialize them.
_restore_lock = threading.Lock()


# ─── Windows builtin clipboard format IDs ─────────────────────────────────
# https://learn.microsoft.com/en-us/windows/win32/dataxchg/standard-clipboard-formats
_CF_TEXT = 1
_CF_BITMAP = 2
_CF_METAFILEPICT = 3
_CF_SYLK = 4
_CF_DIF = 5
_CF_TIFF = 6
_CF_OEMTEXT = 7
_CF_DIB = 8
_CF_PALETTE = 9
_CF_PENDATA = 10
_CF_RIFF = 11
_CF_WAVE = 12
_CF_UNICODETEXT = 13
_CF_ENHMETAFILE = 14
_CF_HDROP = 15
_CF_LOCALE = 16
_CF_DIBV5 = 17

# Builtin format ID → human-readable name. Used when GetClipboardFormatNameW
# returns 0 (which means the format is a builtin and has no registered name).
_BUILTIN_FORMAT_NAMES: dict[int, str] = {
    _CF_TEXT: "CF_TEXT",
    _CF_BITMAP: "CF_BITMAP",
    _CF_METAFILEPICT: "CF_METAFILEPICT",
    _CF_SYLK: "CF_SYLK",
    _CF_DIF: "CF_DIF",
    _CF_TIFF: "CF_TIFF",
    _CF_OEMTEXT: "CF_OEMTEXT",
    _CF_DIB: "CF_DIB",
    _CF_PALETTE: "CF_PALETTE",
    _CF_PENDATA: "CF_PENDATA",
    _CF_RIFF: "CF_RIFF",
    _CF_WAVE: "CF_WAVE",
    _CF_UNICODETEXT: "CF_UNICODETEXT",
    _CF_ENHMETAFILE: "CF_ENHMETAFILE",
    _CF_HDROP: "CF_HDROP",
    _CF_LOCALE: "CF_LOCALE",
    _CF_DIBV5: "CF_DIBV5",
}

# Formats that cannot be round-tripped through GlobalAlloc + memmove because
# they are GDI handles, not byte streams. The actual image data is preserved
# via CF_DIB / CF_DIBV5 (which ARE byte streams), so images are not lost.
# ADR-0010 §4.3, §11.3.
_NON_RESTORABLE_FORMATS: frozenset[int] = frozenset(
    {
        _CF_BITMAP,
        _CF_METAFILEPICT,
        _CF_ENHMETAFILE,
    }
)

# Maximum bytes captured for a single clipboard format. Formats larger
# than this are skipped (with a debug log) so a pathological clipboard
# (e.g. a 200 MB RTF blob from a copied Excel range, or a huge
# private-data format registered by an office suite) cannot exhaust
# Python heap on every dictation. 16 MB comfortably covers every
# realistic text/HTML/RTF format (the largest typical payload is a
# richly-formatted document paste at ~1-2 MB) while still bounding
# peak memory. Image formats (CF_DIB/CF_DIBV5) above the cap are also
# skipped — they would have been restored as raw bytes anyway, which
# for an oversized bitmap is slow and rarely what the user wants
# restored (they typically want the *next* copy to replace it).
_MAX_FORMAT_BYTES = 16 * 1024 * 1024

# Running-total cap on the bytes captured across ALL formats in
# a single ``ClipboardSnapshot``. The Windows clipboard can expose 15+
# formats per copy (CF_UNICODETEXT, CF_TEXT, CF_OEMTEXT, CF_LOCALE,
# CF_DIB, CF_DIBV5, "Rich Text Format", "HTML Format", "XML
# Spreadsheet", "Biff12", "Biff8", "Biff5", "CSV", "Hyperlink", plus
# private formats). Each is read into a fresh ``bytes`` object and held
# in ``items`` until ``restore()`` runs. The per-format cap (above)
# bounds a single format at 16 MB, but the theoretical peak per
# snapshot is 16 MB × 20 formats = 320 MB. 64 MB caps the running
# total: once we've captured 64 MB of formats, we break out of the
# format-walk loop. The captured formats (text first, because
# ``EnumClipboardFormats`` returns CF_* text formats in ID order, and
# most apps register text formats before rich formats) are sufficient
# for restore; later formats (RTF, HTML, image) are best-effort.
_MAX_TOTAL_SNAPSHOT_BYTES = 64 * 1024 * 1024


def _builtin_format_name(fmt: int) -> str:
    """Return the standard name for a builtin clipboard format, or ''."""
    return _BUILTIN_FORMAT_NAMES.get(fmt, "")


@dataclass
class ClipboardSnapshot:
    """A captured snapshot of clipboard content at a point in time.

    Captures all formats (text, RTF, HTML, image, file lists) on Windows
    and macOS. Captures text-only on Linux (X11 and Wayland) due to CLI
    tool limitations — see ADR-0010 §4.5 and §4.6.

    Usage::

        snap = ClipboardSnapshot.capture()
        if snap is not None:
            try:
                pyperclip.copy(transcription_text)
                send_paste_keystroke()
                time.sleep(0.15)
            finally:
                snap.restore()

    The dataclass is intentionally simple — ``items`` is a list of
    platform-specific tuples (the platform knows how to interpret them).
    No methods on the dataclass mutate state; ``restore()`` only reads
    ``self.platform`` and ``self.items``.
    """

    platform: str  # "windows" | "macos" | "linux-x11" | "linux-wayland"
    items: list[tuple[Any, ...]] = field(default_factory=list)
    captured_at: float = 0.0

    # ─── Public API ────────────────────────────────────────────────────

    @classmethod
    def capture(cls) -> ClipboardSnapshot | None:
        """Capture the current clipboard across all formats.

        Returns ``None`` if the clipboard cannot be opened (another app
        holds the lock) or if no formats are present. The caller treats
        ``None`` as "no snapshot to restore" — a degraded but safe mode.
        """
        try:
            if is_windows():
                return cls._capture_windows()
            if is_macos():
                return cls._capture_macos()
            # Linux: dispatch on XDG_SESSION_TYPE (default x11).
            session = os.environ.get("XDG_SESSION_TYPE", "x11").lower()
            if session == "wayland":
                snap = cls._capture_wayland()
                # XWayland fallback: if wl-paste fails (e.g. running under
                # XWayland without a Wayland compositor), try xclip.
                if snap is None or not snap.items:
                    snap = cls._capture_x11()
                return snap
            return cls._capture_x11()
        except Exception:
            log.exception("[CLIPBOARD-SNAPSHOT] capture failed")
            return None

    def restore(self) -> bool:
        """Restore all captured formats.

                Returns ``True`` if the restore completed without raising,
                ``False`` on failure. Best-effort: per-item failures are logged
                but do not abort the loop (we restore as many formats as we can).

        the entire platform-dispatched restore is
                serialized across threads by ``_restore_lock``. Platform
                clipboard APIs are not thread-safe (Win32 ``OpenClipboard``
                fails on the second concurrent opener; macOS
                ``NSPasteboard.clearContents`` / ``writeObjects_`` is
                main-thread-only; Linux ``xclip`` / ``wl-copy`` subprocesses
                race on selection ownership). The lock prevents the daemon
                thread for cycle A's ``snapshot_A.restore()`` from racing the
                atexit handler's ``snapshot_B.restore()`` (or another daemon's
                ``snapshot_C.restore()``) on the platform clipboard APIs.

                The lock is held for the duration of the platform restore
                (Open/Empty/Set/Close on Windows; clearContents/writeObjects on
                macOS; subprocess.run on Linux). This is correct: the platform
                call sequence is the critical section. Per-item failures inside
                ``_restore_windows`` etc. are still logged-and-continue (best
                effort) — the lock is not released between items because
                releasing between items would re-open the race window mid-loop.
        """
        with _restore_lock:
            if self.platform == "windows":
                return self._restore_windows()
            if self.platform == "macos":
                return self._restore_macos()
            if self.platform == "linux-x11":
                return self._restore_x11()
            if self.platform == "linux-wayland":
                return self._restore_wayland()
            log.warning("[CLIPBOARD-SNAPSHOT] unknown platform: %s", self.platform)
            return False

    # ─── Windows (Win32 API) ───────────────────────────────────────────

    @staticmethod
    def _configure_win32_signatures(user32: Any, kernel32: Any) -> None:
        """Pin ctypes ``restype``/``argtypes`` for the Win32 calls we use.

        Without this, ctypes defaults every return value and unspecified
        argument to a 32-bit C ``int``. On 64-bit Windows, clipboard
        HANDLEs and the pointers from ``GlobalLock`` are 64-bit, so the
        default truncates them to 32 bits — a corrupted pointer that,
        when handed to ``ctypes.string_at``/``memmove``, reads or writes
        a garbage address and corrupts the heap (STATUS_HEAP_CORRUPTION,
        0xC0000374). Declaring the signatures makes ctypes marshal the
        full 64-bit values.
        """
        import ctypes
        from ctypes import c_int, c_size_t, c_uint, c_void_p, c_wchar_p

        user32.OpenClipboard.argtypes = [c_void_p]
        user32.OpenClipboard.restype = c_int
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = c_int
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = c_int
        user32.EnumClipboardFormats.argtypes = [c_uint]
        user32.EnumClipboardFormats.restype = c_uint
        user32.GetClipboardFormatNameW.argtypes = [c_uint, c_wchar_p, c_int]
        user32.GetClipboardFormatNameW.restype = c_int
        user32.GetClipboardData.argtypes = [c_uint]
        user32.GetClipboardData.restype = c_void_p
        user32.SetClipboardData.argtypes = [c_uint, c_void_p]
        user32.SetClipboardData.restype = c_void_p
        user32.RegisterClipboardFormatW.argtypes = [c_wchar_p]
        user32.RegisterClipboardFormatW.restype = c_uint

        kernel32.GlobalSize.argtypes = [c_void_p]
        kernel32.GlobalSize.restype = c_size_t
        kernel32.GlobalLock.argtypes = [c_void_p]
        kernel32.GlobalLock.restype = c_void_p
        kernel32.GlobalUnlock.argtypes = [c_void_p]
        kernel32.GlobalUnlock.restype = c_int
        kernel32.GlobalAlloc.argtypes = [c_uint, c_size_t]
        kernel32.GlobalAlloc.restype = c_void_p
        kernel32.GlobalFree.argtypes = [c_void_p]
        kernel32.GlobalFree.restype = c_void_p
        _ = ctypes  # keep the import referenced for clarity

    @classmethod
    def _capture_windows(cls) -> ClipboardSnapshot | None:
        """Capture all formats from the Windows clipboard via Win32 API.

        Walks ``EnumClipboardFormats`` and reads every available format
        as raw bytes via ``GetClipboardData`` + ``GlobalLock`` +
        ``GlobalSize``. Returns ``None`` if the clipboard cannot be
        opened (another app holds it).
        """
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        cls._configure_win32_signatures(user32, kernel32)

        # OpenClipboard(0) — pass NULL owner so we don't associate the
        # clipboard with our window (we have none; we're a tray app).
        if not user32.OpenClipboard(0):
            log.debug("[CLIPBOARD-SNAPSHOT] OpenClipboard failed")
            return None
        try:
            items: list[tuple[int, str, bytes]] = []
            # Running total of bytes captured across ALL formats
            # in this snapshot. Once we hit ``_MAX_TOTAL_SNAPSHOT_BYTES``
            # we break out of the format-walk loop — the captured
            # formats (text first) are sufficient for restore; later
            # formats (RTF, HTML, image) are best-effort. This bounds
            # the worst-case per-snapshot memory at 64 MB + one format
            # over the cap (<= 16 MB), instead of 16 MB × 20 formats
            # = 320 MB.
            total_bytes = 0
            fmt = 0
            while True:
                fmt = user32.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break

                # Skip GDI-handle formats (CF_BITMAP, CF_METAFILEPICT,
                # CF_ENHMETAFILE). For these, GetClipboardData returns a
                # GDI HANDLE — NOT an HGLOBAL — so calling GlobalSize /
                # GlobalLock / string_at on it reads a non-memory handle
                # as if it were a heap block, corrupting the heap
                # (STATUS_HEAP_CORRUPTION, 0xC0000374). We also cannot
                # restore them from raw bytes anyway (the restore path
                # skips the same set), so there is no reason to capture
                # them. Image data is still preserved via CF_DIB/CF_DIBV5.
                if fmt in _NON_RESTORABLE_FORMATS:
                    continue

                # Get human-readable name (for registered formats).
                name_buf = ctypes.create_unicode_buffer(256)
                name_len = user32.GetClipboardFormatNameW(fmt, name_buf, 256)
                name = name_buf.value if name_len > 0 else _builtin_format_name(fmt)

                handle = user32.GetClipboardData(fmt)
                if not handle:
                    continue

                size = kernel32.GlobalSize(handle)
                if size == 0:
                    continue

                # Bounded RAM: skip formats whose payload exceeds the cap.
                # ``ctypes.string_at(ptr, size)`` would otherwise copy the
                # entire payload into a fresh Python ``bytes`` object on
                # every capture — a 200 MB clipboard format (huge RTF
                # from an office suite, oversized private-data format)
                # would balloon Python RSS by 200 MB per dictation and
                # not be released until the snapshot is restored (which
                # may be never if the borrow-then-restore path is
                # skipped due to a paste failure).
                if size > _MAX_FORMAT_BYTES:
                    log.debug(
                        "[CLIPBOARD-SNAPSHOT] skipping fmt=%d name=%r: %d bytes exceeds %d-byte cap",
                        fmt,
                        name,
                        size,
                        _MAX_FORMAT_BYTES,
                    )
                    continue

                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    continue
                try:
                    data = ctypes.string_at(ptr, size)
                finally:
                    kernel32.GlobalUnlock(handle)

                items.append((fmt, name, data))

                # Track the running total of bytes captured
                # across all formats. Once we hit the per-snapshot cap,
                # break out of the format-walk loop — the captured
                # formats (text first) are sufficient for restore;
                # later formats (RTF, HTML, image) are best-effort.
                total_bytes += size
                if total_bytes >= _MAX_TOTAL_SNAPSHOT_BYTES:
                    log.debug(
                        "[CLIPBOARD-SNAPSHOT] total bytes captured (%d) >= %d-byte cap — "
                        "stopping format walk after %d formats (remaining formats are best-effort)",
                        total_bytes,
                        _MAX_TOTAL_SNAPSHOT_BYTES,
                        len(items),
                    )
                    break

            if not items:
                # Empty clipboard — return None so the caller skips restore.
                return None

            return cls(
                platform="windows",
                items=items,
                captured_at=time.monotonic(),
            )
        finally:
            user32.CloseClipboard()

    def _restore_windows(self) -> bool:
        """Restore all captured formats to the Windows clipboard.

                Re-registers registered formats by name (the ID may differ from
                the original because Windows assigns IDs dynamically). Skips
                GDI-handle formats (CF_BITMAP, CF_METAFILEPICT, CF_ENHMETAFILE)
                which cannot be round-tripped through GlobalAlloc.

        (session-DE, Medium, Data integrity): the pre-fix code
                called ``EmptyClipboard()`` unconditionally, then iterated
                ``self.items`` calling ``SetClipboardData`` per format. Per-item
                failures were logged at DEBUG and the item skipped; the function
                returned ``True`` unconditionally — even if EVERY
                ``SetClipboardData`` call failed (e.g. all ``GlobalAlloc``
                returned 0 due to memory pressure). After ``EmptyClipboard()``
                ran, the user's original clipboard content was gone, but the
                caller logged "Restored snapshot" — false success with silent
                permanent data loss.

                Fix: track a success count during the loop. If zero items were
                successfully set, return ``False`` and log at WARNING so the
                caller logs failure instead of "Restored snapshot". The
                ``EmptyClipboard()`` call is preserved (the capture-then-swap
                pattern would be more complex and is left as a future
                improvement); the fix narrows the false-success case from
                "zero items set → True" to "zero items set → False + WARNING".
        """
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._configure_win32_signatures(user32, kernel32)

        # GMEM_MOVEABLE — required by SetClipboardData.
        gmem_moveable = 0x0002

        if not user32.OpenClipboard(0):
            log.debug("[CLIPBOARD-SNAPSHOT] OpenClipboard for restore failed")
            return False
        try:
            user32.EmptyClipboard()
            success_count = 0
            for fmt, name, data in self.items:
                # Skip GDI-handle formats — they cannot be restored from
                # raw bytes. Image data is preserved via CF_DIB / CF_DIBV5.
                if fmt in _NON_RESTORABLE_FORMATS:
                    continue

                target_fmt = fmt
                # Re-register registered formats by name so the ID matches
                # what the consuming app expects (the original ID may
                # differ across processes / sessions).
                if name:
                    registered = user32.RegisterClipboardFormatW(name)
                    if registered:
                        target_fmt = registered
                    else:
                        log.debug(
                            "[CLIPBOARD-SNAPSHOT] RegisterClipboardFormatW failed for %r — skipping",
                            name,
                        )
                        continue

                h_mem = kernel32.GlobalAlloc(gmem_moveable, len(data))
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

                # SetClipboardData takes ownership of h_mem on success.
                # On failure, we must free it ourselves.
                if not user32.SetClipboardData(target_fmt, h_mem):
                    kernel32.GlobalFree(h_mem)
                    log.debug(
                        "[CLIPBOARD-SNAPSHOT] SetClipboardData failed for fmt=%d name=%r",
                        target_fmt,
                        name,
                    )
                    continue
                success_count += 1
            if success_count == 0:
                # zero items were successfully set. EmptyClipboard()
                # has already cleared the clipboard, so the user's prior
                # content is gone. Return False so the caller logs failure
                # instead of "Restored snapshot" — at least the audit
                # trail is honest about the data loss.
                log.warning(
                    "[CLIPBOARD-SNAPSHOT] _restore_windows: 0/%d formats set — "
                    "clipboard is empty after EmptyClipboard (DE-62)",
                    len(self.items),
                )
                return False
            return True
        finally:
            user32.CloseClipboard()

    # ─── macOS (NSPasteboard) ──────────────────────────────────────────

    @classmethod
    def _capture_macos(cls) -> ClipboardSnapshot | None:
        """Capture all formats from the macOS pasteboard via NSPasteboard.

        Records the pasteboard item index so multi-item pasteboards
        (e.g. multiple files copied from Finder) can be restored as
        separate NSPasteboardItem objects.
        """
        try:
            import AppKit  # type: ignore[import-not-found]
        except ImportError:
            log.debug("[CLIPBOARD-SNAPSHOT] AppKit unavailable")
            return None

        pb = AppKit.NSPasteboard.generalPasteboard()
        items: list[tuple[int, str, bytes]] = []

        for idx, item in enumerate(pb.pasteboardItems()):
            for type_name in item.types():
                nsdata = item.dataForType_(type_name)
                if nsdata is None:
                    continue
                length = nsdata.length()
                # Bounded RAM (mirrors the Windows path): skip formats
                # whose payload exceeds the cap so a pathological
                # pasteboard (huge image, large private data) cannot
                # exhaust Python heap on every dictation. ``bytes(...)``
                # below would otherwise copy the entire payload.
                if length > _MAX_FORMAT_BYTES:
                    log.debug(
                        "[CLIPBOARD-SNAPSHOT] skipping type=%r idx=%d: %d bytes exceeds %d-byte cap",
                        type_name,
                        idx,
                        length,
                        _MAX_FORMAT_BYTES,
                    )
                    continue
                # NSData.bytes() returns a pointer; .as_buffer(n) gives
                # us a buffer we can convert to bytes.
                data = b"" if length == 0 else bytes(nsdata.bytes().as_buffer(length))
                items.append((idx, str(type_name), data))

        if not items:
            return None

        return cls(
            platform="macos",
            items=items,
            captured_at=time.monotonic(),
        )

    def _restore_macos(self) -> bool:
        """Restore all captured formats to the macOS pasteboard.

        Groups items by their original pasteboard item index and writes
        one NSPasteboardItem per original index, preserving multi-item
        pasteboards.

        (Data integrity): the pre-fix code called
                ``item.setData_forType_`` and ``pb.writeObjects_`` without
                inspecting their return values — a per-item failure (e.g.
                an unsupported type-name, a payload that violates the
                type's contract) or a ``writeObjects_`` rejection (which
                returns NO if NO items were accepted) was silently
                swallowed, the function returned ``True`` unconditionally,
                and the caller logged "Restored snapshot" while the
                clipboard was left empty (``clearContents`` having already
                wiped it). Mirror of the Windows ``_restore_windows``
                pattern: track ``success_count`` (increment only when
                ``setData_forType_`` returns True), inspect
                ``writeObjects_``'s BOOL return, and return ``False`` with
                a WARNING log if zero items were set OR ``writeObjects_``
                returned False.
        """
        try:
            import AppKit  # type: ignore[import-not-found]
            import Foundation  # type: ignore[import-not-found]
        except ImportError:
            log.debug("[CLIPBOARD-SNAPSHOT] AppKit/Foundation unavailable for restore")
            return False

        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()

        # Group items by pasteboard item index so multi-item pasteboards
        # are restored as separate NSPasteboardItem objects.
        from collections import defaultdict

        grouped: dict[int, list[tuple[str, bytes]]] = defaultdict(list)
        for idx, type_name, data in self.items:
            grouped[idx].append((type_name, data))

        success_count = 0
        ns_items = []
        for idx in sorted(grouped.keys()):
            item = AppKit.NSPasteboardItem.alloc().init()
            for type_name, data in grouped[idx]:
                nsdata = Foundation.NSData.dataWithBytes_length_(data, len(data)) if data else Foundation.NSData.data()
                if item.setData_forType_(nsdata, type_name):
                    success_count += 1
                else:
                    log.debug(
                        "[CLIPBOARD-SNAPSHOT] setData_forType_ failed for idx=%d type=%r",
                        idx,
                        type_name,
                    )
            ns_items.append(item)

        # ``writeObjects_`` returns YES only if at least one NSPasteboardItem
        # was accepted by the pasteboard; a NO return means the clipboard is
        # still empty after clearContents (silent data loss).
        write_ok = bool(pb.writeObjects_(ns_items)) if ns_items else True

        if success_count == 0 or not write_ok:
            log.warning(
                "[CLIPBOARD-SNAPSHOT] _restore_macos: %d/%d items set, "
                "writeObjects_=%s — clipboard may be empty after clearContents",
                success_count,
                len(self.items),
                write_ok,
            )
            return False
        return True

    # ─── Linux X11 (xclip, text-only — documented limitation) ──────────

    @classmethod
    def _capture_x11(cls) -> ClipboardSnapshot | None:
        """Capture text targets from the X11 clipboard via xclip.

        Documented limitation (ADR-0010 §4.5, §11.1): xclip can only
        hold one target per clipboard selection. A full multi-format X11
        implementation requires Gtk.Clipboard via PyGObject, which is
        not a dependency of this project. Images and file lists are not
        preserved.
        """
        import subprocess

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

        if not items:
            return None

        return cls(
            platform="linux-x11",
            items=items,
            captured_at=time.monotonic(),
        )

    def _restore_x11(self) -> bool:
        """Restore text content to the X11 clipboard via xclip.

        (session-DE, Medium, Data integrity): the pre-fix code
                called ``subprocess.run(...)`` without ``check=True``, so a
                non-zero ``xclip`` exit (no ``DISPLAY``, X11 connection
                refused, compositor error) did NOT raise — the function
                returned ``True`` unconditionally and the caller logged
                "Restored snapshot" while the user's clipboard still contained
                the dictated text. Silent data loss with false-success signal.
                Now we pass ``check=True`` so non-zero exits raise
                ``CalledProcessError``, catch it alongside
                ``TimeoutExpired``/``FileNotFoundError``, and return ``False``
                on failure with a WARNING log.
        """
        import subprocess

        if not self.items:
            return True  # nothing to restore

        target, data = self.items[0]
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", target, "-i"],
                input=data,
                timeout=2.0,
                check=True,
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            log.debug("[CLIPBOARD-SNAPSHOT] xclip restore failed (timeout or missing)")
            return False
        except subprocess.CalledProcessError as exc:
            log.warning(
                "[CLIPBOARD-SNAPSHOT] xclip restore failed (exit %d) — "
                "clipboard may still contain dictated text (DE-61)",
                exc.returncode,
            )
            return False

    # ─── Linux Wayland (wl-copy/wl-paste, text-only — documented) ──────

    @classmethod
    def _capture_wayland(cls) -> ClipboardSnapshot | None:
        """Capture text targets from the Wayland clipboard via wl-paste.

        Documented limitation (ADR-0010 §4.6, §11.2): wl-copy can only
        serve one stdin stream for all --type flags. A full multi-format
        Wayland implementation requires a custom wl_data_source client,
        which is out of scope.
        """
        import subprocess

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

        if not items:
            return None

        return cls(
            platform="linux-wayland",
            items=items,
            captured_at=time.monotonic(),
        )

    def _restore_wayland(self) -> bool:
        """Restore text content to the Wayland clipboard via wl-copy.

        (session-DE, Medium, Data integrity): the pre-fix code
                called ``subprocess.run(...)`` without ``check=True``, so a
                non-zero ``wl-copy`` exit (compositor error, no Wayland
                display) did NOT raise — the function returned ``True``
                unconditionally and the caller logged "Restored snapshot"
                while the user's clipboard still contained the dictated text.
                Silent data loss with false-success signal. Now we pass
                ``check=True`` so non-zero exits raise ``CalledProcessError``,
                catch it alongside ``TimeoutExpired``/``FileNotFoundError``,
                and return ``False`` on failure with a WARNING log.
        """
        import subprocess

        if not self.items:
            return True

        target, data = self.items[0]
        try:
            subprocess.run(
                ["wl-copy", "--type", target],
                input=data,
                timeout=2.0,
                check=True,
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            log.debug("[CLIPBOARD-SNAPSHOT] wl-copy restore failed (timeout or missing)")
            return False
        except subprocess.CalledProcessError as exc:
            log.warning(
                "[CLIPBOARD-SNAPSHOT] wl-copy restore failed (exit %d) — "
                "clipboard may still contain dictated text (DE-61)",
                exc.returncode,
            )
            return False
