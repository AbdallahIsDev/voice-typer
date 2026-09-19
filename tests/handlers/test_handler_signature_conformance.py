"""Handler-mixin signature conformance for the FULL handler set."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import types
import typing
from pathlib import Path

import pytest
import voice_typer.server.handlers as _handlers_pkg

# Canonical structural renderings of the ``CommandHandler`` annotations.
_EXPECTED_DATA = "object | None"
_EXPECTED_RESP = "dict[str, object]"
_EXPECTED_RETURN = "dict[str, object] | None"

# Floor for the discovered handler count. Guards against a discovery
_MIN_EXPECTED_HANDLERS = 60

_NONE_TYPE = type(None)

_HANDLERS_DIR = Path(_handlers_pkg.__file__).parent
_IPC_DIR = _HANDLERS_DIR.parent / "ipc"

_HANDLER_SOURCE_DIRS = (_HANDLERS_DIR, _IPC_DIR)


def _resolve_atom(annotation: object) -> str:
    """Render a non-parameterized annotation as a stable string."""
    if annotation is _NONE_TYPE:
        return "None"
    return getattr(annotation, "__name__", str(annotation))


def _canonical(annotation: object) -> str:
    """Render *annotation* structurally, stable across Python versions."""
    if annotation is inspect.Parameter.empty:
        return "<empty>"
    if isinstance(annotation, str):
        return annotation.strip("'\"")
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return " | ".join(_canonical(arg) for arg in args)
    if args:
        return f"{_resolve_atom(origin)}[{', '.join(_canonical(arg) for arg in args)}]"
    return _resolve_atom(annotation)


def _handle_methods(mixin_cls: type) -> list[str]:
    """Return every ``_handle_*`` method name declared on ``mixin_cls``."""
    return [name for name in vars(mixin_cls) if name.startswith("_handle_")]


def _discover_mixins() -> list[type]:
    """Every class in ``voice_typer/server/handlers/`` declaring handlers."""
    mixins: set[type] = set()
    for module_info in sorted(pkgutil.iter_modules(_handlers_pkg.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{_handlers_pkg.__name__}.{module_info.name}")
        for obj in vars(module).values():
            if not inspect.isclass(obj) or obj.__module__ != module.__name__:
                continue
            if _handle_methods(obj):
                mixins.add(obj)
    return sorted(mixins, key=lambda cls: cls.__name__)


_MIXIN_CLASSES = _discover_mixins()


def _handler_source_files() -> list[Path]:
    """Every handler/dispatch source file on disk (sorted)."""
    files: list[Path] = []
    for directory in _HANDLER_SOURCE_DIRS:
        files.extend(sorted(directory.glob("*.py")))
    return files


class TestHandlerSignatureConformance:
    """Every discovered ``_handle_*`` method MUST carry the canonical"""

    @pytest.mark.parametrize(
        "mixin_cls",
        _MIXIN_CLASSES,
        ids=lambda cls: cls.__name__,
    )
    def test_every_handle_method_matches_command_handler_shape(self, mixin_cls: type) -> None:
        """
        canonical ``(object | None, ResponseEnvelope) -> ResponseEnvelope | None``.
        ``CommandHandler`` contract.
        """
        method_names = _handle_methods(mixin_cls)
        assert method_names, f"{mixin_cls.__name__} should declare at least one _handle_* method"
        failures: list[str] = []
        for name in method_names:
            sig = inspect.signature(getattr(mixin_cls, name))
            params = list(sig.parameters.values())
            where = f"{mixin_cls.__name__}.{name}"
            if len(params) != 3:
                failures.append(f"{where}: expected 3 params (self, data, resp), got {len(params)}")
                continue
            self_param, data_param, resp_param = params
            if (self_param.name, data_param.name, resp_param.name) != ("self", "data", "resp"):
                failures.append(
                    f"{where}: params must be named (self, data, resp), got "
                    f"({self_param.name}, {data_param.name}, {resp_param.name})"
                )
                continue
            # ``get_type_hints`` (not the raw ``inspect.signature``
            try:
                hints = typing.get_type_hints(getattr(mixin_cls, name))
            except Exception as exc:  # pragma: no cover - defensive
                failures.append(f"{where}: annotations are not resolvable ({exc!r})")
                continue
            data_ann = _canonical(hints.get("data", inspect.Parameter.empty))
            if data_ann != _EXPECTED_DATA:
                failures.append(f"{where}: data must be annotated `{_EXPECTED_DATA}`, got `{data_ann}`")
            resp_ann = _canonical(hints.get("resp", inspect.Parameter.empty))
            if resp_ann != _EXPECTED_RESP:
                failures.append(f"{where}: resp must be annotated `{_EXPECTED_RESP}`, got `{resp_ann}`")
            return_ann = _canonical(hints.get("return", inspect.Parameter.empty))
            if return_ann != _EXPECTED_RETURN:
                failures.append(f"{where}: return must be annotated `{_EXPECTED_RETURN}`, got `{return_ann}`")
        assert not failures, f"{mixin_cls.__name__} has non-conformant _handle_* methods:\n  - " + "\n  - ".join(
            failures
        )

    def test_discovery_covers_every_mixin_and_handler(self) -> None:
        """Discovery MUST reach every mixin module that declares handlers."""
        discovered_modules = {cls.__module__ for cls in _MIXIN_CLASSES}
        missing: list[str] = []
        for path in sorted(_HANDLERS_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "def _handle_" not in source:
                continue
            module_name = f"{_handlers_pkg.__name__}.{path.stem}"
            if module_name not in discovered_modules:
                missing.append(module_name)
        assert not missing, f"handler modules declaring _handle_* were not discovered: {missing}"

        total = sum(len(_handle_methods(cls)) for cls in _MIXIN_CLASSES)
        assert total >= _MIN_EXPECTED_HANDLERS, (
            f"Expected >= {_MIN_EXPECTED_HANDLERS} _handle_* methods across the discovered mixins; found {total}"
        )


class TestNoLegacyHandlerAnnotation:
    """The legacy ``(data: dict | None, resp: dict) -> dict | None`` handler"""

    def test_no_handler_signature_uses_the_legacy_annotation_pair(self) -> None:
        offenders: list[str] = []
        for path in _handler_source_files():
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if "def _handle_" not in line:
                    continue
                # Signature may wrap across lines; scan a small window.
                signature = " ".join(lines[index : index + 3])
                if "data: dict | None" in signature or "resp: dict) -> dict | None" in signature:
                    offenders.append(f"{path.name}:{index + 1}")
        assert not offenders, (
            "handler signature(s) still use the legacy `dict` annotation style "
            f"(use `object | None` / `ResponseEnvelope`): {offenders}"
        )


class TestResponseEnvelopeImportable:
    """:mod:`voice_typer.server.ipc.validation` (the canonical home since"""

    def test_response_envelope_importable_from_validation(self) -> None:
        # ``ResponseEnvelope = dict[str, object]``, the alias must
        from voice_typer.server.ipc.validation import ResponseEnvelope

        origin = typing.get_origin(ResponseEnvelope)
        assert origin is dict, f"ResponseEnvelope origin must be dict; got {origin!r}"
        args = typing.get_args(ResponseEnvelope)
        assert args == (str, object), f"ResponseEnvelope args must be (str, object); got {args!r}"

    def test_command_handler_importable_from_validation(self) -> None:
        # ``CommandHandler`` is a ``Callable`` alias, sanity-check it
        import collections.abc

        from voice_typer.server.ipc.validation import CommandHandler

        origin = typing.get_origin(CommandHandler)
        assert origin is collections.abc.Callable, (
            f"CommandHandler origin must be collections.abc.Callable; got {origin!r}"
        )
        args = typing.get_args(CommandHandler)
        assert len(args) == 2
        param_types, return_type = args
        # NOTE: ``typing.Callable[[X, Y], Z]`` stores the parameter-type
        assert isinstance(param_types, tuple | list), f"param_types must be a tuple or list; got {type(param_types)!r}"
        # First param: ``object | None``
        assert len(param_types) == 2

    def test_ipc_server_reexports_canonical_aliases(self) -> None:
        """``validation.py`` (not define its own local copies) so the two"""
        from voice_typer.server import ipc_server as s
        from voice_typer.server.ipc import validation as v

        assert s.ResponseEnvelope is v.ResponseEnvelope, (
            "ipc_server.ResponseEnvelope must be the SAME object as "
            "validation.ResponseEnvelope (re-export, not redefine)"
        )
        assert s.CommandHandler is v.CommandHandler, (
            "ipc_server.CommandHandler must be the SAME object as validation.CommandHandler (re-export, not redefine)"
        )


class TestRestoreHistoryNarrowing:
    """Regression for the pre-existing pyrefly ``missing-attribute`` error"""

    def test_restore_history_with_long_text_returns_payload_too_large(self, tmp_config_dir: Path) -> None:
        """``AttributeError`` (the pre-YJ-1 behaviour when ``record`` was"""
        # Configure a fake app + service so IPCServer can be constructed
        from unittest.mock import MagicMock

        from voice_typer.server.ipc_server import IPCServer

        fake_app = MagicMock()
        fake_app._shutting_down = False
        fake_app._ipc_server = None
        fake_app._cached_shutting_down = False
        fake_service = MagicMock()
        fake_service.restore_history.return_value = 42

        server = IPCServer(fake_app, service=fake_service)

        # Build a record with an over-long text field.
        long_text = "x" * 8193
        record = {"text": long_text, "id": 1}

        resp: dict = {}
        result = server._handle_restore_history({"record": record}, resp)

        assert result is not None
        assert result["type"] == "error"
        data = result["data"]
        assert isinstance(data, dict)
        assert data["code"] == "client.payload_too_large"
        # The service MUST NOT have been invoked (the cap rejected
        fake_service.restore_history.assert_not_called()

    def test_restore_history_with_non_dict_record_returns_invalid_payload(self, tmp_config_dir: Path) -> None:
        """A ``record`` value that is not a dict (e.g. a list) MUST"""
        from unittest.mock import MagicMock

        from voice_typer.server.ipc_server import IPCServer

        fake_app = MagicMock()
        fake_app._shutting_down = False
        fake_app._ipc_server = None
        fake_app._cached_shutting_down = False
        fake_service = MagicMock()
        server = IPCServer(fake_app, service=fake_service)

        resp: dict = {}
        result = server._handle_restore_history({"record": ["not", "a", "dict"]}, resp)

        assert result is not None
        assert result["type"] == "error"
        data = result["data"]
        assert isinstance(data, dict)
        assert data["code"] == "client.invalid_field"
        fake_service.restore_history.assert_not_called()

    def test_restore_history_with_valid_short_text_calls_service(self, tmp_config_dir: Path) -> None:
        """invoke ``service.restore_history``. Guards against an accidental"""
        from unittest.mock import MagicMock

        from voice_typer.server.ipc_server import IPCServer

        fake_app = MagicMock()
        fake_app._shutting_down = False
        fake_app._ipc_server = None
        fake_app._cached_shutting_down = False
        fake_service = MagicMock()
        fake_service.restore_history.return_value = 99
        server = IPCServer(fake_app, service=fake_service)

        record = {"text": "hello world", "id": 1}
        resp: dict = {}
        result = server._handle_restore_history({"record": record}, resp)

        assert result is not None
        assert result["type"] == "ack"
        assert result["data"] == {"id": 99}
        fake_service.restore_history.assert_called_once_with(record)
