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

``ImportError`` is cached on the proxy so a missing dependency
is reported once (with a clear error) rather than re-attempted on every
attribute access — re-attempting can mask the root cause with a flood
of identical tracebacks and waste CPU on every call site. The cached
error is per-proxy (not per-module), so a different proxy for the same
module name still gets a fresh attempt — this lets tests inject a
failing import for one proxy while a sibling proxy resolves normally.
"""

from __future__ import annotations

import importlib
from typing import Any


class _LazyModule:
    """Transparent proxy that defers ``import <name>`` to first use.

    The proxy caches the most recent ``ImportError`` () so that
        a missing dependency is reported once with a clear traceback rather
        than re-attempted on every attribute access. The successful module
        is NOT cached — the proxy re-resolves from ``sys.modules`` on every
        access so per-test ``monkeypatch`` mocks are always honoured (see
        the module docstring for the test-safety rationale).

        Cache invalidation: ``reset_cache()`` clears the cached
        ``ImportError`` so the next attribute access re-attempts the real
        import. This is the recovery path for the case where a dependency
        becomes available AFTER the first failed access (e.g. a deferred
        installer finished, or a test fixed ``sys.modules`` after a
        failure). Without this method, the only recovery was to construct a
        new proxy — which is not always possible when the proxy is held as
        a module-level singleton (e.g. ``sd = lazy_module("sounddevice")``
        at the top of ``recording.py``).
    """

    __slots__ = ("_module_name", "_cached_error")

    def __init__(self, module_name: str) -> None:
        # Bypass our own __setattr__ (which delegates to the wrapped
        # module) when storing state on the proxy itself.
        object.__setattr__(self, "_module_name", module_name)
        # cache the most recent ImportError so a missing
        # dependency is reported once, not on every attribute access.
        # ``None`` means "no error cached — caller may attempt import".
        object.__setattr__(self, "_cached_error", None)

    def reset_cache(self) -> None:
        """Clear the cached ``ImportError`` so the next access re-imports.

        Safe to call at any time — clears the per-proxy error cache so
        the next ``__getattr__`` / ``__setattr__`` / ``__delattr__``
        re-resolves the wrapped module via ``importlib.import_module``.
        If the underlying module is now available (e.g. a deferred
        installer finished, or a test injected a mock into
        ``sys.modules``), the next access succeeds; otherwise the
        import fails again and the new error is cached afresh.

        Use this instead of constructing a new proxy when the proxy is
        held as a module-level singleton that cannot be re-bound
        conveniently (e.g. ``sd = lazy_module("sounddevice")`` at the
        top of ``recording.py`` — every callsite uses ``sd.X`` and
        cannot be redirected to a fresh proxy without a code change).
        """
        # Bypass our own __setattr__ (which would delegate to the
        # wrapped module — the very thing we're trying to recover from).
        object.__setattr__(self, "_cached_error", None)

    def _resolve(self):
        # if a previous attempt raised ImportError, re-raise
        # the cached error instead of re-attempting ``import_module``.
        # This prevents a flood of identical tracebacks when a missing
        # dependency is accessed from many call sites, and avoids the
        # CPU cost of repeated failed imports. The cache is reset only
        # by constructing a new proxy (per-proxy, not per-module) OR by
        # calling ``reset_cache()`` (the recovery path for proxies held
        # as module-level singletons).
        cached_error = object.__getattribute__(self, "_cached_error")
        if cached_error is not None:
            raise cached_error
        # import_module checks sys.modules first, so this is cheap after
        # the first real import and picks up test-injected mocks.
        module_name = object.__getattribute__(self, "_module_name")
        try:
            return importlib.import_module(module_name)
        except ImportError as exc:
            # cache the error so subsequent accesses don't
            # re-attempt the (likely still-failing) import.
            object.__setattr__(self, "_cached_error", exc)
            raise

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails — i.e. for
        # anything that isn't _module_name, _cached_error, or a class
        # attribute.  Every wrapped-module attribute goes through here.
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Delegate attribute assignment to the wrapped module in ``sys.modules``.

        (LOAD-BEARING — DO NOT REMOVE): this method MUTATES the real
                module object that lives in ``sys.modules`` (returned by
                ``self._resolve()`` → ``importlib.import_module``). It does NOT
                store the value on the proxy itself — the proxy is intentionally
                stateless for attribute reads (see the module docstring + ``__getattr__``).

                Why this matters
                -----------------
                ``__getattr__`` re-resolves the module from ``sys.modules`` on every
                access (per-test ``monkeypatch`` mocks must be honoured — see the
                module docstring). If ``__setattr__`` stored the value on the proxy
                (e.g. via ``object.__setattr__(self, name, value)``), the value
                would land in a location that ``__getattr__`` NEVER consults —
                ``__getattr__`` only runs when normal lookup fails, and the
                per-instance dict is bypassed by ``__slots__``. The result would be
                a write/read asymmetry: ``proxy.X = v`` would silently no-op,
                ``proxy.X`` would then raise ``AttributeError`` (or return the
                wrapped module's value), and the documented test pattern::

                    monkeypatch.setattr(recording.sd, "InputStream", fake)

                would silently fail to take effect, breaking the entire test
                fixture layer that injects fake ``sounddevice`` / ``pystray`` /
                ``pynput`` backends.

                Because modules are singletons in ``sys.modules``, the mutation is
                visible to ANY other code that imports the same module name —
                including other proxies for the same name (see
                ``test_two_proxies_for_same_module_share_state``). This is
                intentional and mirrors what would happen with a direct
                ``import sounddevice as sd; sd.InputStream = fake``.

                Removal would break: ``tests/test__lazy_import.py``
                (``test_setattr_delegates_to_wrapped_module``,
                ``test_monkeypatch_setattr_on_proxy_then_access_works``,
                ``test_two_proxies_for_same_module_share_state``) plus every
                production test that does ``monkeypatch.setattr(<lazy proxy>,
                <attr>, <fake>)`` to inject a fake backend.

                This behaviour is INTENTIONAL and load-bearing — it is the only
                way to keep the proxy transparent for both reads and writes when
                reads always re-resolve from ``sys.modules``.
        """
        setattr(self._resolve(), name, value)

    def __delattr__(self, name: str) -> None:
        """Delegate attribute deletion to the wrapped module in ``sys.modules``.

        (mirrors ``__setattr__``): deletion must mutate the real
                module so a subsequent ``__getattr__`` re-resolve sees the
                attribute as gone. Deleting on the proxy itself would be
                silently invisible to ``__getattr__`` for the same reason
                ``__setattr__`` would be (see ``__setattr__`` docstring).
        """
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
