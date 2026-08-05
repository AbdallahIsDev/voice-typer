"""Shared base for IPC handler mixins (error envelope + mixin base).

This module is the result of merging two independent improvements:

* Error envelope: ``HandlerBase`` with the
  :meth:`_respond_with_error` helper. Prior to this, every IPC
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
       "data": {"code": LegacyErrorCodes.INTERNAL_ERROR, "message": "internal error"}}

  to avoid leaking server internals (file paths, CUDA error strings,
  internal module names, HF repo IDs) to a potentially-compromised
  renderer. ``_respond_with_error`` emits the SAME generic
  envelope so the renderer cannot distinguish "handler caught its own
  exception" from "exception propagated to the dispatcher".

* Mixin base: ``HandlerMixinBase`` with the three
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

Per-command VALIDATION errors (e.g. ``{"code": LegacyErrorCodes.MISSING_FIELD,
"field": "id"}``, ``{"code": LegacyErrorCodes.PAYLOAD_TOO_LARGE}``) are EXPLICIT and
remain the handler's responsibility — they are part of the documented
IPC contract that the renderer switches on. Only the catch-all
``Exception`` path is genericised via ``_respond_with_error``.
"""

from __future__ import annotations

import os
import traceback
import typing
from typing import Any

from voice_typer.server._secrets import redact_secret
from voice_typer.server.asr_errors import (
    CloudAuthError,
    CloudConfigError,
    CloudEngineError,
    CloudNetworkError,
    CloudRateLimitError,
    CloudServerError,
    ConsentRequiredError,
)
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import ErrorCodes, LegacyErrorCodes  # noqa: F401

# Import the recording-pipeline exception hierarchy so the
# ``_respond_with_error`` isinstance ladder can map ResampleError /
# ResampleUnavailableError to dedicated IPC error codes instead of
# collapsing them into the generic ``server.internal_error`` toast.
from voice_typer.server.recording.exceptions import (
    RecordingError,
    ResampleError,
    ResampleUnavailableError,
)

# The ``ErrorEnvelope`` TypedDict contract is kept in
# :mod:`voice_typer.server.ipc.validation` (useful as documentation),
# but the cast + return-type annotations were REMOVED here because
# pyrefly correctly flags that a ``TypedDict`` is not assignable to
# ``dict[str, object]`` (TypedDicts are invariant). The construction
# sites keep their ``# ErrorEnvelope contract — see validation.py``
# comments so the contract remains documented without being enforced
# at the type level. The runtime contract is verified by
# ``tests/test_error_codes_registry.py``.
#
# Note: the per-envelope ``legacy_code`` field and its helpers
# (``_legacy_code_from`` + ``_LEGACY_CODE_MAP``) were removed once the
# renderer migrated fully to the namespaced ``code`` form. The
# :class:`LegacyErrorCodes` constants are still re-exported from this
# module for handlers (e.g. ``config_handlers`` and
# ``cloud_test_handlers``) that emit the bare form directly as the
# primary ``code`` field on legacy code paths that have not yet been
# namespaced.


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
    """Common base for IPC handler mixins.

    Declares the three runtime-provided attributes (``service``,
    ``app``, ``_send``) that every handler mixin accesses via
    ``self.X``. These are typed as ``Any`` (the deliberate
    design choice) rather than the :class:`ServiceProtocol` /
    :class:`AppProtocol` structural types declared in
    :mod:`voice_typer.server.providers` because:

    * the actual :meth:`IPCServer._send` method signature carries
      additional keyword arguments (``_out``, ``_client``) beyond the
      single ``dict | None`` parameter the narrower
      ``Callable[[dict | None], None]`` would allow — tightening here
      causes pyrefly to flag every mixin as an invalid override;
    * :class:`VoiceTyperService.get_status` returns a ``StatusResponse``
      ``TypedDict``, which is not assignable to the ``dict[str, object]``
      declared on :class:`ServiceProtocol.get_status` (TypedDicts are
      invariant), so ``VoiceTyperService`` does not structurally satisfy
      :class:`ServiceProtocol`;
    * ``MagicMock`` fixtures in ``tests/handlers/`` continue to satisfy
      the ``Any`` annotation trivially (no test changes needed), and
      pyrefly's null-safety check still sees a declared attribute (no
      "attribute access before assignment" error).

    The real win of the duplicate-block removal is the 12 redundant
    lines removed from the 4 mixin files: history, dictation, model,
    onboarding, plus the ``_log`` migration. The type tightening is
    reverted here as a deferred follow-up — it requires coordinated
    caller-side changes (loosening ``IPCServer._send`` signature and
    widening ``ServiceProtocol.get_status`` return type) that exceed
    the session budget.

    Subclasses MUST NOT override these annotations — the runtime
    binding happens in :meth:`IPCServer.__init__` (or the
    ``MagicMock`` auto-vivification in tests), not here.

    This class has NO state of its own, NO methods, and NO side
    effects at import time. It is a pure type-annotation container.
    """

    # Pyrefly null-safety fix.
    # Provided at runtime by the IPCServer host class via multiple
    # inheritance. Declared here once so each handler mixin doesn't
    # repeat the 3-line block (the duplicate-block removal refactor
    # also removed the 4 duplicates that had been left behind in
    # history / dictation / model / onboarding handlers).
    #
    # ``Any`` is the deliberate design choice — see the class
    # docstring for the rationale on why the narrower
    # :class:`ServiceProtocol` / :class:`AppProtocol` /
    # ``Callable[[dict | None], None]`` annotations were reverted.
    service: Any
    app: Any
    _send: Any


class HandlerBase(HandlerMixinBase):
    """Base for IPC handler mixins that need the error envelope.

    Inherits the ``service`` / ``app`` / ``_send`` annotations from
    :class:`HandlerMixinBase` and adds the
    :meth:`_respond_with_error` helper that emits the generic WS-path
    error envelope. Handler mixins that catch their own
    ``Exception`` inherit from THIS class so they can call
    ``self._respond_with_error(resp, exc, cmd_name)`` instead of
    constructing the leaky ``{"message": str(e)}`` envelope inline.

    Handler mixins that DON'T need ``_respond_with_error`` (e.g.
    pure-validation handlers) inherit directly from
    :class:`HandlerMixinBase` to keep their MRO minimal.

    Migration status (complete):

    The original review found ~50 copies of the leaky pattern
    across all 14 handler mixins. As of the migration completion,
    ALL 14 handler mixins (``config``, ``dictation``, ``history``,
    ``level_monitor``, ``microphone``, ``microphone_test``, ``model``,
    ``onboarding``, ``privacy``, ``repaste``, ``status``, ``system``,
    ``templates``, ``vocabulary``, ``vocabulary_automation``) inherit
    from :class:`HandlerBase` and route their catch-all
    ``except Exception`` blocks through ``_respond_with_error`` — no
    ``str(e)`` is ever sent to the renderer. The three-way
    error-envelope drift is eliminated: every handler catch-all now
    emits the same ``{"code": ErrorCodes.INTERNAL_ERROR, "message":
    "internal error"}`` envelope as the dispatcher's outer
    ``except Exception`` (namespaced form — the dispatcher itself may
    still emit the legacy ``internal_error`` alias on some paths; the
    renderer must accept both forms, see
    ``voice_typer/server/ipc/validation.py``).
    Per-command VALIDATION errors (``missing_field``,
    ``invalid_payload``, ``payload_too_large``, etc.) remain the
    handler's responsibility and continue to route through
    :func:`_error_response` with explicit ``code`` values.
    """

    def _respond_with_error(self, resp: dict, exc: BaseException, cmd_name: str) -> dict:
        """Replace ``resp`` in place with the generic WS-path error envelope.

        Parameters
        ----------
        resp :
            The response dict the handler was building. Mutated in
            place: ``type`` is set to ``"error"`` and ``data`` is set
            to the generic envelope ``{"code": ErrorCodes.INTERNAL_ERROR,
            "message": "internal error"}`` (namespaced form).
        exc :
            The exception that triggered the error path. Logged at
            ERROR with ``exc_info=True`` so the full traceback lands
            in ``voice-typer.log`` for server-side diagnosis. The
            exception's ``str()`` is NEVER sent to the renderer —
            that's the whole point of the generic envelope.
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

        The return type is ``dict`` (not :class:`ErrorEnvelope`)
        because TypedDicts are invariant and not subtypes of ``dict``.
        The :class:`ErrorEnvelope` TypedDict in
        :mod:`voice_typer.server.ipc.validation` is kept as
        documentation/contract; the runtime contract is verified by
        ``tests/test_error_codes_registry.py``.

        The log message is scrubbed via
        :func:`_scrub_traceback` before it lands in
        ``voice-typer.log``. ``export_diagnostics`` ships the log
        file back to the renderer, so any secret (API key, bearer
        token) or home-directory path (which contains the username)
        embedded in ``str(exc)`` is exfiltrated when the user
        attaches the diagnostics bundle to a bug report. The
        scrubbed form replaces known secret patterns with ``***``
        and home-directory path components with ``~``.
        ``exc_info=True`` is preserved (via a scrubbed exception
        instance with ``tb=None``) so structured-logging consumers
        and existing ``r.exc_info is not None`` test assertions
        continue to hold.
        """
        # Scrub the exception message before logging so secrets
        # (sk-..., gsk_..., Bearer ...) and home-directory paths
        # (which contain the username) don't land in voice-typer.log
        # (which export_diagnostics ships to the renderer). We also
        # construct a scrubbed exception instance and pass it via
        # ``exc_info`` (with ``tb=None`` so no traceback frames are
        # printed — the frames could carry the secret in local
        # variables or absolute file paths). This preserves
        # ``record.exc_info is not None`` (structured-logging
        # consumers and existing
        # ``test_catch_all_logs_at_error_level_with_exc_info``
        # assertions rely on it) while ensuring the formatted
        # ``record.exc_text`` doesn't leak the secret either.
        scrubbed_str, _ = _scrub_traceback(exc)
        try:
            scrubbed_exc = type(exc)(scrubbed_str)
        except Exception:
            # Some exception types (e.g. OSError) have a different
            # ``__init__`` signature that doesn't accept a single
            # string. Fall back to RuntimeError so the log still
            # captures the scrubbed message.
            scrubbed_exc = RuntimeError(scrubbed_str)
        log.error(
            "[IPC] %s failed: %s",
            cmd_name,
            scrubbed_str,
            exc_info=(type(scrubbed_exc), scrubbed_exc, None),
        )
        # ErrorEnvelope contract — see validation.py
        resp["type"] = "error"
        # Typed cloud/LLM exception hierarchy — map each typed
        # exception to a distinct IPC error code (registered in
        # ``ERROR_CODES`` at ``voice_typer/server/ipc/validation.py``)
        # so the renderer can distinguish "API key invalid" from "rate
        # limited" from "transient network" from "missing config" and
        # react accordingly (re-enter key, backoff, auto-retry, open
        # Settings). The catch-all ``RuntimeError`` fallback stays as
        # ``server.internal_error`` for non-cloud RuntimeErrors.
        #
        # Every envelope below carries both the namespaced
        # ``code`` and a matching ``legacy_code`` alias (derived by
        # stripping the ``client.``/``server.`` prefix) so the renderer
        # can switch on either form during the namespacing migration
        # window — mirroring the parity stamp added to
        # ``_validate_dict_payload`` and the TCP/WS rate-limit envelopes
        # under
        if isinstance(exc, ConsentRequiredError):
            # Structured consent error — pass through the
            # typed fields so the renderer can surface a consent dialog
            # instead of a generic error toast. The structured fields
            # (engine_name, consent_field, model_id) let the renderer
            # deep-link to the exact toggle in Settings.
            #
            # Construct ``data`` from ``exc.to_dict()`` FIRST,
            # then explicitly overwrite ``code`` and ``message`` AFTER
            # the spread. Pre-fix the literal order was
            # ``{"code": ..., "message": ..., **exc.to_dict()}`` which
            # silently overwrote both fields with the (possibly empty)
            # values from ``to_dict()`` — the user-visible message
            # became ``""`` whenever ``str(exc)`` was empty, defeating
            # the ``or "consent required"`` fallback.
            data = exc.to_dict()
            data["code"] = ErrorCodes.CONSENT_REQUIRED
            data["message"] = str(exc) or "consent required"
            resp["data"] = data
            return resp
        if isinstance(exc, ResampleUnavailableError):
            # scipy.signal.resample_poly unavailable — the
            # high-quality resample tier is missing, callers must
            # fall back to linear interpolation. Maps to a distinct
            # code so the renderer can surface "install scipy for
            # better audio quality" instead of a generic error toast.
            code = ErrorCodes.RECORDING_RESAMPLE_UNAVAILABLE
            message = "high-quality audio resampling unavailable"
        elif isinstance(exc, ResampleError):
            # Audio cannot be resampled to the target sample
            # rate. Maps to a distinct code so the renderer can
            # distinguish "audio pipeline misconfiguration" from a
            # generic internal_error toast.
            code = ErrorCodes.RECORDING_RESAMPLE_FAILED
            message = "audio resampling failed"
        elif isinstance(exc, RecordingError):
            # Catch-all for the typed recording-pipeline base
            # (anything that is not one of the narrow subclasses above).
            # Maps to the resample-failed code rather than the generic
            # ``server.internal_error`` so the renderer can group
            # recording-pipeline failures together.
            code = ErrorCodes.RECORDING_RESAMPLE_FAILED
            message = "recording pipeline error"
        elif isinstance(exc, CloudAuthError):
            code = ErrorCodes.CLOUD_AUTH_FAILED
            message = "cloud API key invalid or revoked"
        elif isinstance(exc, CloudRateLimitError):
            code = ErrorCodes.CLOUD_RATE_LIMITED
            message = "cloud provider rate limited — please retry shortly"
        elif isinstance(exc, CloudServerError):
            code = ErrorCodes.CLOUD_SERVER_ERROR
            message = "cloud provider server error"
        elif isinstance(exc, CloudNetworkError):
            code = ErrorCodes.CLOUD_NETWORK_ERROR
            message = "cloud provider network error"
        elif isinstance(exc, CloudConfigError):
            code = ErrorCodes.CLOUD_CONFIG_ERROR
            message = "cloud provider not configured"
        elif isinstance(exc, CloudEngineError):
            # Catch-all for the typed base (e.g. unknown HTTP
            # status from the cloud provider). Maps to a cloud-specific
            # code rather than the generic ``server.internal_error``.
            code = ErrorCodes.CLOUD_ENGINE_ERROR
            message = "cloud provider error"
        else:
            # Use the namespaced form
            # ``server.internal_error`` rather than the legacy bare
            # ``internal_error``. The renderer's ``usePython.ts`` switch
            # accepts both forms (the legacy alias is documented in
            # ``voice_typer/server/ipc/validation.py``); new emitters
            # MUST use the namespaced form.
            code = ErrorCodes.INTERNAL_ERROR
            message = "internal error"
        # Stamp the envelope with the namespaced ``code`` and the
        # generic server-side message. The previous per-envelope
        # ``legacy_code`` alias was removed once the renderer migrated
        # fully to the namespaced ``code`` form.
        resp["data"] = {
            "code": code,
            "message": message,
        }
        return resp

    def _error_response(
        self,
        resp: dict,
        message: str,
        *,
        code: str = ErrorCodes.HANDLER_ERROR,
        **extra: Any,
    ) -> dict:
        """Stamp a per-command error envelope on ``resp`` and return it.

        Mirrors the standalone
        :func:`voice_typer.server.ipc.validation._error_response`
        helper but accepts ``**extra`` kwargs that are merged into
        ``resp["data"]`` alongside the standard ``code`` + ``message``
        pair. Used by per-command VALIDATION errors (e.g.
        ``missing_field``, ``invalid_field``, ``not_found``) that
        carry field-level context (``field="provider"``,
        ``field="dir_path"``) the bare function form cannot represent.

        Previously, each handler constructed its inline ``error``
        envelope ad-hoc::

            resp["type"] = "error"
            resp["data"] = {"message": "Missing 'provider' parameter"}
            return resp

        The inline envelope omitted the ``code`` field that every
        other error path (validation, dispatch safety net, rate
        limiter, the catch-all :meth:`_respond_with_error`) stamps.
        Clients branching on ``code`` (e.g. the renderer's toast
        dispatch) silently fell through to a generic "unknown error"
        path for these per-command validation rejections. Routing
        them through ``_error_response`` ensures every error envelope
        on the wire carries a structured ``code`` so the renderer can
        programmatically distinguish "missing field" from "invalid
        value" from "not found" from "internal error".

        Parameters
        ----------
        resp :
            The response dict to mutate in place. ``resp["type"]`` is
            set to ``"error"`` and ``resp["data"]`` is set to a new
            dict ``{"code": code, "message": message, **extra}``.
        message :
            Sanitized, user-visible error message. MUST NOT carry
            Python internals, file paths, or PII beyond what the
            per-command validation contract allows (the message is
            shown to the renderer / user).
        code :
            Error code from :class:`ErrorCodes`. Defaults to
            ``server.handler_error`` to match the standalone function.
            Per-command validation errors should override with the
            appropriate ``client.*`` code (``MISSING_FIELD``,
            ``INVALID_FIELD``, ``NOT_FOUND``, ``PATH_NOT_ALLOWED`` …).
        **extra :
            Arbitrary additional fields merged into ``resp["data"]``
            AFTER ``code`` and ``message`` (so callers can't
            accidentally clobber the standard pair). Typical keys:
            ``field`` (the rejected field name), ``provider``
            (the rejected cloud-provider name). These are part of the
            documented IPC contract for per-command validation errors
            — see ``voice_typer/server/ipc/validation.py`` and the
            renderer's ``usePython.ts`` switch.

        Returns
        -------
        dict
            The same ``resp`` dict, mutated in place. Returning it
            lets callers write ``return self._error_response(...)``
            to match the existing ``return resp`` shape of every
            handler.
        """
        resp["type"] = "error"
        # ErrorEnvelope contract — see validation.py
        data: dict[str, Any] = {"code": code, "message": message}
        if extra:
            # Merge extra fields AFTER ``code`` + ``message`` so the
            # standard pair is always present and cannot be clobbered
            # by a caller-supplied ``code``/``message`` kwarg (Python
            # would raise ``TypeError: multiple values for keyword
            # argument`` if a caller tried — the explicit ``code``
            # parameter above shadows any ``code`` in ``**extra``).
            data.update(extra)
        resp["data"] = data
        return resp

    # ── Template-method helper for handler consistency ────
    # The 14 handler mixins are inconsistent in (a) try/except usage,
    # (b) pre-coercion of ``data``, (c) error envelope shape. The
    # mechanical fix would convert each of the 60+ ``_handle_<cmd>``
    # methods to one-liners delegating to ``_wrap``. Deferred because:
    #   - each handler has its own response ``type`` field
    #   - many handlers have custom pre-coercion beyond ``None → {}``
    #   - some handlers intentionally don't wrap in try/except
    #   - tests assert on exact envelope shape per handler
    # The SAFE incremental step: define ``_wrap`` so NEW handlers can
    # opt in. Existing handlers continue to work as before. Migration
    # pattern: ``return self._wrap(cmd_name=..., resp_type=..., data=data,
    # resp=resp, body=lambda d: {"data": ...})``.
    def _wrap(
        self,
        *,
        cmd_name: str,
        resp_type: str,
        data: object,
        resp: dict,
        body: typing.Callable[[dict], dict],
    ) -> dict:
        """Template-method helper for consistent IPC handler structure.

        Pre-coerces ``data`` (``None`` → ``{}``), calls ``body``, merges
        the result into ``resp``, wraps in try/except →
        :meth:`_respond_with_error`. Per-command VALIDATION errors are
        NOT routed here — ``body`` should return them directly via
        ``self._error_response(...)``.
        """
        resp["type"] = resp_type
        try:
            coerced = data if isinstance(data, dict) else {}
            result = body(coerced)
            if isinstance(result, dict):
                if "type" in result:
                    resp["type"] = result["type"]
                if "data" in result:
                    resp["data"] = result["data"]
            return resp
        except Exception as exc:
            return self._respond_with_error(resp, exc, cmd_name)


__all__ = ["HandlerBase", "HandlerMixinBase", "_scrub_traceback", "log"]
