"""Unit tests for ``voice_typer/server/_lazy_import.py``.

PERF-COLDSTART-001 introduced ``_LazyModule`` as a stateless proxy that
defers ``import <name>`` to first attribute access.  These tests pin the
public contract of the module:

- ``lazy_module()`` returns a ``_LazyModule`` and never imports on
  construction.
- ``__getattr__`` triggers a real ``importlib.import_module`` call on
  every access (no successful-module caching — the proxy is stateless
  so per-test ``monkeypatch.setitem(sys.modules, ...)`` mocks are always
  honoured).
- ``__getattr__`` propagates ``ImportError`` / ``AttributeError`` — it
  never silently returns ``None``.
- G4-M-43: ``ImportError`` is CACHED on the proxy so subsequent accesses
  don't re-attempt ``importlib.import_module``. The cache is per-proxy
  (a fresh proxy gets a fresh attempt) so tests that fix the import via
  ``monkeypatch.setitem(sys.modules, name, mock)`` MUST construct a new
  proxy rather than reusing the failed one.
- ``__setattr__`` and ``__delattr__`` delegate to the wrapped module, so
  ``monkeypatch.setattr(proxy, attr, value)`` keeps working.
- Accessing the private ``_module_name`` slot does NOT trigger an
  import (slot lookup happens before ``__getattr__``).
- Multiple proxies are independent; two proxies for the same module
  share state through ``sys.modules``.
- G4-M-43: ``probe_required_deps()`` returns the list of missing
  modules from a probe set.

All tests use ``monkeypatch`` to install lightweight ``MagicMock``
fakes in ``sys.modules`` (or to spy on ``importlib.import_module``) —
no real audio, GUI, or network deps are loaded.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server._lazy_import import _LazyModule, lazy_module, probe_required_deps

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def fake_module(monkeypatch):
    """Install a MagicMock under a unique ``sys.modules`` key.

    Returns ``(name, mock)`` so individual tests can mutate the mock
    (or replace it) between accesses to verify the proxy re-resolves
    on every attribute lookup.
    """
    name = "_lazy_import_test_fake_module"
    mock = MagicMock(name=name)
    monkeypatch.setitem(sys.modules, name, mock)
    return name, mock


@pytest.fixture
def import_spy(monkeypatch):
    """Wrap ``importlib.import_module`` with a call-counting spy.

    Returns a dict ``{"calls": list[str], "real": Callable}`` so tests
    can assert on which module names were resolved and how many times.
    The real ``import_module`` is still invoked (so ``sys.modules``
    lookups behave normally), it is just observed.
    """
    state: dict = {"calls": []}
    real_import = importlib.import_module

    def spy(name, *args, **kwargs):
        state["calls"].append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", spy)
    return state


# ── lazy_module() factory ─────────────────────────────────────────────


def test_lazy_module_returns_lazy_module_instance():
    """``lazy_module()`` must return a ``_LazyModule`` proxy instance."""
    proxy = lazy_module("os")
    assert isinstance(proxy, _LazyModule)


def test_lazy_module_repr_shows_module_name():
    """``__repr__`` exposes the wrapped module name for debugging."""
    proxy = lazy_module("some.deeply.nested.module")
    assert repr(proxy) == "<_LazyModule 'some.deeply.nested.module'>"


def test_lazy_module_does_not_import_on_construction(import_spy):
    """Constructing a proxy must NOT trigger import — that's the whole
    point of PERF-COLDSTART-001 (defer heavy imports to first use).
    """
    proxy = lazy_module("os")
    # Just constructing the proxy should leave import_module untouched.
    assert proxy is not None
    assert import_spy["calls"] == []


# ── __getattr__ ───────────────────────────────────────────────────────


def test_getattr_triggers_import_on_first_access(fake_module, import_spy):
    """First attribute access resolves the wrapped module from
    ``sys.modules`` and returns the requested attribute.
    """
    name, mock = fake_module
    mock.some_attr = "hello"
    proxy = lazy_module(name)

    assert proxy.some_attr == "hello"
    assert import_spy["calls"] == [name]


def test_getattr_re_resolves_on_every_access_no_caching(fake_module, import_spy):
    """The proxy is stateless: every access re-resolves from
    ``sys.modules``.

    This is the documented contract that makes per-test ``monkeypatch``
    mocks safe (no stale caching, no cross-test leakage).  Note: this
    differs from a typical lazy-import cache that imports once — the
    design here explicitly re-resolves every time.
    """
    name, _ = fake_module
    proxy = lazy_module(name)

    proxy.foo
    proxy.foo
    proxy.foo

    assert import_spy["calls"] == [name, name, name]


def test_getattr_reflects_sys_modules_changes_between_accesses(fake_module):
    """If the entry in ``sys.modules`` is swapped between accesses, the
    proxy must pick up the new module — proving it re-resolves each
    time rather than caching the first resolved module.
    """
    name, mock1 = fake_module
    mock1.value = 1

    proxy = lazy_module(name)
    assert proxy.value == 1

    # Swap the module out from under the proxy.  Use direct assignment
    # rather than monkeypatch — the fixture's teardown will still
    # restore the pre-fixture state.
    mock2 = MagicMock()
    mock2.value = 2
    sys.modules[name] = mock2

    assert proxy.value == 2


def test_getattr_propagates_import_error(monkeypatch):
    """If ``import_module`` raises, the proxy must propagate — never
    silently return ``None`` or a MagicMock.
    """

    def boom(name, *args, **kwargs):
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr(importlib, "import_module", boom)
    proxy = lazy_module("nonexistent.module.xyz")

    with pytest.raises(ModuleNotFoundError):
        proxy.anything


def test_getattr_propagates_attribute_error_from_wrapped_module(monkeypatch):
    """``AttributeError`` from the wrapped module must propagate so
    ``hasattr(proxy, attr)`` returns ``False`` (rather than triggering
    a spurious import or returning a MagicMock).
    """

    class RealModule:
        existing = "yes"

    name = "_lazy_import_test_attr_error_module"
    monkeypatch.setitem(sys.modules, name, RealModule())
    proxy = lazy_module(name)

    assert proxy.existing == "yes"
    with pytest.raises(AttributeError):
        proxy.missing


def test_getattr_for_module_name_does_not_trigger_import(fake_module, import_spy):
    """Accessing the private ``_module_name`` slot must NOT trigger an
    import — slot lookup happens before ``__getattr__`` runs.
    """
    name, _ = fake_module
    proxy = lazy_module(name)

    # Should return the stored string, not call import_module.
    assert proxy._module_name == name
    assert import_spy["calls"] == []


# ── __setattr__ ───────────────────────────────────────────────────────


def test_setattr_delegates_to_wrapped_module(fake_module):
    """``__setattr__`` writes to the wrapped module, not the proxy.

    This is what makes the documented use case work::

        monkeypatch.setattr(recording.sd, "InputStream", fake)
    """
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy.new_value = 42

    assert mock.new_value == 42


def test_setattr_on_module_name_delegates_to_wrapped_not_slot(fake_module):
    """Setting ``proxy._module_name`` delegates to the wrapped module
    (because ``__setattr__`` always delegates).  It does NOT reassign
    the proxy's own slot — ``object.__setattr__`` is only used in
    ``__init__``.
    """
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy._module_name = "reassigned"

    # The wrapped module received the assignment.
    assert mock._module_name == "reassigned"
    # The proxy's actual slot is unchanged.
    assert object.__getattribute__(proxy, "_module_name") == name


def test_proxy_dict_delegates_to_wrapped_module(fake_module):
    """``__dict__`` is not a slot on ``_LazyModule``, so accessing it
    falls through to ``__getattr__`` and returns the wrapped module's
    ``__dict__`` — the proxy itself has no instance dict of its own.

    This is a useful transparency guarantee: code that inspects
    ``proxy.__dict__`` sees the wrapped module's namespace, not the
    proxy's internals.
    """
    name, mock = fake_module
    mock.some_key = "some_value"
    proxy = lazy_module(name)

    # proxy.__dict__ resolves to mock.__dict__ via __getattr__.
    assert proxy.__dict__ is mock.__dict__
    assert proxy.__dict__["some_key"] == "some_value"


def test_proxy_forwards_callable_attributes(fake_module):
    """Calling a function returned by ``proxy.fn(...)`` must invoke the
    wrapped module's ``fn`` with the same arguments — the proxy is a
    transparent drop-in for ``import <name> as m; m.fn(...)``.
    """
    name, mock = fake_module
    mock.fn.return_value = "called"
    proxy = lazy_module(name)

    result = proxy.fn(1, 2, key="val")

    assert result == "called"
    mock.fn.assert_called_once_with(1, 2, key="val")


# ── __delattr__ ───────────────────────────────────────────────────────


def test_delattr_delegates_to_wrapped_module(fake_module):
    """``__delattr__`` deletes the attribute on the wrapped module."""
    name, mock = fake_module
    mock.to_delete = "bye"
    proxy = lazy_module(name)

    del proxy.to_delete

    assert not hasattr(mock, "to_delete")


# ── Cross-proxy independence & sharing ────────────────────────────────


def test_multiple_proxies_for_different_modules_are_independent(monkeypatch):
    """Two proxies for different module names must not interfere."""
    mock_a = MagicMock()
    mock_b = MagicMock()
    mock_a.value = "a"
    mock_b.value = "b"
    monkeypatch.setitem(sys.modules, "_lazy_import_test_mod_a", mock_a)
    monkeypatch.setitem(sys.modules, "_lazy_import_test_mod_b", mock_b)

    proxy_a = lazy_module("_lazy_import_test_mod_a")
    proxy_b = lazy_module("_lazy_import_test_mod_b")

    assert proxy_a.value == "a"
    assert proxy_b.value == "b"


def test_two_proxies_for_same_module_share_state(fake_module):
    """Two proxies for the same module name both resolve to the same
    ``sys.modules`` entry, so mutations via one are visible from the
    other.
    """
    name, mock = fake_module
    mock.shared = "x"

    p1 = lazy_module(name)
    p2 = lazy_module(name)

    assert p1.shared == "x"
    assert p2.shared == "x"

    # Mutating via p1 is visible via p2.
    p1.shared = "y"
    assert p2.shared == "y"
    assert mock.shared == "y"


# ── Drop-in compatibility ────────────────────────────────────────────


def test_monkeypatch_setattr_on_proxy_then_access_works(monkeypatch, fake_module):
    """Documented use case: ``monkeypatch.setattr(proxy, attr, value)``
    followed by ``proxy.attr`` works because both go through the wrapped
    module — call sites in recording.py / tray_icon.py don't need to
    change when switching from a real ``import`` to ``lazy_module()``.
    """
    name, mock = fake_module
    proxy = lazy_module(name)

    monkeypatch.setattr(proxy, "InputStream", "fake_stream")

    assert proxy.InputStream == "fake_stream"
    assert mock.InputStream == "fake_stream"


# ── G4-M-43: ImportError caching ────────────────────────────────────────


class TestImportErrorCaching:
    """G4-M-43: ``ImportError`` is cached on the proxy so subsequent
    accesses don't re-attempt ``importlib.import_module``.

    The cache is per-proxy (a fresh proxy gets a fresh attempt) so
    tests that fix the import via ``monkeypatch.setitem(sys.modules,
    name, mock)`` after a failure MUST construct a new proxy rather
    than reusing the failed one.
    """

    def test_import_error_cached_so_second_access_no_reimport(self, monkeypatch, import_spy):
        """A second attribute access after a failed import does NOT call
        ``importlib.import_module`` again — the cached error is re-raised.
        """
        call_count = {"n": 0}

        def boom(name, *args, **kwargs):
            call_count["n"] += 1
            raise ModuleNotFoundError(f"No module named '{name}'")

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy = lazy_module("nonexistent.module.xyz")

        with pytest.raises(ModuleNotFoundError):
            proxy.foo
        with pytest.raises(ModuleNotFoundError):
            proxy.bar
        with pytest.raises(ModuleNotFoundError):
            proxy.baz

        # import_module called exactly once (not three times).
        assert call_count["n"] == 1, f"import_module should be called once (cached), got {call_count['n']}"

    def test_cached_error_is_same_instance(self, monkeypatch):
        """The cached error is the SAME exception instance — re-raised
        verbatim on every subsequent access, not a fresh copy.
        """
        original_error = ModuleNotFoundError("cached sentinel")

        def boom(name, *args, **kwargs):
            raise original_error

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy = lazy_module("nonexistent.module.cached")

        with pytest.raises(ModuleNotFoundError) as exc_info_1:
            proxy.foo
        with pytest.raises(ModuleNotFoundError) as exc_info_2:
            proxy.bar

        assert exc_info_1.value is original_error
        assert exc_info_2.value is original_error
        # Same instance both times.
        assert exc_info_1.value is exc_info_2.value

    def test_cached_error_does_not_affect_sibling_proxy(self, monkeypatch):
        """A failed import on one proxy does NOT poison a fresh proxy for
        the same module name — the cache is per-proxy, not per-module.
        This lets a test that fixes the import after a failure recover
        by constructing a new proxy.
        """
        # Save the real import_module so we can restore it after the
        # first proxy fails.
        real_import = importlib.import_module

        # First proxy fails.
        def boom(name, *args, **kwargs):
            raise ModuleNotFoundError(f"No module named '{name}'")

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy1 = lazy_module("nonexistent.module.sibling")
        with pytest.raises(ModuleNotFoundError):
            proxy1.foo

        # Second proxy for the same name gets a fresh attempt — install
        # a successful mock and restore the real import_module so the
        # proxy can resolve via sys.modules.
        mock = MagicMock()
        mock.value = "recovered"
        monkeypatch.setitem(sys.modules, "nonexistent.module.sibling", mock)
        monkeypatch.setattr(importlib, "import_module", real_import)
        proxy2 = lazy_module("nonexistent.module.sibling")
        assert proxy2.value == "recovered"

    def test_successful_import_does_not_cache_error(self, fake_module, import_spy):
        """A successful import leaves the cached-error slot ``None`` so
        subsequent accesses can re-resolve from ``sys.modules``.
        """
        name, mock = fake_module
        mock.value = "ok"
        proxy = lazy_module(name)

        # First access succeeds.
        assert proxy.value == "ok"
        # Cached error slot is still None.
        assert object.__getattribute__(proxy, "_cached_error") is None
        # Second access still re-resolves (no error cached to re-raise).
        assert proxy.value == "ok"
        # import_module called twice (no short-circuit on success).
        assert import_spy["calls"] == [name, name]


# ── G4-M-43: probe_required_deps ────────────────────────────────────────


class TestProbeRequiredDeps:
    """G4-M-43: ``probe_required_deps`` returns the list of missing modules."""

    def test_returns_empty_list_when_all_present(self, monkeypatch):
        """When every module in the probe set imports successfully, returns []."""
        # Install a MagicMock for each module name so import_module succeeds.
        for name in ("test_probe_present_a", "test_probe_present_b"):
            monkeypatch.setitem(sys.modules, name, MagicMock())

        missing = probe_required_deps(("test_probe_present_a", "test_probe_present_b"))
        assert missing == []

    def test_returns_missing_modules_sorted(self, monkeypatch):
        """Missing modules are returned sorted by name."""
        # Install one present module; leave the other two missing.
        monkeypatch.setitem(sys.modules, "test_probe_present", MagicMock())
        # Ensure the "missing" names really are missing.
        for name in ("test_probe_missing_zzz", "test_probe_missing_aaa"):
            monkeypatch.delitem(sys.modules, name, raising=False)

        missing = probe_required_deps(
            (
                "test_probe_missing_zzz",
                "test_probe_present",
                "test_probe_missing_aaa",
            )
        )
        assert missing == ["test_probe_missing_aaa", "test_probe_missing_zzz"]

    def test_default_probe_set_includes_sounddevice_and_pystray(self):
        """The default probe set includes the heavy cold-start deps."""
        # Don't actually probe — just check the constant.
        from voice_typer.server._lazy_import import _REQUIRED_DEPS

        assert "sounddevice" in _REQUIRED_DEPS
        assert "pystray" in _REQUIRED_DEPS
        # numpy is a hard dependency (audio buffers) — always required.
        assert "numpy" in _REQUIRED_DEPS

    def test_probe_with_empty_set_returns_empty_list(self):
        """An empty probe set returns an empty list (no work to do)."""
        assert probe_required_deps(()) == []

    def test_probe_does_not_cache_results(self, monkeypatch):
        """``probe_required_deps`` re-imports on every call so callers can
        re-probe after the user installs a missing package.
        """
        # First call: module missing.
        monkeypatch.delitem(sys.modules, "test_probe_recheck", raising=False)
        assert probe_required_deps(("test_probe_recheck",)) == ["test_probe_recheck"]

        # Second call: module now present (mock installed).
        monkeypatch.setitem(sys.modules, "test_probe_recheck", MagicMock())
        assert probe_required_deps(("test_probe_recheck",)) == []
