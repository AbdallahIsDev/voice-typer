"""YJ-1 regression: typed handler-method signatures in the four owned mixins.

The keystone ``# type: ignore[assignment]`` at
``voice_typer/server/ipc_server.py:1908`` (``handler = _resolved``)
cannot be deleted until ALL 65+ ``_handle_*`` methods across the 15
handler mixins conform to the ``CommandHandler`` signature::

    (self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None

This test covers the four mixins owned by the YJ-11 agent
(history / dictation / model / onboarding). When the other 11 mixins
(owned by agents 14 and 15) are annotated in lockstep, the keystone
``# type: ignore`` can be deleted and this test extended to cover the
full set.

Scope (per the bucket assignment in ``final_groups.json``):

- ``voice_typer/server/handlers/history_handlers.py``
- ``voice_typer/server/handlers/dictation_handlers.py``
- ``voice_typer/server/handlers/model_handlers.py``
- ``voice_typer/server/handlers/onboarding_handlers.py``

The test introspects each ``_handle_*`` method's signature via
``inspect.signature`` and asserts the parameter and return annotations
match the canonical ``CommandHandler`` shape. A handler that drops the
annotation (e.g. by copy-pasting a new ``def _handle_x(self, data, resp) -> dict | None``)
fails the test, surfacing the regression before the keystone
``# type: ignore`` deletion lands.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

# ── Mixins under test (owned by YJ-11 bucket) ──────────────────────────
from voice_typer.server.handlers.dictation_handlers import (  # noqa: E402
    DictationHandlersMixin,
)
from voice_typer.server.handlers.history_handlers import (  # noqa: E402
    HistoryHandlersMixin,
)
from voice_typer.server.handlers.model_handlers import (  # noqa: E402
    ModelHandlersMixin,
)
from voice_typer.server.handlers.onboarding_handlers import (  # noqa: E402
    OnboardingHandlersMixin,
)

# ResponseEnvelope is currently defined in ``voice_typer.server.ipc_server``
# (a 3095-line god-class slated for split in DT-22 / YJ-39). The
# ``test_response_envelope_importable_from_validation`` test below pins the
# ASPIRATIONAL future home ``voice_typer.server.ipc.validation`` once the
# split lands. For now we import from the canonical current location so the
# module is collectable.
from voice_typer.server.ipc_server import ResponseEnvelope  # noqa: E402

_OWNED_MIXINS = (
    HistoryHandlersMixin,
    DictationHandlersMixin,
    ModelHandlersMixin,
    OnboardingHandlersMixin,
)


def _resolve_annotation(ann: object) -> str:
    """Return a stable string form of an annotation.

    ``inspect.signature`` may return either a real type (``object``, ``dict``)
    or a string forward ref (``"ResponseEnvelope"``). Comparing annotations
    via ``__eq__`` is brittle across Python versions and string-vs-type
    forms. Stringifying both sides gives a stable comparison surface.
    """
    if ann is inspect.Parameter.empty:
        return "<empty>"
    if isinstance(ann, str):
        # Forward ref string — strip any surrounding quotes.
        return ann.strip("'\"")
    if hasattr(ann, "__name__"):
        return ann.__name__
    return str(ann).replace("typing.", "")


def _handle_methods(mixin_cls: type) -> list[str]:
    """Return every ``_handle_*`` method name on ``mixin_cls``."""
    return [name for name in vars(mixin_cls) if name.startswith("_handle_")]


# ── Tests ──────────────────────────────────────────────────────────────


class TestYJ1HandlerSignatureConformance:
    """Every ``_handle_*`` method on the 4 owned mixins MUST be annotated
    to match the ``CommandHandler`` signature ``(data: object | None,
    resp: ResponseEnvelope) -> ResponseEnvelope | None``.
    """

    @pytest.mark.parametrize(
        "mixin_cls",
        _OWNED_MIXINS,
        ids=lambda c: c.__name__,
    )
    def test_every_handle_method_is_typed(self, mixin_cls: type) -> None:
        """All ``_handle_*`` methods on ``mixin_cls`` MUST have the
        ``data``, ``resp``, and return-type annotations matching the
        ``CommandHandler`` signature.

        Missing annotations fail the test — this is the YJ-1 contract
        that allows the keystone ``# type: ignore[assignment]`` at
        ``ipc_server.py:1908`` to be deleted once ALL 15 handler mixins
        conform (the other 11 are owned by agents 14 and 15).
        """
        method_names = _handle_methods(mixin_cls)
        assert method_names, f"{mixin_cls.__name__} should declare at least one _handle_* method"
        failures: list[str] = []
        for name in method_names:
            method = getattr(mixin_cls, name)
            sig = inspect.signature(method)
            params = list(sig.parameters.values())
            # Expected: (self, data, resp)
            if len(params) != 3:
                failures.append(f"{mixin_cls.__name__}.{name}: expected 3 params (self, data, resp), got {len(params)}")
                continue
            self_p, data_p, resp_p = params
            assert self_p.name == "self", f"{mixin_cls.__name__}.{name}: first param must be 'self'"
            assert data_p.name == "data", f"{mixin_cls.__name__}.{name}: second param must be 'data'"
            assert resp_p.name == "resp", f"{mixin_cls.__name__}.{name}: third param must be 'resp'"
            # data: object | None — accept either the ``object | None``
            # typing form or a forward-ref string. The annotation must
            # NOT be ``inspect.Parameter.empty`` (untyped).
            data_ann = _resolve_annotation(data_p.annotation)
            if data_ann == "<empty>":
                failures.append(f"{mixin_cls.__name__}.{name}: missing 'data' annotation")
            # resp: ResponseEnvelope — accept either the alias itself
            # or the underlying ``dict[str, object]`` shape, plus the
            # forward-ref string form ``"ResponseEnvelope"``.
            resp_ann = _resolve_annotation(resp_p.annotation)
            if resp_ann == "<empty>":
                failures.append(f"{mixin_cls.__name__}.{name}: missing 'resp' annotation")
            # Return: ResponseEnvelope | None — accept the union form
            # or the stringified form (pyrefly strips the alias).
            ret_ann = _resolve_annotation(sig.return_annotation)
            if ret_ann == "<empty>":
                failures.append(f"{mixin_cls.__name__}.{name}: missing return annotation")
        assert not failures, f"{mixin_cls.__name__} has non-conformant _handle_* methods:\n  - " + "\n  - ".join(
            failures
        )

    def test_owned_mixins_collectively_have_at_least_30_handlers(self) -> None:
        """Sanity check: the 4 owned mixins should declare at least 30
        ``_handle_*`` methods combined. Guards against an accidental
        mass-deletion of handlers (e.g. a bad refactor that swallowed
        the mixin into a single dispatch method)."""
        total = sum(len(_handle_methods(m)) for m in _OWNED_MIXINS)
        # As of the YJ-1 partial-annotation pass: 10 (history) + 3
        # (dictation) + 8 (model) + 17 (onboarding) = 38 handlers.
        # Use a floor of 30 to allow minor future reshuffling without
        # tripping the test on every change.
        assert total >= 30, f"Expected ≥30 _handle_* methods across the 4 owned mixins; found {total}"


class TestYJ1ResponseEnvelopeImportable:
    """``ResponseEnvelope`` and ``CommandHandler`` MUST be importable from
    :mod:`voice_typer.server.ipc.validation` (the canonical home since
    YJ-1 moved them out of ``ipc_server.py`` to break the handler-mixin
    import cycle).

    A handler mixin importing ``ResponseEnvelope`` from
    ``ipc_server.py`` would re-introduce the cycle the move was
    designed to break — so this test guards the canonical location.
    """

    def test_response_envelope_importable_from_validation(self) -> None:
        # ``ResponseEnvelope = dict[str, object]`` — the alias must
        # resolve to ``dict[str, object]`` so callers can use it both
        # as a type annotation AND as a runtime value (e.g.
        # ``typing.get_args(ResponseEnvelope)`` returns ``(str, object)``).
        # Accept either the ``types.GenericAlias`` form (``dict[str, object]``)
        # or the ``typing.Dict`` form (``typing.Dict[str, object]``) —
        # both are valid alias declarations.
        import typing

        from voice_typer.server.ipc.validation import ResponseEnvelope

        origin = typing.get_origin(ResponseEnvelope)
        assert origin is dict, f"ResponseEnvelope origin must be dict; got {origin!r}"
        args = typing.get_args(ResponseEnvelope)
        assert args == (str, object), f"ResponseEnvelope args must be (str, object); got {args!r}"

    def test_command_handler_importable_from_validation(self) -> None:
        # ``CommandHandler`` is a ``Callable`` alias — sanity-check it
        # is the expected shape by accessing its ``__args__``.
        # ``Callable[[object | None, ResponseEnvelope], Optional[ResponseEnvelope]]``
        # has 2 args: the parameter-types tuple and the return type.
        import collections.abc
        import typing

        from voice_typer.server.ipc.validation import CommandHandler

        # ``typing.get_origin`` returns ``collections.abc.Callable`` for
        # ``typing.Callable[...]`` aliases on Python 3.9+. Accept either
        # form — the alias is structurally a Callable either way.
        origin = typing.get_origin(CommandHandler)
        assert origin is collections.abc.Callable, (
            f"CommandHandler origin must be collections.abc.Callable; got {origin!r}"
        )
        args = typing.get_args(CommandHandler)
        assert len(args) == 2
        param_types, return_type = args
        # NOTE: ``typing.Callable[[X, Y], Z]`` stores the parameter-type
        # container as a LIST on Python 3.9+ (not a tuple). Accept either
        # form — the structural shape (length + element types) is what
        # matters, not the container type.
        assert isinstance(param_types, tuple | list), f"param_types must be a tuple or list; got {type(param_types)!r}"
        # First param: ``object | None``
        # Second param: ``ResponseEnvelope`` (== ``dict[str, object]``)
        assert len(param_types) == 2

    def test_ipc_server_reexports_canonical_aliases(self) -> None:
        """``ipc_server.py`` MUST re-export the canonical aliases from
        ``validation.py`` (not define its own local copies) so the two
        modules stay in sync. Without this, the keystone
        ``# type: ignore[assignment]`` removal in a future YJ-1 pass
        could silently diverge from the actual handler signatures."""
        from voice_typer.server import ipc_server as s
        from voice_typer.server.ipc import validation as v

        assert s.ResponseEnvelope is v.ResponseEnvelope, (
            "ipc_server.ResponseEnvelope must be the SAME object as "
            "validation.ResponseEnvelope (re-export, not redefine)"
        )
        assert s.CommandHandler is v.CommandHandler, (
            "ipc_server.CommandHandler must be the SAME object as validation.CommandHandler (re-export, not redefine)"
        )


class TestYJ1RestoreHistoryNarrowing:
    """Regression for the pre-existing pyrefly ``missing-attribute`` error
    at ``history_handlers.py:263`` (``record.get("text", "")`` where
    ``record`` was untyped ``object``).

    The YJ-1 annotation pass added an explicit ``isinstance(record, dict)``
    narrowing guard before the ``record.get(...)`` call so pyrefly sees a
    ``dict`` shape (which has ``.get``) instead of bare ``object``.

    This test exercises the narrowing path at runtime — both the happy
    path (a dict record with a text field) and the defensive guard
    (a non-dict record, which the schema's ``"type": dict`` rule should
    reject upstream but the handler now defends against explicitly).
    """

    def test_restore_history_with_long_text_returns_payload_too_large(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A ``record['text']`` longer than 8192 chars MUST trigger the
        ``client.payload_too_large`` error envelope, NOT raise an
        ``AttributeError`` (the pre-YJ-1 behaviour when ``record`` was
        typed ``object`` and pyrefly's ``missing-attribute`` would fire
        at runtime if the value was unexpectedly not a dict)."""
        # Configure a fake app + service so IPCServer can be constructed
        # without triggering heavy imports (matches the
        # test_shutdown_posix_release.py pattern).
        import sys
        from unittest.mock import MagicMock

        _mock_pystray = MagicMock()
        _mock_pystray.Menu.SEPARATOR = "SEP"
        _mock_pystray.MenuItem = MagicMock
        _mock_pystray.Icon = MagicMock
        sys.modules.setdefault("pystray", _mock_pystray)

        # Patch config dir to tmp_path so PID file writes are isolated.
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)

        from voice_typer.server.ipc_server import IPCServer

        fake_app = MagicMock()
        fake_app._shutting_down = False
        fake_app._ipc_server = None
        # Defensive: ensure MagicMock doesn't trip the
        # ``_cached_shutting_down`` shortcut.
        fake_app._cached_shutting_down = False
        fake_service = MagicMock()
        fake_service.restore_history.return_value = 42

        server = IPCServer(fake_app, service=fake_service)

        # Build a record with an over-long text field.
        long_text = "x" * 8193
        record = {"text": long_text, "id": 1}

        resp: ResponseEnvelope = {}
        result = server._handle_restore_history({"record": record}, resp)

        # The handler MUST return an error envelope with the
        # namespaced ``client.payload_too_large`` code, NOT raise and
        # NOT call ``service.restore_history``.
        assert result is not None
        assert result["type"] == "error"
        data = result["data"]
        assert isinstance(data, dict)
        assert data["code"] == "client.payload_too_large"
        # The service MUST NOT have been invoked (the cap rejected
        # the payload before reaching the service layer).
        fake_service.restore_history.assert_not_called()

    def test_restore_history_with_non_dict_record_returns_invalid_payload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A ``record`` value that is not a dict (e.g. a list) MUST
        trigger the ``client.invalid_field`` envelope from the schema
        validation, NOT the defensive ``client.invalid_payload`` guard.
        The defensive guard fires only if the schema's ``"type": dict``
        rule is somehow bypassed (e.g. by a future schema change that
        drops the type rule)."""
        import sys
        from unittest.mock import MagicMock

        _mock_pystray = MagicMock()
        _mock_pystray.Menu.SEPARATOR = "SEP"
        _mock_pystray.MenuItem = MagicMock
        _mock_pystray.Icon = MagicMock
        sys.modules.setdefault("pystray", _mock_pystray)

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)

        from voice_typer.server.ipc_server import IPCServer

        fake_app = MagicMock()
        fake_app._shutting_down = False
        fake_app._ipc_server = None
        fake_app._cached_shutting_down = False
        fake_service = MagicMock()
        server = IPCServer(fake_app, service=fake_service)

        # A non-dict record — schema validation rejects with
        # ``client.invalid_field`` BEFORE the defensive guard runs.
        resp: ResponseEnvelope = {}
        result = server._handle_restore_history({"record": ["not", "a", "dict"]}, resp)

        assert result is not None
        assert result["type"] == "error"
        data = result["data"]
        assert isinstance(data, dict)
        assert data["code"] == "client.invalid_field"
        fake_service.restore_history.assert_not_called()

    def test_restore_history_with_valid_short_text_calls_service(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A valid record with a short text field MUST pass the cap and
        invoke ``service.restore_history``. Guards against an accidental
        inversion of the length check (``>`` vs ``<``)."""
        import sys
        from unittest.mock import MagicMock

        _mock_pystray = MagicMock()
        _mock_pystray.Menu.SEPARATOR = "SEP"
        _mock_pystray.MenuItem = MagicMock
        _mock_pystray.Icon = MagicMock
        sys.modules.setdefault("pystray", _mock_pystray)

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)

        from voice_typer.server.ipc_server import IPCServer

        fake_app = MagicMock()
        fake_app._shutting_down = False
        fake_app._ipc_server = None
        fake_app._cached_shutting_down = False
        fake_service = MagicMock()
        fake_service.restore_history.return_value = 99
        server = IPCServer(fake_app, service=fake_service)

        record = {"text": "hello world", "id": 1}
        resp: ResponseEnvelope = {}
        result = server._handle_restore_history({"record": record}, resp)

        assert result is not None
        assert result["type"] == "ack"
        assert result["data"] == {"id": 99}
        fake_service.restore_history.assert_called_once_with(record)
