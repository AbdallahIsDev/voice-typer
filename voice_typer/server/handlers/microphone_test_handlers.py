"""Microphone-test IPC handler mixin: 4 microphone_test_* commands.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

(2026-07-30): ``_handle_microphone_test_status`` was REMOVED —
the renderer polls ``microphone_test_get_level`` at 60 Hz during a
test; the separate status query was unused. The service-layer method
``service.microphone_test_status`` still exists for internal callers;
only the IPC dispatch route was deleted.
"""

from voice_typer.server.asr_errors import ConsentRequiredError
from voice_typer.server.handlers._base import HandlerBase, log
from voice_typer.server.ipc.validation import _validate_dict_payload


class MicrophoneTestHandlersMixin(HandlerBase):
    """Mixin: microphone-test IPC handlers (start / stop / cancel / get_level).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak).

    ``_handle_microphone_test_start`` enforces
        ``voice_biometric_consent`` BEFORE capturing any test audio. The mic
        test records up to 60s of audio and returns base64-encoded WAV over
        IPC — the same privacy contract as dictation
        (``recording_controller.py:248-263``). Without this gate, a
        renderer-side bug or compromised renderer could trigger a test
        recording and exfiltrate up to 60s of biometric voice data without
        the user's explicit consent. The handler raises
        :class:`ConsentRequiredError` which the existing
        :meth:`HandlerBase._respond_with_error` maps to the structured
        ``client.consent_required`` envelope so the renderer can surface a
        consent dialog instead of a generic error toast.
    """

    def _handle_microphone_test_start(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``microphone_test_start`` IPC command.

        (session-DE): ``duration`` is now in the schema with
                ``clamp_range: (1.0, 60.0)``. Previously the inline
                ``float(d.get("duration") or 10.0)`` coercion accepted any
                numeric value (including ``1e300`` and ``-5.0``) without
                bounds — a misbehaving caller could request a 1-hour
                microphone test that would block the recorder, or a
                negative-duration test that would crash the service layer.
                The clamp enforces a sane [1.0, 60.0] window.

                The ``clamp_range`` rule in the schema only applies to
                int/float values (the helper skips strings). For string
                values (e.g. ``"7.5"`` from a form input), the helper
                passes the string through unchanged; we then do
                ``float(validated["duration"])`` and re-clamp manually to
                enforce the same [1.0, 60.0] window on the coerced value.

                Note: the previous ``float(d.get("duration") or 10.0)``
                treated ``0`` as falsy and used the default ``10.0``. The
                new clamp treats ``0`` as a real value and clamps it to
                the lower bound ``1.0``. This is the documented behavior
        change in  — ``0`` is no longer "use default", it's
                a clamped value.
        """
        # TODO: not migrated to ``_wrap`` — has side effects
        # (consent-check raises ``ConsentRequiredError`` + ``log.exception``
        # call + ``self.service.microphone_test_start`` mutates audio state).
        try:
            # enforce voice_biometric_consent BEFORE
            # capturing any test audio. The mic test returns up to 60s
            # of base64-encoded WAV over IPC — same privacy contract
            # as dictation (recording_controller.py:248-263). We raise
            # ConsentRequiredError rather than building the envelope
            # inline so the existing _respond_with_error path maps it
            # to the structured ``client.consent_required`` envelope
            # (carrying engine_name/consent_field/model_id fields the
            # renderer uses to deep-link to the Settings toggle).
            #
            # Fail-open policy: if the config read itself raises
            # (e.g. config file locked / corrupted), we log and
            # continue rather than lock the user out of the mic
            # test dialog. Matches recording_controller.py:264-268.
            try:
                if not getattr(self.app.config, "voice_biometric_consent", False):
                    raise ConsentRequiredError(
                        "voice biometric consent required to start microphone test",
                        engine_name="microphone_test",
                        consent_field="voice_biometric_consent",
                    )
            except ConsentRequiredError:
                raise
            except Exception:
                log.exception("[IPC] microphone_test_start: failed to read voice_biometric_consent — failing open")

            # validate ``mic_id`` and ``filters`` types via the
            # shared ``_validate_dict_payload`` helper. Non-dict
            # ``data`` is pre-coerced to ``{}`` so the
            # ``test_non_dict_data_uses_defaults`` contract (None →
            # defaults) still holds; ``_validate_dict_payload`` would
            # otherwise reject non-dict with ``invalid_payload``.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "mic_id": {
                        "type": (str, type(None)),
                        "required": False,
                        "default": None,
                    },
                    # ADR 0007 filter-config contract: ``filters`` is
                    # a DICT of noise_filter_* keys (the renderer's
                    # ``buildTestFilters`` builds it from config), not
                    # a list. Every downstream consumer treats it as a
                    # mapping: ``level_monitor.test_recording`` stores
                    # it via ``dict(filters)``, merges via
                    # ``update_test_filters(...).update()``, reads it
                    # with ``filters.get("noise_filter_enabled", ...)``,
                    # and unpacks it as ``types.SimpleNamespace(**filters)``
                    # before constructing ``AudioProcessor``. A list
                    # payload would crash those call sites at stop time,
                    # so reject non-dict values here at the validation
                    # boundary.
                    "filters": {
                        "type": (dict, type(None)),
                        "required": False,
                        "default": None,
                    },
                    # ``duration`` in schema with clamp_range.
                    # ``type: (int, float, str)`` preserves the
                    # documented string → float coercion for form-input
                    # compatibility (``"7.5" → 7.5``). The
                    # ``clamp_range`` rule clamps int/float values to
                    # [1.0, 60.0]; strings are clamped after the
                    # ``float()`` coercion below.
                    "duration": {
                        "type": (int, float, str),
                        "required": False,
                        "default": 10.0,
                        "clamp_range": (1.0, 60.0),
                    },
                },
            )
            if error:
                # the helper emits namespaced
                # ``client.invalid_field`` for the ``duration`` type
                # check and namespaced ``client.invalid_payload`` for
                # non-dict ``data``. Test assertions expect the
                # namespaced form, so the error passes through
                # unchanged.
                return error
            assert validated is not None  # narrowed by the error guard above
            mic_id = validated.get("mic_id")
            filters = validated.get("filters")
            # coerce to float (handles string values like
            # ``"7.5"``) and re-clamp. The schema's ``clamp_range``
            # already clamped int/float values, but strings bypass it
            # (the helper only clamps int/float). A string like
            # ``"1e300"`` would coerce to ``inf`` here; the re-clamp
            # brings it back to 60.0.
            duration = float(validated["duration"])
            duration = max(1.0, min(duration, 60.0))
            result = self.service.microphone_test_start(mic_id=mic_id, duration=duration, filters=filters)
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "microphone_test_start")
        return resp

    def _handle_microphone_test_stop(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``microphone_test_stop`` IPC command."""
        try:
            result = self.service.microphone_test_stop()
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "microphone_test_stop")
        return resp

    def _handle_microphone_test_cancel(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``microphone_test_cancel`` IPC command."""
        try:
            result = self.service.microphone_test_cancel()
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "microphone_test_cancel")
        return resp

    def _handle_microphone_test_get_level(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``microphone_test_get_level`` IPC command."""
        try:
            result = self.service.microphone_test_get_level()
            resp["type"] = "microphone_test_level"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "microphone_test_get_level")
        return resp
