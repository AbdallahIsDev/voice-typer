"""R13-F3: handler catch-all error envelope consistency tests.

Pre-R13-F3, each handler's ``except Exception as e:`` catch-all built
its own ad-hoc error envelope inline::

    except Exception as e:
        log.error("[IPC] <cmd> failed: %s", e[, exc_info=True])
        resp["type"] = "error"
        resp["data"] = {"message": str(e)}
    return resp

The ad-hoc envelope OMITTED the ``code`` field that every other error
path (validation, dispatch safety net, rate limiter, internal_error)
sets. Clients branching on ``code`` (e.g. the renderer's toast-dispatch
logic) silently fell through to a generic "unknown error" path for
handler exceptions, even though the message text was rich.

R13-F3 introduces :func:`voice_typer.server.ipc.validation._error_response`
and routes every handler catch-all through it. The helper stamps
``code: "handler_error"`` (overridable for known-error paths) and
preserves the message text (so existing tests that assert on
``str(e)`` content continue to pass). The caller is responsible for
logging the full exception server-side at ERROR with ``exc_info=True``
— the helper does NOT log, only constructs the envelope.

These tests pin the new contract:

1. ``_error_response`` stamps the ``code`` + ``message`` fields and
   returns the mutated ``resp`` (preserving any pre-existing ``id``).
2. The default code is ``"handler_error"``; the caller can override
   (e.g. ``"not_initialized"``) for known-error paths that still want
   the helper's envelope shape.
3. Every handler file in ``voice_typer/server/handlers/`` (except the
   ``_base.py`` mixin + ``__init__.py`` re-export) uses the helper in
   its catch-all — verified via static source scan so a future
   regression that re-introduces an inline envelope is caught.
4. A live handler dispatch (``toggle_dictation`` with a service that
   raises) produces a response with the structured ``code`` field.
5. The full exception is logged server-side with ``exc_info=True``
   (verified via ``caplog``) — the client only sees the sanitized
   message text, not the traceback.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

# ── Helper unit tests ───────────────────────────────────────────────────


class TestErrorResponseHelper:
    """R13-F3: ``_error_response`` is the single source of truth for
    the handler catch-all envelope shape."""

    def test_stamps_code_and_message(self):
        """The helper stamps ``code: "handler_error"`` (default) and
        the provided message, then returns the mutated ``resp``."""
        from voice_typer.server.ipc.validation import _error_response

        resp = {"id": 42}
        result = _error_response(resp, "disk full")
        assert result is resp, "helper must return the same resp dict (mutated in place)"
        assert result["type"] == "error"
        assert result["data"] == {"code": "handler_error", "message": "disk full"}
        # The pre-existing ``id`` field is preserved (the helper does
        # NOT touch it — ``_dispatch`` relies on this to round-trip
        # the request id to the client).
        assert result["id"] == 42

    def test_default_code_is_handler_error(self):
        """The default code is ``"handler_error"`` — the standard for
        an unexpected exception caught by a handler's catch-all."""
        from voice_typer.server.ipc.validation import _error_response

        resp = {}
        _error_response(resp, "boom")
        assert resp["data"]["code"] == "handler_error"

    def test_code_can_be_overridden(self):
        """The caller can override the code for known-error paths that
        still want the helper's envelope shape (e.g.
        ``"not_initialized"``)."""
        from voice_typer.server.ipc.validation import _error_response

        resp = {}
        _error_response(resp, "vocabulary automation is not initialized", code="not_initialized")
        assert resp["data"]["code"] == "not_initialized"
        assert resp["data"]["message"] == "vocabulary automation is not initialized"

    def test_preserves_existing_id_field(self):
        """The helper must NOT clobber a pre-existing ``id`` field —
        ``_dispatch`` populates ``resp["id"] = msg["id"]`` before
        calling the handler, and the client uses ``id`` to correlate
        request/response. A regression that drops the id would break
        every request/response correlation in the protocol."""
        from voice_typer.server.ipc.validation import _error_response

        resp = {"id": 99, "type": "ack", "data": {"in_progress": True}}
        _error_response(resp, "handler raised")
        assert resp["id"] == 99, "id must be preserved"
        assert resp["type"] == "error", "type must be overwritten to error"
        assert resp["data"] == {"code": "handler_error", "message": "handler raised"}, (
            "data must be overwritten to the structured error envelope"
        )

    def test_message_is_not_truncated_or_modified(self):
        """The message is passed through verbatim — the caller decides
        what's safe to expose (the helper does NOT sanitize)."""
        from voice_typer.server.ipc.validation import _error_response

        resp = {}
        weird_message = "ValueError: bad input '\\x00' [extra] <html>"
        _error_response(resp, weird_message)
        assert resp["data"]["message"] == weird_message


# ── Static source scan: every handler file uses _error_response ─────────


class TestHandlerFilesUseHelper:
    """R13-F3: every handler mixin file must use ``_error_response``
    in its catch-all (no inline ``resp["data"] = {"message": str(e)}``
    envelopes remain).

    This is a static source-scan test — it doesn't dispatch any IPC,
    it just verifies the source code of each handler file. A future
    regression that re-introduces an inline envelope (e.g. someone
    copy-pastes a new handler without using the helper) is caught
    here.
    """

    HANDLERS_DIR = Path("voice_typer/server/handlers")

    # Files that are NOT handler mixins (don't need the helper).
    NON_HANDLER_FILES = {"__init__.py", "_base.py"}

    @pytest.fixture(autouse=True)
    def _handlers_dir_exists(self):
        """Skip the test class if the handlers dir is missing (e.g.
        running from a tarball without the source tree)."""
        if not self.HANDLERS_DIR.is_dir():
            pytest.skip(f"handlers dir not found: {self.HANDLERS_DIR}")

    def _handler_files(self):
        """Yield (filename, source) for each handler mixin file."""
        for fpath in sorted(self.HANDLERS_DIR.glob("*.py")):
            if fpath.name in self.NON_HANDLER_FILES:
                continue
            yield fpath, fpath.read_text()

    def test_every_handler_file_imports_error_response(self):
        """Each handler file must import ``_error_response`` from
        ``voice_typer.server.ipc.validation`` (or via the re-export at
        ``voice_typer.server.ipc_server``)."""
        missing = []
        for fpath, src in self._handler_files():
            if "_error_response" not in src:
                missing.append(fpath.name)
        assert not missing, (
            "R13-F3: every handler file must use _error_response in its "
            f"catch-all; the following files are missing the import: {missing}"
        )

    def test_no_handler_file_has_inline_str_e_envelope(self):
        """No handler file may have the pre-R13-F3 inline envelope
        ``resp["data"] = {"message": str(e)}`` (the helper replaces it).

        The string ``resp["data"] = {"message": str(e)}`` was the
        canonical pre-R13-F3 catch-all envelope. Post-R13-F3 every
        catch-all calls ``_error_response(resp, str(e))`` instead, so
        the literal string should NOT appear in any handler file.
        """
        offenders = []
        for fpath, src in self._handler_files():
            # Look for the exact pre-R13-F3 inline envelope line.
            # Tolerate single-quote variants (``resp['data'] = {'message': str(e)}``)
            # in case a future contributor uses a different quote style.
            if 'resp["data"] = {"message": str(e)}' in src or ("resp['data'] = {'message': str(e)}" in src):
                offenders.append(fpath.name)
        assert not offenders, (
            "R13-F3: the following handler files still contain the "
            "pre-R13-F3 inline catch-all envelope "
            '(`resp["data"] = {"message": str(e)}`); they must be '
            f"converted to use _error_response: {offenders}"
        )

    def test_every_handler_file_uses_helper_in_catch_all(self):
        """Each handler file must call ``_error_response(resp, str(e))``
        at least once (in at least one catch-all).

        This is a weaker check than per-catch-all verification but
        catches the case where a file has zero catch-alls using the
        helper (e.g. someone removed all the catch-alls, or added a
        new handler file without the helper).
        """
        no_helper_use = []
        for fpath, src in self._handler_files():
            # Files with no ``except Exception as e:`` block at all
            # are exempt (they have no catch-all to convert).
            if "except Exception as e:" not in src:
                continue
            if "_error_response(resp, str(e))" not in src:
                no_helper_use.append(fpath.name)
        assert not no_helper_use, (
            "R13-F3: the following handler files have at least one "
            "``except Exception as e:`` block but do not call "
            "``_error_response(resp, str(e))`` in any catch-all: "
            f"{no_helper_use}"
        )


# ── Live handler dispatch tests ─────────────────────────────────────────


class TestHandlerCatchAllEnvelopeShape:
    """R13-F3: a live handler dispatch that hits the catch-all must
    produce a response with the structured ``code`` field."""

    def test_toggle_dictation_catch_all_has_code_field(self, ipc_server, fake_service):
        """``toggle_dictation`` whose service raises must produce
        ``{"type": "error", "data": {"code": "handler_error",
        "message": <str(e)>}}`` — not the pre-R13-F3 bare
        ``{"message": str(e)}`` envelope."""
        fake_service.toggle_dictation.side_effect = RuntimeError("simulated boom")

        resp = ipc_server._handle_toggle_dictation(None, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "handler_error", (
            "R13-F3: handler catch-all envelope must carry the structured "
            "``code: 'handler_error'`` field so clients can branch on code "
            "rather than parsing the message text"
        )
        assert resp["data"]["message"] == "simulated boom"

    def test_get_vocabulary_catch_all_has_code_field(self, ipc_server, fake_service):
        """``get_vocabulary`` whose service raises must produce the
        structured envelope (different handler — verifies the fix is
        applied consistently across handler files, not just one)."""
        fake_service.get_vocabulary.side_effect = RuntimeError("vocab db corrupt")

        resp = ipc_server._handle_get_vocabulary({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "handler_error"
        assert "vocab db corrupt" in resp["data"]["message"]

    def test_export_diagnostics_catch_all_has_code_field(self, ipc_server, fake_service):
        """``export_diagnostics`` whose service raises must produce
        the structured envelope (system_handlers.py — different file
        from the above two)."""
        fake_service.export_diagnostics.side_effect = RuntimeError("disk full")

        resp = ipc_server._handle_export_diagnostics({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "handler_error"
        assert "disk full" in resp["data"]["message"]

    def test_get_microphones_catch_all_has_code_field(self, ipc_server, fake_service):
        """``get_microphones`` whose service raises must produce the
        structured envelope (microphone_handlers.py — different file
        again)."""
        fake_service.get_microphones.side_effect = RuntimeError("portaudio init failed")

        resp = ipc_server._handle_get_microphones({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "handler_error"
        assert "portaudio init failed" in resp["data"]["message"]

    def test_repaste_last_catch_all_has_code_field(self, ipc_server, fake_service):
        """``repaste_last`` whose service raises must produce the
        structured envelope (repaste_handlers.py — the newest handler
        file, added in UX-23)."""
        fake_service.repaste_last.side_effect = RuntimeError("clipboard busy")

        resp = ipc_server._handle_repaste_last({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "handler_error"
        assert "clipboard busy" in resp["data"]["message"]


# ── Server-side logging tests ───────────────────────────────────────────


class TestHandlerCatchAllLogging:
    """R13-F3: the full exception is logged server-side at ERROR with
    ``exc_info=True``; the client only sees the sanitized message text
    (no traceback)."""

    def test_catch_all_logs_at_error_level_with_exc_info(self, ipc_server, fake_service, caplog):
        """The catch-all must call ``log.error(..., exc_info=True)``
        so the traceback lands in the server log; the client envelope
        carries only ``str(e)`` (the message text, no traceback)."""
        fake_service.toggle_dictation.side_effect = RuntimeError("boom with detail")

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_toggle_dictation(None, {})

        # The client envelope carries the message text but NOT the traceback.
        assert resp["data"]["message"] == "boom with detail"
        assert "Traceback" not in resp["data"]["message"]
        assert resp["data"]["code"] == "handler_error"

        # The server log carries the ERROR-level entry with exc_info
        # attached (the traceback is in ``record.exc_info``).
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "catch-all must log at ERROR level"
        assert any("toggle_dictation failed" in r.getMessage() for r in error_records), (
            "the ERROR log message must identify the failing command (toggle_dictation)"
        )
        # R13-F3: ``exc_info=True`` is set so the traceback is captured
        # on the log record (not just the message text).
        assert any(r.exc_info is not None for r in error_records), (
            "R13-F3: catch-all must log with ``exc_info=True`` so the "
            "traceback lands in the server log; the client envelope only "
            "carries ``str(e)``"
        )

    def test_catch_all_does_not_leak_traceback_to_client(self, ipc_server, fake_service):
        """The client envelope must NOT contain the Python traceback —
        only ``str(e)``. The traceback is logged server-side only."""
        fake_service.export_diagnostics.side_effect = ValueError("malformed input\n  detail line 1\n  detail line 2")

        resp = ipc_server._handle_export_diagnostics({}, {})

        # The client sees the exception message (which may include
        # newlines from the exception text) but NOT a Python traceback
        # (no "Traceback (most recent call last)" prefix, no file/line
        # info).
        msg = resp["data"]["message"]
        assert "Traceback" not in msg
        assert "malformed input" in msg  # the str(e) text
        assert resp["data"]["code"] == "handler_error"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
