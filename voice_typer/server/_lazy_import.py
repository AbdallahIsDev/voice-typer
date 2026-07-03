"""PERF-COLDSTART-001: lazy-import helpers for cold-start optimization.

Some third-party modules perform expensive work — or trigger hardware
side effects — at *import* time:

- ``pystray``: the Linux xorg backend runs ``Xlib.display.Display()``
  at module top, costing ~48 ms and failing entirely without an X
  display (headless CI, Wayland without XWayland).
- ``sounddevice``: loads the PortAudio C library and, on some platforms,
  probes audio hardware.

Importing these eagerly on the tray / app startup path adds tens of
milliseconds to cold start and can crash headless test runs.  The
helpers below defer the real import to first attribute access while
keeping the same call-site syntax (``sd.InputStream(...)``,
``pystray.Menu(...)``) — so no function body needs to change.

Design
------
``lazy_module("pystray")`` returns a stateless :class:`_LazyModule`
proxy.  Every attribute access re-resolves the module from
``sys.modules`` (via :func:`importlib.import_module`), which means:

- Production: the first access pays the import cost; later accesses are
  a ``sys.modules`` dict lookup (microseconds).
- Tests: the per-test ``monkeypatch.setitem(sys.modules, "pystray",
  mock)`` fixture (see ``tests/conftest.py``) is honoured on every
  access — no stale caching, no cross-test leakage.

Both ``getattr`` and ``setattr`` are delegated, so tests that do
``monkeypatch.setattr(recording.sd, "InputStream", fake)`` keep working
without modification.
"""

from __future__ import annotations

import importlib
from typing import Any


class _LazyModule:
    """Transparent proxy that defers ``import <name>`` to first use.

    The proxy is intentionally stateless apart from the module name: it
    never caches the resolved module, so attribute access always
    reflects the current contents of ``sys.modules``.  This makes it
    safe to share a single proxy across tests with independent mocks.
    """

    __slots__ = ("_module_name",)

    def __init__(self, module_name: str) -> None:
        # Bypass our own __setattr__ (which delegates to the wrapped
        # module) when storing the name on the proxy itself.
        object.__setattr__(self, "_module_name", module_name)

    def _resolve(self):
        # import_module checks sys.modules first, so this is cheap after
        # the first real import and picks up test-injected mocks.
        return importlib.import_module(object.__getattribute__(self, "_module_name"))

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails — i.e. for
        # anything that isn't _module_name or a class attribute.  Every
        # wrapped-module attribute goes through here.
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._resolve(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._resolve(), name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<_LazyModule {object.__getattribute__(self, '_module_name')!r}>"


def lazy_module(name: str) -> _LazyModule:
    """Return a transparent lazy proxy for the top-level package ``name``.

    Use it as a drop-in replacement for a module-level ``import``::

        # before:  import sounddevice as sd   (eager — loads PortAudio)
        # after:
        from voice_typer.server._lazy_import import lazy_module
        sd = lazy_module("sounddevice")  # PERF-COLDSTART-001

    All ``sd.X`` call sites work unchanged; the real import happens on
    first attribute access.
    """
    return _LazyModule(name)
