"""Mocked coverage for the macOS pasteboard capture path.

The restore half already has thorough mocked tests elsewhere; the
capture half had none. These tests exercise
``ClipboardSnapshot._capture_macos`` headlessly by installing a fake
``AppKit`` module, so they run on Linux CI and on the macOS
host-validation runner without touching the real pasteboard server
(which needs an interactive session).
"""

from __future__ import annotations

import sys
import time
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

from voice_typer.server import clipboard_snapshot as snap_mod
from voice_typer.server.clipboard_snapshot import (
    _MAX_FORMAT_BYTES,
    ClipboardSnapshot,
)


class _FakeNSData:
    """Minimal stand-in for NSData with a fixed byte payload."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def length(self) -> int:
        return len(self._payload)

    def bytes(self) -> Any:
        payload = self._payload

        class _Ptr:
            def as_buffer(self, n: int) -> bytes:
                return payload[:n]

        return _Ptr()


class _FakeOversizedNSData:
    """Reports a huge length so the size cap path triggers."""

    def __init__(self, length: int) -> None:
        self._length = length

    def length(self) -> int:
        return self._length

    def bytes(self) -> Any:  # pragma: no cover - must not be reached
        raise AssertionError("oversized payload must be skipped before bytes()")


class _FakeItem:
    def __init__(self, entries: dict[str, Any]) -> None:
        self._entries = entries

    def types(self) -> list[str]:
        return list(self._entries.keys())

    def dataForType_(self, type_name: str) -> Any:  # noqa: N802 - mirrors the ObjC selector
        return self._entries.get(type_name)


def _install_fake_appkit(items: list[_FakeItem]) -> ModuleType:
    pb = MagicMock(name="ns_pasteboard")
    pb.pasteboardItems.return_value = items
    ns_cls = MagicMock(name="NSPasteboard")
    ns_cls.generalPasteboard.return_value = pb
    appkit = ModuleType("AppKit")
    appkit.NSPasteboard = ns_cls  # type: ignore[attr-defined]
    return appkit


class TestCaptureMacosAppKitUnavailable:
    def test_returns_none_when_appkit_missing(self) -> None:
        with patch.dict(sys.modules, {"AppKit": None}):
            assert ClipboardSnapshot._capture_macos() is None


class TestCaptureMacosEmpty:
    def test_returns_none_on_empty_pasteboard(self) -> None:
        appkit = _install_fake_appkit([])
        with patch.dict(sys.modules, {"AppKit": appkit}):
            assert ClipboardSnapshot._capture_macos() is None

    def test_returns_none_when_all_types_yield_no_data(self) -> None:
        appkit = _install_fake_appkit([_FakeItem({"public.utf8-plain-text": None})])
        with patch.dict(sys.modules, {"AppKit": appkit}):
            assert ClipboardSnapshot._capture_macos() is None


class TestCaptureMacosItems:
    def test_multi_item_capture_preserves_index(self) -> None:
        before = time.monotonic()
        appkit = _install_fake_appkit(
            [
                _FakeItem({"public.utf8-plain-text": _FakeNSData(b"hello")}),
                _FakeItem({"public.utf8-plain-text": _FakeNSData(b"world")}),
            ]
        )
        with patch.dict(sys.modules, {"AppKit": appkit}):
            snap = ClipboardSnapshot._capture_macos()
        assert snap is not None
        assert snap.platform == "macos"
        assert snap.captured_at >= before
        assert (0, "public.utf8-plain-text", b"hello") in snap.items
        assert (1, "public.utf8-plain-text", b"world") in snap.items

    def test_zero_length_data_yields_empty_bytes(self) -> None:
        appkit = _install_fake_appkit(
            [_FakeItem({"public.utf8-plain-text": _FakeNSData(b"")})]
        )
        with patch.dict(sys.modules, {"AppKit": appkit}):
            snap = ClipboardSnapshot._capture_macos()
        assert snap is not None
        assert snap.items == [(0, "public.utf8-plain-text", b"")]

    def test_oversized_format_skipped_with_debug_log(self) -> None:
        appkit = _install_fake_appkit(
            [
                _FakeItem(
                    {
                        "public.utf8-plain-text": _FakeNSData(b"small"),
                        "public.tiff": _FakeOversizedNSData(_MAX_FORMAT_BYTES + 1),
                    }
                )
            ]
        )
        with (
            patch.dict(sys.modules, {"AppKit": appkit}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            snap = ClipboardSnapshot._capture_macos()
        assert snap is not None
        assert snap.items == [(0, "public.utf8-plain-text", b"small")]
        mock_log.debug.assert_called_once()

    def test_oversized_only_returns_none(self) -> None:
        appkit = _install_fake_appkit(
            [_FakeItem({"public.tiff": _FakeOversizedNSData(_MAX_FORMAT_BYTES + 1)})]
        )
        with (
            patch.dict(sys.modules, {"AppKit": appkit}),
            patch.object(snap_mod, "log"),
        ):
            assert ClipboardSnapshot._capture_macos() is None


class TestRestoreMacosWithoutAppKit:
    def test_returns_false_when_appkit_missing(self) -> None:
        snap = ClipboardSnapshot(
            platform="macos",
            items=[(0, "public.utf8-plain-text", b"hello")],
            captured_at=0.0,
        )
        with patch.dict(sys.modules, {"AppKit": None, "Foundation": None}):
            assert snap._restore_macos() is False
