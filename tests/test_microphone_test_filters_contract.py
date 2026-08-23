"""Contract tests: ``microphone_test_start`` ``filters`` field is a DICT.

Pins the ADR 0007 filter-config wire contract between the renderer and
the IPC layer:

- The renderer's ``buildTestFilters`` (``pages/microphone/lib/buildTestFilters.ts``)
  sends ``filters`` as a DICT of ``noise_filter_*`` keys built from the
  user's config — a full dict for any non-``"off"`` preset, and
  ``{noise_filter_enabled: false}`` for the off-preset / no-config case.
- ``_handle_microphone_test_start`` must accept that dict verbatim and
  forward it unchanged to ``service.microphone_test_start``.
- Downstream consumers all require a MAPPING
  (``level_monitor/test_recording.py``: ``dict(filters)`` at start,
  ``filters.get("noise_filter_enabled", ...)`` + ``.get()`` reads and
  ``types.SimpleNamespace(**filters)`` at stop; ``update_test_filters``
  merges via ``.update()``), so non-dict values are rejected at the
  validation boundary with ``client.invalid_field`` instead of crashing
  inside the recording pipeline at stop time.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

# Realistic payload exactly as the renderer's ``buildTestFilters``
# produces it for a non-"off" preset (subset of keys + the master flag;
# shape, not completeness, is what this contract pins).
FULL_FILTER_DICT: dict[str, object] = {
    "noise_filter_enabled": True,
    "noise_filter_highpass": True,
    "noise_filter_highpass_cutoff_hz": 80,
    "noise_suppression_method": "rnnoise",
    "noise_filter_gate": True,
    "noise_filter_gate_open_threshold_db": -26,
    "noise_filter_eq": True,
    "noise_filter_compressor_ratio": 3,
    "noise_filter_limiter_ceiling_db": -6,
}

OFF_PRESET_FILTER_DICT: dict[str, object] = {"noise_filter_enabled": False}


@pytest.fixture()
def ipc_server_and_fakes():
    server, fake_app, fake_service = make_ipc_server_with_fakes()
    return server, fake_app, fake_service


class TestMicrophoneTestStartFiltersContract:
    """``filters`` on ``microphone_test_start`` — dict wire contract."""

    def test_full_dict_payload_forwarded_unchanged(self, ipc_server_and_fakes):
        """(a) Full non-"off" dict → success envelope + dict reaches the
        service fake UNCHANGED (same content, deep-equal)."""
        server, _, fake_service = ipc_server_and_fakes
        fake_service.microphone_test_start.return_value = {
            "success": True,
            "message": "Recording test...",
            "duration": 5.0,
            "sample_rate": 16000,
        }
        payload = copy.deepcopy(FULL_FILTER_DICT)
        resp = server._handle_microphone_test_start(
            {"mic_id": "usb_mic_1", "duration": 5.0, "filters": payload},
            {},
        )
        assert resp["type"] == "microphone_test_result"
        assert resp["data"]["success"] is True
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id="usb_mic_1",
            duration=5.0,
            filters=FULL_FILTER_DICT,
        )
        # The handler must not mutate the caller's payload dict.
        assert payload == FULL_FILTER_DICT

    def test_off_preset_minimal_dict_accepted(self, ipc_server_and_fakes):
        """(b) Off-preset ``{noise_filter_enabled: false}`` → accepted."""
        server, _, fake_service = ipc_server_and_fakes
        fake_service.microphone_test_start.return_value = {"success": True}
        resp = server._handle_microphone_test_start(
            {"filters": OFF_PRESET_FILTER_DICT},
            {},
        )
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=10.0,
            filters=OFF_PRESET_FILTER_DICT,
        )

    def test_none_filters_forwarded_as_none(self, ipc_server_and_fakes):
        """(c) Absent AND explicit-null ``filters`` → ``None`` reaches the
        service (schema default; ``none_to_default`` treats null as absent).

        Both mean "no filter overrides" downstream — the level monitor
        seeds an empty dict and the stop path skips the post-hoc filter.
        """
        server, _, fake_service = ipc_server_and_fakes
        fake_service.microphone_test_start.return_value = {"success": True}

        resp_absent = server._handle_microphone_test_start({}, {})
        assert resp_absent["type"] == "microphone_test_result"
        assert fake_service.microphone_test_start.call_args.kwargs["filters"] is None

        fake_service.microphone_test_start.reset_mock()
        resp_null = server._handle_microphone_test_start({"filters": None}, {})
        assert resp_null["type"] == "microphone_test_result"
        assert fake_service.microphone_test_start.call_args.kwargs["filters"] is None

    def test_legacy_list_rejected_at_boundary(self, ipc_server_and_fakes):
        """(d) Legacy list payloads are REJECTED — no list compat kept.

        Decision: the renderer never sent lists (``buildTestFilters``
        has always returned a dict), and every downstream consumer
        requires a mapping — accepting a list would defer the failure
        to ``stop_test_recording`` where ``filters.get(...)`` /
        ``SimpleNamespace(**filters)`` crash mid-recording-cycle.
        Rejecting at the validation boundary is strictly safer than a
        shim that forwards a value the pipeline cannot consume.
        """
        server, _, fake_service = ipc_server_and_fakes
        resp = server._handle_microphone_test_start(
            {"filters": ["noise_suppressor"]},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "filters"
        fake_service.microphone_test_start.assert_not_called()

    @pytest.mark.parametrize("garbage", [42, "not-a-dict", [1, 2], True])
    def test_garbage_filters_type_rejected(self, ipc_server_and_fakes, garbage):
        """(e) Non-dict garbage → ``client.invalid_field`` envelope naming
        ``filters`` + the expected ``dict|NoneType``, service untouched."""
        server, _, fake_service = ipc_server_and_fakes
        resp = server._handle_microphone_test_start({"filters": garbage}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "filters"
        assert "dict|NoneType" in resp["data"]["message"]
        fake_service.microphone_test_start.assert_not_called()

    def test_dispatch_round_trip_full_dict(self, ipc_server_and_fakes):
        """Full-dispatch wiring: ``{"type": "microphone_test_start"}``
        routes through the registry to the handler with the dict intact."""
        server, _, fake_service = ipc_server_and_fakes
        fake_service.microphone_test_start.return_value = {"success": True}
        resp = server._dispatch(
            {
                "id": "req-filters-1",
                "type": "microphone_test_start",
                "data": {
                    "mic_id": None,
                    "duration": 3,
                    "filters": dict(FULL_FILTER_DICT),
                },
            }
        )
        assert resp["type"] == "microphone_test_result"
        assert resp.get("id") == "req-filters-1"
        sent = fake_service.microphone_test_start.call_args.kwargs["filters"]
        assert sent == FULL_FILTER_DICT


class TestBuildTestFiltersKeyParity:
    """Renderer ``buildTestFilters`` keys ↔ backend filter-chain fields.

    A key rename on either side of the IPC boundary is a SILENT no-op:
    the dict still validates (shape contract above), the service still
    receives it, but ``build_chain`` reads its attributes off the
    unpacked namespace and falls back to defaults — the user's filter
    settings stop affecting the test recording with no error anywhere.
    These tests pin both directions of the key mapping, mirroring the
    cross-language parity-test pattern used for the command allowlists.
    """

    @staticmethod
    def _renderer_emitted_keys() -> set[str]:
        source = (
            Path(__file__).resolve().parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "pages"
            / "microphone"
            / "lib"
            / "buildTestFilters.ts"
        ).read_text(encoding="utf-8")
        return set(re.findall(r"\b(noise_filter_[a-z0-9_]+|noise_suppression_[a-z0-9_]+)\b", source))

    def test_renderer_emits_every_field_the_filter_chain_reads(self):
        """Every field ``build_chain`` reads via direct attribute access
        must be emitted by ``buildTestFilters`` — otherwise that filter's
        user config silently stops applying to test recordings.

        ``audio_preset`` is excluded (preset routing, not a chain input —
        and it is deliberately NOT forwarded per-test). The master gate
        ``noise_filter_enabled`` is required: it is read by
        ``level_monitor.test_recording`` + ``monitoring`` to decide
        whether the chain runs at all. ``noise_filter_gate_adaptive``
        is exempt: ``build_chain`` reads it via ``getattr(..., False)``
        (safe default) and the renderer's ``VoiceTyperConfig`` type does
        not expose it.
        """
        from voice_typer.server.audio_processor import _CONFIG_SIGNATURE_FIELDS

        chain_read = {name for name in _CONFIG_SIGNATURE_FIELDS if name != "audio_preset"} | {"noise_filter_enabled"}
        missing = chain_read - self._renderer_emitted_keys()
        assert not missing, (
            "buildTestFilters no longer emits field(s) the filter chain "
            f"reads directly: {sorted(missing)}. The affected filters would "
            "silently fall back to defaults in microphone tests."
        )

    def test_renderer_emits_no_unknown_backend_fields(self):
        """Every emitted key must exist on the backend ``Config`` — a
        key the backend does not declare is dropped by every consumer
        (or crashes ``SimpleNamespace``-based construction paths) while
        looking like a valid setting."""
        from voice_typer.server.config import Config

        backend_fields = {
            name for name in Config.__dataclass_fields__ if name.startswith(("noise_filter_", "noise_suppression_"))
        }
        unknown = self._renderer_emitted_keys() - backend_fields
        assert not unknown, (
            "buildTestFilters emits key(s) unknown to the backend Config: "
            f"{sorted(unknown)}. They are silently ignored downstream."
        )
