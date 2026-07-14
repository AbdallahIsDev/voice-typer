"""Regression tests for ARCH-REFAC-004: DI boundary for ``IPCServer``.

These tests verify three properties of the dependency-injection seam
introduced in ``voice_typer/server/providers.py`` and the
``IPCServer.__init__`` signature change:

1. **DI mode works**: ``IPCServer(app, service=fake)`` stores the
   injected service verbatim and does NOT construct a real
   ``VoiceTyperService``.
2. **Backward compat works**: ``IPCServer(app)`` (no ``service``
   argument) still constructs a real ``VoiceTyperService`` over
   ``app``, exactly as before ARCH-REFAC-004.  All 20+ existing test
   files that instantiate ``IPCServer(app)`` with a MagicMock ``app``
   continue to work.
3. **Protocol drift detection**: ``AppProtocol`` and
   ``ServiceProtocol`` declare every attribute / method that the IPC
   handler mixins actually access.  If a future handler starts reading
   ``self.app.new_field`` without ``new_field`` being declared on
   ``AppProtocol``, the introspection test fails — forcing an
   explicit decision about whether to widen the protocol or refactor
   the handler to go through the service layer.

The factory :func:`voice_typer.server.providers.build_ipc_server` is
also exercised to confirm it returns a working ``IPCServer``.
"""

from __future__ import annotations

import ast

# Mock heavy imports BEFORE importing the server stack — mirrors the
# pattern in tests/test_server.py.  Without this, pystray tries to
# connect to an X display on Linux and crashes in headless CI.
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

_mock_pystray = MagicMock()
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

from voice_typer.server.ipc_server import IPCServer  # noqa: E402
from voice_typer.server.providers import (  # noqa: E402
    AppProtocol,
    ServiceProtocol,
    build_ipc_server,
)
from voice_typer.server.service import VoiceTyperService  # noqa: E402

# ── Helpers ─────────────────────────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parent.parent
_HANDLERS_DIR = _REPO_ROOT / "voice_typer" / "server" / "handlers"
_IPC_SERVER_PY = _REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"


def _protocol_declared_names(proto_cls) -> set:
    """Return every name declared on a ``typing.Protocol`` class.

    Combines:

    - Annotated data attributes (``__annotations__`` keys) — e.g.
      ``config``, ``history_db``, ``_shutting_down``.
    - Methods defined directly on the class (callable values in
      ``__dict__``) — e.g. ``change_model``, ``toggle_dictation``.

    Excludes dunder attributes (``__init__``, ``__repr__``, etc.) and
    names inherited from ``Protocol`` / ``object``.
    """
    names = set()
    # Annotated data attributes (no default value → only in __annotations__).
    names.update(getattr(proto_cls, "__annotations__", {}).keys())
    # Methods (def foo(self, ...): ...) — only those defined directly
    # on THIS class, not inherited from Protocol/object.
    for name, value in vars(proto_cls).items():
        if name.startswith("__"):
            continue
        if callable(value):
            names.add(name)
    return names


def _collect_attr_accesses(py_path: Path, base_attr: str) -> set:
    """Return attribute names accessed via ``self.<base_attr>.X``.

    Walks the AST of ``py_path`` and collects every ``X`` where the
    source contains ``self.<base_attr>.X`` (e.g. ``self.app.config``
    yields ``"config"``).  Handles chained accesses like
    ``self.app.config.save()`` (returns just ``"config"`` — the
    immediate attribute on ``self.<base_attr>``).

    Parameters
    ----------
    py_path :
        Path to a ``.py`` file to introspect.
    base_attr :
        The attribute on ``self`` to look for — typically ``"app"``
        (for ``AppProtocol`` drift detection) or ``"service"``
        (for ``ServiceProtocol`` drift detection).
    """
    source = py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_path))
    used: set[str] = set()
    for node in ast.walk(tree):
        # We're looking for `self.<base_attr>.<something>` — that's
        # ast.Attribute(value=ast.Attribute(value=ast.Name('self'),
        # attr=<base_attr>), attr=<something>).
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


def _all_handler_files() -> list:
    """Return every ``.py`` file under ``voice_typer/server/handlers/``."""
    return sorted(p for p in _HANDLERS_DIR.glob("*.py") if p.name != "__init__.py")


# ── Tests: DI mode ─────────────────────────────────────────────────────


class TestDIInjection:
    """Verify ``IPCServer(app, service=fake)`` DI mode."""

    def test_ipc_server_accepts_injected_service(self):
        """``service=<fake>`` is stored verbatim; no VoiceTyperService constructed."""
        from tests.fixtures.ipc_test_helpers import (
            make_fake_app,
            make_fake_service,
        )

        fake_app = make_fake_app()
        fake_service = make_fake_service()
        server = IPCServer(fake_app, service=fake_service)

        # The injected service must be the exact object passed in —
        # not a VoiceTyperService wrapper, not a copy.
        assert server.service is fake_service
        assert server.app is fake_app
        # Type check: confirms the DI seam is in effect.
        assert not isinstance(server.service, VoiceTyperService), (
            "DI mode must NOT construct a real VoiceTyperService — "
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

        # The injected service must have been called — not a real
        # VoiceTyperService that would have tried to read
        # app.tray.state.value etc.
        fake_service.get_status.assert_called_once()
        assert result["type"] == "status"
        assert result["id"] == 1
        assert result["data"]["status"] == "recording"
        assert result["data"]["xruns_since_start"] == 7


# ── Tests: backward compatibility ──────────────────────────────────────


class TestBackwardCompat:
    """Verify ``IPCServer(app)`` (no service) still constructs a real service."""

    def test_ipc_server_backward_compat_constructs_service_from_app(self):
        """``IPCServer(app)`` must construct a real ``VoiceTyperService``.

        This is the path used by all 20+ existing test files and the
        production entry point.  The DI seam must not change it.
        """
        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        server = IPCServer(fake_app)

        # The server must have constructed a real VoiceTyperService
        # over the app — not stored None, not stored a MagicMock.
        assert isinstance(server.service, VoiceTyperService), (
            "IPCServer(app) without `service=` must construct a real "
            "VoiceTyperService over `app` — this is the backward-compat "
            "path that all existing call sites depend on."
        )
        # And the service must have been wired to the same app.
        assert server.service._app is fake_app
        assert server.app is fake_app

    def test_ipc_server_default_service_argument_is_none(self):
        """The ``service`` parameter defaults to ``None`` (not a sentinel).

        This is what makes ``IPCServer(app)`` (positional, no keyword)
        equivalent to ``IPCServer(app, service=None)`` and triggers the
        backward-compat branch.
        """
        import inspect

        sig = inspect.signature(IPCServer.__init__)
        service_param = sig.parameters["service"]
        assert service_param.default is None, "service= must default to None so IPCServer(app) keeps working."
        # app must remain the first positional parameter.
        params = list(sig.parameters.keys())
        assert params[0] == "self"
        assert params[1] == "app", "app must remain the first positional parameter so IPCServer(app) continues to work."

    def test_existing_mock_app_pattern_still_works(self):
        """A plain ``MagicMock()`` app (no AppProtocol import) must work.

        This mirrors the pattern in tests/test_server.py:MockApp and
        ~20 other test files — they pass a MagicMock (or a hand-rolled
        MockApp) positionally to IPCServer(app).  The DI refactor
        must not require them to import AppProtocol.
        """
        app = MagicMock()
        app._config_mutation_lock = threading.RLock()
        app._shutting_down = False
        server = IPCServer(app)
        # Just confirm construction succeeds and the service is real.
        assert isinstance(server.service, VoiceTyperService)
        # And dispatching a simple command doesn't crash.
        app.tray.state.value = "idle"
        result = server._dispatch({"type": "get_status"})
        assert result["type"] == "status"


# ── Tests: protocol drift detection ────────────────────────────────────


class TestProtocolDrift:
    """Verify ``AppProtocol`` / ``ServiceProtocol`` cover handler usage.

    These tests are the regression guard for protocol drift: if a
    future handler starts reading ``self.app.new_field``, the
    AppProtocol test fails until ``new_field`` is added to the
    protocol (forcing an explicit decision about whether the new
    access is a smell or an accepted widening of the surface).
    """

    def test_app_protocol_lists_all_attributes_used_by_handlers(self):
        """``AppProtocol`` must declare every ``self.app.X`` accessed.

        Walks every handler under ``voice_typer/server/handlers/`` AND
        ``voice_typer/server/ipc_server.py`` (the IPCServer class itself
        also reaches into ``self.app.X`` for tray hooking / shutdown
        detection) and collects every attribute name accessed via
        ``self.app.<name>``.  Each must be declared on
        ``AppProtocol`` — either as an annotated data attribute
        (in ``__annotations__``) or as a method (in ``__dict__``).
        """
        used_attrs: set[str] = set()
        for handler_py in _all_handler_files():
            used_attrs |= _collect_attr_accesses(handler_py, base_attr="app")
        # Also include self.app.X accesses in ipc_server.py itself —
        # IPCServer.__init__, .start(), ._hook_tray_set_state(), and
        # the TCP connection handler all reach into self.app.X.
        used_attrs |= _collect_attr_accesses(_IPC_SERVER_PY, base_attr="app")

        declared = _protocol_declared_names(AppProtocol)

        missing = used_attrs - declared
        assert not missing, (
            f"AppProtocol is missing {len(missing)} attribute(s) that "
            f"handlers/ipc_server access via self.app.<name>: "
            f"{sorted(missing)}.  Either add these to AppProtocol (if "
            f"the access is an accepted part of the IPC layer's "
            f"contract with the app) or refactor the handler to go "
            f"through the service layer (preferred — the protocol "
            f"surface should stay small)."
        )

    def test_service_protocol_lists_all_methods_used_by_handlers(self):
        """``ServiceProtocol`` must declare every ``self.service.X`` called.

        Mirrors the AppProtocol drift test but for the service layer.
        Excludes private attributes (names starting with ``_``) —
        e.g. ``self.service._app`` is an implementation detail of
        ``VoiceTyperService`` and should NOT be part of the public
        service contract; handlers that reach into it are a smell
        (status_handlers._handle_get_audio_status does this today
        and is a candidate for future cleanup).
        """
        used_attrs: set[str] = set()
        for handler_py in _all_handler_files():
            used_attrs |= _collect_attr_accesses(handler_py, base_attr="service")
        # Also include self.service.X accesses in ipc_server.py.
        used_attrs |= _collect_attr_accesses(_IPC_SERVER_PY, base_attr="service")

        # Filter out private attributes — _app is the main one and is
        # intentionally NOT in ServiceProtocol (handlers reaching into
        # service._app is a leaky abstraction; the right fix is to
        # add a public method to the service for whatever the handler
        # needed from _app).
        public_used = {name for name in used_attrs if not name.startswith("_")}

        declared = _protocol_declared_names(ServiceProtocol)

        missing = public_used - declared
        assert not missing, (
            f"ServiceProtocol is missing {len(missing)} method(s) that "
            f"handlers/ipc_server call via self.service.<name>: "
            f"{sorted(missing)}.  Add these to ServiceProtocol so the "
            f"contract is explicit and the introspection test catches "
            f"future drift."
        )

    def test_app_protocol_declares_documented_members(self):
        """Smoke test: ``AppProtocol`` declares the key documented members.

        Catches accidental removal of a member during a refactor.
        The set below mirrors the post-ADR-0008-§3.1 surface: the
        public domain objects (``config``, ``history_db``, ``models``,
        ``recording``, ``hotkeys``, ``recorder``, ``tray``), the
        private attributes handlers / ipc_server still access
        (``_ipc_server``, ``_shutting_down``, ``_esc_cancel_paused``),
        and the methods the service layer delegates to the app
        (``change_model``, ``toggle_dictation``, ``undo_last``,
        ``repaste_last``, ``restart_app``, ``quit_app``, ``quit``,
        ``start``).

        TASK-2 (ADR 0008 §3.1) removed ``_audio_processor``,
        ``_volume_ducker``, and ``_config_mutation_lock`` — those
        private attrs are no longer accessed by handlers because the
        ``get_audio_status``, ``get_volume_backend_status``, and
        ``apply_config`` paths now go through :class:`ServiceProtocol`.
        """
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
                f"AppProtocol must declare `{required}` — it's a core domain object the IPC layer exposes."
            )
        # Private attributes handlers / ipc_server still access
        # post-ADR-0008-§3.1.
        for required in (
            "_ipc_server",
            "_shutting_down",
            "_esc_cancel_paused",
        ):
            assert required in declared, (
                f"AppProtocol must declare `{required}` — handlers or ipc_server.py access it via self.app.{required}."
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
                f"AppProtocol must declare `{required}()` — the service layer delegates this call to the app."
            )
        # TASK-2: private attrs removed from AppProtocol because the
        # service layer now wraps their access.  These MUST NOT be
        # re-added — handlers reaching into them is a smell that ADR
        # 0008 §3.1 explicitly prohibits.
        for forbidden in (
            "_audio_processor",
            "_volume_ducker",
            "_config_mutation_lock",
        ):
            assert forbidden not in declared, (
                f"AppProtocol must NOT declare `{forbidden}` post-"
                f"ADR-0008-§3.1 — the service layer (get_audio_status / "
                f"get_volume_backend_status / apply_config) wraps its "
                f"access.  Re-adding it would re-introduce the leaky "
                f"abstraction the refactor removed."
            )

    def test_service_protocol_declares_core_methods(self):
        """Smoke test: ``ServiceProtocol`` declares the core service methods."""
        declared = _protocol_declared_names(ServiceProtocol)
        # A representative sample — the full surface is large; this
        # test just catches accidental removal of the most-used ones.
        for required in (
            "get_status",
            "toggle_dictation",
            "undo_last",
            "get_config",
            "set_config",
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
            "export_diagnostics",
            "apply_config_side_effects",
            # TASK-2 (ADR 0008 §3.1): new service-layer wrappers for
            # private-attr access previously done in handlers.
            "get_audio_status",
            "change_model",
            "set_active_backend",
            "apply_config",
            "force_cancel_transcription",
        ):
            assert required in declared, (
                f"ServiceProtocol must declare `{required}()` — it's part of the service surface handlers call."
            )


# ── Tests: factory (composition root) ──────────────────────────────────


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
        # Must have constructed a real VoiceTyperService (the factory
        # is the production path — it does NOT accept an injected
        # service; tests that want DI should call IPCServer(app,
        # service=fake) directly).
        assert isinstance(server.service, VoiceTyperService)
        # The server must be dispatch-ready: a basic get_status call
        # shouldn't crash.
        fake_app.tray.state.value = "idle"
        result = server._dispatch({"id": 42, "type": "get_status"})
        assert result["id"] == 42
        assert result["type"] == "status"

    def test_build_ipc_server_does_not_accept_service_kwarg(self):
        """``build_ipc_server`` is the production path — no DI.

        Tests that want DI should call ``IPCServer(app, service=fake)``
        directly.  Keeping the factory signature simple means future
        wiring (logging, metrics, feature flags) lives in ONE place
        (the factory body) rather than being threaded through every
        call site.
        """
        import inspect

        sig = inspect.signature(build_ipc_server)
        params = list(sig.parameters.keys())
        assert params == ["app"], (
            "build_ipc_server should take exactly one parameter (app) — "
            f"got {params}.  The factory is the production composition "
            f"root; tests that need DI should call IPCServer(app, "
            f"service=fake) directly."
        )


# ── Tests: protocol structural compatibility ───────────────────────────


def _structurally_satisfies(obj, proto_cls) -> bool:
    """Return True if ``obj`` has every attribute/method on ``proto_cls``.

    This is a manual structural check that works for ``MagicMock``
    instances (which fail ``isinstance`` against
    ``runtime_checkable`` Protocols because Python's
    ``Protocol.__instancecheck__`` uses ``inspect.getattr_static``,
    which doesn't trigger ``MagicMock.__getattr__``).  Using our own
    ``hasattr``-based check is correct for both real objects and
    MagicMock-based fakes.
    """
    return all(hasattr(obj, name) for name in _protocol_declared_names(proto_cls))


class TestProtocolStructuralCompat:
    """Verify fakes and real objects structurally satisfy the protocols.

    Note on ``runtime_checkable`` Protocol and ``MagicMock``: Python's
    ``isinstance(obj, Protocol)`` check for ``runtime_checkable``
    Protocols uses ``inspect.getattr_static``, which does NOT trigger
    ``MagicMock.__getattr__``.  This means a ``MagicMock``-based fake
    fails ``isinstance`` even though it has every required attribute.
    We use a manual ``hasattr``-based check below instead.
    """

    def test_fake_app_satisfies_app_protocol(self):
        """``make_fake_app()`` returns an object with every AppProtocol member.

        Verifies that every annotated data attribute AND every method
        declared on ``AppProtocol`` is present on the fake app.  For
        annotated attributes (``config``, ``history_db``, etc.) we
        additionally check that ``make_fake_app`` explicitly configured
        them (not just relying on MagicMock's auto-stub) — this catches
        drift where a new attribute is added to the protocol but
        ``make_fake_app`` isn't updated.
        """
        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        assert _structurally_satisfies(fake_app, AppProtocol), (
            "make_fake_app() must return an object that has every "
            "attribute/method declared on AppProtocol — otherwise "
            "tests using the fake would diverge from the real contract."
        )

        # Stricter check: every annotated data attribute on AppProtocol
        # must be EXPLICITLY set on the fake (not auto-stubbed by
        # MagicMock).  This catches the case where someone adds a new
        # annotated attribute to AppProtocol but forgets to add it to
        # make_fake_app — the test would still pass the structural
        # check above (MagicMock auto-stubs) but the fake wouldn't have
        # the right default value (e.g. a real RLock for
        # _config_mutation_lock vs. a child mock that breaks `with`).
        annotated = set(getattr(AppProtocol, "__annotations__", {}).keys())
        # The fake sets attributes on the instance, not the class —
        # so we check via __dict__ on the instance.
        fake_app_dict = {k: v for k, v in vars(fake_app).items() if not k.startswith("_mock")}
        for name in annotated:
            # Skip dunder / MagicMock-internal attrs.
            if name.startswith("__"):
                continue
            assert name in fake_app_dict, (
                f"make_fake_app() does NOT explicitly set `{name}` — "
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
        """The real ``VoiceTyperService`` must structurally satisfy ``ServiceProtocol``.

        Guards against the protocol drifting from the concrete
        implementation: if someone renames a method on
        ``VoiceTyperService`` without updating the protocol, this
        test fails.  Uses ``isinstance`` (which works for real
        classes — only MagicMock fails the runtime_checkable check).
        """
        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        real_service = VoiceTyperService(fake_app)
        # isinstance works here because VoiceTyperService is a real
        # class with real methods on its type's __dict__.
        assert isinstance(real_service, ServiceProtocol), (
            "VoiceTyperService must structurally satisfy "
            "ServiceProtocol — if not, the protocol has drifted "
            "from the implementation."
        )
        # Also confirm via our manual check (belt-and-suspenders).
        assert _structurally_satisfies(real_service, ServiceProtocol)
