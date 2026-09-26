"""Regression tests for ARCH-REFAC-004: DI boundary for ``IPCServer``."""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from unittest.mock import MagicMock

from voice_typer.server.ipc_server import IPCServer  # noqa: E402
from voice_typer.server.providers import (  # noqa: E402
    AppProtocol,
    ServiceProtocol,
    build_ipc_server,
)
from voice_typer.server.service import LausuService  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HANDLERS_DIR = _REPO_ROOT / "voice_typer" / "server" / "handlers"
_IPC_SERVER_PY = _REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"


def _protocol_declared_names(proto_cls) -> set:
    """Return every name declared on a ``typing.Protocol`` class."""
    names = set()
    # Annotated data attributes (no default value → only in __annotations__).
    names.update(getattr(proto_cls, "__annotations__", {}).keys())
    # Methods (def foo(self, ...): ...), only those defined directly
    for name, value in vars(proto_cls).items():
        if name.startswith("__"):
            continue
        if callable(value):
            names.add(name)
    return names


def _collect_attr_accesses(py_path: Path, base_attr: str) -> set:
    """Return attribute names accessed via ``self.<base_attr>.X``."""
    source = py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_path))
    used: set[str] = set()
    for node in ast.walk(tree):
        # We're looking for `self.<base_attr>.<something>`, that's
        if not isinstance(node, ast.Attribute):
            continue
        inner = node.value
        if not isinstance(inner, ast.Attribute):
            continue
        if inner.attr != base_attr:
            continue
        if not isinstance(inner.value, ast.Name):
            continue
        if inner.value.id != "self":
            continue
        used.add(node.attr)
    return used


def _collect_getattr_string_accesses(py_path: Path, base_attr: str) -> set:
    """Return attribute names accessed via ``getattr(self.<base_attr>, \"X\", ...)``."""
    source = py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_path))
    used: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match the bare ``getattr(...)`` builtin call (not
        if not isinstance(func, ast.Name) or func.id != "getattr":
            continue
        if len(node.args) < 2:
            continue
        # First arg must be ``self.<base_attr>``.
        first = node.args[0]
        if not isinstance(first, ast.Attribute):
            continue
        if first.attr != base_attr:
            continue
        if not isinstance(first.value, ast.Name):
            continue
        if first.value.id != "self":
            continue
        # Second arg must be a string literal Constant.
        second = node.args[1]
        # Python 3.7+: ast.Constant (and ast.Str for older, but we
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            used.add(second.value)
        elif hasattr(ast, "Str") and isinstance(second, ast.Str):  # pragma: no cover
            used.add(second.s)  # type: ignore[attr-defined]
    return used


# (preferred, keeps the contract honest) or refactor the call site
_KNOWN_APP_GETATTR_BYPASSES: set[str] = {
    "_thread_registry",
}


def _all_handler_files() -> list:
    """Return every ``.py`` file under ``voice_typer/server/handlers/``."""
    return sorted(p for p in _HANDLERS_DIR.glob("*.py") if p.name != "__init__.py")


class TestDIInjection:
    """Verify ``IPCServer(app, service=fake)`` DI mode."""

    def test_ipc_server_accepts_injected_service(self):
        """``service=<fake>`` is stored verbatim; no LausuService constructed."""
        from tests.fixtures.ipc_test_helpers import (
            make_fake_app,
            make_fake_service,
        )

        fake_app = make_fake_app()
        fake_service = make_fake_service()
        server = IPCServer(fake_app, service=fake_service)

        # The injected service must be the exact object passed in —
        assert server.service is fake_service
        assert server.app is fake_app
        # Type check: confirms the DI seam is in effect.
        assert not isinstance(server.service, LausuService), (
            "DI mode must NOT construct a real LausuService, "
            "the whole point is to substitute a fake for the service "
            "layer so the IPC dispatch path can be tested in isolation."
        )

    def test_ipc_server_di_mode_dispatch_uses_injected_service(self):
        """End-to-end: a dispatched command must call the injected service."""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        server, fake_app, fake_service = make_ipc_server_with_fakes()
        # Configure the fake's return value for get_status.
        fake_service.get_status.return_value = {
            "status": "recording",
            "xruns_since_start": 7,
            "loaded_via": "test",
        }

        result = server._dispatch({"id": 1, "type": "get_status"})

        # The injected service must have been called, not a real
        fake_service.get_status.assert_called_once()
        assert result["type"] == "status"
        assert result["id"] == 1
        assert result["data"]["status"] == "recording"
        assert result["data"]["xruns_since_start"] == 7


class TestBackwardCompat:
    """Verify ``IPCServer(app)`` (no service) still constructs a real service."""

    def test_ipc_server_backward_compat_constructs_service_from_app(self):
        """``IPCServer(app)`` must construct a real ``LausuService``."""
        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        server = IPCServer(fake_app)

        # The server must have constructed a real LausuService
        assert isinstance(server.service, LausuService), (
            "IPCServer(app) without `service=` must construct a real "
            "LausuService over `app`, this is the backward-compat "
            "path that all existing call sites depend on."
        )
        # And the service must have been wired to the same app.
        assert server.service._app is fake_app
        assert server.app is fake_app

    def test_ipc_server_default_service_argument_is_none(self):
        """The ``service`` parameter defaults to ``None`` (not a sentinel)."""
        import inspect

        sig = inspect.signature(IPCServer.__init__)
        service_param = sig.parameters["service"]
        assert service_param.default is None, "service= must default to None so IPCServer(app) keeps working."
        params = list(sig.parameters.keys())
        assert params[0] == "self"
        assert params[1] == "app", "app must remain the first positional parameter so IPCServer(app) continues to work."

    def test_existing_mock_app_pattern_still_works(self):
        """A plain ``MagicMock()`` app (no AppProtocol import) must work."""
        app = MagicMock()
        app._config_mutation_lock = threading.RLock()
        app._shutting_down = False
        server = IPCServer(app)
        # Just confirm construction succeeds and the service is real.
        assert isinstance(server.service, LausuService)
        # And dispatching a simple command doesn't crash.
        app.tray.state.value = "idle"
        result = server._dispatch({"type": "get_status"})
        assert result["type"] == "status"


class TestProtocolDrift:
    """Verify ``AppProtocol`` / ``ServiceProtocol`` cover handler usage."""

    def test_app_protocol_lists_all_attributes_used_by_handlers(self):
        """``AppProtocol`` must declare every ``self.app.X`` accessed."""
        used_attrs: set[str] = set()
        for handler_py in _all_handler_files():
            used_attrs |= _collect_attr_accesses(handler_py, base_attr="app")
            used_attrs |= _collect_getattr_string_accesses(handler_py, base_attr="app")
        # Also include self.app.X accesses in ipc_server.py itself —
        used_attrs |= _collect_attr_accesses(_IPC_SERVER_PY, base_attr="app")
        used_attrs |= _collect_getattr_string_accesses(_IPC_SERVER_PY, base_attr="app")

        declared = _protocol_declared_names(AppProtocol)

        used_attrs -= _KNOWN_APP_GETATTR_BYPASSES

        missing = used_attrs - declared
        assert not missing, (
            f"AppProtocol is missing {len(missing)} attributes that "
            f"handlers/ipc_server access via self.app.<name>: "
            f"{sorted(missing)}.  Either add these to AppProtocol (if "
            f"the access is an accepted part of the IPC layer's "
            f"contract with the app) or refactor the handler to go "
            f"through the service layer (preferred, the protocol "
            f"surface should stay small)."
        )

    def test_service_protocol_lists_all_methods_used_by_handlers(self):
        """``ServiceProtocol`` must declare every ``self.service.X`` called."""
        used_attrs: set[str] = set()
        for handler_py in _all_handler_files():
            used_attrs |= _collect_attr_accesses(handler_py, base_attr="service")
        # Also include self.service.X accesses in ipc_server.py.
        used_attrs |= _collect_attr_accesses(_IPC_SERVER_PY, base_attr="service")

        # Filter out private attributes, _app is the main one and is
        public_used = {name for name in used_attrs if not name.startswith("_")}

        declared = _protocol_declared_names(ServiceProtocol)

        missing = public_used - declared
        assert not missing, (
            f"ServiceProtocol is missing {len(missing)} methods that "
            f"handlers/ipc_server call via self.service.<name>: "
            f"{sorted(missing)}.  Add these to ServiceProtocol so the "
            f"contract is explicit and the introspection test catches "
            f"future drift."
        )

    def test_app_protocol_declares_documented_members(self):
        """Smoke test: ``AppProtocol`` declares the key documented members."""
        declared = _protocol_declared_names(AppProtocol)
        # Public domain objects mentioned in the task description.
        for required in (
            "config",
            "history_db",
            "models",
            "recording",
            "hotkeys",
            "recorder",
            "tray",
        ):
            assert required in declared, (
                f"AppProtocol must declare `{required}`, it's a core domain object the IPC layer exposes."
            )
        # Private attributes handlers / ipc_server still access
        for required in (
            "_ipc_server",
            "_shutting_down",
            "_esc_cancel_paused",
            "_vocabulary_automation",
            "_waveform_bubble",
        ):
            assert required in declared, (
                f"AppProtocol must declare `{required}`, handlers or ipc_server.py access it via self.app.{required}."
            )
        # Methods the service layer delegates to the app.
        for required in (
            "change_model",
            "toggle_dictation",
            "undo_last",
            "repaste_last",
            "restart_app",
            "quit_app",
            "quit",
            "start",
        ):
            assert required in declared, (
                f"AppProtocol must declare `{required}()`, the service layer delegates this call to the app."
            )
        for forbidden in (
            "_audio_processor",
            "_volume_ducker",
            "_config_mutation_lock",
        ):
            assert forbidden not in declared, (
                f"AppProtocol must NOT declare `{forbidden}` post-"
                f"ADR-0008-§3.1, the service layer (get_audio_status / "
                f"get_volume_backend_status / apply_config) wraps its "
                f"access.  Re-adding it would re-introduce the leaky "
                f"abstraction the refactor removed."
            )

    def test_service_protocol_declares_core_methods(self):
        """Smoke test: ``ServiceProtocol`` declares the core service methods."""
        declared = _protocol_declared_names(ServiceProtocol)
        # A representative sample, the full surface is large; this
        for required in (
            "get_status",
            "toggle_dictation",
            "undo_last",
            "get_config",
            "get_history",
            "clear_history",
            "get_microphones",
            "refresh_microphones",
            "download_model",
            "cancel_model_download",
            "get_vocabulary",
            "save_vocabulary_with_diff",
            "get_templates",
            "save_templates",
            "restart",
            "quit",
            "apply_config_side_effects",
            "get_audio_status",
            "change_model",
            "set_active_backend",
            "apply_config",
            "force_cancel_transcription",
        ):
            assert required in declared, (
                f"ServiceProtocol must declare `{required}()`, it's part of the service surface handlers call."
            )


class TestBuildIPCServer:
    """Verify :func:`build_ipc_server` returns a working IPCServer."""

    def test_providers_build_ipc_server_factory_works(self):
        """``build_ipc_server(app)`` returns a ready-to-use IPCServer."""
        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        server = build_ipc_server(fake_app)

        # Must be an IPCServer instance.
        assert isinstance(server, IPCServer)
        # Must have wired the app.
        assert server.app is fake_app
        # Must have constructed a real LausuService (the factory
        assert isinstance(server.service, LausuService)
        # The server must be dispatch-ready: a basic get_status call
        fake_app.tray.state.value = "idle"
        result = server._dispatch({"id": 42, "type": "get_status"})
        assert result["id"] == 42
        assert result["type"] == "status"

    def test_build_ipc_server_does_not_accept_service_kwarg(self):
        """``build_ipc_server`` is the production path, no DI."""
        import inspect

        sig = inspect.signature(build_ipc_server)
        params = list(sig.parameters.keys())
        assert params == ["app"], (
            "build_ipc_server should take exactly one parameter (app), "
            f"got {params}.  The factory is the production composition "
            f"root; tests that need DI should call IPCServer(app, "
            f"service=fake) directly."
        )


def _structurally_satisfies(obj, proto_cls) -> bool:
    """Return True if ``obj`` has every attribute/method on ``proto_cls``."""
    return all(hasattr(obj, name) for name in _protocol_declared_names(proto_cls))


class TestProtocolStructuralCompat:
    """Verify fakes and real objects structurally satisfy the protocols."""

    def test_fake_app_satisfies_app_protocol(self):
        """``make_fake_app()`` returns an object with every AppProtocol member."""
        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        assert _structurally_satisfies(fake_app, AppProtocol), (
            "make_fake_app() must return an object that has every "
            "attribute/method declared on AppProtocol, otherwise "
            "tests using the fake would diverge from the real contract."
        )

        # Stricter check: every annotated data attribute on AppProtocol
        annotated = set(getattr(AppProtocol, "__annotations__", {}).keys())
        # ``_vocabulary_automation`` and ``_waveform_bubble``
        _FAKE_APP_AUTO_STUB_OK = {  # noqa: N806
            "_vocabulary_automation",
            "_waveform_bubble",
        }
        annotated -= _FAKE_APP_AUTO_STUB_OK
        # The fake sets attributes on the instance, not the class —
        fake_app_dict = {k: v for k, v in vars(fake_app).items() if not k.startswith("_mock")}
        for name in annotated:
            # Skip dunder / MagicMock-internal attrs.
            if name.startswith("__"):
                continue
            assert name in fake_app_dict, (
                f"make_fake_app() does NOT explicitly set `{name}`, "
                f"it's relying on MagicMock's auto-stub.  Add an "
                f"explicit assignment in make_fake_app so the fake "
                f"has a sensible default (e.g. a real RLock for "
                f"_config_mutation_lock, not a child mock that breaks "
                f"`with` semantics)."
            )

    def test_fake_service_satisfies_service_protocol(self):
        """``make_fake_service()`` returns an object with every ServiceProtocol method."""
        from tests.fixtures.ipc_test_helpers import make_fake_service

        fake_service = make_fake_service()
        assert _structurally_satisfies(fake_service, ServiceProtocol), (
            "make_fake_service() must return an object that has every method declared on ServiceProtocol."
        )

    def test_real_voice_typer_service_satisfies_service_protocol(self):
        """The real ``LausuService`` must structurally satisfy ``ServiceProtocol``."""
        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        real_service = LausuService(fake_app)
        assert isinstance(real_service, ServiceProtocol), (
            "LausuService must structurally satisfy "
            "ServiceProtocol, if not, the protocol has drifted "
            "from the implementation."
        )
        # Also confirm via our manual check (belt-and-suspenders).
        assert _structurally_satisfies(real_service, ServiceProtocol)


# Tests:, ServiceProtocol parameter type narrowing ────────────


def _service_protocol_method_node(method_name: str):
    """Return the ``ast.FunctionDef`` node for ``method_name`` on ``ServiceProtocol``."""
    import inspect

    src_file = inspect.getsourcefile(ServiceProtocol)
    assert src_file is not None, "ServiceProtocol must have a discoverable source file"
    tree = ast.parse(Path(src_file).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ServiceProtocol":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    return None


class TestServiceProtocolTypeNarrowing:
    """``mic_id`` / ``duration`` / ``filters``."""

    @staticmethod
    def _param_annotation_text(method_name: str, param_name: str) -> str:
        """Return the source-text annotation for ``param_name`` on ``method_name``."""
        import ast as _ast

        func_node = _service_protocol_method_node(method_name)
        assert func_node is not None, (
            f"ServiceProtocol must declare `{method_name}()`, YJ-7 test depends on this method existing."
        )
        # Skip 'self' (positional 0); locate the named arg.
        all_args = list(func_node.args.args) + list(func_node.args.kwonlyargs)
        for arg in all_args:
            if arg.arg == param_name:
                if arg.annotation is None:
                    return ""
                return _ast.unparse(arg.annotation)
        raise AssertionError(
            f"`{method_name}()` does not declare a parameter named `{param_name}`, has the signature changed?"
        )

    def test_microphone_test_start_mic_id_not_any(self):
        """``mic_id`` on ``microphone_test_start`` must not be ``Any``."""
        ann = self._param_annotation_text("microphone_test_start", "mic_id")
        assert ann != "Any", (
            "YJ-7 regression: `microphone_test_start(self, mic_id: Any)`, "
            "`mic_id` must be narrowed to a concrete union (e.g. "
            "`str | None`).  The IPC handler validates it as `str | None`."
        )
        assert "str" in ann, (
            f"YJ-7 regression: `mic_id` annotation `{ann}` must mention `str` "
            f"(the renderer sends mic indices as strings)."
        )

    def test_microphone_test_start_duration_not_any(self):
        """``duration`` on ``microphone_test_start`` must not be ``Any``."""
        ann = self._param_annotation_text("microphone_test_start", "duration")
        assert ann != "Any", (
            "YJ-7 regression: `microphone_test_start(self, ..., duration: Any)` "
            "— `duration` must be narrowed (e.g. `float`).  The IPC handler "
            "coerces it to a float in [1.0, 60.0]."
        )

    def test_microphone_test_start_filters_not_any(self):
        """``filters`` on ``microphone_test_start`` must not be ``Any``."""
        ann = self._param_annotation_text("microphone_test_start", "filters")
        assert ann != "Any", (
            "YJ-7 regression: `microphone_test_start(self, ..., filters: Any)` "
            "— `filters` must be narrowed (e.g. `dict | None`)."
        )

    def test_level_monitor_start_mic_id_not_any(self):
        """``mic_id`` on ``level_monitor_start`` must not be ``Any``."""
        ann = self._param_annotation_text("level_monitor_start", "mic_id")
        assert ann != "Any", (
            "YJ-7 regression: `level_monitor_start(self, mic_id: Any)`: `mic_id` must be narrowed (e.g. `str | None`)."
        )

    def test_onboarding_set_microphone_mic_id_not_any(self):
        """``mic_id`` on ``onboarding_set_microphone`` must not be ``Any``."""
        ann = self._param_annotation_text("onboarding_set_microphone", "mic_id")
        assert ann != "Any", (
            "YJ-7 regression: `onboarding_set_microphone(self, mic_id: Any)`, "
            "`mic_id` must be narrowed (e.g. `str | None`)."
        )
