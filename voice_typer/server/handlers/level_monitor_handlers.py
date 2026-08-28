"""Level-monitor IPC handler mixin: 2 level_monitor_* commands.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

(2026-07-30): ``_handle_level_monitor_status`` was REMOVED —
the renderer subscribes to the ``level_monitor_level`` push event
instead of polling a status endpoint. The service-layer method
``service.level_monitor_status`` still exists for internal callers;
only the IPC dispatch route was deleted.
"""

from voice_typer.server.asr_errors import ConsentRequiredError
from voice_typer.server.handlers._base import HandlerBase, log
from voice_typer.server.ipc.validation import _validate_dict_payload


class LevelMonitorHandlersMixin(HandlerBase):
    """Mixin: level-monitor IPC handlers (start / stop).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak).

    ``_handle_level_monitor_start`` enforces
        ``voice_biometric_consent`` BEFORE opening the continuous-monitor
        InputStream. The level monitor captures audio chunks at the device
        native rate (16k–48k samples/sec) and runs them through the filter
        chain + RMS/peak computation. Even though the IPC response carries
        only numerical dBFS values (not raw audio), the act of opening the
        InputStream is itself a biometric-data capture under GDPR Art. 9 —
        the audio is processed in memory and could be observed via a
        debugger or compromised process. Enforcing consent at the IPC entry
        point matches the dictation path (recording_controller.py:248-263)
        and the mic-test path (microphone_test_handlers.py).
    """

    def _handle_level_monitor_start(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``level_monitor_start`` IPC command.

        Migrated to :meth:`HandlerBase._wrap` — the helper handles the
        surrounding ``try/except`` → ``_respond_with_error`` catch-all
        and the non-dict ``data`` pre-coercion identically to the
        inline ``if not isinstance(data, dict): data = {}`` guard.
        """

        def body(d: dict) -> dict:
            # enforce voice_biometric_consent BEFORE
            # opening the InputStream. The monitor captures audio
            # continuously; even though only dBFS values are returned
            # over IPC, the audio is processed in memory and the
            # capture itself requires biometric consent under GDPR
            # Art. 9. Matches dictation (recording_controller.py:248)
            # and mic-test (microphone_test_handlers.py) gating.
            #
            # Fail-open policy: if the config read itself raises
            # (e.g. config file locked / corrupted), we log and
            # continue rather than lock the user out of the level
            # monitor. Matches recording_controller.py:264-268.
            try:
                if not getattr(self.app.config, "voice_biometric_consent", False):
                    raise ConsentRequiredError(
                        "voice biometric consent required to start level monitor",
                        engine_name="level_monitor",
                        consent_field="voice_biometric_consent",
                    )
            except ConsentRequiredError:
                raise
            except Exception:
                log.exception("[IPC] level_monitor_start: failed to read voice_biometric_consent — failing open")

            # validate ``mic_id`` type via the shared
            # ``_validate_dict_payload`` helper. Non-dict ``data`` is
            # pre-coerced to ``{}`` by ``_wrap`` so the
            # ``test_non_dict_data_defaults_mic_id_to_none`` contract
            # (None → mic_id=None) still holds; ``_validate_dict_payload``
            # would otherwise reject non-dict with ``invalid_payload``.
            validated, error = _validate_dict_payload(
                d,
                {
                    "mic_id": {
                        "type": (str, type(None)),
                        "required": False,
                        "default": None,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            mic_id = validated.get("mic_id")
            result = self.service.level_monitor_start(mic_id=mic_id)
            return {"type": "level_monitor_status", "data": result}

        return self._wrap(
            cmd_name="level_monitor_start",
            resp_type="level_monitor_status",
            data=data,
            resp=resp,
            body=body,
        )

    def _handle_level_monitor_stop(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``level_monitor_stop`` IPC command."""
        try:
            result = self.service.level_monitor_stop()
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "level_monitor_stop")
        return resp
