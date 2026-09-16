"""Handler-mixin signature conformance for the FULL handler set.

Every ``_handle_*`` method across the handler mixins MUST be annotated with
the canonical ``CommandHandler`` shape declared in
``voice_typer/server/ipc/validation.py``::

    (self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None

``ResponseEnvelope`` is the alias ``dict[str, object]`` (the structural shape
every IPC frame carries), so the expectations below are expressed structurally:
they keep holding if the mixin modules import the alias instead of the raw
``dict`` spelling.

History (why this file exists)
------------------------------

Typing the handler surface happened in stages. An early pass annotated only
four mixins (history / dictation / model / onboarding) and this test covered
exactly those, while the remaining mixins still used the older
``(data: dict | None, resp: dict) -> dict | None`` style. Two consequences:
the declared ``CommandHandler`` contract could not be enforced (a ``dict``
annotation is not parameter-compatible with the ``object | None`` payload the
dispatcher actually passes), and there was no single source of truth for the
response envelope.

The migration is complete: every handler mixin annotates the canonical shape.
The test therefore

* **discovers** the mixins instead of naming four of them, so a new handler
  module is covered without editing this file, and
* asserts the **exact** canonical annotations rather than merely "an
  annotation is present", so reintroducing either half of the old pair
  (``data: dict | None`` / ``resp: dict`` / ``-> dict | None``) fails loudly.

``TestNoLegacyHandlerAnnotation`` additionally guards the handler sources that
are NOT introspected here (the ``ipc/`` dispatch mixins) by scanning the
signature lines on disk.
"""

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
# regression (e.g. a botched ``pkgutil`` change) silently shrinking the
# checked surface to a handful of methods.
_MIN_EXPECTED_HANDLERS = 60

_NONE_TYPE = type(None)

_HANDLERS_DIR = Path(_handlers_pkg.__file__).parent
_IPC_DIR = _HANDLERS_DIR.parent / "ipc"

# Directories whose ``_handle_*`` signature lines are scanned for the
# legacy annotation pair by ``TestNoLegacyHandlerAnnotation``.
_HANDLER_SOURCE_DIRS = (_HANDLERS_DIR, _IPC_DIR)


def _resolve_atom(annotation: object) -> str:
    """Render a non-parameterized annotation as a stable string."""
    if annotation is _NONE_TYPE:
        return "None"
    return getattr(annotation, "__name__", str(annotation))


def _canonical(annotation: object) -> str:
    """Render *annotation* structurally, stable across Python versions.

    ``typing.get_type_hints`` hands back real objects (``types.UnionType``,
    ``types.GenericAlias``, plain classes) or forward-ref strings. Comparing
    those directly is brittle (``dict[str, object]`` has a ``__name__`` of
    ``"dict"`` via attribute proxying, ``X | None`` has none at all), so both
    sides of a comparison are rendered through this function.
    """
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
    """Every class in ``voice_typer/server/handlers/`` declaring handlers.

    Modules are imported via ``pkgutil`` so a newly added handler mixin is
    picked up automatically.
    """
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


# ── Tests ──────────────────────────────────────────────────────────────


class TestHandlerSignatureConformance:
    """Every discovered ``_handle_*`` method MUST carry the canonical
    ``CommandHandler`` annotations.
    """

    @pytest.mark.parametrize(
        "mixin_cls",
        _MIXIN_CLASSES,
        ids=lambda cls: cls.__name__,
    )
    def test_every_handle_method_matches_command_handler_shape(self, mixin_cls: type) -> None:
        """``data`` / ``resp`` / return annotations MUST be exactly the
        canonical ``(object | None, ResponseEnvelope) -> ResponseEnvelope | None``.

        A handler that drops or reverts an annotation (e.g. a copy-pasted
        ``def _handle_x(self, data: dict | None, resp: dict) -> dict | None``)
        fails here before it can drift from the dispatcher's
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
            # annotations) because a mixin module may opt into postponed
            # evaluation (``from __future__ import annotations``), which
            # turns every annotation into a string: the ``ResponseEnvelope``
            # alias would then compare as the bare name instead of its
            # ``dict[str, object]`` shape.
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
        """Discovery MUST reach every mixin module that declares handlers.

        Cross-checks the introspected set against the sources on disk: every
        ``voice_typer/server/handlers/*.py`` file containing a ``_handle_*``
        definition must contribute at least one discovered mixin (a module
        added to the package can therefore never be silently skipped).
        """
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
    """The legacy ``(data: dict | None, resp: dict) -> dict | None`` handler
    annotation MUST NOT reappear anywhere in the handler/dispatch sources.

    The introspective test above only sees the ``handlers/`` package mixins;
    this scan also covers the ``ipc/`` dispatch mixins (``DispatcherMixin``,
    ``LifecycleMixin``) whose ``_handle_*`` methods are annotated in place.
    """

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
    """``ResponseEnvelope`` and ``CommandHandler`` MUST be importable from
    :mod:`voice_typer.server.ipc.validation` (the canonical home since
    YJ-1 moved them out of ``ipc_server.py`` to break the handler-mixin
    import cycle).

    A handler mixin importing ``ResponseEnvelope`` from
    ``ipc_server.py`` would re-introduce the cycle the move was
    designed to break, so this test guards the canonical location.
    """

    def test_response_envelope_importable_from_validation(self) -> None:
        # ``ResponseEnvelope = dict[str, object]``, the alias must
        # resolve to ``dict[str, object]`` so callers can use it both
        # as a type annotation AND as a runtime value (e.g.
        # ``typing.get_args(ResponseEnvelope)`` returns ``(str, object)``).
        # Accept either the ``types.GenericAlias`` form (``dict[str, object]``)
        # or the ``typing.Dict`` form (``typing.Dict[str, object]``) —
        # both are valid alias declarations.
        from voice_typer.server.ipc.validation import ResponseEnvelope

        origin = typing.get_origin(ResponseEnvelope)
        assert origin is dict, f"ResponseEnvelope origin must be dict; got {origin!r}"
        args = typing.get_args(ResponseEnvelope)
        assert args == (str, object), f"ResponseEnvelope args must be (str, object); got {args!r}"

    def test_command_handler_importable_from_validation(self) -> None:
        # ``CommandHandler`` is a ``Callable`` alias, sanity-check it
        # is the expected shape by accessing its ``__args__``.
        # ``Callable[[object | None, ResponseEnvelope], Optional[ResponseEnvelope]]``
        # has 2 args: the parameter-types tuple and the return type.
        import collections.abc

        from voice_typer.server.ipc.validation import CommandHandler

        # ``typing.get_origin`` returns ``collections.abc.Callable`` for
        # ``typing.Callable[...]`` aliases on Python 3.9+. Accept either
        # form, the alias is structurally a Callable either way.
        origin = typing.get_origin(CommandHandler)
        assert origin is collections.abc.Callable, (
            f"CommandHandler origin must be collections.abc.Callable; got {origin!r}"
        )
        args = typing.get_args(CommandHandler)
        assert len(args) == 2
        param_types, return_type = args
        # NOTE: ``typing.Callable[[X, Y], Z]`` stores the parameter-type
        # container as a LIST on Python 3.9+ (not a tuple). Accept either
        # form, the structural shape (length + element types) is what
        # matters, not the container type.
        assert isinstance(param_types, tuple | list), f"param_types must be a tuple or list; got {type(param_types)!r}"
        # First param: ``object | None``
        # Second param: ``ResponseEnvelope`` (== ``dict[str, object]``)
        assert len(param_types) == 2

    def test_ipc_server_reexports_canonical_aliases(self) -> None:
        """``ipc_server.py`` MUST re-export the canonical aliases from
        ``validation.py`` (not define its own local copies) so the two
        modules stay in sync."""
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
    """Regression for the pre-existing pyrefly ``missing-attribute`` error
    at ``history_handlers.py:263`` (``record.get("text", "")`` where
    ``record`` was untyped ``object``).

    The YJ-1 annotation pass added an explicit ``isinstance(record, dict)``
    narrowing guard before the ``record.get(...)`` call so pyrefly sees a
    ``dict`` shape (which has ``.get``) instead of bare ``object``.

    This test exercises the narrowing path at runtime, both the happy
    path (a dict record with a text field) and the defensive guard
    (a non-dict record, which the schema's ``"type": dict`` rule should
    reject upstream but the handler now defends against explicitly).
    """

    def test_restore_history_with_long_text_returns_payload_too_large(self, tmp_config_dir: Path) -> None:
        """A ``record['text']`` longer than 8192 chars MUST trigger the
        ``client.payload_too_large`` error envelope, NOT raise an
        ``AttributeError`` (the pre-YJ-1 behaviour when ``record`` was
        typed ``object`` and pyrefly's ``missing-attribute`` would fire
        at runtime if the value was unexpectedly not a dict)."""
        # Configure a fake app + service so IPCServer can be constructed
        # without triggering heavy imports (matches the
        # test_shutdown_posix_release.py pattern).
        from unittest.mock import MagicMock

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

        resp: dict = {}
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

    def test_restore_history_with_non_dict_record_returns_invalid_payload(self, tmp_config_dir: Path) -> None:
        """A ``record`` value that is not a dict (e.g. a list) MUST
        trigger the ``client.invalid_field`` envelope from the schema
        validation, NOT the defensive ``client.invalid_payload`` guard.
        The defensive guard fires only if the schema's ``"type": dict``
        rule is somehow bypassed (e.g. by a future schema change that
        drops the type rule)."""
        from unittest.mock import MagicMock

        from voice_typer.server.ipc_server import IPCServer

        fake_app = MagicMock()
        fake_app._shutting_down = False
        fake_app._ipc_server = None
        fake_app._cached_shutting_down = False
        fake_service = MagicMock()
        server = IPCServer(fake_app, service=fake_service)

        # A non-dict record, schema validation rejects with
        # ``client.invalid_field`` BEFORE the defensive guard runs.
        resp: dict = {}
        result = server._handle_restore_history({"record": ["not", "a", "dict"]}, resp)

        assert result is not None
        assert result["type"] == "error"
        data = result["data"]
        assert isinstance(data, dict)
        assert data["code"] == "client.invalid_field"
        fake_service.restore_history.assert_not_called()

    def test_restore_history_with_valid_short_text_calls_service(self, tmp_config_dir: Path) -> None:
        """A valid record with a short text field MUST pass the cap and
        invoke ``service.restore_history``. Guards against an accidental
        inversion of the length check (``>`` vs ``<``)."""
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
