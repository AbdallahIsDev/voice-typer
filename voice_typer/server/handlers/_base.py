"""Shared base for IPC handler mixins (CR-20 + R4-F3 combined).

This module is the result of merging two independent improvements:

* **CR-20 (session-1)**: ``HandlerBase`` with the
  :meth:`_respond_with_error` helper. Prior to CR-20, every IPC
  handler mixin caught its own ``Exception`` and returned the Python
  exception text verbatim to the renderer::

      except Exception as e:
          log.error("[IPC] <cmd> failed: %s", e)
          resp["type"] = "error"
          resp["data"] = {"message": str(e)}

  That violated the WS-path error-envelope contract documented in
  ``docs/architecture/error-envelope-contract.md`` and implemented by
  the dispatcher's outer ``except Exception`` in
  ``voice_typer/server/ipc_server.py`` — which deliberately
  emits a GENERIC envelope::

      {"type": "error",
       "data": {"code": "internal_error", "message": "internal error"}}

  to avoid leaking server internals (file paths, CUDA error strings,
  internal module names, HF repo IDs) to a potentially-compromised
  renderer. CR-20's ``_respond_with_error`` emits the SAME generic
  envelope so the renderer cannot distinguish "handler caught its own
  exception" from "exception propagated to the dispatcher".

* **R4-F3 (session-3)**: ``HandlerMixinBase`` with the three
  runtime-provided attribute annotations (``service``, ``app``,
  ``_send``). Previously every one of the 14 handler mixins
  re-declared the same 3-line ``Any`` annotation block to keep
  pyrefly's null-safety analysis happy. Centralizing the declaration
  in ``HandlerMixinBase`` removes the 42-times-duplicated annotation
  block (3 annotations × 14 handler modules).

The two classes are composed: ``HandlerBase`` inherits from
``HandlerMixinBase`` so handlers that need ``_respond_with_error`` get
both the method AND the annotations in one inheritance. Handlers that
don't need ``_respond_with_error`` inherit directly from
``HandlerMixinBase`` (just the annotations). This preserves the
architecture rule: thin base, focused subclasses.

Per-command VALIDATION errors (e.g. ``{"code": "missing_field",
"field": "id"}``, ``{"code": "payload_too_large"}``) are EXPLICIT and
remain the handler's responsibility — they are part of the documented
IPC contract that the renderer switches on. Only the catch-all
``Exception`` path is genericised via ``_respond_with_error``.
"""

from __future__ import annotations

import os
import traceback
from typing import Any

from voice_typer.server._secrets import redact_secret
from voice_typer.server.handlers._log import log


def _scrub_traceback(exc: BaseException) -> tuple[str, str]:
    """Scrub secrets and home-directory paths from an exception message
    and its formatted traceback before logging.

    Parameters
    ----------
    exc :
        The exception to scrub.

    Returns
    -------
    tuple[str, str]
        ``(scrubbed_str, scrubbed_tb)`` where *scrubbed_str* is the
        redacted ``str(exc)`` and *scrubbed_tb* is the redacted
        formatted traceback.  Both have home-directory paths replaced
        with ``~`` and known secret patterns (API keys, bearer tokens)
        replaced via :func:`~voice_typer.server._secrets.redact_secret`.
    """
    home = os.path.expanduser("~")
    exc_str = str(exc)
    scrubbed_str = redact_secret(exc_str)
    if home and home != "~":
        scrubbed_str = scrubbed_str.replace(home, "~")
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_text = "".join(tb_lines)
    scrubbed_tb = redact_secret(tb_text)
    if home and home != "~":
        scrubbed_tb = scrubbed_tb.replace(home, "~")
    return scrubbed_str, scrubbed_tb


class HandlerMixinBase:
    """Common base for IPC handler mixins (R4-F3).

    Declares the three runtime-provided attributes (``service``,
    ``app``, ``_send``) that every handler mixin accesses via
    ``self.X``.  The annotations are ``Any`` (not a Protocol) so:

    * pyrefly's null-safety check sees a declared attribute (no
      "attribute access before assignment" error);
    * handler mixins stay decoupled from the concrete
      :class:`VoiceTyperService` / :class:`VoiceTyperApp` types
      (MagicMock fixtures in ``tests/handlers/`` satisfy the loose
      ``Any`` typing without needing the real service classes);
    * ``IPCServer`` can compose any number of mixins via multiple
      inheritance without the mixins needing to know about each other.

    Subclasses MUST NOT override these annotations — the runtime
    binding happens in :meth:`IPCServer.__init__` (or the
    ``MagicMock`` auto-vivification in tests), not here.

    This class has NO state of its own, NO methods, and NO side
    effects at import time. It is a pure type-annotation container.
    """

    # ARCH-REFAC-002 / TASK-10 / R4-F3: pyrefly null-safety fix.
    # Provided at runtime by the IPCServer host class via multiple
    # inheritance. Declared here once so each handler mixin doesn't
    # repeat the 3-line block. See the module docstring for the
    # rationale (loose ``Any`` typing instead of a coupled Protocol).
    service: Any
    app: Any
    _send: Any


class HandlerBase(HandlerMixinBase):
    """Base for IPC handler mixins that need the CR-20 error envelope.

    Inherits the ``service`` / ``app`` / ``_send`` annotations from
    :class:`HandlerMixinBase` (R4-F3) and adds the
    :meth:`_respond_with_error` helper that emits the generic WS-path
    error envelope (CR-20). Handler mixins that catch their own
    ``Exception`` inherit from THIS class so they can call
    ``self._respond_with_error(resp, exc, cmd_name)`` instead of
    constructing the leaky ``{"message": str(e)}`` envelope inline.

    Handler mixins that DON'T need ``_respond_with_error`` (e.g.
    pure-validation handlers) inherit directly from
    :class:`HandlerMixinBase` to keep their MRO minimal.

    Migration status (CR-20, complete):

    The original review (CR-20) found ~50 copies of the leaky pattern
    across all 14 handler mixins. As of PVT-G5-021 (FA16, 2026-07-19),
    ALL 14 handler mixins (``config``, ``dictation``, ``history``,
    ``level_monitor``, ``microphone``, ``microphone_test``, ``model``,
    ``onboarding``, ``privacy``, ``repaste``, ``status``, ``system``,
    ``templates``, ``vocabulary``, ``vocabulary_automation``) inherit
    from :class:`HandlerBase` and route their catch-all
    ``except Exception`` blocks through ``_respond_with_error`` — no
    ``str(e)`` is ever sent to the renderer. The three-way
    error-envelope drift documented in PVT-G5-070 is eliminated:
    every handler catch-all now emits the same
    ``{"code": "server.internal_error", "message": "internal error"}``
    envelope as the dispatcher's outer ``except Exception`` (G4-M-22
    namespaced form — the dispatcher itself may still emit the legacy
    ``internal_error`` alias on some paths; the renderer must accept
    both forms, see ``voice_typer/server/ipc/validation.py``).
    Per-command VALIDATION errors (``missing_field``,
    ``invalid_payload``, ``payload_too_large``, etc.) remain the
    handler's responsibility and continue to route through
    :func:`_error_response` with explicit ``code`` values (PVT-G5-071).
    """

    def _respond_with_error(self, resp: dict, exc: BaseException, cmd_name: str) -> dict:
        """Replace ``resp`` in place with the generic WS-path error envelope.

        Parameters
        ----------
        resp :
            The response dict the handler was building. Mutated in
            place: ``type`` is set to ``"error"`` and ``data`` is set
            to the generic envelope ``{"code": "server.internal_error",
            "message": "internal error"}`` (G4-M-22 namespaced form).
        exc :
            The exception that triggered the error path. Logged at
            ERROR with ``exc_info=True`` so the full traceback lands
            in ``voice-typer.log`` for server-side diagnosis. The
            exception's ``str()`` is NEVER sent to the renderer —
            that's the whole point of CR-20.
        cmd_name :
            The IPC command name (e.g. ``"download_model"``) — used
            only for the log message so operators can correlate the
            ERROR line in the log with the failing IPC request.

        Returns
        -------
        dict
            The same ``resp`` dict, mutated in place. Returning it
            lets callers write ``return self._respond_with_error(...)``
            to match the existing ``return resp`` shape of every
            handler.

        Notes
        -----
        This method matches the dispatcher's outer ``except Exception``
        envelope at ``voice_typer/server/ipc_server.py`` verbatim —
        same ``type``, same ``data.code``, same ``data.message`` — so
        the renderer cannot tell whether the exception was caught
        inside the handler or propagated to the dispatcher. This is
        intentional: it removes the information channel that the old
        ``str(e)`` leak created.

        Per-command validation errors (``missing_field``,
        ``invalid_payload``, ``payload_too_large``, etc.) are NOT
        routed through this method — they are explicit, documented
        error codes the renderer switches on, and they carry
        field-level context (``"field": "id"``) the generic envelope
        cannot represent.
        """
        log.error("[IPC] %s failed: %s", cmd_name, exc, exc_info=True)
        resp["type"] = "error"
        # G4-M-22 / EC-FIX-4: use the namespaced form
        # ``server.internal_error`` rather than the legacy bare
        # ``internal_error``. The renderer's ``usePython.ts`` switch
        # accepts both forms (the legacy alias is documented in
        # ``voice_typer/server/ipc/validation.py``); new emitters MUST
        # use the namespaced form.
        resp["data"] = {
            "code": "server.internal_error",
            "message": "internal error",
        }
        return resp


__all__ = ["HandlerBase", "HandlerMixinBase", "_scrub_traceback", "log"]
